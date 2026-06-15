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
    risk_frac: float | None = None  # fractional risk actually taken (entry→stop) — R = ret / risk_frac


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
            return FirstTouch(Label.WIN, i, entry, tp, sp)
        if hit_s:
            return FirstTouch(Label.LOSS, i, entry, -sp, sp)
    return FirstTouch(Label.SCRATCH, last, entry,
                      float(forward_ohlc.iloc[last]["close"]) / entry - 1.0, sp)


# --- Exit-model variants ------------------------------------------------------------------
# The fixed −6%/+12% barriers stop out the median backtest trade at −1R (the stop sits inside
# small/mid-cap noise). These re-express the exit as a function of each name's own ATR(14):
#   "atr"        — barriers at entry ± k·ATR (volatility-proportional risk; honest R-accounting).
#   "atr_trail"  — Chandelier: initial ATR stop, then ratchet to (run-high − k·ATR), no cap, so a
#                  青天井 runner is ridden instead of sold at a fixed +12%.
# Both keep the sacred rails: stop-high no-fill on the entry bar, gap-aware fills (open beyond a
# level fills at the open). `atr_at_entry` is ATR in *price* units known as-of the entry bar
# (caller computes it point-in-time); when it is unavailable the model degrades to the fixed %.

@dataclass(frozen=True)
class ExitModel:
    kind: str = "fixed"               # "fixed" | "atr" | "atr_trail"
    target_pct: float = PARAMS.target_pct
    stop_pct: float = PARAMS.stop_pct
    atr_stop_mult: float = PARAMS.atr_stop_mult
    atr_target_mult: float = PARAMS.atr_target_mult
    atr_trail_mult: float = PARAMS.atr_trail_mult


def exit_model_from_params(params: Params = PARAMS, kind: str | None = None) -> ExitModel:
    """Build an ExitModel from Params (kind override lets the bake-off sweep all three)."""
    return ExitModel(kind=kind or params.exit_model, target_pct=params.target_pct,
                     stop_pct=params.stop_pct, atr_stop_mult=params.atr_stop_mult,
                     atr_target_mult=params.atr_target_mult, atr_trail_mult=params.atr_trail_mult)


def _classify(ret: float, risk_frac: float) -> Label:
    """Label a trailing/horizon exit by realized R (no fixed barrier touched). A ±0.5R dead-band
    keeps win-rate roughly comparable with the barrier model's WIN/LOSS/SCRATCH buckets."""
    if ret >= 0.5 * risk_frac:
        return Label.WIN
    if ret <= -0.5 * risk_frac:
        return Label.LOSS
    return Label.SCRATCH


def simulate_exit(entry_idx: int, forward_ohlc: "pd.DataFrame", params: Params = PARAMS, *,
                  model: ExitModel | None = None, atr_at_entry: float | None = None) -> FirstTouch:
    """First-touch outcome under a configurable exit model (fixed / atr / atr_trail).

    `atr_at_entry` is ATR in price units as-of the entry bar; the caller owns its point-in-time
    computation (e.g. `coil.atr(bars[:entry_idx+1]).iloc[-1]`). Falls back to fixed % barriers when
    ATR is missing (short history) so the function never raises. Same gap-aware / stop-high-no-fill
    discipline as `first_touch_detail`; returns `risk_frac` so callers normalize R by real risk.
    """
    model = model or exit_model_from_params(params)
    n = len(forward_ohlc)
    if entry_idx < 0 or entry_idx >= n:
        return FirstTouch(Label.SCRATCH, None, None, None, None)

    entry_bar = forward_ohlc.iloc[entry_idx]
    prev_close = float(forward_ohlc.iloc[entry_idx - 1]["close"]) if entry_idx > 0 else None
    if _is_locked_limit_up(entry_bar, prev_close):
        return FirstTouch(Label.NO_FILL, None, None, None, None)
    entry = float(entry_bar["close"])
    if entry <= 0:
        return FirstTouch(Label.SCRATCH, None, entry, None, None)

    a = float(atr_at_entry) if (atr_at_entry is not None and atr_at_entry == atr_at_entry
                                and atr_at_entry > 0) else None
    use_atr = model.kind in ("atr", "atr_trail") and a is not None
    trailing = model.kind == "atr_trail" and use_atr

    if not use_atr:  # "fixed", or ATR-model with no ATR available → fixed % fallback
        stop = entry * (1.0 - model.stop_pct)
        target: float | None = entry * (1.0 + model.target_pct)
    elif model.kind == "atr":
        stop = entry - model.atr_stop_mult * a
        target = entry + model.atr_target_mult * a
    else:  # atr_trail — initial ATR stop, no fixed target
        stop = entry - model.atr_stop_mult * a
        target = None
    risk_frac = max(1e-6, (entry - stop) / entry)

    last = min(n - 1, entry_idx + params.horizon_sessions)
    cur_stop = stop
    run_high = entry
    for i in range(entry_idx + 1, last + 1):
        bar = forward_ohlc.iloc[i]
        op, hi, lo = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if op <= cur_stop:  # gapped through the (possibly trailed) stop — fill at the open
            r = op / entry - 1.0
            return FirstTouch(_classify(r, risk_frac) if trailing else Label.LOSS, i, entry, r, risk_frac)
        if target is not None and op >= target:  # gapped through the target — keep the windfall
            return FirstTouch(Label.WIN, i, entry, op / entry - 1.0, risk_frac)
        hit_t = target is not None and hi >= target
        hit_s = lo <= cur_stop
        if hit_t and hit_s:  # both in one bar → stop first (conservative)
            r = cur_stop / entry - 1.0
            return FirstTouch(Label.LOSS, i, entry, r, risk_frac)
        if hit_t:
            return FirstTouch(Label.WIN, i, entry, target / entry - 1.0, risk_frac)
        if hit_s:
            r = cur_stop / entry - 1.0
            return FirstTouch(_classify(r, risk_frac) if trailing else Label.LOSS, i, entry, r, risk_frac)
        if trailing:  # ratchet the stop up only after surviving this bar (chandelier)
            run_high = max(run_high, hi)
            cur_stop = max(cur_stop, run_high - model.atr_trail_mult * a)

    exit_close = float(forward_ohlc.iloc[last]["close"])  # horizon time-stop → exit at the close
    ret = exit_close / entry - 1.0
    return FirstTouch(_classify(ret, risk_frac) if trailing else Label.SCRATCH,
                      last, entry, ret, risk_frac)
