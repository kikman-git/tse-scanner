"""Ignition — "has it fired?" — trigger, long-only (Phase 1 — DESIGN.md §3.3).

- **Break (standard):** close above the box high + volume surge + wide, band-expanding bar.
  Close (not an intraday poke); volume + width reject fakeouts.
- **Spring (premium tag):** price first dips below the box low (sweeps sell-side stops → validates
  shorts, who add), then reclaims and breaks up — maximal trap. Ranks higher (DESIGN.md §7).
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


def detect(ohlc: "pd.DataFrame", box_high: float, box_low: float, params: Params = PARAMS) -> Ignition:
    """Classify the trigger: SPRING (swept box low then reclaimed+broke) > BREAK > NONE."""
    if not confirmed_break(ohlc, box_high, params):
        return Ignition.NONE
    if _swept_below(ohlc, box_low, params.setup_sessions):
        return Ignition.SPRING
    return Ignition.BREAK


@dataclass
class IgnitionState:
    type: Ignition
    vol_mult: float
    range_atr: float
    quality: float  # [0, 1] — for within-stage ranking (DESIGN.md §7)


def assess(ohlc: "pd.DataFrame", box_high: float, box_low: float, params: Params = PARAMS) -> IgnitionState:
    """Trigger type + a normalized quality score (volume surge × bar width, spring premium)."""
    ig = detect(ohlc, box_high, box_low, params)
    vm, rm = _vol_mult(ohlc), _range_atr_mult(ohlc)
    if ig is Ignition.NONE:
        return IgnitionState(type=ig, vol_mult=vm, range_atr=rm, quality=0.0)
    vol_q = min(1.0, vm / (2.0 * params.volume_surge_mult))
    wide_q = min(1.0, rm / (2.0 * params.wide_bar_atr_mult))
    base = 0.5 * vol_q + 0.5 * wide_q
    quality = min(1.0, base * (1.25 if ig is Ignition.SPRING else 1.0))  # spring premium (§7)
    return IgnitionState(type=ig, vol_mult=vm, range_atr=rm, quality=quality)
