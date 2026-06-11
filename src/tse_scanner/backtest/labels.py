"""First-touch triple-barrier labels (Phase 1 — DESIGN.md §6).

From an ignition entry, within `horizon_sessions`:
  +target% hit first  → WIN
  −stop% hit first    → LOSS
  neither             → SCRATCH

Yields a *tradeable* expectancy (win-rate × payoff), matches how a squeeze is actually traded
(scale into the spike, cut if wrong), and handles spike-and-fade. Target/stop/horizon are tunable
(Params). Critically, fills assume **stop-high no-fill** (DESIGN.md §6.2): if the entry bar is
locked ストップ高, the entry is missed, not granted.
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
    NO_FILL = "no_fill"  # entry bar locked ストップ高 — could not buy (DESIGN.md §6.2)


def _is_locked_limit_up(bar, prev_close: float | None) -> bool:
    """Proxy for ストップ高 lock: zero intraday range on an up gap → no fills available (§6.2).

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


def first_touch_detail(entry_idx: int, forward_ohlc: "pd.DataFrame", params: Params = PARAMS) -> FirstTouch:
    """As `first_touch`, but also returns entry price, exit index, and realized return."""
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
    target = entry * (1.0 + params.target_pct)
    stop = entry * (1.0 - params.stop_pct)

    last = min(n - 1, entry_idx + params.horizon_sessions)
    for i in range(entry_idx + 1, last + 1):
        bar = forward_ohlc.iloc[i]
        hi, lo = float(bar["high"]), float(bar["low"])
        hit_t, hit_s = hi >= target, lo <= stop
        if hit_t and hit_s:  # both in one bar → assume stop first (conservative; DESIGN.md §6)
            return FirstTouch(Label.LOSS, i, entry, -params.stop_pct)
        if hit_t:
            return FirstTouch(Label.WIN, i, entry, params.target_pct)
        if hit_s:
            return FirstTouch(Label.LOSS, i, entry, -params.stop_pct)
    return FirstTouch(Label.SCRATCH, last, entry, float(forward_ohlc.iloc[last]["close"]) / entry - 1.0)
