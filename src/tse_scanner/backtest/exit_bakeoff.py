"""Exit-model bake-off — hold the entries fixed, swap only the exit, measure the difference.

The squeeze/deadline studies showed the *median* trade exits at −1R: a fixed −6% stop sits inside
small/mid-cap noise. This harness re-labels the **same entry events** (read from a study's per-event
parquet) under each exit model in `labels.simulate_exit` — fixed (the legacy barriers), `atr`
(barriers at entry ± k·ATR), `atr_trail` (chandelier) — so the only thing that varies is the exit.
That isolates the exit model's contribution: if `atr`/`atr_trail` lift expectancy and pull the
median off −1R across setups *and* windows, the exit geometry was the leak, not the entry signal.

Point-in-time is preserved: entry is the first bar on/after the event's signal date, and ATR is
computed only from bars up to and including that entry bar. The harness is a *measurement* tool —
it does not tune the multipliers (those are committed first-principles values in Params); it reports
fixed-vs-official as a sanity check that the re-derived entries match the source study.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from tse_scanner.config import PARAMS, Params
from tse_scanner.backtest.labels import (
    Label, exit_model_from_params, simulate_exit,
)

if TYPE_CHECKING:
    import pandas as pd

    from tse_scanner.data.store import Store

_KINDS = ("fixed", "atr", "atr_trail")


def _r(ret: float | None, risk: float | None) -> float | None:
    if ret is None or risk is None or risk <= 0 or ret != ret:
        return None
    return ret / risk


def relabel(events: "pd.DataFrame", store: "Store", params: Params = PARAMS, *,
            kinds: Iterable[str] = _KINDS, entry_offset: int = 0,
            cohort_col: str = "cohort", progress=None) -> "pd.DataFrame":
    """Re-label every event under each exit model. Returns a long frame with one row per
    (event, kind): code, date, cohort, kind, label, ret, risk_frac, R, exit_date.

    `entry_offset` shifts the entry bar relative to the first bar on/after the signal date (1 =
    strictly-next session, matching the reactive-revision convention). Prices are cached per code.
    """
    import pandas as pd

    from tse_scanner.signals.coil import atr as atr_series

    px_cache: dict[str, pd.DataFrame] = {}

    def _px(code: str) -> "pd.DataFrame":
        if code not in px_cache:
            p = store.prices(code)
            px_cache[code] = p.reset_index() if not p.empty else p
        return px_cache[code]

    models = {k: exit_model_from_params(params, k) for k in kinds}
    has_cohort = cohort_col in events.columns
    out: list[dict] = []
    step = max(1, len(events) // 20)
    for n, ev in enumerate(events.itertuples(index=False), 1):
        code = str(ev.code)
        sig = pd.Timestamp(ev.date)
        px = _px(code)
        if px is None or px.empty:
            continue
        after = px[px["date"] >= sig]
        if after.empty:
            continue
        entry_idx = int(px.index.get_loc(after.index[0])) + entry_offset
        if entry_idx < 0 or entry_idx >= len(px):
            continue
        # point-in-time ATR known at the entry bar (bars up to and including it)
        a_raw = atr_series(px.iloc[:entry_idx + 1]).iloc[-1]
        atr = float(a_raw) if a_raw == a_raw and a_raw > 0 else None
        cohort = getattr(ev, cohort_col) if has_cohort else "all"
        for kind, model in models.items():
            ft = simulate_exit(entry_idx, px, params, model=model, atr_at_entry=atr)
            exit_date = (px.iloc[ft.exit_idx]["date"] if ft.exit_idx is not None else None)
            out.append({"code": code, "date": ev.date, "cohort": cohort, "kind": kind,
                        "label": ft.label.value, "ret": ft.ret, "risk_frac": ft.risk_frac,
                        "R": _r(ft.ret, ft.risk_frac), "exit_date": exit_date})
        if progress and (n % step == 0 or n == len(events)):
            progress(f"    relabel {n}/{len(events)} events")
    return pd.DataFrame(out)


def summarize(long: "pd.DataFrame", store: "Store | None" = None) -> "pd.DataFrame":
    """Per (cohort, kind): n_filled, win_rate, expectancy_R (mean), median_R, and excess-R vs TOPIX
    over matched entry→exit windows. Ordered cohort-major so each exit model lines up under it."""
    import numpy as np
    import pandas as pd

    df = long[long["label"] != Label.NO_FILL.value].copy()

    tclose = None
    if store is not None:
        tpx = store.topix()
        if not tpx.empty:
            tclose = tpx["close"]

    def _excess(sub: "pd.DataFrame") -> float:
        if tclose is None:
            return float("nan")
        ex = []
        for e in sub.itertuples(index=False):
            if e.exit_date is None or e.R is None or e.ret is None or e.risk_frac in (None, 0):
                continue
            t0, t1 = tclose.asof(pd.Timestamp(e.date)), tclose.asof(pd.Timestamp(e.exit_date))
            if t0 == t0 and t1 == t1 and t0 > 0:
                ex.append((e.ret - (t1 / t0 - 1.0)) / e.risk_frac)
        return float(np.mean(ex)) if ex else float("nan")

    rows = []
    for cohort in sorted(df["cohort"].astype(str).unique()):
        for kind in _KINDS:
            sub = df[(df["cohort"].astype(str) == cohort) & (df["kind"] == kind)]
            if sub.empty:
                continue
            rs = sub["R"].dropna()
            rows.append({
                "cohort": cohort, "kind": kind, "n": int(len(sub)),
                "win_rate": float((sub["label"] == Label.WIN.value).mean()),
                "expectancy_R": float(rs.mean()) if len(rs) else float("nan"),
                "median_R": float(rs.median()) if len(rs) else float("nan"),
                "vs_topix_R": _excess(sub),
            })
    return pd.DataFrame(rows)


def _fmt(summary: "pd.DataFrame") -> str:
    lines = []
    last = None
    for r in summary.itertuples(index=False):
        if r.cohort != last:
            lines.append(f"\n  · {r.cohort}")
            last = r.cohort
        lines.append(f"      {r.kind:<10} n={r.n:<6} win {r.win_rate:5.1%}  "
                     f"exp {r.expectancy_R:+.3f}R  med {r.median_R:+.2f}R  vsTPX {r.vs_topix_R:+.3f}R")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    import duckdb

    from tse_scanner.data.store import Store

    ap = argparse.ArgumentParser(description="Exit-model bake-off over a study's per-event parquet.")
    ap.add_argument("parquet", help="path to a backtest_*_events_*.parquet")
    ap.add_argument("--entry-offset", type=int, default=0,
                    help="entry bar offset vs first bar ≥ signal date (1 = next session)")
    ap.add_argument("--horizon", type=int, default=PARAMS.horizon_sessions,
                    help="label horizon (the shared time-stop) — must match the source study "
                         "(deadline/revision=20, squeeze=10) for a valid fixed-vs-official check")
    ap.add_argument("--cohort-col", default="cohort",
                    help="column to split cohorts on (squeeze parquets use 'ignition')")
    args = ap.parse_args(argv)

    store = Store()
    params = Params(**{**PARAMS.__dict__, "horizon_sessions": args.horizon})
    events = duckdb.connect().execute(f"SELECT * FROM '{args.parquet}'").df()
    print(f"exit bake-off: {len(events)} events from {args.parquet} (horizon {args.horizon})")
    long = relabel(events, store, params, entry_offset=args.entry_offset,
                   cohort_col=args.cohort_col, progress=lambda m: print(m, flush=True))
    summary = summarize(long, store)
    print(_fmt(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
