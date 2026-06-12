"""Ignition — "has it fired?" — trigger, long-only (Phase 1).

- **Break (standard):** close above the box high + volume surge + wide, band-expanding bar.
  Close (not an intraday poke); volume + width reject fakeouts.
- **Spring (premium tag):** price first dips below the box low (sweeps sell-side stops → validates
  shorts, who add), then reclaims and breaks up — maximal trap. Ranks higher.
- **Sweep (early entry, 2026-06-12):** the *sweep-reversal bar itself* — low takes out the box low
  (liquidity sweep) but the close reclaims the box, leaving a long lower wick. Entry at the
  reversal with the stop under the wick low: earlier and tighter-risk than waiting for the break,
  at the cost of no breakout confirmation. Whether that trade-off pays is an event-study cohort
  question (sweep vs break vs spring), not an assumption. A sweep that *also* clears the box high
  the same day classifies as BREAK/SPRING (confirmation wins).

The detector is long-side only on purpose (thesis + the user's borrow constraints); the short
mirror (sweep above box high, long upper wick, close back inside) is deliberately not implemented
until short backtests can model 逆日歩/borrow cost honestly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params
from tse_scanner.signals.coil import atr

if TYPE_CHECKING:
    import pandas as pd

_VOL_WINDOW = 20


class Ignition(Enum):
    NONE = "none"
    BREAK = "break"
    SPRING = "spring"  # premium sub-type
    SWEEP = "sweep"    # early entry at the sweep-reversal bar (stop = wick low)


def _vol_mult(ohlc: "pd.DataFrame") -> float:
    """Latest volume ÷ trailing 20-session average volume (excluding the latest bar)."""
    if len(ohlc) < 2:
        return 0.0
    avg = ohlc["volume"].iloc[:-1].tail(_VOL_WINDOW).mean()
    if not avg or avg <= 0:
        return 0.0
    return float(ohlc["volume"].iloc[-1] / avg)


def _range_atr_mult(ohlc: "pd.DataFrame") -> float:
    """Latest bar range (high−low) in ATR units — the 'band-expanding / wide bar' test."""
    a = atr(ohlc).iloc[-1]
    if not a or a <= 0:
        return 0.0
    last = ohlc.iloc[-1]
    return float((last["high"] - last["low"]) / a)


def confirmed_break(ohlc: "pd.DataFrame", box_high: float, params: Params = PARAMS) -> bool:
    """Close > box_high AND volume ≥ volume_surge_mult × 20d AND range ≥ wide_bar_atr_mult × ATR."""
    if len(ohlc) < 2:
        return False
    close = float(ohlc["close"].iloc[-1])
    return (
        close > box_high
        and _vol_mult(ohlc) >= params.volume_surge_mult
        and _range_atr_mult(ohlc) >= params.wide_bar_atr_mult
)


def _swept_below(ohlc: "pd.DataFrame", box_low: float, window: int) -> bool:
    """Did any of the recent setup-window bars (before the breakout bar) dip below the box low?"""
    if len(ohlc) < 2:
        return False
    recent = ohlc.iloc[:-1].tail(window)
    return bool((recent["low"] < box_low).any())


def sweep_reversal(ohlc: "pd.DataFrame", box_low: float, params: Params = PARAMS) -> bool:
    """Is the latest bar a sweep-reversal: low < box low, close reclaimed, long lower wick?

    The wick must dominate the bar (≥ sweep_wick_min_frac of the range) and the close must finish
    in the upper part of the range (≥ sweep_close_pos_min) — a *rejection* of the sweep, not a
    breakdown that happened to bounce.
    """
    if len(ohlc) < 2:
        return False
    last = ohlc.iloc[-1]
    lo, hi = float(last["low"]), float(last["high"])
    op, cl = float(last["open"]), float(last["close"])
    if lo >= box_low or cl <= box_low:  # no sweep / closed below = breakdown, not a reversal
        return False
    rng = hi - lo
    if rng <= 0:
        return False
    lower_wick = min(op, cl) - lo
    close_pos = (cl - lo) / rng
    return (lower_wick / rng >= params.sweep_wick_min_frac
            and close_pos >= params.sweep_close_pos_min)


def detect(ohlc: "pd.DataFrame", box_high: float, box_low: float, params: Params = PARAMS) -> Ignition:
    """Classify the trigger: SPRING (swept then reclaimed+broke) > BREAK > SWEEP (reversal bar) > NONE."""
    if confirmed_break(ohlc, box_high, params):
        if _swept_below(ohlc, box_low, params.setup_sessions):
            return Ignition.SPRING
        return Ignition.BREAK
    if sweep_reversal(ohlc, box_low, params):
        return Ignition.SWEEP
    return Ignition.NONE


@dataclass
class IgnitionState:
    type: Ignition
    vol_mult: float
    range_atr: float
    quality: float  # [0, 1] — for within-stage ranking


def assess(ohlc: "pd.DataFrame", box_high: float, box_low: float, params: Params = PARAMS) -> IgnitionState:
    """Trigger type + a normalized quality score (volume surge × bar width, spring premium).

    SWEEP quality is rejection geometry instead (close position × wick dominance) — there is no
    breakout bar to grade yet.
    """
    ig = detect(ohlc, box_high, box_low, params)
    vm, rm = _vol_mult(ohlc), _range_atr_mult(ohlc)
    if ig is Ignition.NONE:
        return IgnitionState(type=ig, vol_mult=vm, range_atr=rm, quality=0.0)
    if ig is Ignition.SWEEP:
        last = ohlc.iloc[-1]
        rng = float(last["high"]) - float(last["low"])
        close_pos = (float(last["close"]) - float(last["low"])) / rng if rng > 0 else 0.0
        wick_frac = ((min(float(last["open"]), float(last["close"])) - float(last["low"])) / rng
                     if rng > 0 else 0.0)
        quality = min(1.0, 0.5 * close_pos + 0.5 * wick_frac)
        return IgnitionState(type=ig, vol_mult=vm, range_atr=rm, quality=quality)
    vol_q = min(1.0, vm / (2.0 * params.volume_surge_mult))
    wide_q = min(1.0, rm / (2.0 * params.wide_bar_atr_mult))
    base = 0.5 * vol_q + 0.5 * wide_q
    quality = min(1.0, base * (1.25 if ig is Ignition.SPRING else 1.0))  # spring premium
    return IgnitionState(type=ig, vol_mult=vm, range_atr=rm, quality=quality)
