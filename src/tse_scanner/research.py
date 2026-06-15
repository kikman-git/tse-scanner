"""On-demand deep fundamental research per ticker.

The scanner reads positioning + price state; it cannot know *why* shorts are short or whether a
公募増資 is about to kill the squeeze. This module is the wrapper around one headless `claude -p`
run that answers those questions:

  - builds a **context pack** (scan row, margin history, params, best-effort yfinance snapshot)
    so the agent starts from ground truth,
  - spawns `claude` with the repo's `.claude/skills/deep-research/SKILL.md` playbook —
    orchestrator = `TSE_RESEARCH_MODEL` (synthesis), collectors = Sonnet subagents (ingest),
  - enforces the rails: freshness TTL, wall-clock timeout, max-turns, tool allowlist
    (`WebSearch,WebFetch,Read,Write,Glob,Task` — no Bash, no Edit, no DB),
  - and owns **all DuckDB writes** (the agent writes files only; DuckDB is single-writer
    across processes): `running` row at spawn → `done`/`failed` row on exit.

Artifacts land in  data/research/<code>/<YYYY-MM-DD>/{pack.md, memo.md, verdict.json, raw/, run.log}.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params, Settings, load_settings

if TYPE_CHECKING:
    from tse_scanner.data.store import Store

# Repo root (…/src/tse_scanner/research.py → two levels up) — `claude` must run here so the
# project-level.claude/ (skill + collector agent) is discovered.
REPO_ROOT = Path(__file__).resolve().parents[2]

SKILL_REL = ".claude/skills/deep-research/SKILL.md"
ALLOWED_TOOLS = "WebSearch,WebFetch,Read,Write,Glob,Task,Agent"
VERDICTS = {"SUPPORTS", "NEUTRAL", "OPPOSES"}


@dataclass
class ResearchResult:
    code: str
    research_date: date
    status: str                      # done | failed | skipped (TTL)
    verdict: str | None = None
    one_liner: str | None = None
    memo_path: str | None = None
    run_dir: Path | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    duration_s: float | None = None
    error: str | None = None
    pack_warnings: list[str] = field(default_factory=list)


# --- context pack -----------------------------------------------------------------------------

_SECTOR_RS_SESSIONS = 20     # ~1 month — the sympathy/relative-strength window
_SECTOR_RS_BAND = 0.02       # peer median vs TOPIX beyond ±2% = tailwind / headwind


def _fmt(v, nd: int = 0) -> str:
    return "—" if v is None or v != v else f"{v:,.{nd}f}"


def _exit_map_block(code: str, scan_row, store: "Store", params: Params) -> str | None:
    """The technical exit map — price-structure targets as the counterweight to the analyst
    target-price cluster. Prefers the scan's persisted numbers (what the UI shows); recomputes
    from bars only when the row predates the exit-map columns or the name was never scanned."""
    from tse_scanner.signals import coil as coil_mod
    from tse_scanner.signals import targets as tgt_mod

    vals: dict = {}
    if scan_row is not None and scan_row.get("t1") is not None and scan_row.get("t1") == scan_row.get("t1"):
        vals = {k: scan_row.get(k) for k in
                ("t1", "t2", "atr", "rr_t1", "next_resistance", "high_52w", "blue_sky",
                 "box_high", "box_low", "last_close")}
        entry_ref = max(vals["last_close"] or 0, vals["box_high"] or 0)
        stop_ref = vals["box_low"]
    else:
        bars = store.prices(code, start=date.today() - timedelta(days=730))
        if bars is None or len(bars) < 30:
            return None
        ohlc = bars.tail(params.exit_swing_lookback + 10)
        c = coil_mod.assess(ohlc, params)
        em = tgt_mod.compute(ohlc, c.box_high, c.box_low, params)
        vals = {"t1": em.t1, "t2": em.t2, "atr": em.atr, "rr_t1": em.rr_t1,
                "next_resistance": em.resistances[0] if em.resistances else None,
                "high_52w": em.high_52w, "blue_sky": em.blue_sky,
                "box_high": c.box_high, "box_low": c.box_low,
                "last_close": float(ohlc["close"].iloc[-1])}
        entry_ref, stop_ref = em.entry_ref, em.stop_ref

    if not entry_ref or entry_ref != entry_ref or entry_ref <= 0:
        return None
    pct = lambda t: f"{(t / entry_ref - 1):+.1%}" if t and t == t else "—"  # noqa: E731
    return (
        "## Technical exit map (computed from price structure — ground truth, like the scanner state)\n"
        f"- entry ref: {_fmt(entry_ref)}   structural stop: {_fmt(stop_ref)} (box low)   "
        f"R:R at T1: {_fmt(vals.get('rr_t1'), 1)}\n"
        f"- T1 (box measured move, 節目-snapped): {_fmt(vals.get('t1'))} ({pct(vals.get('t1'))})   "
        f"T2 (stretch): {_fmt(vals.get('t2'))} ({pct(vals.get('t2'))})\n"
        f"- next overhead supply: {_fmt(vals.get('next_resistance'))}   "
        f"52w high: {_fmt(vals.get('high_52w'))}   "
        f"blue sky (no overhead supply): {'YES — trail, don’t cap' if vals.get('blue_sky') else 'no'}\n"
        f"- ATR(14): {_fmt(vals.get('atr'), 1)}   backtest barrier for comparison: "
        f"+{params.target_pct:.0%} / −{params.stop_pct:.0%} / {params.horizon_sessions} sessions\n"
        "- Memo §Valuation/§Trade must reconcile this map with the published analyst cluster: "
        "whichever ceiling is *lower* binds first; analyst TPs **below** T1 = expect supply early.\n"
    )


def _sector_block(code: str, listed, scan, store: "Store") -> str | None:
    """Computed sector & sympathy context — squeezes cluster by theme, so peers' state frames
    the single name. Numbers only (33業種 peers from the universe, stages from today's scan,
    relative strength vs TOPIX); collectors verify the narrative *why*."""
    import pandas as pd

    if listed is None or listed.empty or "sector33" not in listed.columns:
        return None
    me = listed[listed["code"] == code]
    if not len(me):
        return None
    sector = me.iloc[0].get("sector33")
    if sector is None or sector != sector or not str(sector).strip():
        return None
    peers = listed[(listed["sector33"] == sector) & (listed["code"] != code)]["code"].tolist()
    if not peers:
        return None

    lines = [f"## Sector context (computed — 33業種: {sector})"]
    in_scan = scan[scan["code"].isin(peers)] if scan is not None and not scan.empty else None
    rs_codes = [code]
    if in_scan is not None and len(in_scan):
        stages = in_scan["stage"].value_counts().to_dict()
        active = " · ".join(f"{s} {n}" for s, n in stages.items() if s != "NONE")
        lines.append(f"- peers in today's universe: {len(in_scan)}"
                     + (f" — stages: {active}" if active else " — none beyond NONE"))
        if "has_fuel" in in_scan.columns:
            n_fuel = int(in_scan["has_fuel"].fillna(False).sum())
            lines.append(f"- crowded-short peers (fuel gate on): {n_fuel}/{len(in_scan)}"
                         + (" → sympathy-squeeze backdrop" if n_fuel >= 2 else ""))
        rs_codes += in_scan["code"].tolist()

    # Relative strength: median peer return vs TOPIX over the sympathy window.
    n = _SECTOR_RS_SESSIONS
    px = store.all_prices(rs_codes, start=date.today() - timedelta(days=90))
    rets: dict[str, float] = {}
    if px is not None and not px.empty:
        for c, g in px.sort_values("date").groupby("code"):
            cl = g["close"].tail(n + 1)
            if len(cl) == n + 1 and cl.iloc[0] > 0:
                rets[c] = float(cl.iloc[-1] / cl.iloc[0] - 1)
    peer_rets = pd.Series([v for k, v in rets.items() if k != code], dtype=float)
    tpx = store.topix(start=date.today() - timedelta(days=90))
    tpx_ret = None
    if tpx is not None and len(tpx) > n:
        cl = tpx["close"].tail(n + 1)
        tpx_ret = float(cl.iloc[-1] / cl.iloc[0] - 1)
    if len(peer_rets) and tpx_ret is not None:
        med = float(peer_rets.median())
        own = rets.get(code)
        diff = med - tpx_ret
        trend = ("sector tailwind" if diff >= _SECTOR_RS_BAND
                 else "sector headwind" if diff <= -_SECTOR_RS_BAND else "sector neutral")
        lines.append(
            f"- {n}-session returns: peers median {med:+.1%} ({len(peer_rets)} names)"
            + (f" · {code} itself {own:+.1%}" if own is not None else "")
            + f" · TOPIX {tpx_ret:+.1%} → **{trend}**")
    lines.append("- Squeezes cluster by theme: peers also squeezing/breaking out supports "
                 "follow-through; a lone breakout against a falling sector argues for the "
                 "mechanical exit. Verify the narrative (sector news/theme) via collectors.")
    return "\n".join(lines) + "\n"


def build_pack(code: str, store: "Store", params: Params = PARAMS) -> tuple[str, list[str]]:
    """Serialize what the store already knows about `code` into a markdown pack.

    Ground truth in the prompt beats the agent re-deriving (or hallucinating) it. Returns
    (markdown, warnings) — warnings surface gaps (e.g. never scanned) instead of failing.
    """
    warnings: list[str] = []
    jst = datetime.now(timezone(timedelta(hours=9)))
    parts: list[str] = [f"# Context pack — {code} (generated {jst:%Y-%m-%d %H:%M} JST)\n"]

    listed = store.listed()
    li = listed[listed["code"] == code] if not listed.empty else listed
    if li is not None and len(li):
        r = li.iloc[0]
        parts.append(f"**Name:** {r['name']}   **Segment:** {r['segment']}   "
                     f"**Sector33:** {r.get('sector33')}   **貸借銘柄:** {bool(r['is_taishaku'])}\n")
    else:
        warnings.append("code not in listed_info — name/segment unknown to the store")

    scan = store.read_scan()
    row = scan[scan["code"] == code] if not scan.empty else scan
    if row is not None and len(row):
        r = row.iloc[0]
        parts.append(
            "## Scanner state (technical/positioning — treat as ground truth)\n"
            f"- scan date: {r['scan_date']}   stage: **{r['stage']}**   score: {r['score']:.2f}\n"
            f"- fuel: {r['fuel']:.2f}   coil: {r['coil']:.2f}   ignition: {r['ignition']}\n"
            f"- days-to-cover: {r['days_to_cover']:.1f}   信用倍率: {r['credit_ratio']:.2f}\n"
            f"- 信用売残: {r['short_total']:,.0f}   信用買残: {r['long_total']:,.0f}\n"
            f"- BB-width pct: {r['bb_width_pct']:.2f}   box: {r['box_low']:,.0f}–{r['box_high']:,.0f}"
            f"   last close: {r['last_close']:,.0f}   ADV: ¥{r['adv_yen']:,.0f}\n"
)
    else:
        warnings.append("no scan row — researching outside the scanner's candidate set")

    scan_row = row.iloc[0] if row is not None and len(row) else None
    try:  # best-effort blocks below: a research memo without them still beats no memo
        em = _exit_map_block(code, scan_row, store, params)
        if em:
            parts.append(em)
        else:
            warnings.append("technical exit map unavailable (not enough bars)")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"technical exit map unavailable ({type(e).__name__})")

    try:
        sec = _sector_block(code, listed, scan, store)
        if sec:
            parts.append(sec)
        else:
            warnings.append("sector context unavailable (no sector33/peers in store)")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"sector context unavailable ({type(e).__name__})")

    mh = store.margin_history(code, prints=12)
    if mh is not None and len(mh):
        cols = ["as_of_date", "published_date", "short_total", "long_total"]
        parts.append("## Weekly margin history (oldest→newest; reads are publication-date safe)\n"
                     + mh[cols].to_string(index=False) + "\n")
    else:
        warnings.append("no weekly margin history in store")

    try:  # best-effort fundamentals snapshot; yfinance.info is flaky — never fail the pack on it
        import yfinance as yf

        info = yf.Ticker(f"{code}.T").info or {}
        keep = {k: info.get(k) for k in
                ("marketCap", "trailingPE", "priceToBook", "sharesOutstanding", "floatShares",
                 "totalCash", "totalDebt", "longName") if info.get(k) is not None}
        if keep:
            parts.append("## Yahoo snapshot (best-effort, may be stale)\n"
                         + "\n".join(f"- {k}: {v:,}" if isinstance(v, (int, float)) else f"- {k}: {v}"
                                     for k, v in keep.items()) + "\n")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"yfinance snapshot unavailable ({type(e).__name__})")

    parts.append(
        "## Trade mechanics this memo must serve\n"
        f"- Long-only squeeze entry on ignition; first-touch barriers: target +{params.target_pct:.0%}, "
        f"stop -{params.stop_pct:.0%}, horizon {params.horizon_sessions} sessions.\n"
        "- Exits are two ceilings: the technical exit map above (price structure) and the published "
        "analyst TP cluster (fundamentals) — the memo reconciles them; the lower one binds first.\n"
        "- The verdict is a display-only overlay for the human — it never feeds the scan score.\n"
)
    if warnings:
        parts.append("## Pack warnings\n" + "\n".join(f"- ⚠️ {w}" for w in warnings) + "\n")
    return "\n".join(parts), warnings


# --- claude envelope --------------------------------------------------------------------------

def _parse_envelope(log_text: str) -> dict:
    """Pull the final `--output-format json` result object out of the run log (stderr noise mixed in)."""
    for line in reversed(log_text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (obj.get("type") == "result" or "total_cost_usd" in obj):
            return obj
    return {}


def _read_artifacts(run_dir: Path) -> tuple[dict | None, str | None, str | None]:
    """Returns (verdict_dict, memo_text, error). Files are the agent's completion contract."""
    vpath, mpath = run_dir / "verdict.json", run_dir / "memo.md"
    if not vpath.exists():
        return None, None, "agent did not produce verdict.json (see run.log)"
    try:
        v = json.loads(vpath.read_text())
    except json.JSONDecodeError as e:
        return None, None, f"verdict.json is not valid JSON: {e}"
    if v.get("verdict") not in VERDICTS:
        return None, None, f"verdict.json has invalid verdict {v.get('verdict')!r} (need {sorted(VERDICTS)})"
    if not mpath.exists():
        return None, None, "agent did not produce memo.md (see run.log)"
    return v, mpath.read_text(), None


