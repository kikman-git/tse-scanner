"""First-touch triple-barrier labels (Phase 1).

From an ignition entry, within `horizon_sessions`:
  +target% hit first  → WIN
  −stop% hit first    → LOSS
  neither             → SCRATCH

Yields a *tradeable* expectancy (win-rate × payoff), matches how a squeeze is actually traded
(scale into the spike, cut if wrong), and handles spike-and-fade. Target/stop/horizon are tunable
(Params) and overridable per event (structural stops). Critically, fills assume **stop-high
no-fill** (a locked ストップ高 entry bar is missed, not granted) and are **gap-aware** (a bar
opening beyond a barrier fills at the open — overnight 決算 gaps don't honor stop levels).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params

if TYPE_CHECKING:
    import pandas as pd


class Label(Enum):
    WIN = "win"
    LOSS = "loss"
    SCRATCH = "scratch"
    NO_FILL = "no_fill"  # entry bar locked ストップ高 — could not buy


def _is_locked_limit_up(bar, prev_close: float | None) -> bool:
    """Proxy for ストップ高 lock: zero intraday range on an up gap → no fills available.

    A locked limit-up board trades at a single price all session (high == low == close). Without the
    JPX price-limit table we approximate the lock by zero range on an up day; conservative — it only
    ever *removes* fills, never grants them.
    """
    rng = float(bar["high"]) - float(bar["low"])
    if rng > 1e-9:
        return False
    if prev_close is None:
        return False
    return float(bar["close"]) > float(prev_close)


@dataclass
class FirstTouch:
    label: Label
    exit_idx: int | None          # index into forward_ohlc where a barrier was touched
    entry_price: float | None
    ret: float | None             # realized return at the barrier (None for NO_FILL)


def first_touch(entry_idx: int, forward_ohlc: "pd.DataFrame", params: Params = PARAMS) -> Label:
    """Walk forward from entry; return the first barrier touched (or SCRATCH / NO_FILL)."""
    return first_touch_detail(entry_idx, forward_ohlc, params).label


def first_touch_detail(entry_idx: int, forward_ohlc: "pd.DataFrame", params: Params = PARAMS,
                       *, target_pct: float | None = None,
                       stop_pct: float | None = None) -> FirstTouch:
    """As `first_touch`, but also returns entry price, exit index, and realized return.

    `target_pct`/`stop_pct` override the Params barriers per event — structural stops (e.g. the
    SWEEP trigger stops under the sweep bar's wick low) make the barrier event-specific.

    **Gap-aware fills:** a bar that *opens* beyond a barrier fills at its open, not at the barrier —
    a gap down through the stop loses more than −stop%, a gap up through the target pays more than
    +target%. Without this, any setup that holds through overnight disclosures (決算/上方修正) gets
    a fantasy stop that the market never offered.
    """
    tp = params.target_pct if target_pct is None else target_pct
    sp = params.stop_pct if stop_pct is None else stop_pct
    n = len(forward_ohlc)
    if entry_idx < 0 or entry_idx >= n:
        return FirstTouch(Label.SCRATCH, None, None, None)

    entry_bar = forward_ohlc.iloc[entry_idx]
    prev_close = float(forward_ohlc.iloc[entry_idx - 1]["close"]) if entry_idx > 0 else None
    if _is_locked_limit_up(entry_bar, prev_close):
        return FirstTouch(Label.NO_FILL, None, None, None)

    entry = float(entry_bar["close"])
    if entry <= 0:
        return FirstTouch(Label.SCRATCH, None, entry, None)
    target = entry * (1.0 + tp)
    stop = entry * (1.0 - sp)

    last = min(n - 1, entry_idx + params.horizon_sessions)
    for i in range(entry_idx + 1, last + 1):
        bar = forward_ohlc.iloc[i]
        op, hi, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if op <= stop:  # gapped through the stop — filled at the open, not the barrier
            return FirstTouch(Label.LOSS, i, entry, op / entry - 1.0)
        if op >= target:  # gapped through the target — keep the windfall
            return FirstTouch(Label.WIN, i, entry, op / entry - 1.0)
        hit_t, hit_s = hi >= target, lo <= stop
        if hit_t and hit_s:  # both in one bar → assume stop first (conservative)
            return FirstTouch(Label.LOSS, i, entry, -sp)
        if hit_t:
            return FirstTouch(Label.WIN, i, entry, tp)
        if hit_s:
            return FirstTouch(Label.LOSS, i, entry, -sp)
    return FirstTouch(Label.SCRATCH, last, entry, float(forward_ohlc.iloc[last]["close"]) / entry - 1.0)
