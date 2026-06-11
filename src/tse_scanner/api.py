"""FastAPI view layer over the Store (PLAN.md Track A — DESIGN.md §8, §14).

Presentation split: the entire Python core (store/signals/backtest/research) stays; this API is
a thin, read-mostly skin for the Next.js front-end (`web/`). Loopback-only by deployment
(`uvicorn --host 127.0.0.1 --port 8602`; the Next server proxies browser calls), so there is no
auth layer here — do not bind it wider.

Mutations spawn the CLI detached and return 202; state lives in the store / log files, never in
this process. DuckDB single-writer stays safe because every read here is a sub-second Store
connection and the spawned children are separate processes (Store.connect retries transient lock
collisions).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date

import pandas as pd
from fastapi import FastAPI, HTTPException, Response

from tse_scanner.config import load_settings
from tse_scanner.data.store import Store
from tse_scanner.research import REPO_ROOT

app = FastAPI(title="tse-scanner API", version="1")

# Server-global child registry (one uvicorn worker process — keep it that way; a second worker
# would mean two registries and duplicate idempotency state).
_procs: dict = {"rescan": None, "rescan_started": 0.0, "rescan_failed": None, "research": {}}

_RUNNING_STALE_S = 20 * 60  # 'running' research row older than this = crashed run (DESIGN.md §15)


def _store() -> Store:
    return Store()


def _iso(v) -> str | None:
    """date/Timestamp/str → 'YYYY-MM-DD' (None passed through) — header freshness fields."""
    if v is None or v != v:
        return None
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _records(df: "pd.DataFrame") -> list[dict]:
    """DataFrame → JSON-safe records: NaN/NaT → null, dates/timestamps → ISO strings."""
    if df is None or df.empty:
        return []
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
        elif df[c].dtype == object:
            df[c] = df[c].map(
                lambda v: v.isoformat() if isinstance(v, (date, pd.Timestamp)) else v)
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


# --- reads ---------------------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"ok": True, "source": load_settings().data_source}


@app.get("/summary")
def summary() -> dict:
    out = _store().summary()
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in out.items()}


@app.get("/scan")
def scan(scan_date: str | None = None) -> dict:
    """Latest scan table + the freshness header fields (§6.1 lag made visible)."""
    store = _store()
    df = store.read_scan(pd.to_datetime(scan_date).date() if scan_date else None)
    if df.empty:
        return {"scan_date": None, "rows": [], "counts": {}, "margin_as_of": None,
                "margin_published": None, "verdicts": {}}
    first = df.iloc[0]
    m_asof = df["margin_as_of"].dropna()
    m_pub = df["margin_published"].dropna()
    verdicts = {
        r["code"]: r for r in _records(store.research_verdicts())
    }
    return {
        "scan_date": _iso(first["scan_date"]),
        "margin_as_of": _iso(m_asof.iloc[0]) if len(m_asof) else None,
        "margin_published": _iso(m_pub.iloc[0]) if len(m_pub) else None,
        "counts": df["stage"].value_counts().to_dict(),
        "rows": _records(df),
        "verdicts": verdicts,
    }


@app.get("/prices/{code}")
def prices(code: str, sessions: int = 260) -> dict:
    df = _store().prices(code)
    if df.empty:
        return {"code": code, "bars": []}
    return {"code": code, "bars": _records(df.tail(sessions).reset_index())}


@app.get("/margin/{code}")
def margin(code: str, prints: int = 26) -> dict:
    df = _store().margin_history(code, prints=prints)
    return {"code": code, "prints": _records(df)}


@app.get("/research")
def research_verdicts() -> dict:
    return {"verdicts": _records(_store().research_verdicts())}


def _research_alive(raw: "pd.Series") -> bool:
    """Is a 'running' research row backed by a live wrapper process? (DESIGN.md §15)

    Two guards, both required to read 'running':
      - age: a legit run can't outlast the wrapper's timeout; past `_RUNNING_STALE_S` it is a
        hang/crash regardless of PID (also defends against PID reuse).
      - PID: the wrapper records its PID; a same-host reader checks `os.kill(pid, 0)`, so a dead
        process fails the run immediately instead of blind-waiting out the stale timer.
    Legacy rows without a PID fall back to the age guard alone.
    """
    started = raw.get("started_at")
    if started is None or started != started:  # NaT / missing → can't be a live run
        return False
    if time.time() - pd.Timestamp(started).timestamp() > _RUNNING_STALE_S:
        return False
    pid = raw.get("pid")
    if pid is None or pid != pid:  # legacy row: trust the age guard above
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False  # wrapper gone without finalizing → crashed
    except PermissionError:
        return True   # exists but another uid (not expected on this loopback host)


@app.get("/research/{code}")
def research(code: str) -> dict:
    """Latest research row for `code` + derived state for the panel's 4 states
    (none / running / failed / done — DESIGN.md §15). A 'running' row whose process is dead or
    past the staleness window is reported as failed."""
    df = _store().latest_research(code)
    if df.empty:
        spawned = _procs["research"].get(code)
        running = spawned is not None and (spawned.poll() is None)
        return {"code": code, "state": "running" if running else "none", "row": None}
    raw = df.iloc[0]  # full-precision timestamps + pid (the _records projection truncates dates)
    state = raw["status"]
    if state == "running" and not _research_alive(raw):
        state = "failed"
    row = _records(df)[0]
    for ts in ("started_at", "finished_at"):  # restore time-of-day _records dropped
        v = raw.get(ts)
        row[ts] = None if (v is None or v != v) else pd.Timestamp(v).isoformat()
    if state == "failed" and raw["status"] == "running":
        row["status"] = "failed"
        row["error"] = row.get("error") or "run crashed before writing a result (stale 'running' row)"
    return {"code": code, "state": state, "row": row}


# --- mutations: spawn the CLI detached, 202, poll via GET ----------------------------------------
def _rescan_running() -> bool:
    p = _procs["rescan"]
    return p is not None and p.poll() is None


@app.post("/rescan", status_code=202)
def rescan(response: Response) -> dict:
    """Kick off `tse-scanner rescan` detached (idempotent: already-running → 200 + running)."""
    if _rescan_running():
        response.status_code = 200
        return {"running": True, "started_at": _procs["rescan_started"]}
    log = REPO_ROOT / "data" / "rescan.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as out:  # truncate: status tails this; stale lines would mislead
        _procs["rescan"] = subprocess.Popen(
            [sys.executable, "-m", "tse_scanner", "rescan"],
            stdout=out, stderr=subprocess.STDOUT, cwd=REPO_ROOT, start_new_session=True)
    _procs["rescan_started"] = time.time()
    _procs["rescan_failed"] = None
    return {"running": True, "started_at": _procs["rescan_started"]}


@app.get("/rescan/status")
def rescan_status() -> dict:
    p = _procs["rescan"]
    if p is not None and p.poll() is not None:  # reap + record outcome
        if p.returncode != 0:
            _procs["rescan_failed"] = p.returncode
        _procs["rescan"] = None
    last = ""
    try:
        lines = (REPO_ROOT / "data" / "rescan.log").read_text(errors="replace").strip().splitlines()
        last = lines[-1].strip()[:140] if lines else ""
    except OSError:
        pass
    return {
        "running": _rescan_running(),
        "started_at": _procs["rescan_started"] or None,
        "last_log_line": last,
        "failed_rc": _procs["rescan_failed"],
    }


@app.post("/research/{code}", status_code=202)
def spawn_research(code: str, response: Response, force: bool = False) -> dict:
    """Launch `tse-scanner research <code>` detached (the wrapper owns the §15 lifecycle)."""
    if not code.isalnum() or not (4 <= len(code) <= 5):
        raise HTTPException(422, "code must be a 4–5 char TSE issue code")
    prev = _procs["research"].get(code)
    if prev is not None and prev.poll() is None:
        response.status_code = 200
        return {"code": code, "state": "running"}
    log_dir = REPO_ROOT / "data" / "research" / code
    log_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "tse_scanner", "research", code] + (["--force"] if force else [])
    with (log_dir / "cli.log").open("ab") as out:
        _procs["research"][code] = subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT, cwd=REPO_ROOT, start_new_session=True)
    return {"code": code, "state": "running"}