# --- the run ----------------------------------------------------------------------------------

def run_research(code: str, *, force: bool = False, settings: Settings | None = None,
                 store: "Store | None" = None, params: Params = PARAMS) -> ResearchResult:
    from tse_scanner.data.store import Store

    settings = settings or load_settings()
    store = store or Store(settings)
    store.init_schema()
    today = date.today()

    # Freshness TTL — research is expensive; a memo < ttl_days old answers today's question too.
    if not force:
        prev = store.latest_research(code, status="done")
        if len(prev):
            prev_date = prev.iloc[0]["research_date"]
            prev_date = prev_date.date() if hasattr(prev_date, "date") else prev_date
            if prev_date >= today - timedelta(days=settings.research_ttl_days):
                return ResearchResult(
                    code=code, research_date=prev_date, status="skipped",
                    verdict=prev.iloc[0]["verdict"], one_liner=prev.iloc[0]["one_liner"],
                    memo_path=prev.iloc[0]["memo_path"],
                    error=f"fresh memo from {prev_date} (< {settings.research_ttl_days}d; --force to re-run)",
)

    run_dir = (settings.data_dir / "research" / code / today.isoformat()).resolve()
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    pack_md, pack_warnings = build_pack(code, store, params)
    pack_path = run_dir / "pack.md"
    pack_path.write_text(pack_md)

    started = datetime.now()
    # Record our PID on the 'running' row: a reader (the API) can then check real process
    # liveness and fail a crashed run immediately, instead of blind-waiting out a stale timer.
    store.upsert_research({"code": code, "research_date": today, "status": "running",
                           "model": settings.research_model, "started_at": started,
                           "pid": os.getpid()})

    prompt = (
        f"Read the file {SKILL_REL} and execute it now.\n"
        f"code={code}\npack={pack_path}\nout={run_dir}\n"
        "Follow the skill exactly: delegate ingestion to research-collector subagents, write "
        "memo.md and verdict.json into the out directory, and stop."
)
    cmd = [settings.claude_bin, "-p", prompt,
           "--model", settings.research_model,
           "--max-turns", str(settings.research_max_turns),
           "--output-format", "json",
           "--allowedTools", ALLOWED_TOOLS,
           "--add-dir", str(run_dir)]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # allow nested headless runs

    log_path = run_dir / "run.log"
    error: str | None = None
    with log_path.open("w") as log:
        try:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=REPO_ROOT, env=env,
                           timeout=settings.research_timeout_s, check=False)
        except subprocess.TimeoutExpired:
            error = f"timed out after {settings.research_timeout_s}s"
        except FileNotFoundError:
            error = f"claude binary not found ({settings.claude_bin!r}) — set TSE_CLAUDE_BIN"

    # Finalize inside a guard: any crash here (envelope parse, artifact read, DB write) must still
    # replace the 'running' row, never orphan it. A hard kill (OOM/SIGKILL) skips this — that case
    # is caught by the reader's PID liveness check — but a catchable error self-heals to 'failed'.
    try:
        envelope = _parse_envelope(log_path.read_text()) if log_path.exists() else {}
        verdict, memo_md, art_err = (None, None, error) if error else _read_artifacts(run_dir)
        if verdict is None and not error and envelope.get("is_error"):
            # the agent died API-side (429/5xx/limit) — its own words beat "no verdict.json"
            art_err = f"claude run errored: {envelope.get('result') or envelope.get('subtype')}"
        finished = datetime.now()

        row = {
            "code": code, "research_date": today, "model": settings.research_model,
            "started_at": started, "finished_at": finished,
            "cost_usd": envelope.get("total_cost_usd"), "num_turns": envelope.get("num_turns"),
            "duration_s": (envelope.get("duration_ms") or 0) / 1000 or (finished - started).total_seconds(),
            "memo_path": str(run_dir / "memo.md"),
        }
        if verdict is None:
            row.update({"status": "failed", "error": art_err or "unknown failure"})
            store.upsert_research(row)
            return ResearchResult(code=code, research_date=today, status="failed", error=row["error"],
                                  run_dir=run_dir, cost_usd=row["cost_usd"], num_turns=row["num_turns"],
                                  duration_s=row["duration_s"], pack_warnings=pack_warnings)

        row.update({
            "status": "done", "verdict": verdict["verdict"],
            "confidence": verdict.get("confidence"), "one_liner": verdict.get("one_liner"),
            "squeeze_killers": json.dumps(verdict.get("squeeze_killers", []), ensure_ascii=False),
            "catalysts": json.dumps(verdict.get("catalysts", []), ensure_ascii=False),
            "analyst_targets": json.dumps(verdict.get("analyst_targets", []), ensure_ascii=False),
            "sector_view": (json.dumps(verdict["sector_view"], ensure_ascii=False)
                            if verdict.get("sector_view") else None),
            "sources": json.dumps(verdict.get("sources", []), ensure_ascii=False),
            "memo_md": memo_md,
        })
        store.upsert_research(row)
        return ResearchResult(code=code, research_date=today, status="done",
                              verdict=verdict["verdict"], one_liner=verdict.get("one_liner"),
                              memo_path=row["memo_path"], run_dir=run_dir,
                              cost_usd=row["cost_usd"], num_turns=row["num_turns"],
                              duration_s=row["duration_s"], pack_warnings=pack_warnings)
    except Exception as e:  # noqa: BLE001 — never leave the 'running' row dangling
        try:
            store.upsert_research({"code": code, "research_date": today, "status": "failed",
                                   "model": settings.research_model, "started_at": started,
                                   "finished_at": datetime.now(),
                                   "error": f"wrapper crashed finalizing: {type(e).__name__}: {e}"})
        except Exception:  # noqa: BLE001 — preserve the original traceback if even this fails
            pass
        raise
