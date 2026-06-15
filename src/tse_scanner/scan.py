"""Scan orchestrator — run the lifecycle pipeline over the universe (Phase 2).

Composes the point-in-time inputs into one ranked table:

    universe  →  fuel + coil + ignition  →  stage  →  score

Gate-by-stage then a transparent within-stage weighted score (fuel weighted highest). All margin
reads are publication-date point-in-time (the store enforces it); `CONFIRMED` requires the *next*
weekly print's 売残 to drop, which is lagging and not knowable live, so the highest stage a live
scan assigns is `IGNITING`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params
from tse_scanner.pipeline import Stage, classify
from tse_scanner.signals import coil as coil_mod
from tse_scanner.signals import fuel as fuel_mod
from tse_scanner.signals import ignition as ign_mod
from tse_scanner.signals import targets as tgt_mod

if TYPE_CHECKING:
    import pandas as pd

    from tse_scanner.data.store import Store

_MIN_BARS = 30  # need enough bars for BB(20)/ATR(14) to be meaningful


@dataclass
class ScanResult:
    scan_date: date
    table: "pd.DataFrame"          # one row per evaluated code, ranked
    counts: dict                   # stage -> count


def _coil_component(bb_width_pct: float) -> float:
    """Tighter coil → higher score. NaN band-width (too little history) → 0."""
    if bb_width_pct != bb_width_pct:  # NaN
        return 0.0
    return max(0.0, min(1.0, 1.0 - bb_width_pct))


def run_scan(as_of: date | None = None, params: Params = PARAMS,
             store: "Store | None" = None, write: bool = True) -> ScanResult:
    """Evaluate every universe name as-of `as_of`, bucket by stage, score, and (optionally) persist."""
    import pandas as pd

    from tse_scanner.data.store import Store
    from tse_scanner.data.universe import build_universe

    store = store or Store()
    if as_of is None:
        # default to the latest available price date
        pm = store.price_metrics()
        as_of = pd.to_datetime(pm["last_date"]).max().date() if not pm.empty else date.today()

    uni = build_universe(as_of, params, store)
    if uni.empty:
        empty = pd.DataFrame()
        return ScanResult(scan_date=as_of, table=empty, counts={})

    # Signals read at most (baseline_sessions + 60) ≈ 180 *sessions*; a 2-calendar-year lower
    # bound covers that with ~2× headroom even through holiday runs and halts, and spares each
    # replay date from re-reading the full 10y price history (the backtest's I/O bottleneck).
    # Side benefit: a delist→relist name (e.g. 8303, 2023→2025) no longer bridges its listing gap
    # into the volatility baseline — "this name's normal" stays within the current price regime.
    prices = store.all_prices(uni["code"].tolist(), as_of, start=as_of - timedelta(days=730))
    # Cap each code's slice to the longest signal lookback. bb_width_percentile only ranks the
    # tail(baseline_sessions) of the rolling-BB(20) widths; box/ATR/volume are tail(≤20). So the
    # last (baseline_sessions + 60) sessions reproduce every signal **identically** while sparing
    # coil/ignition from chewing each name's full 10y history per scan (the backtest's cost).
    # The exit map's 52w-high/resistance window (exit_swing_lookback) is the one reader that
    # wants more than the signals do — extend the slice for it, signals stay tail()-identical.
    lookback = max(params.baseline_sessions + 60, params.exit_swing_lookback + 10)
    by_code = {c: g.set_index("date").tail(lookback) for c, g in prices.groupby("code")} \
        if not prices.empty else {}
    # Batched once (was a per-code query inside the loop — ~1.6k connects, the scan's bottleneck).
    trends = store.margin_trends(uni["code"].tolist(), as_of, params.short_balance_trend_prints)

    # Cross-sectional fuel pre-pass: days-to-cover percentile is ranked against *today's whole
    # universe* (one cross-section per scan date — point-in-time by construction).
    dtc_all = pd.Series(
        [fuel_mod.days_to_cover(
            float(s) if pd.notna(s) else 0.0,
            float(a) if pd.notna(a) else 0.0)
         for s, a in zip(uni["short_total"], uni["adv_shares"])],
        index=uni.index, dtype=float)
    dtc_pctiles = fuel_mod.dtc_percentile_ranks(dtc_all).tolist()

    rows: list[dict] = []
    for i, r in enumerate(uni.itertuples(index=False)):
        ohlc = by_code.get(r.code)
        if ohlc is None or len(ohlc) < _MIN_BARS:
            continue

        fuel = fuel_mod.assess(
            short_balance=float(r.short_total) if pd.notna(r.short_total) else 0.0,
            long_balance=float(r.long_total) if pd.notna(r.long_total) else 0.0,
            adv=float(r.adv_shares) if pd.notna(r.adv_shares) else 0.0,
            dtc_pctile=dtc_pctiles[i],
            rising=fuel_mod.short_balance_rising(
                trends.get(r.code), params.short_balance_trend_prints
),
            gyaku_hibu=False,  # 逆日歩 booster is Phase 4 (JSF) —
            params=params,
)
        coil = coil_mod.assess(ohlc, params)
        ign = ign_mod.assess(ohlc, coil.box_high, coil.box_low, params)
        # Exit map: display-only overlay for the human's exits (PLAYBOOK §F-01-H) — like the
        # research verdict it must never feed stage/score below.
        exit_map = tgt_mod.compute(ohlc, coil.box_high, coil.box_low, params)

        stage = classify(
            has_fuel=fuel.has_fuel,
            is_coiled=coil.is_coiled,
            has_ignition=ign.type is not ign_mod.Ignition.NONE,
            has_followthrough=False,  # 売残-drop is next-print, lagging — not knowable live
            require_fuel=params.require_fuel,
)

        w = params.score_weights
        coil_c = _coil_component(coil.bb_width_pct)
        score = w["fuel"] * fuel.score + w["coil"] * coil_c + w["ignition"] * ign.quality

        rows.append({
            "scan_date": as_of,
            "code": r.code,
            "name": r.name,
            "segment": r.segment,
            "stage": stage.name,
            "score": round(float(score), 4),
            "fuel": round(float(fuel.score), 4),
            "coil": round(float(coil_c), 4),
            "ignition": ign.type.value,
            "has_fuel": bool(fuel.has_fuel),
            "days_to_cover": round(fuel.days_to_cover, 2) if fuel.days_to_cover == fuel.days_to_cover else None,
            "dtc_pctile": round(fuel.dtc_pctile, 4) if fuel.dtc_pctile == fuel.dtc_pctile else None,
            "credit_ratio": round(fuel.credit_ratio, 4) if fuel.credit_ratio < 1e9 else None,
            "short_total": int(r.short_total) if pd.notna(r.short_total) else None,
            "long_total": int(r.long_total) if pd.notna(r.long_total) else None,
            "bb_width_pct": round(coil.bb_width_pct, 4) if coil.bb_width_pct == coil.bb_width_pct else None,
            "box_height_atr": (round(coil.box_height_atr, 2)
                               if coil.box_height_atr == coil.box_height_atr
                               and coil.box_height_atr != float("inf") else None),
            "box_high": round(coil.box_high, 2),
            "box_low": round(coil.box_low, 2),
            "t1": round(exit_map.t1, 2),
            "t2": round(exit_map.t2, 2),
            "atr": round(exit_map.atr, 2) if exit_map.atr is not None else None,
            "rr_t1": round(exit_map.rr_t1, 2) if exit_map.rr_t1 is not None else None,
            "next_resistance": (round(exit_map.resistances[0], 2)
                                if exit_map.resistances else None),
            "high_52w": round(exit_map.high_52w, 2) if exit_map.high_52w is not None else None,
            "blue_sky": bool(exit_map.blue_sky),
            "last_close": round(float(r.last_close), 2),
            "adv_yen": round(float(r.adv_yen), 0) if pd.notna(r.adv_yen) else None,
            "margin_as_of": r.margin_as_of if pd.notna(r.margin_as_of) else None,
            "margin_published": r.margin_published if pd.notna(r.margin_published) else None,
        })

    if not rows:
        return ScanResult(scan_date=as_of, table=pd.DataFrame(), counts={})

    table = pd.DataFrame(rows)
    # gate-by-stage, then within-stage score
    stage_rank = {s.name: int(s) for s in Stage}
    table["_stage_rank"] = table["stage"].map(stage_rank)
    table = table.sort_values(["_stage_rank", "score"], ascending=[False, False]).drop(columns="_stage_rank")
    table = table.reset_index(drop=True)

    counts = table["stage"].value_counts().to_dict()
    if write:
        store.write_scan(table)
    return ScanResult(scan_date=as_of, table=table, counts=counts)
