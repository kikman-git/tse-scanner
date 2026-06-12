"""Unit tests for the pure signal + label logic (no network; pandas only)."""

import numpy as np
import pandas as pd
import pytest

from tse_scanner.backtest.labels import Label, first_touch
from tse_scanner.config import PARAMS
from tse_scanner.signals import coil, fuel, ignition


# --- fuel ----------------------------------------------------
def test_days_to_cover_and_ratio():
    assert fuel.days_to_cover(100_000, 10_000) == 10.0          # 10 days to cover
    assert fuel.days_to_cover(100_000, 0) == float("inf")        # no volume → uncoverable
    assert fuel.credit_ratio(50, 100) == 0.5                     # 倍率 = 買/売 < 1 → short-heavy
    assert fuel.credit_ratio(100, 0) == float("inf")             # no shorts → not short-heavy


def test_fuel_gate():
    # trapped: top-decile DtC rank and 倍率 < 1 → has_fuel
    trapped = fuel.assess(short_balance=100_000, long_balance=10_000, adv=10_000, dtc_pctile=0.95)
    assert trapped.has_fuel and trapped.score > 0
    # mid-pack cross-sectionally → no fuel even though 倍率 < 1 and DtC is high in absolute terms
    midpack = fuel.assess(short_balance=100_000, long_balance=10_000, adv=10_000, dtc_pctile=0.50)
    assert not midpack.has_fuel
    assert trapped.score > midpack.score  # percentile is the primary score term
    # long-heavy (倍率 > 1) fails the gate even at the top of the cross-section
    longish = fuel.assess(short_balance=100_000, long_balance=200_000, adv=10_000, dtc_pctile=0.95)
    assert not longish.has_fuel
    # below the absolute floor (< 1 day of ADV short) → no fuel even if ranked top decile
    shallow = fuel.assess(short_balance=5_000, long_balance=1_000, adv=10_000, dtc_pctile=0.95)
    assert not shallow.has_fuel
    # no cross-section supplied (NaN pctile) → gate cannot pass
    blind = fuel.assess(short_balance=100_000, long_balance=10_000, adv=10_000)
    assert not blind.has_fuel


def test_dtc_percentile_ranks():
    s = pd.Series([0.5, 1.0, 2.0, 4.0, 8.0, float("inf"), float("nan"), 0.0])
    r = fuel.dtc_percentile_ranks(s)
    assert r.iloc[4] > r.iloc[1] > r.iloc[0]      # monotone in DtC
    assert r.iloc[5] == 1.0                        # uncoverable pins to the top
    assert r.iloc[6] != r.iloc[6]                  # no margin print → NaN, not zero
    assert r.iloc[7] != r.iloc[7]                  # no shorts → NaN (absence of evidence)
    valid = r.dropna()
    assert valid.max() <= 1.0 and valid.min() > 0.0


def test_short_balance_rising():
    rising = pd.DataFrame({"short_total": [100, 150, 200]})
    flat = pd.DataFrame({"short_total": [200, 150, 100]})
    assert fuel.short_balance_rising(rising, prints=3)
    assert not fuel.short_balance_rising(flat, prints=3)
    assert not fuel.short_balance_rising(rising.head(1), prints=3)  # too few prints → can't confirm


# --- coil ----------------------------------------------------
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


# --- ignition ------------------------------------------------
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


def test_sweep_reversal_detection():
    box_high, box_low = 101.0, 99.0
    f = _frame([100.0] * 40)
    # sweep bar: open 100, dives to 96 (below box low), closes 100.4 → wick 4.4/4.6 of range,
    # close at the top of the bar, still inside the box → SWEEP (early entry)
    f.loc[39, ["open", "high", "low", "close", "volume"]] = [100.0, 100.6, 96.0, 100.4, 1_500]
    assert ignition.sweep_reversal(f, box_low, PARAMS)
    assert ignition.detect(f, box_high, box_low, PARAMS) is ignition.Ignition.SWEEP
    st = ignition.assess(f, box_high, box_low, PARAMS)
    assert st.type is ignition.Ignition.SWEEP and st.quality > 0.5

    # closes back *below* the box low → breakdown, not a reversal
    g = _frame([100.0] * 40)
    g.loc[39, ["open", "high", "low", "close"]] = [100.0, 100.2, 96.0, 98.0]
    assert not ignition.sweep_reversal(g, box_low, PARAMS)
    # never trades below the box low → nothing swept
    h = _frame([100.0] * 40)
    h.loc[39, ["open", "high", "low", "close"]] = [100.0, 100.6, 99.5, 100.4]
    assert not ignition.sweep_reversal(h, box_low, PARAMS)
    # big red bar that dives and only limps back over the box low: close mid-range, thin wick
    # relative to the range → indecision, not rejection
    j = _frame([100.0] * 40)
    j.loc[39, ["open", "high", "low", "close"]] = [103.0, 104.0, 96.0, 99.5]
    assert not ignition.sweep_reversal(j, box_low, PARAMS)


def test_confirmed_break_outranks_sweep_same_bar():
    # one bar that both sweeps the low and confirms the break → classify by the confirmation
    # (BREAK: _swept_below only credits *prior* setup-window bars as a spring's sweep leg)
    box_high, box_low = 101.0, 99.0
    f = _frame([100.0] * 40)
    f.loc[39, ["open", "high", "low", "close", "volume"]] = [100.0, 108.0, 96.0, 107.0, 5_000]
    assert ignition.detect(f, box_high, box_low, PARAMS) is ignition.Ignition.BREAK


# --- first-touch labels ---------------------------------------
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
    # entry bar locked limit-up (zero range, up gap) → cannot buy
    locked = pd.DataFrame({"open": [100, 130], "high": [100, 130], "low": [100, 130],
                           "close": [100, 130], "volume": [1, 1]})
    assert first_touch(1, locked, PARAMS) is Label.NO_FILL


def test_first_touch_gap_fills_at_open():
    from tse_scanner.backtest.labels import first_touch_detail

    # overnight gap straight through the -6% stop (open 88): the fill is the open, not the stop
    gap_down = pd.DataFrame({"open": [100, 88], "high": [100, 90], "low": [100, 86],
                             "close": [100, 89], "volume": [1, 1]})
    ft = first_touch_detail(0, gap_down, PARAMS)
    assert ft.label is Label.LOSS
    assert ft.ret == pytest.approx(-0.12)  # realized loss = the gap, 2× the nominal stop distance
    # gap up through the +12% target (open 115) → windfall kept
    gap_up = pd.DataFrame({"open": [100, 115], "high": [100, 118], "low": [100, 114],
                           "close": [100, 116], "volume": [1, 1]})
    ft = first_touch_detail(0, gap_up, PARAMS)
    assert ft.label is Label.WIN
    assert ft.ret == pytest.approx(0.15)


def test_first_touch_structural_stop_override():
    from tse_scanner.backtest.labels import first_touch_detail

    # sweep-style barriers: stop 3% / target 6%; a -4% low would miss the default -6% stop
    bars = pd.DataFrame({"open": [100, 99], "high": [100, 100], "low": [100, 96.5],
                         "close": [100, 97], "volume": [1, 1]})
    ft = first_touch_detail(0, bars, PARAMS, target_pct=0.06, stop_pct=0.03)
    assert ft.label is Label.LOSS and ft.ret == -0.03
    assert first_touch(0, bars, PARAMS) is Label.SCRATCH  # default barriers untouched
