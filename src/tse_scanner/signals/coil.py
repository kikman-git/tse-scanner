"""Coil — "is the spring wound?" — timing (Phase 1).

State, not symptom. We want volatility unusually low **for this name** AND price contained in a
tight band:
- **BB-width squeeze:** Bollinger band-width at a low percentile vs the ~120-session baseline
  (TTM-squeeze convention: lowest band width in ~6 months).
- **AND** price boxed over the ~14-session setup window.
- Recent narrow bars = optional *final-tightening* confirmation (demoted from definition).

Bollinger / ATR are computed with plain pandas (rolling mean/std) rather than pandas-ta, to keep
the math explicit and the import surface small.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params

if TYPE_CHECKING:
    import pandas as pd

_BB_PERIOD = 20  # Bollinger lookback (TTM-squeeze convention)
_ATR_PERIOD = 14


def bb_width(close: "pd.Series", period: int = _BB_PERIOD) -> "pd.Series":
    """Bollinger band-width = (upper − lower) / middle = 4·σ / SMA. Unitless, comparable over time."""
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    return (4.0 * sd) / mid


def bb_width_percentile(close: "pd.Series", baseline: int = PARAMS.baseline_sessions) -> float:
    """Current Bollinger band-width as a percentile (0–1) of its own `baseline`-session history.

    Low = compressed vs this name's normal. NaN if not enough history.
    """
    w = bb_width(close).dropna()
    if len(w) < 2:
        return float("nan")
    window = w.tail(baseline)
    current = window.iloc[-1]
    return float((window <= current).mean())


def atr(ohlc: "pd.DataFrame", period: int = _ATR_PERIOD) -> "pd.Series":
    """Average True Range (simple rolling mean of true range)."""
    high, low, close = ohlc["high"], ohlc["low"], ohlc["close"]
    prev_close = close.shift(1)
    tr = (high - low).combine((high - prev_close).abs(), max).combine(
        (low - prev_close).abs(), max
)
    return tr.rolling(period).mean()


def box(high: "pd.Series", low: "pd.Series", window: int = PARAMS.setup_sessions) -> tuple[float, float]:
    """Return (box_high, box_low) over the setup window — the levels ignition breaks/sweeps."""
    return float(high.tail(window).max()), float(low.tail(window).min())


@dataclass
class Coil:
    bb_width_pct: float
    box_high: float
    box_low: float
    box_height_atr: float  # box height in ATR units (tightness)
    is_coiled: bool


def assess(ohlc: "pd.DataFrame", params: Params = PARAMS) -> Coil:
    """BB-width ≤ pctile cutoff AND box height tight (≤ box_height_max_atr × ATR).

    The box is measured over the setup window **excluding the latest bar** so that an ignition bar
    doesn't inflate its own box (the latest bar is the candidate break, tested in `ignition`).
    """
    pre = ohlc.iloc[:-1] if len(ohlc) > 1 else ohlc
    bw_pct = bb_width_percentile(ohlc["close"], params.baseline_sessions)
    bh, bl = box(pre["high"], pre["low"], params.setup_sessions)
    a = atr(ohlc, _ATR_PERIOD).iloc[-1]
    height_atr = float((bh - bl) / a) if a and a > 0 else float("inf")

    tight_bb = (bw_pct == bw_pct) and bw_pct <= params.bb_width_pctile_max  # NaN-safe
    tight_box = height_atr <= params.box_height_max_atr
    return Coil(bb_width_pct=bw_pct, box_high=bh, box_low=bl,
                box_height_atr=height_atr, is_coiled=bool(tight_bb and tight_box))


def is_coiled(ohlc: "pd.DataFrame", params: Params = PARAMS) -> bool:
    """BB-width ≤ pctile cutoff AND box height tight (≤ box_height_max_atr × ATR)."""
    return assess(ohlc, params).is_coiled
