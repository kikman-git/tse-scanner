"""FastAPI view layer over the Store.

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
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Response

from tse_scanner.config import load_settings
from tse_scanner.data.store import Store
from tse_scanner.research import REPO_ROOT

app = FastAPI(title="tse-scanner API", version="1")

# Server-global child registry (one uvicorn worker process — keep it that way; a second worker
# would mean two registries and duplicate idempotency state).
_procs: dict = {"rescan": None, "rescan_started": 0.0, "rescan_failed": None, "research": {}}

_RUNNING_STALE_S = 20 * 60  # 'running' research row older than this = crashed run


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
    """Latest scan table + the freshness header fields (lag made visible)."""
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
    """Is a 'running' research row backed by a live wrapper process?

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
    (none / running / failed / done). A 'running' row whose process is dead or
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


# --- jobs: visibility into the long-running batch work --------------------------------------------
# Jobs are *discovered*, not registered: every batch process already writes a log under data/
# (the API's own spawns, CLI runs from a shell, chained background replays). The list below is
# therefore complete by construction — anything that logs shows up, whoever started it.

_JOB_FRESH_S = 150          # log written this recently = running even without a matched process
_LOG_TAIL_BYTES = 64 * 1024  # read at most this much from the file end per request

# replay 360/731 (trading day 2024-11-26) — 43 events so far
_RE_REPLAY = re.compile(r"replay (\d+)/(\d+)")
_RE_EVENTS = re.compile(r"—\s*(\d+) events")
# fins: 1200/4000 codes … | bars: 100/2900 new codes … | margin: 26/507 weeks …
_RE_STEP = re.compile(r"(?:fins|bars|margin):\s*(\d+)/(\d+)")
_RE_FAILED = re.compile(r"traceback \(most recent|failed|error:", re.IGNORECASE)
_RE_BACKTEST_DONE = re.compile(r"^\s*events:\s*\d+")


def _job_kind(name: str) -> str:
    if name.startswith("backtest"):
        return "backtest"
    if name.startswith(("rescan", "ingest", "jquants")):
        return "ingest"
    if name.startswith(("api", "web")):
        return "service"
    return "batch"


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    """Last `max_lines` of a log, reading only the file's tail (logs grow to MBs)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - _LOG_TAIL_BYTES))
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = chunk.splitlines()
    if size > _LOG_TAIL_BYTES and lines:
        lines = lines[1:]  # first line is almost surely clipped mid-way
    return lines[-max_lines:]


def _parse_progress(lines: list[str]) -> tuple[dict | None, str | None]:
    """(progress, terminal_state_hint) from a log tail. Hint: 'done' | 'failed' | None."""
    progress = None
    hint = None
    for ln in lines:
        m = _RE_REPLAY.search(ln)
        if not m:
            m2 = _RE_STEP.search(ln)
            if m2:
                done, total = int(m2.group(1)), int(m2.group(2))
                progress = {"done": done, "total": total,
                            "pct": round(100.0 * done / total, 1) if total else None,
                            "detail": ln.strip()}
            continue
        done, total = int(m.group(1)), int(m.group(2))
        progress = {"done": done, "total": total,
                    "pct": round(100.0 * done / total, 1) if total else None,
                    "detail": ln.strip()}
        ev = _RE_EVENTS.search(ln)
        if ev:
            progress["events"] = int(ev.group(1))
    for ln in lines:
        if _RE_BACKTEST_DONE.match(ln):
            hint = "done"
        elif _RE_FAILED.search(ln):
            # keep scanning — a later success line (e.g. a finished study) outranks a mid-log error
            hint = hint or "failed"
    return progress, hint


def _batch_processes() -> list[dict]:
    """Live tse-scanner batch processes (any spawner), via one `ps` pass. Excludes this API."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,etimes,args", "--no-headers"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    procs = []
    for ln in out.splitlines():
        parts = ln.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, etimes, cmd = parts
        if ("tse_scanner" not in cmd and "tse-scanner" not in cmd) or "api:app" in cmd:
            continue
        if cmd.startswith(("ps ", "grep ")) or " -eo " in cmd:
            continue
        kind = "batch"
        for kw, k in (("backtest", "backtest"), ("rescan", "ingest"),
                      ("ingest", "ingest"), ("research", "research")):
            if kw in cmd:
                kind = k
                break
        procs.append({"pid": int(pid), "elapsed_s": int(etimes), "cmd": cmd[:200], "kind": kind})
    return procs


