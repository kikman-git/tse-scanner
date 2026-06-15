"""Exit-model variants (labels.simulate_exit): fixed / atr / atr_trail.

The diagnosis the exit model exists to fix: a fixed −6% stop sits inside small/mid-cap noise, so
the median backtest trade stops out at −1R before the thesis plays out. These tests pin the three
behaviours that matter — an ATR stop survives noise a fixed stop dies to, a trailing stop rides a
青天井 runner past the +12% cap, and both still honour the sacred rails (gap-aware fills, stop-high
no-fill on the entry bar).
"""

import pandas as pd
import pytest

from tse_scanner.backtest.labels import (
    ExitModel, Label, first_touch_detail, simulate_exit,
)
from tse_scanner.config import PARAMS


def _bars(rows: list[tuple]) -> pd.DataFrame:
    """rows = [(open, high, low, close), …] → OHLC frame on a plain RangeIndex."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


def _fixed() -> ExitModel:
    return ExitModel(kind="fixed")


def _atr() -> ExitModel:
    return ExitModel(kind="atr")


def _trail() -> ExitModel:
    return ExitModel(kind="atr_trail")


def test_fixed_model_matches_legacy_first_touch():
    # entry 100, bar 1 tags the +12% target → both paths agree on WIN and the realized return.
    bars = _bars([(100, 101, 99, 100), (110, 113, 108, 112)])
    legacy = first_touch_detail(0, bars, PARAMS)
    new = simulate_exit(0, bars, PARAMS, model=_fixed())
    assert legacy.label == new.label == Label.WIN
    assert new.ret == pytest.approx(legacy.ret)
    assert new.risk_frac == pytest.approx(PARAMS.stop_pct)


def test_atr_stop_survives_the_noise_that_taps_the_fixed_stop():
    # ATR=4 (4% of price): fixed stop −6% (=94) is tapped by a 93 low; the 2.5×ATR stop (=90) is
    # not, so the ATR model holds and then catches the +5×ATR target (=120). This is the whole fix.
    bars = _bars([
        (100, 101, 99, 100),   # entry close 100
        (98, 99, 93, 96),      # dips to 93: fixed stop hit, ATR stop (90) intact
        (110, 121, 109, 119),  # runs to 121: ATR target (120) hit
    ])
    fixed = simulate_exit(0, bars, PARAMS, model=_fixed())
    atr = simulate_exit(0, bars, PARAMS, model=_atr(), atr_at_entry=4.0)
    assert fixed.label == Label.LOSS and fixed.ret == pytest.approx(-PARAMS.stop_pct)
    assert atr.label == Label.WIN and atr.ret == pytest.approx(0.20)
    assert atr.risk_frac == pytest.approx(0.10)  # (100-90)/100


def test_atr_trail_rides_a_runner_past_the_fixed_cap():
    # A 青天井 grind to ~150 then a pullback. Fixed caps the win at +12%; the chandelier trail
    # (run-high − 3×ATR) rides far past it, exiting on the reversal well above the +12% cap.
    bars = _bars([
        (100, 101, 99, 100),    # entry 100, ATR 4 → initial stop 90, trail = run_high − 12
        (101, 112, 100, 111),   # run_high 112 → stop 100
        (112, 130, 111, 129),   # run_high 130 → stop 118
        (129, 150, 128, 148),   # run_high 150 → stop 138
        (147, 148, 135, 137),   # low 135 ≤ 138 → trail exit at 138 (+38%)
    ])
    fixed = simulate_exit(0, bars, PARAMS, model=_fixed())
    trail = simulate_exit(0, bars, PARAMS, model=_trail(), atr_at_entry=4.0)
    assert fixed.label == Label.WIN and fixed.ret == pytest.approx(PARAMS.target_pct)  # +12% cap
    assert trail.label == Label.WIN
    assert trail.ret == pytest.approx(0.38)
    assert trail.ret > fixed.ret  # the point: the runner is ridden, not capped


def test_atr_trail_initial_stop_is_a_clean_one_R_loss():
    # Immediate drop into the initial ATR stop (90) → −1R, classified LOSS.
    bars = _bars([(100, 101, 99, 100), (95, 96, 89, 90)])
    trail = simulate_exit(0, bars, PARAMS, model=_trail(), atr_at_entry=4.0)
    assert trail.label == Label.LOSS
    assert trail.ret == pytest.approx(-0.10)
    assert trail.ret / trail.risk_frac == pytest.approx(-1.0)  # exactly −1R


def test_gap_through_atr_stop_fills_at_open_not_the_barrier():
    # Sacred: a bar that *opens* below the stop fills at the open, losing more than the −1R stop.
    bars = _bars([(100, 101, 99, 100), (88, 90, 85, 86)])  # opens at 88, below the 90 ATR stop
    atr = simulate_exit(0, bars, PARAMS, model=_atr(), atr_at_entry=4.0)
    assert atr.label == Label.LOSS
    assert atr.ret == pytest.approx(-0.12)  # filled at the 88 open, worse than the −10% stop
    assert atr.ret / atr.risk_frac < -1.0


def test_locked_limit_up_entry_is_no_fill_under_every_model():
    # Sacred: a ストップ高-locked entry bar (zero range, up gap) cannot be bought.
    bars = _bars([(100, 101, 99, 100), (110, 110, 110, 110), (112, 115, 111, 114)])
    for model in (_fixed(), _atr(), _trail()):
        ft = simulate_exit(1, bars, PARAMS, model=model, atr_at_entry=4.0)
        assert ft.label == Label.NO_FILL


def test_atr_models_fall_back_to_fixed_when_atr_missing():
    # Short history → no ATR → the ATR models must not raise; they degrade to the fixed barriers.
    bars = _bars([(100, 101, 99, 100), (110, 113, 108, 112)])
    fixed = simulate_exit(0, bars, PARAMS, model=_fixed())
    for kind in ("atr", "atr_trail"):
        ft = simulate_exit(0, bars, PARAMS, model=ExitModel(kind=kind), atr_at_entry=None)
        assert ft.label == fixed.label and ft.ret == pytest.approx(fixed.ret)
        assert ft.risk_frac == pytest.approx(PARAMS.stop_pct)
