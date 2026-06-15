"""Deadline (期日スイング) event study — does the *timing* of forced 制度信用 settlement add edge?

The squeeze study already established the null we must beat: "high short interest" alone is flat
(fuel-gated breakouts did *not* outperform). So this study is built around one falsifiable A/B:

  TREATMENT — name-weeks where a short vintage's 6-month deadline is **imminent** (short_due_dtc ≥
              floor): forced buying about to hit.
  PLACEBO   — the **same names**, at weeks when 制度 short is present but *no* deadline is near
              (short_due_dtc ≈ 0): the name without the forced-flow catalyst.

If treatment ≫ placebo, the deadline *timing* carries edge beyond name-selection. If they match,
the deadline adds nothing and the setup dies cleanly — same discipline as the squeeze gate.

Secondary cohorts: due-DtC quartiles (does *more* forced cover → better?), coil-composed (does the
catalyst pay more when price is already wound?), and a K2 long-overhang split (names whose 制度
*long* is force-selling = a headwind). Point-in-time throughout: vintages are FIFO-rebuilt from
publication-date margin only, ADV is trailing-as-of, entry is the first bar on/after the print's
publication date, labels are the gap-aware first-touch barriers (shared with the squeeze study).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Callable

from tse_scanner.config import PARAMS, Params
from tse_scanner.backtest.event_study import EventStudyResult, _r_multiple
from tse_scanner.backtest.labels import Label, exit_model_from_params, simulate_exit

if TYPE_CHECKING:
    import pandas as pd

    from tse_scanner.data.store import Store

_ADV_WINDOW = 20
_CONTROL_DUE_FRAC = 0.25  # placebo eligibility: short_due_dtc < floor × this (no imminent deadline)


def _candidate_codes(store: "Store") -> list[str]:
    """Ever-貸借 names from the margin tape (point-in-time universe, incl. delisted)."""
    with store.connect() as con:
        return [r[0] for r in con.execute(
            "SELECT DISTINCT code FROM weekly_margin WHERE is_taishaku ORDER BY code").fetchall()]


def _weekly_pctile(panel: "pd.DataFrame", col: str) -> "pd.Series":
    """Cross-sectional percentile of `col` within each as_of_date (fuel-style: finite>0 pool, inf→1)."""
    import numpy as np

    def _rank(s):
        out = s.copy().astype(float)
        finite = out[np.isfinite(out) & (out > 0)]
        out[:] = float("nan")
        if len(finite):
            out.loc[finite.index] = finite.rank(pct=True)
        out[np.isposinf(s.astype(float))] = 1.0
        return out

    return panel.groupby("as_of_date")[col].transform(_rank)


def _dedup(events: "pd.DataFrame", horizon_days: int) -> "pd.DataFrame":
    """One position per code at a time: walk a code's events in date order, skip re-entry until the
    prior trade's window (≈ horizon trading days, padded to calendar) clears."""
    keep = []
    for _code, g in events.sort_values("published_date").groupby("code"):
        blocked = None
        for row in g.itertuples(index=False):
            if blocked is not None and row.published_date < blocked:
                continue
            keep.append(row)
            blocked = row.published_date + timedelta(days=int(horizon_days * 1.6))
    import pandas as pd

    return pd.DataFrame(keep)


