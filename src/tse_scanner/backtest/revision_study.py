"""Revision (上方修正先回り) event study — does *anticipating* the guidance hike beat *reacting* to it?

Two arms, both point-in-time (fins read strictly-before as-of, gap-aware fills):

  REACTIVE (baseline) — the 修正 lands after the close; buy the **next** session and hold the drift
      (a PEAD-style read). Direction is classified from the revised full-year forecast vs the
      guidance previously in force (`guidance_timeline`), so up- and down-revisions are split.

  ANTICIPATORY — the whole thesis: the surprise prints as an un-buyable opening gap (often straight
      to ストップ高), so position *before* it. Entries are placed ~`rev_anticipate_window_days`
      ahead of each expected announcement (a statement anniversary — the only backtestable forward
      date, since the official calendar has no history) and the trade is held *through* the print.
      The revision-pressure features (sandbagger / progress-anomaly) then split the cohort: if they
      carry signal, high-pressure windows must out-earn low-pressure ones.

The edge question is the A/B: anticipatory-hot vs reactive vs the low-pressure baseline. As with
the other setups the features are *measured*, never assumed — a flat split kills the overlay.
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
_MIN_PRIOR_STATEMENTS = 4  # ≥1y of disclosure history before an anticipatory entry is meaningful


def _norm_dates(hist: "pd.DataFrame") -> "pd.DataFrame":
    """Coerce the date columns the revision logic compares to python `date` (store reads give
    Timestamps, and Timestamp-vs-date comparisons raise). Mutates a copy, returns it."""
    import pandas as pd

    h = hist.copy()
    for c in ("disc_date", "fy_end", "nxt_fy_end", "per_end"):
        if c in h.columns:
            h[c] = [d.date() if isinstance(d, pd.Timestamp) else d
                    for d in pd.to_datetime(h[c], errors="coerce")]
    return h


def run_revision_study(start: date, end: date, params: Params = PARAMS,
                       store: "Store | None" = None,
                       progress: "Callable[[str], None] | None" = None) -> EventStudyResult:
    """Replay both arms over [start, end] and aggregate the anticipate-vs-react cohorts."""
    import pandas as pd

    from tse_scanner.data.store import Store
    from tse_scanner.signals import revision as rev_sig
    from tse_scanner.signals.coil import atr as coil_atr

    store = store or Store()
    hp = Params(**{**params.__dict__, "horizon_sessions": params.rev_horizon_sessions})
    exit_model = exit_model_from_params(hp)  # default "fixed" reproduces first_touch_detail exactly
    codes = sorted(store.fins_codes())
    if not codes:
        return EventStudyResult(0, float("nan"), float("nan"), float("nan"), float("nan"),
                                note="revision study: fins_summary empty — run `ingest --fins` first.")
    if progress:
        progress(f"  revision study: {len(codes)} codes with disclosures; both arms, "
                 f"horizon {params.rev_horizon_sessions} sessions …")

    recs: list[dict] = []
    step = max(1, len(codes) // 20)
    for ci, code in enumerate(codes, 1):
        hist = _norm_dates(store.fins_history(code))  # all; sliced strictly-before per entry below
        if hist.empty:
            continue
        px = store.prices(code)
        if px.empty:
            continue
        fwd = px.reset_index()
        fwd_dates = fwd["date"]
        vol20 = fwd["volume"].rolling(_ADV_WINDOW).mean()
        close = fwd["close"]
        atr_full = coil_atr(fwd)  # point-in-time ATR(14): atr_full.iloc[i] uses bars ≤ i

        def _label(entry_after: "pd.Timestamp", strict: bool, cohort: str, **extra):
            mask = fwd_dates > entry_after if strict else fwd_dates >= entry_after
            hit = mask.values.argmax() if mask.any() else -1
            if hit < 0:
                return
            adv_yen = float((vol20.iloc[hit] or 0) * (close.iloc[hit] or 0))
            if adv_yen < params.turnover_floor_yen or float(close.iloc[hit]) < params.min_price_yen:
                return
            a = atr_full.iloc[hit]
            atr = float(a) if a == a and a > 0 else None
            ft = simulate_exit(hit, fwd, hp, model=exit_model, atr_at_entry=atr)
            exit_date = (fwd.iloc[ft.exit_idx]["date"] if ft.exit_idx is not None else None)
            recs.append({"code": code, "date": entry_after.date() if hasattr(entry_after, "date")
                         else entry_after, "cohort": cohort, "label": ft.label, "ret": ft.ret,
                         "exit_date": exit_date, "stop_pct": params.stop_pct,
                         "target_pct": params.target_pct, "risk_frac": ft.risk_frac, **extra})

        # --- Arm A: reactive — classify each 修正's direction, enter the next session ---
        revs = hist[(hist["doc_type"].astype(str) == "EarnForecastRevision")
                    & (hist["disc_date"] >= start) & (hist["disc_date"] <= end)]
        for r in revs.itertuples(index=False):
            before = hist[hist["disc_date"] < r.disc_date]
            metric = rev_sig._pick_metric(before)
            # the revised forecast comes straight off the 修正 row: real fins data carries pandas
            # nullable dtypes, so coerce to a plain float (pd.NA/NaT/None → nan) *before* any
            # self-comparison — `pd.NA == pd.NA` is itself NA and bool(NA) raises.
            raw_revised = getattr(r, f"f_{metric}", None)
            revised = float(raw_revised) if pd.notna(raw_revised) else float("nan")
            tl = rev_sig.guidance_timeline(before, r.fy_end, metric)
            prior = float(tl.iloc[-1]["value"]) if not tl.empty else float("nan")
            mag = (revised / prior - 1.0) if (prior == prior and prior > 0
                                              and revised == revised) else float("nan")
            cohort = "reactive 上方修正 (up)" if (mag == mag and mag > 0) else (
                "reactive 下方修正 (down)" if mag == mag else "reactive (direction n/a)")
            _label(pd.Timestamp(r.disc_date), strict=True, cohort=cohort, rev_mag=mag,
                   score=float("nan"), sandbag=float("nan"), anomaly=float("nan"))

        # --- Arm B: anticipatory — enter ahead of each expected announcement anniversary ---
        stmts = hist[hist["doc_type"].astype(str).str.contains("FinancialStatements", na=False)]
        if len(stmts) >= _MIN_PRIOR_STATEMENTS:
            expected = sorted({d + timedelta(days=365) for d in stmts["disc_date"]})
            last_entry: date | None = None
            for a in expected:
                if not (start <= a <= end):
                    continue
                entry_date = a - timedelta(days=int(params.rev_anticipate_window_days))
                if last_entry is not None and (entry_date - last_entry).days < params.rev_horizon_sessions:
                    continue  # dedup: one anticipatory position per name at a time
                before = hist[hist["disc_date"] < entry_date]
                if (before["doc_type"].astype(str).str.contains("FinancialStatements", na=False).sum()
                        < _MIN_PRIOR_STATEMENTS):
                    continue
                rv = rev_sig.assess(before, entry_date, params)
                _label(pd.Timestamp(entry_date), strict=False, cohort="anticipatory",
                       rev_mag=float("nan"), score=rv.score,
                       sandbag=rv.sandbag if rv.sandbag == rv.sandbag else float("nan"),
                       anomaly=rv.anomaly if rv.anomaly == rv.anomaly else float("nan"))
                last_entry = entry_date

        if progress and (ci % step == 0 or ci == len(codes)):
            progress(f"    {ci}/{len(codes)} codes ({len(recs)} events so far)")

    return _aggregate(recs, params, store, start, end, n_codes=len(codes))


def _aggregate(recs: list[dict], params: Params, store, start: date, end: date,
               n_codes: int) -> EventStudyResult:
    import numpy as np
    import pandas as pd

    if not recs:
        return EventStudyResult(0, float("nan"), float("nan"), float("nan"), float("nan"),
                                note="revision study: no events labeled.")
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

    anti = filled[filled["cohort"] == "anticipatory"]
    splits: dict = {
        "reactive 上方修正 drift (up)": _cohort(filled[filled["cohort"] == "reactive 上方修正 (up)"]),
        "reactive 下方修正 (down, contrast)": _cohort(filled[filled["cohort"] == "reactive 下方修正 (down)"]),
        "anticipatory (all windows)": _cohort(anti),
        "anticipatory HOT (score≥hot)": _cohort(anti[anti["score"] >= params.rev_score_hot]),
        "anticipatory cold (score<hot)": _cohort(anti[anti["score"] < params.rev_score_hot]),
        "anticipatory · sandbagger≥.5": _cohort(anti[anti["sandbag"] >= 0.5]),
        "anticipatory · sandbagger<.5": _cohort(anti[anti["sandbag"] < 0.5]),
    }
    an = anti["anomaly"]
    if an.notna().sum() >= 8:
        med = float(an.median())
        splits["anticipatory · anomaly≥median (ahead of pace)"] = _cohort(anti[an >= med])
        splits["anticipatory · anomaly<median"] = _cohort(anti[an < med])

    events_path = ""
    try:
        import duckdb

        out_dir = store.settings.data_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"backtest_revision_events_{date.today():%Y%m%d}.parquet"
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
        note=(f"revision: {len(filled)} filled / {counts.get('no_fill', 0)} no-fill over {n_codes} "
              f"codes in [{start}..{end}]; reactive next-open + anticipatory −"
              f"{int(params.rev_anticipate_window_days)}d, horizon {params.rev_horizon_sessions}."))
