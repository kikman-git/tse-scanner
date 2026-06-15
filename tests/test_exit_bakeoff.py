"""Exit-model bake-off harness plumbing (hermetic tmp store).

Seeds two codes with known trajectories (one rises into the +target, one falls into the −stop),
emits a small events frame across two cohorts, and checks the harness: entries are reconstructed
from the signal date, ATR-at-entry is computed point-in-time, and the summary carries all three
exit models per cohort with the expected fixed-model signs. simulate_exit's own mechanics are
covered in test_exit_model.py — this is about the relabel/summarize wiring.
"""

import pandas as pd
import pytest

from tse_scanner.backtest.exit_bakeoff import relabel, summarize
from tse_scanner.config import PARAMS, Settings
from tse_scanner.data.store import Store

pytest.importorskip("duckdb")


def _seed(tmp_path) -> Store:
    s = Store(Settings(data_dir=tmp_path, duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    bdays = pd.bdate_range("2022-01-03", periods=80)

    def _frame(code, closes):
        c = pd.Series(closes)
        return pd.DataFrame({"code": code, "date": bdays, "open": c.values,
                             "high": (c * 1.01).values, "low": (c * 0.99).values,
                             "close": c.values, "volume": 500_000})

    up = [100.0 * (1.0 + 0.02) ** i for i in range(80)]    # +2%/bar → tags +12% target within horizon
    down = [100.0 * (1.0 - 0.02) ** i for i in range(80)]  # −2%/bar → tags −6% stop within horizon
    s.upsert_prices(_frame("1111", up))
    s.upsert_prices(_frame("2222", down))
    s.upsert_topix(pd.DataFrame({"date": bdays, "open": 2000.0, "high": 2000.0,
                                 "low": 2000.0, "close": 2000.0}))
    return s


def test_bakeoff_plumbing_and_fixed_signs(tmp_path):
    s = _seed(tmp_path)
    # entries at bar ~30 (ATR window is satisfied), one per cohort
    sig = pd.bdate_range("2022-01-03", periods=80)[30].date()
    events = pd.DataFrame([
        {"code": "1111", "date": sig, "cohort": "up"},
        {"code": "2222", "date": sig, "cohort": "down"},
    ])

    long = relabel(events, s, PARAMS)
    # 2 events × 3 exit models = 6 rows, ATR resolved (not the fixed fallback) for the ATR kinds
    assert len(long) == 6
    assert set(long["kind"]) == {"fixed", "atr", "atr_trail"}
    assert long["R"].notna().all()

    summ = summarize(long, s)
    for cohort in ("up", "down"):
        sub = summ[summ["cohort"] == cohort]
        assert set(sub["kind"]) == {"fixed", "atr", "atr_trail"}

    up_fixed = summ[(summ["cohort"] == "up") & (summ["kind"] == "fixed")].iloc[0]
    down_fixed = summ[(summ["cohort"] == "down") & (summ["kind"] == "fixed")].iloc[0]
    assert up_fixed["win_rate"] == 1.0 and up_fixed["expectancy_R"] > 0      # rode into +target
    assert down_fixed["win_rate"] == 0.0 and down_fixed["expectancy_R"] < 0  # hit −stop
    # the ATR stop is volatility-scaled, so its per-trade risk differs from the flat 6%
    atr_risk = long[(long["cohort"] == "up") & (long["kind"] == "atr")].iloc[0]["risk_frac"]
    assert atr_risk != pytest.approx(PARAMS.stop_pct)


def test_entry_offset_shifts_the_entry_bar(tmp_path):
    s = _seed(tmp_path)
    sig = pd.bdate_range("2022-01-03", periods=80)[30].date()
    events = pd.DataFrame([{"code": "1111", "date": sig, "cohort": "up"}])
    base = relabel(events, s, PARAMS, entry_offset=0)
    nxt = relabel(events, s, PARAMS, entry_offset=1)
    # next-session entry on a +1%/bar riser → a higher entry price than the on-signal bar
    assert nxt[nxt["kind"] == "fixed"].iloc[0]["risk_frac"] == pytest.approx(PARAMS.stop_pct)
    assert len(base) == len(nxt) == 3
