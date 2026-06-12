"""Fuel — "are shorts crowded and trapped?" — the edge (Phase 1).

- **Primary gauge:** days-to-cover = 信用売残 ÷ ADV. High = shorts can't all exit quickly = trapped.
  DTC is ADV-deflated, so unlike raw 売残 it *is* comparable across names — the gate is its
  **cross-sectional percentile within today's universe** (top decile), plus a small absolute floor.
  (The old absolute DTC ≥ 5 gate passed 13/1,633 names and produced 1 event in the 10y study —
  unfalsifiable; the percentile gate self-adjusts to the market.)
- **Gate:** 信用倍率 (買残 ÷ 売残) < 1.0 → more shorts than longs ("取り組み good").
- **Boosters** (weights *earned* by the event study, not assumed): 逆日歩 present/rising;
  信用売残 rising across ~3 weekly prints in the setup window.

All reads must come from the publication-date-aware store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params

if TYPE_CHECKING:
    import pandas as pd


def days_to_cover(short_balance: float, adv: float) -> float:
    """信用売残 ÷ average daily volume. The cleanest operationalization of 'trapped'.

    ADV is in shares (same unit as 信用売残). adv ≤ 0 → inf (can't be covered at all).
    """
    if adv is None or adv <= 0 or short_balance is None:
        return float("inf") if short_balance else 0.0
    return float(short_balance) / float(adv)


def credit_ratio(long_balance: float, short_balance: float) -> float:
    """信用倍率 = 買残 ÷ 売残. < 1.0 passes the gate. No shorts → inf (not short-heavy)."""
    if short_balance is None or short_balance <= 0:
        return float("inf")
    return float(long_balance) / float(short_balance)


def dtc_percentile_ranks(dtc: "pd.Series") -> "pd.Series":
    """Cross-sectional percentile rank (0, 1] of days-to-cover across one day's universe.

    The ranking pool is names with a real short base (finite dtc > 0). +inf (shorts that can never
    cover — zero ADV) pins to 1.0. No shorts / no margin print → NaN: *absence of evidence*, which
    fails the fuel gate but no longer blocks the stage ladder (require_fuel=False).
    """
    import numpy as np
    import pandas as pd

    vals = dtc.astype(float)
    out = pd.Series(float("nan"), index=vals.index)
    finite = vals[np.isfinite(vals) & (vals > 0)]
    if len(finite):
        out.loc[finite.index] = finite.rank(pct=True, method="average")
    out[np.isposinf(vals)] = 1.0
    return out


def short_balance_rising(margin_history: "pd.DataFrame", prints: int = PARAMS.short_balance_trend_prints) -> bool:
    """Is 信用売残 rising across the last `prints` weekly prints? (fuel building).

    `margin_history` is oldest→newest (store.margin_history). Needs ≥`prints` rows to confirm; with
    fewer prints available (free path keeps only a few PDFs) we cannot confirm a trend → False.
    """
    if margin_history is None or len(margin_history) < prints:
        return False
    s = margin_history["short_total"].tail(prints).astype(float).tolist()
    return all(b >= a for a, b in zip(s, s[1:])) and s[-1] > s[0]


@dataclass
class Fuel:
    """Computed fuel state for one name as-of a date."""

    days_to_cover: float
    credit_ratio: float
    dtc_pctile: float  # cross-sectional rank of dtc within the day's universe (NaN = no margin)
    rising: bool
    gyaku_hibu: bool
    has_fuel: bool
    score: float  # normalized composite in [0, 1]


def assess(*, short_balance: float, long_balance: float, adv: float,
           dtc_pctile: float = float("nan"),
           rising: bool = False, gyaku_hibu: bool = False, params: Params = PARAMS) -> Fuel:
    """Full fuel assessment for one name: the gate + normalized booster-weighted score.

    Gate: 信用倍率 < credit_ratio_max AND dtc_pctile ≥ dtc_pctile_min AND dtc ≥ days_to_cover_floor.
    `dtc_pctile` comes from `dtc_percentile_ranks` over the whole universe (the caller owns the
    cross-section); without one the gate cannot pass. Score (in [0,1]): the percentile is the
    primary term; 売残-rising and 逆日歩 are boosters whose weights are *placeholders* until the
    event study earns them.
    """
    dtc = days_to_cover(short_balance, adv)
    cr = credit_ratio(long_balance, short_balance)
    pctile_ok = (dtc_pctile == dtc_pctile) and dtc_pctile >= params.dtc_pctile_min  # NaN-safe
    has_fuel = (cr < params.credit_ratio_max) and pctile_ok and (dtc >= params.days_to_cover_floor)

    base = dtc_pctile if dtc_pctile == dtc_pctile else 0.0
    boost = (0.15 if rising else 0.0) + (0.15 if gyaku_hibu else 0.0)
    score = max(0.0, min(1.0, 0.70 * base + boost))
    if not has_fuel:
        score *= 0.5  # un-gated names still rank, but below any gated name of equal raw fuel
    return Fuel(days_to_cover=dtc, credit_ratio=cr, dtc_pctile=dtc_pctile, rising=rising,
                gyaku_hibu=gyaku_hibu, has_fuel=has_fuel, score=score)
