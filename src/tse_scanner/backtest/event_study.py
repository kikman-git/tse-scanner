"""Point-in-time event study — the go/no-go gate (Phase 1 — DESIGN.md §6).

For every historical *Igniting* signal, compute the outcome using **only data available that day**,
then aggregate into a tradeable expectancy and compare to TOPIX.

Non-negotiables (or the backtest lies — DESIGN.md §6):
  1. **Publication-date margin** — read weekly margin at its publication date, never its Friday
     as-of date (§6.1). Enforced by the store's `margin_asof` / `latest_margin`.
  2. **Stop-high no-fill** — locked-限界 entries are missed (§6.2). Enforced by labels.first_touch.
  3. **Point-in-time universe** — include delisted / de-margined names (no survivorship bias).
  4. **Walk-forward** parameter tuning; benchmark vs **TOPIX**.

Output: expectancy (win-rate × payoff in R), hit-rate, Primed→Igniting progression, vs-TOPIX edge.
**Be willing to kill the project here if the edge isn't real** (DESIGN.md §9, Phase 1).

History note (free path): JPX keeps only a handful of weekly margin PDFs, so historical *Igniting*
events are sparse until the store has been accumulated forward (or a paid J-Quants history pull is
done — DESIGN.md §5). The study below runs correctly on whatever history exists and reports
`n_events` honestly; a thin store yields a small/zero `n_events`, not a fabricated edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Callable

from tse_scanner.config import PARAMS, Params
from tse_scanner.backtest.labels import Label, first_touch_detail

if TYPE_CHECKING:
    from tse_scanner.data.store import Store


@dataclass
class EventStudyResult:
    n_events: int
    win_rate: float
    expectancy_r: float          # win-rate × payoff, in R
    vs_topix: float              # mean excess R over TOPIX, matched windows (NaN if no benchmark)
    primed_to_igniting_rate: float  # validation by-product (DESIGN.md §2)
    label_counts: dict = field(default_factory=dict)
    splits: dict = field(default_factory=dict)  # named sub-cohorts: {name: {n, win_rate, expectancy_r}}
    note: str = ""


def _r_multiple(ret: float, params: Params) -> float:
    """Convert a realized return into R units (1R = the stop distance)."""
    return ret / params.stop_pct if params.stop_pct else 0.0


def run_event_study(start: date, end: date, params: Params = PARAMS,
                    store: "Store | None" = None,
                    progress: "Callable[[str], None] | None" = None,
                    cadence: str = "daily") -> EventStudyResult:
    """Replay history point-in-time, label each ignition, aggregate. This is the GO/NO-GO.

    `cadence` sets the replay clock:
      - "daily" (default): step on **every trading day** — the representative gate. Fuel still
        reads point-in-time inside `run_scan` (latest weekly margin published ≤ d — margin is
        constant intra-week, so this is exact, not an approximation); only coil/ignition move daily.
      - "weekly": step on margin **publication dates** only — the original §6.1 cadence (when fuel
        state changes). Cheap, but ignition (a daily breakout bar) rarely lands on one, so it
        badly under-samples events.
    """
    import numpy as np
    import pandas as pd

    from tse_scanner.data.store import Store
    from tse_scanner.scan import run_scan

    store = store or Store()

    # Build the replay clock (see `cadence` in the docstring). Either way, scan as-of each date,
    # collect IGNITING events, label forward — the scan reads margin point-in-time internally.
    if cadence == "daily":
        tdf = store.topix(start, end)  # date-indexed ascending
        if not tdf.empty:
            replay_dates = [pd.Timestamp(x).date() for x in tdf.index]
        else:  # no TOPIX stored → fall back to the distinct trading calendar in prices
            with store.connect() as con:
                rows = con.execute(
                    "SELECT DISTINCT date FROM prices WHERE date BETWEEN ? AND ? ORDER BY date",
                    [start, end]).fetchall()
            replay_dates = [r[0] for r in rows]
        unit = "trading day"
    elif cadence == "weekly":
        with store.connect() as con:
            pubs = con.execute(
                """SELECT DISTINCT published_date FROM weekly_margin
                   WHERE published_date IS NOT NULL AND published_date BETWEEN ? AND ?
                   ORDER BY published_date""",
                [start, end],
            ).fetchall()
        replay_dates = [p[0] for p in pubs]
        unit = "publication date"
    else:
        raise ValueError(f"cadence must be 'weekly' or 'daily', got {cadence!r}")

    n_dates = len(replay_dates)
    step = max(1, n_dates // 20)  # ~20 progress prints regardless of cadence
    events: list[dict] = []
    open_until: dict[str, "pd.Timestamp"] = {}  # code → no re-entry before this date (dedup)
    primed = igniting_from_primed = 0

    for i, d in enumerate(replay_dates, 1):
        if progress and (i % step == 0 or i == n_dates):
            progress(f"  replay {i}/{n_dates} ({unit} {d}) — {len(events)} events so far")
        res = run_scan(as_of=d, params=params, store=store, write=False)
        if res.table is None or res.table.empty:
            continue
        primed += int((res.table["stage"] == "PRIMED").sum())
        igniting_from_primed += int((res.table["stage"] == "IGNITING").sum())

        d_ts = pd.Timestamp(d)
        for r in res.table[res.table["stage"] == "IGNITING"].itertuples(index=False):
            # Dedup — one position per name at a time. Under daily cadence the same breakout flags
            # IGNITING for several consecutive sessions; skip re-entry until the prior trade's
            # forward window has resolved so a single squeeze is counted once.
            blocked = open_until.get(r.code)
            if blocked is not None and d_ts < blocked:
                continue
            fwd = store.prices(r.code, start=d)  # entry day onward
            if fwd.empty:
                continue
            fwd = fwd.reset_index()
            entry_idx = int((fwd["date"] <= d_ts).sum()) - 1
            if entry_idx < 0:
                continue
            ft = first_touch_detail(entry_idx, fwd, params)
            # 優待クロス proxy (PLAN.md Track B §6, from the 8179 finding): hedged 優待 shorts
            # live mostly in 一般信用 — a high neg_short share at entry may mark "fake fuel".
            # Recorded as a *split*, never a score input, until the study earns it weight.
            neg_share = None
            mg = store.margin_asof(r.code, d)
            if not mg.empty:
                st_, ns = mg.iloc[0]["short_total"], mg.iloc[0]["neg_short"]
                if pd.notna(st_) and float(st_) > 0 and pd.notna(ns):
                    neg_share = float(ns) / float(st_)
            exit_date = (fwd.iloc[ft.exit_idx]["date"] if ft.exit_idx is not None else None)
            # Cooldown until the trade resolves (its exit bar). If it never resolves in-window
            # (NO_FILL / data runs out), block for the full horizon so it can't re-fire next session.
            open_until[r.code] = (pd.Timestamp(exit_date) if exit_date is not None
                                  else d_ts + pd.Timedelta(days=2 * params.horizon_sessions))
            events.append({"code": r.code, "date": d, "label": ft.label, "ret": ft.ret,
                           "exit_date": exit_date, "neg_share": neg_share})

    n = len(events)
    if n == 0:
        return EventStudyResult(
            n_events=0, win_rate=float("nan"), expectancy_r=float("nan"),
            vs_topix=float("nan"),
            primed_to_igniting_rate=(igniting_from_primed / primed if primed else float("nan")),
            label_counts={}, note=(
                f"No Igniting events in [{start}..{end}] over {n_dates} {unit}(s) ({cadence} cadence). "
                "On weekly cadence this is expected — ignition is a daily breakout that rarely lands "
                "on a publication Tuesday; re-run with cadence='daily' (DESIGN.md §6)."
            ),
        )

    ev = pd.DataFrame(events)
    filled = ev[ev["label"] != Label.NO_FILL]
    wins = int((filled["label"] == Label.WIN).sum())
    win_rate = wins / len(filled) if len(filled) else float("nan")
    r_mults = [_r_multiple(x, params) for x in filled["ret"].dropna()]
    expectancy_r = float(np.mean(r_mults)) if r_mults else float("nan")
    counts = {lbl.value: int((ev["label"] == lbl).sum()) for lbl in Label}

    # vs TOPIX (§6 non-negotiable #4): per-event excess return over the matched entry→exit
    # window, in R units. Needs the topix table (Track B sync); NaN when absent.
    vs_topix = float("nan")
    topix = store.topix()
    if not topix.empty and len(filled):
        tclose = topix["close"]
        excess: list[float] = []
        for e in filled.itertuples(index=False):
            if e.ret is None or e.exit_date is None:
                continue
            t0 = tclose.asof(pd.Timestamp(e.date))
            t1 = tclose.asof(pd.Timestamp(e.exit_date))
            if t0 == t0 and t1 == t1 and t0 > 0:
                excess.append(_r_multiple(e.ret - (t1 / t0 - 1.0), params))
        if excess:
            vs_topix = float(np.mean(excess))

    def _cohort(sub: "pd.DataFrame") -> dict:
        rs = [_r_multiple(x, params) for x in sub["ret"].dropna()]
        return {"n": int(len(sub)),
                "win_rate": (int((sub["label"] == Label.WIN).sum()) / len(sub)) if len(sub) else float("nan"),
                "expectancy_r": float(np.mean(rs)) if rs else float("nan")}

    splits: dict = {}
    with_share = filled[filled["neg_share"].notna()]
    if len(with_share):
        splits["neg_short≥50% (優待クロス proxy)"] = _cohort(with_share[with_share["neg_share"] >= 0.5])
        splits["neg_short<50%"] = _cohort(with_share[with_share["neg_share"] < 0.5])

    return EventStudyResult(
        n_events=n, win_rate=win_rate, expectancy_r=expectancy_r,
        vs_topix=vs_topix,
        primed_to_igniting_rate=(igniting_from_primed / primed if primed else float("nan")),
        label_counts=counts, splits=splits,
        note=f"{len(filled)} filled / {counts.get('no_fill', 0)} stop-high no-fill across "
             f"{n_dates} {unit}(s) ({cadence} cadence).",
    )
