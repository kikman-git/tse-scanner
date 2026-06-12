"""Lifecycle state machine — the one pipeline that buckets every universe name by stage.

: setup and trigger are the *same* pipeline at different stages — you can't detect the
break without already knowing where the fuel and the coil are. Acting target is `IGNITING`; the
watchlist is `PRIMED`; `CONFIRMED` (売残 actually dropping) is lagging post-trade validation.

De-gated ladder (default since 2026-06-12): **coil + ignition drive the actionable stages; fuel is
a ranking overlay**, not a progression gate. The fuel-gated AND fired once in the 10y event study
(unfalsifiable), so fuel's contribution is now *tested* by the study's fuel-quartile cohort splits
instead of assumed. `require_fuel=True` restores the legacy squeeze-only ladder.

    | Stage     | Condition (require_fuel=False)                          | Use               |
    |-----------|---------------------------------------------------------|-------------------|
    | CROWDED   | Fuel present (top-decile DtC, 信用倍率<1), no coil       | Squeeze watch     |
    | PRIMED    | Coiled (BB squeeze + tight box) — fuel optional          | Active watchlist  |
    | IGNITING  | Coiled + confirmed upside break (spring = premium tag)   | ACT               |
    | CONFIRMED | Follow-through + (lagging) 売残 dropping                 | Confirmation      |

Phase 2 wiring. Pure-stdlib so it imports in the smoke path.
"""

from __future__ import annotations

from enum import IntEnum


class Stage(IntEnum):
    """Ordered so that higher = closer to action (gate-by-stage ranking)."""

    NONE = 0       # in universe, no fuel
    CROWDED = 1    # fuel present, not yet coiled
    PRIMED = 2     # fuel + coil → active watchlist
    IGNITING = 3   # confirmed break → ACT
    CONFIRMED = 4  # follow-through + 売残 dropping → post-trade

    @property
    def is_actionable(self) -> bool:
        return self is Stage.IGNITING


def classify(*, has_fuel: bool, is_coiled: bool, has_ignition: bool, has_followthrough: bool,
             require_fuel: bool = False) -> Stage:
    """Map boolean signal states to a lifecycle Stage.

    Thin and explicit on purpose — the *thresholds* live in signals/ (fuel/coil/ignition); this only
    composes their booleans into the ladder. `require_fuel=False` (default) lets coil/ignition
    progress without fuel — fuel still ranks within stage and marks CROWDED; `require_fuel=True`
    is the legacy squeeze-only ladder (kept for event-study cohort comparisons).
    """
    fuel_ok = has_fuel or not require_fuel
    if fuel_ok and is_coiled and has_ignition and has_followthrough:
        return Stage.CONFIRMED
    if fuel_ok and is_coiled and has_ignition:
        return Stage.IGNITING
    if fuel_ok and is_coiled:
        return Stage.PRIMED
    if has_fuel:
        return Stage.CROWDED
    return Stage.NONE
