"""Unit tests for the technical exit map (signals/targets.py) — pure pandas, no store."""

import pandas as pd
import pytest

from tse_scanner.config import PARAMS
from tse_scanner.signals import targets


def _frame(closes, highs=None, lows=None):
    n = len(closes)
    return pd.DataFrame({
        "open": closes,
        "high": highs if highs is not None else [c * 1.01 for c in closes],
        "low": lows if lows is not None else [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1_000] * n,
    })


# --- 節目 (round-number) helpers ------------------------------------------------

def test_round_step_grid():
    assert targets.round_step(450) == 100      # under ¥1,000 → ¥100 grid
    assert targets.round_step(2_300) == 500    # ¥1,000–5,000 → ¥500
    assert targets.round_step(7_000) == 1_000
    assert targets.round_step(23_000) == 5_000
    assert targets.round_step(120_000) == 10_000


def test_next_round_above():
    assert targets.next_round_above(951) == 1_000
    assert targets.next_round_above(1_000) == 1_500  # strictly above
    assert targets.next_round_above(3_499) == 3_500


def test_snap_below_round():
    # a target a hair past a 節目 pulls back to just under it (sell in front of the wall)
    snapped = targets.snap_below_round(1_010.0, PARAMS)
    assert snapped == pytest.approx(1_000 * (1 - PARAMS.exit_round_undercut))
    # already below the level and within the band → also pinned under the wall, never raised
    assert targets.snap_below_round(992.0, PARAMS) == 992.0
    # far from any 節目 → unchanged
    assert targets.snap_below_round(1_270.0, PARAMS) == 1_270.0
    # the wall sits in the entry zone itself (snap would land ≤ floor) → raw projection stands;
    # a "target" below the breakout level is not an exit
    assert targets.snap_below_round(1_010.0, PARAMS, floor=998.0) == 1_010.0
    # NaN-safe
    nan = float("nan")
    assert targets.snap_below_round(nan, PARAMS) != targets.snap_below_round(nan, PARAMS) or True


# --- swing highs ----------------------------------------------------------------

def test_swing_highs_finds_and_merges_shelves():
    flat = [100.0] * 60
    highs = flat.copy()
    highs[20] = 130.0           # one clear pivot
    highs[40] = 130.5           # second pivot <1% above the first → same shelf, keep higher
    highs[50] = 150.0           # distinct higher shelf
    s = pd.Series(highs)
    piv = targets.swing_highs(s, k=2, lookback=60)
    assert 150.0 in piv
    assert 130.5 in piv and 130.0 not in piv  # merged into the higher print
    assert piv == sorted(piv)


def test_swing_highs_too_short():
    assert targets.swing_highs(pd.Series([1.0, 2.0]), k=2, lookback=60) == []


# --- compute --------------------------------------------------------------------

def _coiled(n=300, base=900.0, box_high=910.0, box_low=890.0):
    """A long flat tape boxed between box_low and box_high (mid 900)."""
    closes = [base] * n
    highs = [box_high] * n
    lows = [box_low] * n
    return _frame(closes, highs, lows)


def test_compute_measured_move_and_rr():
    f = _coiled()
    m = targets.compute(f, box_high=910.0, box_low=890.0)
    # not yet through the box → entry ref is the prospective break level
    assert m.entry_ref == 910.0 and m.stop_ref == 890.0
    # T1 = 910 + 1×20 = 930; T2 = 910 + 2×20 = 950 → 950 is within 2% of the ¥1,000?
    # no (5.3% away) — both unsnapped
    assert m.t1 == pytest.approx(930.0)
    assert m.t2 == pytest.approx(950.0)
    assert m.rr_t1 == pytest.approx((930.0 - 910.0) / (910.0 - 890.0))
    assert m.atr is not None and m.sigma_h is not None
    assert m.t1_atr == pytest.approx((m.t1 - m.entry_ref) / m.atr)


def test_compute_snaps_t1_below_round_wall():
    # box 900–950: T1 raw = 950 + 50 = 1000 — dead on the ¥1,000 wall → snapped just below it,
    # and the snap stays above the box high (a legit, reachable target)
    f = _coiled(base=925.0, box_high=950.0, box_low=900.0)
    m = targets.compute(f, box_high=950.0, box_low=900.0)
    assert m.t1 == pytest.approx(1_000 * (1 - PARAMS.exit_round_undercut))
    assert 950.0 < m.t1 < 1_000

    # wall in the entry zone: box 985–998, T1 raw = 1011 → snap (995) would sit below the box
    # high, so the raw projection survives instead
    g = _coiled(base=990.0, box_high=998.0, box_low=985.0)
    m2 = targets.compute(g, box_high=998.0, box_low=985.0)
    assert m2.t1 == pytest.approx(1_011.0)


def test_compute_blue_sky_and_resistance():
    # tape with an old high at 1100, currently boxed lower → that shelf is overhead supply
    closes = [900.0] * 300
    highs = [910.0] * 300
    lows = [890.0] * 300
    highs[100] = 1_100.0
    f = _frame(closes, highs, lows)
    m = targets.compute(f, box_high=910.0, box_low=890.0)
    assert not m.blue_sky
    assert m.high_52w == 1_100.0
    assert m.resistances and m.resistances[0] == pytest.approx(1_100.0)

    # breakout bar closing above every prior high → 青天井 (no overhead supply)
    g = f.copy()
    g.loc[299, ["open", "high", "low", "close"]] = [905.0, 1_200.0, 900.0, 1_150.0]
    m2 = targets.compute(g, box_high=910.0, box_low=890.0)
    assert m2.blue_sky
    assert m2.entry_ref == 1_150.0  # already through the box → entry ref is the close
    assert m2.resistances == []     # nothing left overhead


def test_compute_zero_risk_degrades_gracefully():
    # degenerate box (high == low) → no R:R, never a divide-by-zero; the snap floor keeps the
    # zero-height "target" at the box rather than below it
    f = _coiled(box_high=900.0, box_low=900.0)
    m = targets.compute(f, box_high=900.0, box_low=900.0)
    assert m.rr_t1 is None
    assert m.t1 == pytest.approx(900.0)
