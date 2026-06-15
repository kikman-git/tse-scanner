"""Technical exit map — "where do I sell?" — price-structure targets (display overlay).

The deep-research memo surfaces the *fundamental* ceiling (the published analyst target-price
cluster); this module is the *technical* counterweight, computed deterministically from bars we
already own — never by an LLM eyeballing charts. Methods chosen for TSE coil/squeeze names:

- **Measured move off the coil box** (T1 = box high + 1×height, T2 = +2×height): the canonical
  projection for a volatility-contraction box and self-consistent with the coil that gated the
  signal — the spring's stored energy *is* the box height.
- **Overhead-supply map**: fractal swing highs above the entry within ~252 sessions plus the
  52-week high. Squeezes run until they meet supply; above the 52w high there is none —
  **青天井 (blue sky)** — so the rule inverts: trail the runner, don't cap it.
- **ATR feasibility**: targets in ATR units plus the ~1σ move the 10-session horizon allows
  (ATR·√h). A target 3σ away inside the time stop is fantasy; the human sees the multiple.
- **節目 (round-number) snap**: TSE order books stack sell walls at round yen levels; a target
  just past one is snapped to just *below* it — sell in front of the wall, not into it.

Deliberately skipped: Fibonacci extensions (discretion without a falsifiable basis) and volume
profile / anchored VWAP (needs intraday data this store doesn't have).

Same red line as the research verdict: **display-only — never a stage/score input.** Pure
functions of bars up to the signal date, so the map is point-in-time valid if the event study
later tests structural exits as alternative barriers (labels.py already supports per-event ones).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params
from tse_scanner.signals.coil import atr as atr_series

if TYPE_CHECKING:
    import pandas as pd


def round_step(price: float) -> float:
    """The 節目 grid spacing at this price level (¥100 steps under ¥1,000, ¥500 to ¥5,000, …)."""
    if price < 1_000:
        return 100.0
    if price < 5_000:
        return 500.0
    if price < 10_000:
        return 1_000.0
    if price < 50_000:
        return 5_000.0
    return 10_000.0


def next_round_above(price: float) -> float:
    """Nearest 節目 strictly above `price` — the first sell wall overhead."""
    step = round_step(price)
    return math.floor(price / step) * step + step


def snap_below_round(target: float, params: Params = PARAMS, floor: float = 0.0) -> float:
    """Pull a raw target to just below a 節目 when it lands within ±snap% of one.

    A target a hair past ¥1,000 is unreachable in practice (the wall eats the move); a target a
    hair under it is fine as-is. Snapping only ever moves the target *down* — conservative —
    but never through `floor` (the box high): when the wall sits in the entry zone itself,
    snapping would put the "target" below the breakout, so the raw projection stands.
    """
    if target <= 0 or target != target:
        return target
    step = round_step(target)
    level = round(target / step) * step
    if level > 0 and abs(target - level) / level <= params.exit_round_snap_pct:
        snapped = level * (1.0 - params.exit_round_undercut)
        if snapped > floor:
            return min(target, snapped)
    return target


def swing_highs(high: "pd.Series", k: int, lookback: int) -> list[float]:
    """Fractal swing highs (the local max of a 2k+1 window) within `lookback` sessions.

    Vectorized (one centered rolling max) because this runs inside the scan loop, which is also
    the backtest's per-replay-date inner loop. Near-duplicates (<1% apart) merge into one level —
    a supply shelf is one shelf, not five prints of it. Ascending order.
    """
    h = high.tail(lookback)
    if len(h) < 2 * k + 1:
        return []
    rm = h.rolling(2 * k + 1, center=True).max()
    pivots = sorted(float(v) for v, m in zip(h, rm) if m == m and v >= m)
    merged: list[float] = []
    for p in pivots:
        if merged and (p - merged[-1]) / merged[-1] < 0.01:
            merged[-1] = p  # same shelf — keep the higher print
        else:
            merged.append(p)
    return merged


@dataclass
class ExitMap:
    entry_ref: float            # close if already through the box, else box_high (prospective)
    stop_ref: float             # box_low — the structural stop (sweep wick-low is per-event)
    atr: float | None
    t1: float                   # measured move, 節目-snapped
    t2: float                   # stretch projection, 節目-snapped
    t1_atr: float | None        # (t1 − entry) in ATR units — feasibility
    sigma_h: float | None       # ATR·√horizon ≈ 1σ move available inside the time stop
    rr_t1: float | None         # (t1 − entry) / (entry − stop)
    resistances: list[float]    # overhead supply above entry_ref, ascending (≤3 shown)
    high_52w: float | None
    blue_sky: bool              # entry above the 52w high → no overhead supply: trail, don't cap


def compute(ohlc: "pd.DataFrame", box_high: float, box_low: float,
            params: Params = PARAMS) -> ExitMap:
    """Build the exit map from bars + the coil box the scan already measured.

    Uses only data up to the last bar (point-in-time). The 52w high is measured on bars *before*
    the last one so an igniting bar printing a fresh high reads as blue sky, not as its own
    resistance.
    """
    close = float(ohlc["close"].iloc[-1])
    entry_ref = close if close > box_high else box_high
    stop_ref = box_low
    height = max(0.0, box_high - box_low)

    a_raw = atr_series(ohlc).iloc[-1]
    a = float(a_raw) if a_raw == a_raw and a_raw > 0 else None

    t1 = snap_below_round(box_high + params.exit_box_mult_t1 * height, params, floor=box_high)
    t2 = snap_below_round(box_high + params.exit_box_mult_t2 * height, params, floor=box_high)

    prior = ohlc.iloc[:-1] if len(ohlc) > 1 else ohlc
    hi_52w_raw = prior["high"].tail(params.exit_swing_lookback).max()
    high_52w = float(hi_52w_raw) if hi_52w_raw == hi_52w_raw else None
    blue_sky = high_52w is not None and entry_ref >= high_52w

    res = [r for r in swing_highs(prior["high"], params.exit_swing_k, params.exit_swing_lookback)
           if r > entry_ref * 1.005]  # ignore shelves basically at the entry print
    resistances = res[:3]

    risk = entry_ref - stop_ref
    return ExitMap(
        entry_ref=entry_ref,
        stop_ref=stop_ref,
        atr=a,
        t1=t1,
        t2=t2,
        t1_atr=(t1 - entry_ref) / a if a else None,
        sigma_h=a * math.sqrt(params.horizon_sessions) if a else None,
        rr_t1=(t1 - entry_ref) / risk if risk > 0 else None,
        resistances=resistances,
        high_52w=high_52w,
        blue_sky=blue_sky,
    )