def _list_jobs() -> dict:
    data_dir = REPO_ROOT / "data"
    procs = _batch_processes()
    live_kinds = {p["kind"] for p in procs}
    now = time.time()

    paths = sorted(data_dir.glob("*.log")) + sorted(data_dir.glob("research/*/cli.log"))
    jobs = []
    for p in paths:
        try:
            st = p.stat()
        except OSError:
            continue
        rel = p.relative_to(data_dir).as_posix()
        name = rel[:-len(".log")].replace("/", "·")
        kind = "research" if rel.startswith("research/") else _job_kind(p.name)
        lines = _tail_lines(p, 40)
        progress, hint = _parse_progress(lines)
        age = now - st.st_mtime
        running = age <= _JOB_FRESH_S
        jobs.append({
            "id": rel, "label": name, "kind": kind,
            "running": running, "status": "running" if running else (hint or "idle"),
            "progress": progress,
            "last_line": (lines[-1].strip()[:200] if lines else ""),
            "mtime": st.st_mtime, "age_s": round(age), "size": st.st_size,
            "pids": [],
        })

    # A live process marks the freshest log of its kind running even between sparse progress
    # prints (a 10y daily replay can go ~25 min between lines — mtime alone would read 'idle').
    for kind in live_kinds:
        cand = [j for j in jobs if j["kind"] == kind and j["status"] != "done"]
        if not cand:
            continue
        newest = max(cand, key=lambda j: j["mtime"])
        newest["running"] = True
        newest["status"] = "running"
        newest["pids"] = [p["pid"] for p in procs if p["kind"] == kind]

    jobs.sort(key=lambda j: (not j["running"], j["kind"] == "service", -j["mtime"]))
    return {"jobs": jobs, "processes": procs, "ts": now}


@app.get("/jobs")
def jobs() -> dict:
    return _list_jobs()


@app.get("/jobs/log")
def job_log(id: str, tail: int = 400) -> dict:
    """Tail one job log. `id` is the path relative to data/ as listed by /jobs — re-validated
    here (resolved path must stay inside data/ and end in .log; no traversal)."""
    data_dir = (REPO_ROOT / "data").resolve()
    try:
        path = (data_dir / id).resolve()
    except OSError:
        raise HTTPException(422, "bad id")
    if not path.is_relative_to(data_dir) or path.suffix != ".log":
        raise HTTPException(422, "id must be a .log under data/")
    if not path.exists():
        raise HTTPException(404, f"no such log: {id}")
    st = path.stat()
    lines = _tail_lines(path, max(1, min(int(tail), 2000)))
    return {"id": id, "lines": lines, "size": st.st_size, "mtime": st.st_mtime,
            "running": (time.time() - st.st_mtime) <= _JOB_FRESH_S}


@app.get("/playbook")
def playbook() -> dict:
    """PLAYBOOK.md verbatim — the web Playbook tab renders it, so edits to the file ship to the
    UI without a rebuild. The markdown file stays the single source of truth."""
    p = REPO_ROOT / "PLAYBOOK.md"
    if not p.exists():
        raise HTTPException(404, "PLAYBOOK.md not found in the repo root")
    st = p.stat()
    return {"markdown": p.read_text(encoding="utf-8"), "mtime": st.st_mtime,
            "updated": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))}


@app.post("/research/{code}", status_code=202)
def spawn_research(code: str, response: Response, force: bool = False) -> dict:
    """Launch `tse-scanner research <code>` detached (the wrapper owns the lifecycle)."""
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