def run_deadline_study(start: date, end: date, params: Params = PARAMS,
                       store: "Store | None" = None,
                       progress: "Callable[[str], None] | None" = None,
                       seed: int = 7) -> EventStudyResult:
    """Replay the forced-flow setup point-in-time over [start, end] and aggregate the A/B cohorts."""
    import numpy as np
    import pandas as pd

    from tse_scanner.data.store import Store
    from tse_scanner.signals import coil as coil_sig
    from tse_scanner.signals.deadline import pressure_timeline

    store = store or Store()
    codes = _candidate_codes(store)
    if progress:
        progress(f"  deadline study: {len(codes)} ever-貸借 candidates, building vintage panel …")

    # --- Pass 1: per-code FIFO vintage panel (one forward pass each) ---
    panel_rows: list[pd.DataFrame] = []
    step = max(1, len(codes) // 20)
    for i, code in enumerate(codes, 1):
        margin = store.margin_history(code, asof=end, prints=10_000)
        if margin.empty or "sys_short" not in margin:
            continue
        px = store.prices(code, end=end)
        if px.empty:
            continue
        vol20 = px["volume"].rolling(_ADV_WINDOW).mean()
        close = px["close"]
        # point-in-time ADV (shares) and turnover (¥) known as-of each Friday print
        def _adv_of(d, _v=vol20):
            v = _v.asof(pd.Timestamp(d))
            return float(v) if v == v else 0.0

        tl = pressure_timeline(margin, _adv_of, params)
        if tl.empty:
            continue
        tl["code"] = code
        # liquidity + price gate at the print (cut untradeable junk, same spirit as the universe)
        advyen = (vol20 * close)
        tl["adv_yen"] = [float(advyen.asof(pd.Timestamp(d)) or 0.0) for d in tl["as_of_date"]]
        tl["close_at_week"] = [float(close.asof(pd.Timestamp(d)) or 0.0) for d in tl["as_of_date"]]
        panel_rows.append(tl)
        if progress and (i % step == 0 or i == len(codes)):
            progress(f"    panel {i}/{len(codes)} codes ({sum(len(p) for p in panel_rows)} name-weeks)")

    if not panel_rows:
        return EventStudyResult(0, float("nan"), float("nan"), float("nan"), float("nan"),
                                note="deadline study: empty panel (no margin/prices in range).")
    panel = pd.concat(panel_rows, ignore_index=True)
    # restrict to events whose *publication* date is in-window (entry can't precede publication)
    panel = panel[(panel["published_date"] >= start) & (panel["published_date"] <= end)].copy()
    panel = panel[(panel["adv_yen"] >= params.turnover_floor_yen)
                  & (panel["close_at_week"] >= params.min_price_yen)]
    if panel.empty:
        return EventStudyResult(0, float("nan"), float("nan"), float("nan"), float("nan"),
                                note="deadline study: no tradeable name-weeks after liquidity gate.")
    panel["due_pctile"] = _weekly_pctile(panel, "short_due_dtc")

    # --- Event selection: treatment (imminent deadline) + timing-matched placebo (same names) ---
    floor = params.deadline_dtc_floor
    treat = panel[(panel["short_due_dtc"] >= floor) & np.isfinite(panel["short_due_dtc"])].copy()
    treat["cohort"] = "treatment"
    treat = _dedup(treat, params.deadline_horizon_sessions)

    rng = np.random.default_rng(seed)
    treat_counts = treat.groupby("code").size().to_dict()
    control_pool = panel[(panel["sys_short"] > 0)
                         & (panel["short_due_dtc"] < floor * _CONTROL_DUE_FRAC)
                         & panel["code"].isin(treat_counts)]
    placebo_parts = []
    for code, g in control_pool.groupby("code"):
        k = min(len(g), treat_counts.get(code, 0))
        if k:
            placebo_parts.append(g.iloc[rng.choice(len(g), size=k, replace=False)])
    placebo = pd.concat(placebo_parts, ignore_index=True) if placebo_parts else panel.iloc[0:0].copy()
    if len(placebo):
        placebo = placebo.copy()
        placebo["cohort"] = "placebo"
        placebo = _dedup(placebo, params.deadline_horizon_sessions)
    events = pd.concat([treat, placebo], ignore_index=True)
    if progress:
        progress(f"  events: {len(treat)} treatment + {len(placebo)} placebo → labeling forward …")

    # --- Pass 2: label each event forward (gap-aware first-touch, deadline horizon) ---
    hp = Params(**{**params.__dict__, "horizon_sessions": params.deadline_horizon_sessions})
    exit_model = exit_model_from_params(hp)  # default "fixed" reproduces first_touch_detail exactly
    recs: list[dict] = []
    for code, g in events.groupby("code"):
        fwd_all = store.prices(code)
        if fwd_all.empty:
            continue
        fwd_all = fwd_all.reset_index()
        atr_full = coil_sig.atr(fwd_all)  # point-in-time ATR(14): atr_full.iloc[i] uses bars ≤ i
        for ev in g.itertuples(index=False):
            pub = pd.Timestamp(ev.published_date)
            entry_rows = fwd_all[fwd_all["date"] >= pub]
            if entry_rows.empty:
                continue
            entry_idx = int(fwd_all.index.get_loc(entry_rows.index[0]))
            a = atr_full.iloc[entry_idx]
            atr = float(a) if a == a and a > 0 else None
            ft = simulate_exit(entry_idx, fwd_all, hp, model=exit_model, atr_at_entry=atr)
            exit_date = (fwd_all.iloc[ft.exit_idx]["date"] if ft.exit_idx is not None else None)
            # coil state at entry (for the coil-composed cohort) — price window up to the entry bar
            coiled = False
            win = fwd_all.iloc[max(0, entry_idx - params.baseline_sessions):entry_idx + 1]
            if len(win) > params.setup_sessions + 2:
                try:
                    coiled = coil_sig.assess(win.set_index("date"), params).is_coiled
                except Exception:  # noqa: BLE001 — a degenerate window must not kill the run
                    coiled = False
            recs.append({
                "code": code, "date": ev.published_date, "cohort": ev.cohort,
                "label": ft.label, "ret": ft.ret, "exit_date": exit_date,
                "short_due_dtc": float(ev.short_due_dtc), "due_pctile": float(ev.due_pctile),
                "short_due_frac": float(ev.short_due_frac), "long_due_dtc": float(ev.long_due_dtc),
                "sys_short": float(ev.sys_short), "coiled": bool(coiled),
                "stop_pct": params.stop_pct, "target_pct": params.target_pct,
                "risk_frac": ft.risk_frac,
            })

    return _aggregate(recs, params, store, start, end, n_codes=len(codes))


def _aggregate(recs: list[dict], params: Params, store, start: date, end: date,
               n_codes: int) -> EventStudyResult:
    import numpy as np
    import pandas as pd

    if not recs:
        return EventStudyResult(0, float("nan"), float("nan"), float("nan"), float("nan"),
                                note="deadline study: no events labeled.")
    ev = pd.DataFrame(recs)
    filled = ev[ev["label"] != Label.NO_FILL]

    def _risk(e) -> float:  # actual risk taken (ATR exits vary it); == stop_pct for the fixed model
        rf = getattr(e, "risk_frac", None)
        return rf if rf is not None and rf == rf and rf > 0 else e.stop_pct

    def _cohort(sub: "pd.DataFrame") -> dict:
        rs = [_r_multiple(e.ret, _risk(e)) for e in sub.itertuples(index=False)
              if e.ret is not None and e.ret == e.ret]
        return {"n": int(len(sub)),
                "win_rate": (int((sub["label"] == Label.WIN).sum()) / len(sub)) if len(sub) else float("nan"),
                "expectancy_r": float(np.mean(rs)) if rs else float("nan")}

    overall = _cohort(filled)
    counts = {lbl.value: int((ev["label"] == lbl).sum()) for lbl in Label}

    # vs TOPIX over matched entry→exit windows (in R), same as the squeeze study
    vs_topix = float("nan")
    topix = store.topix()
    if not topix.empty and len(filled):
        tclose = topix["close"]
        excess = []
        for e in filled.itertuples(index=False):
            if e.ret is None or e.exit_date is None:
                continue
            t0, t1 = tclose.asof(pd.Timestamp(e.date)), tclose.asof(pd.Timestamp(e.exit_date))
            if t0 == t0 and t1 == t1 and t0 > 0:
                excess.append(_r_multiple(e.ret - (t1 / t0 - 1.0), _risk(e)))
        if excess:
            vs_topix = float(np.mean(excess))

    splits: dict = {}
    treat = filled[filled["cohort"] == "treatment"]
    placebo = filled[filled["cohort"] == "placebo"]
    splits["期日向かい treatment (due≥floor)"] = _cohort(treat)
    splits["placebo (same names, no deadline)"] = _cohort(placebo)
    dp = treat["due_pctile"]
    for name, lo, hi in [("due-DtC Q4 (≥.75)", 0.75, 1.01), ("due-DtC Q3", 0.50, 0.75),
                         ("due-DtC Q2", 0.25, 0.50), ("due-DtC Q1 (<.25)", 0.00, 0.25)]:
        splits[name] = _cohort(treat[dp.notna() & (dp >= lo) & (dp < hi)])
    splits["treatment ∩ coiled"] = _cohort(treat[treat["coiled"]])
    splits["treatment ¬coiled"] = _cohort(treat[~treat["coiled"]])
    splits["K2 long-overhang (long_due_dtc≥1)"] = _cohort(treat[treat["long_due_dtc"] >= 1.0])

    # per-event parquet (re-slice barriers/cohorts offline) — in-memory duckdb COPY, no store lock
    events_path = ""
    try:
        import duckdb

        out_dir = store.settings.data_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"backtest_deadline_events_{date.today():%Y%m%d}.parquet"
        dump = ev.copy()
        dump["label"] = dump["label"].map(lambda x: x.value)
        mem = duckdb.connect()
        try:
            mem.register("_ev", dump)
            mem.execute(f"COPY (SELECT * FROM _ev ORDER BY date, code) TO '{target}' (FORMAT PARQUET)")
        finally:
            mem.close()
        events_path = str(target)
    except Exception:  # noqa: BLE001
        pass

    return EventStudyResult(
        n_events=len(ev), win_rate=overall["win_rate"], expectancy_r=overall["expectancy_r"],
        vs_topix=vs_topix, primed_to_igniting_rate=float("nan"),
        label_counts=counts, splits=splits, events_path=events_path,
        note=(f"deadline: {len(filled)} filled / {counts.get('no_fill', 0)} no-fill over "
              f"{n_codes} candidates in [{start}..{end}]; horizon={params.deadline_horizon_sessions} "
              f"sessions, barrier ±{params.target_pct:.0%}/{params.stop_pct:.0%}."))
