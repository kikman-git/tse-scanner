"""Fuel — "are shorts crowded and trapped?" — the edge (Phase 1 — DESIGN.md §3.1).

- **Primary gauge:** days-to-cover = 信用売残 ÷ ADV. High = shorts can't all exit quickly = trapped.
- **Gate:** 信用倍率 (買残 ÷ 売残) < 1.0 → more shorts than longs ("取り組み good").
- **Boosters** (weights *earned* by the event study, not assumed): 逆日歩 present/rising;
  信用売残 rising across ~3 weekly prints in the setup window.
- Normalize against the name's **own** history — raw 売残 is meaningless cross-sectionally.

All reads must come from the publication-date-aware store (DESIGN.md §6.1).
"""

from __future__ import annotations

import math
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
    rising: bool
    gyaku_hibu: bool
    has_fuel: bool
    score: float  # normalized composite in [0, 1]


def _dtc_component(dtc: float, threshold: float) -> float:
    """Squash days-to-cover into [0, 1], saturating ~2× the threshold. Monotone in 'trapped'."""
    if not math.isfinite(dtc):
        return 1.0
    return max(0.0, min(1.0, dtc / (2.0 * threshold)))


def assess(*, short_balance: float, long_balance: float, adv: float,
           rising: bool = False, gyaku_hibu: bool = False, params: Params = PARAMS) -> Fuel:
    """Full fuel assessment for one name: the gate + normalized booster-weighted score.

    Gate (DESIGN.md §3.1): 信用倍率 < credit_ratio_max AND days-to-cover ≥ days_to_cover_min.
    Score (in [0,1]): days-to-cover is the primary term; 売残-rising and 逆日歩 are boosters whose
    weights are *placeholders* until the event study earns them (DESIGN.md §3.1, §10).
    """
    dtc = days_to_cover(short_balance, adv)
    cr = credit_ratio(long_balance, short_balance)
    has_fuel = (cr < params.credit_ratio_max) and (dtc >= params.days_to_cover_min)

    base = _dtc_component(dtc, params.days_to_cover_min)
    boost = (0.15 if rising else 0.0) + (0.15 if gyaku_hibu else 0.0)
    score = max(0.0, min(1.0, 0.70 * base + boost + (0.0 if has_fuel else -0.0)))
    if not has_fuel:
        score *= 0.5  # un-gated names still rank, but below any gated name of equal raw fuel
    return Fuel(days_to_cover=dtc, credit_ratio=cr, rising=rising,
                gyaku_hibu=gyaku_hibu, has_fuel=has_fuel, score=score)
