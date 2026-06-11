"""Lifecycle state machine — the one pipeline that buckets every universe name by stage.

DESIGN.md §2: setup and trigger are the *same* pipeline at different stages — you can't detect the
break without already knowing where the fuel and the coil are. Acting target is `IGNITING`; the
watchlist is `PRIMED`; `CONFIRMED` (売残 actually dropping) is lagging post-trade validation.

    | Stage     | Condition                                              | Use               |
    |-----------|--------------------------------------------------------|-------------------|
    | CROWDED   | Fuel present (high days-to-cover, 信用倍率<1), no coil  | Background watch  |
    | PRIMED    | Fuel + coiled (BB squeeze + tight box)                 | Active watchlist  |
    | IGNITING  | Primed + confirmed upside break (spring = premium tag) | ACT               |
    | CONFIRMED | Follow-through + (lagging) 売残 dropping                | Confirmation      |

Phase 2 wiring (DESIGN.md §9). Pure-stdlib so it imports in the smoke path.
"""

from __future__ import annotations

from enum import IntEnum


class Stage(IntEnum):
    """Ordered so that higher = closer to action (gate-by-stage ranking, DESIGN.md §7)."""

    NONE = 0       # in universe, no fuel
    CROWDED = 1    # fuel present, not yet coiled
    PRIMED = 2     # fuel + coil → active watchlist
    IGNITING = 3   # confirmed break → ACT
    CONFIRMED = 4  # follow-through + 売残 dropping → post-trade

    @property
    def is_actionable(self) -> bool:
        return self is Stage.IGNITING


def classify(*, has_fuel: bool, is_coiled: bool, has_ignition: bool, has_followthrough: bool) -> Stage:
    """Map boolean signal states to a lifecycle Stage.

    Thin and explicit on purpose — the *thresholds* live in signals/ (fuel/coil/ignition); this only
    composes their booleans into the DESIGN.md §2 ladder.
    """
    if has_fuel and is_coiled and has_ignition and has_followthrough:
        return Stage.CONFIRMED
    if has_fuel and is_coiled and has_ignition:
        return Stage.IGNITING
    if has_fuel and is_coiled:
        return Stage.PRIMED
    if has_fuel:
        return Stage.CROWDED
    return Stage.NONE
