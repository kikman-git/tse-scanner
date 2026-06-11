"""Unit tests for the pure signal + label logic (no network; pandas only)."""

import numpy as np
import pandas as pd

from tse_scanner.backtest.labels import Label, first_touch
from tse_scanner.config import PARAMS
from tse_scanner.signals import coil, fuel, ignition


# --- fuel (DESIGN.md §3.1) ----------------------------------------------------
def test_days_to_cover_and_ratio():
    assert fuel.days_to_cover(100_000, 10_000) == 10.0          # 10 days to cover
    assert fuel.days_to_cover(100_000, 0) == float("inf")        # no volume → uncoverable
    assert fuel.credit_ratio(50, 100) == 0.5                     # 倍率 = 買/売 < 1 → short-heavy
    assert fuel.credit_ratio(100, 0) == float("inf")             # no shorts → not short-heavy


def test_fuel_gate():
    # trapped: DtC ≥ 5 and 倍率 < 1 → has_fuel
    trapped = fuel.assess(short_balance=100_000, long_balance=10_000, adv=10_000)
    assert trapped.has_fuel and trapped.score > 0
    # not trapped: plenty of volume (DtC tiny) → no fuel
    loose = fuel.assess(short_balance=10_000, long_balance=5_000, adv=100_000)
    assert not loose.has_fuel
    # long-heavy (倍率 > 1) fails the gate even with high DtC
    longish = fuel.assess(short_balance=100_000, long_balance=200_000, adv=10_000)
    assert not longish.has_fuel


def test_short_balance_rising():
    rising = pd.DataFrame({"short_total": [100, 150, 200]})
    flat = pd.DataFrame({"short_total": [200, 150, 100]})
    assert fuel.short_balance_rising(rising, prints=3)
    assert not fuel.short_balance_rising(flat, prints=3)
    assert not fuel.short_balance_rising(rising.head(1), prints=3)  # too few prints → can't confirm


# --- coil (DESIGN.md §3.2) ----------------------------------------------------
def _frame(closes, highs=None, lows=None, vols=None):
    n = len(closes)
    return pd.DataFrame({
        "open": closes,
        "high": highs if highs is not None else [c * 1.01 for c in closes],
        "low": lows if lows is not None else [c * 0.99 for c in closes],
        "close": closes,
        "volume": vols if vols is not None else [1_000] * n,
    })


def test_bb_width_lower_when_compressed():
    rng = np.random.default_rng(0)
    volatile = 1000 + np.cumsum(rng.normal(0, 30, 150))
    series = pd.Series(np.r_[volatile, [volatile[-1]] * 30])  # tack on a flat (compressed) tail
    pct = coil.bb_width_percentile(series, baseline=120)
    assert 0.0 <= pct <= 1.0
    assert pct < 0.5  # a flat tail sits in the low band-width percentiles


def test_box_levels():
    f = _frame([100, 110, 105], highs=[101, 112, 106], lows=[99, 108, 104])
    bh, bl = coil.box(f["high"], f["low"], window=3)
    assert bh == 112 and bl == 99


# --- ignition (DESIGN.md §3.3) ------------------------------------------------
def test_confirmed_break_requires_volume_and_width():
    closes = [100.0] * 40
    f = _frame(closes)
    box_high = 101.0
    # quiet last bar just above box but no volume/width surge → not confirmed
    f.loc[39, ["high", "low", "close", "volume"]] = [101.5, 101.0, 101.2, 1_000]
    assert not ignition.confirmed_break(f, box_high, PARAMS)
    # break bar: closes well above, big range, volume surge → confirmed
    f.loc[39, ["high", "low", "close", "volume"]] = [108.0, 100.0, 107.0, 5_000]
    assert ignition.confirmed_break(f, box_high, PARAMS)


def test_spring_beats_break_when_swept():
    closes = [100.0] * 40
    f = _frame(closes)
    box_high, box_low = 101.0, 99.0
    f.loc[37, ["high", "low", "close"]] = [100.5, 97.0, 98.0]  # sweep below box low
    f.loc[39, ["high", "low", "close", "volume"]] = [108.0, 100.0, 107.0, 5_000]  # reclaim + break
    assert ignition.detect(f, box_high, box_low, PARAMS) is ignition.Ignition.SPRING


# --- first-touch labels (DESIGN.md §6) ---------------------------------------
def test_first_touch_win_loss_scratch():
    # entry close 100 at idx 0; +12% target = 112, -6% stop = 94
    win = pd.DataFrame({"open": [100, 105], "high": [100, 113], "low": [100, 104],
                        "close": [100, 112], "volume": [1, 1]})
    assert first_touch(0, win, PARAMS) is Label.WIN
    loss = pd.DataFrame({"open": [100, 96], "high": [100, 99], "low": [100, 93],
                         "close": [100, 94], "volume": [1, 1]})
    assert first_touch(0, loss, PARAMS) is Label.LOSS
    flat = pd.DataFrame({"open": [100] * 12, "high": [101] * 12, "low": [99] * 12,
                         "close": [100] * 12, "volume": [1] * 12})
    assert first_touch(0, flat, PARAMS) is Label.SCRATCH


def test_first_touch_stop_high_no_fill():
    # entry bar locked limit-up (zero range, up gap) → cannot buy (DESIGN.md §6.2)
    locked = pd.DataFrame({"open": [100, 130], "high": [100, 130], "low": [100, 130],
                           "close": [100, 130], "volume": [1, 1]})
    assert first_touch(1, locked, PARAMS) is Label.NO_FILL
