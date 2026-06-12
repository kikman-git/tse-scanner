"""Event-study cadence tests (the GO/NO-GO replay clock).

Weekly steps on margin **publication dates**; daily steps on **every trading day** — ignition is a
daily breakout, so the weekly cadence under-samples it. Both read margin point-in-time. These tests
exercise the clock plumbing (flat prices → 0 events on purpose); real-edge validation is the live
10y run.
"""

import re
from datetime import date

import pandas as pd
import pytest

from tse_scanner.backtest.event_study import run_event_study
from tse_scanner.config import PARAMS, Settings
from tse_scanner.data.store import Store

pytest.importorskip("duckdb")


def _seed(tmp_path) -> Store:
    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    mg = pd.DataFrame([{
        "code": "1301", "code5": "13010", "name": "T", "is_taishaku": True,
        "short_total": 100_000, "long_total": 10_000,
        "neg_short": 0, "sys_short": 100_000, "neg_long": 0, "sys_long": 10_000,
    }])
    s.upsert_weekly_margin(mg, as_of=date(2024, 1, 5), published=date(2024, 1, 9))
    dates = pd.bdate_range("2024-01-09", periods=15)
    s.upsert_prices(pd.DataFrame({
        "code": "1301", "date": dates,
        "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1000.0, "volume": 5_000,
    }))
    s.upsert_topix(pd.DataFrame({
        "date": dates, "open": 2000.0, "high": 2000.0, "low": 2000.0, "close": 2000.0,
    }))
    return s


def _date_count(note: str) -> int:
    m = re.search(r"(?:over|across) (\d+) (?:trading day|publication date)", note)
    assert m, f"no replay-date count in note: {note!r}"
    return int(m.group(1))


def test_cadence_invalid_raises(tmp_path):
    s = _seed(tmp_path)
    with pytest.raises(ValueError, match="cadence"):
        run_event_study(date(2024, 1, 9), date(2024, 1, 31), PARAMS, s, cadence="bogus")


def test_daily_replays_more_dates_than_weekly(tmp_path):
    s = _seed(tmp_path)
    start, end = date(2024, 1, 9), date(2024, 1, 31)
    weekly = run_event_study(start, end, PARAMS, s, cadence="weekly")
    daily = run_event_study(start, end, PARAMS, s, cadence="daily")

    assert "publication date" in weekly.note and "weekly cadence" in weekly.note
    assert "trading day" in daily.note and "daily cadence" in daily.note
    # one publication Tuesday in-window vs the whole trading calendar
    assert _date_count(weekly.note) == 1
    assert _date_count(daily.note) > _date_count(weekly.note)


def test_default_cadence_is_daily(tmp_path):
    # the GO/NO-GO defaults to the representative daily clock when cadence is unspecified
    s = _seed(tmp_path)
    res = run_event_study(date(2024, 1, 9), date(2024, 1, 31), PARAMS, s)
    assert "daily cadence" in res.note


# --- de-gated ladder + fuel cohort A/B ---------------------------------------------------------

def _coil_breakout_bars(dates) -> pd.DataFrame:
    """135 sessions that oscillate then flatten into a coil, a confirmed breakout bar, then a
    run to +12% (WIN). Satisfies every universe gate (price, ADV, n_sessions, staleness)."""
    rows: list[tuple[float, float, float, int]] = []  # (close, high, low, volume)
    for i in range(110):  # volatile era → BB-width history has spread
        c = 950.0 if i % 2 == 0 else 1050.0
        rows.append((c, c + 5, c - 5, 200_000))
    rows += [(1000.0, 1005.0, 995.0, 200_000)] * 25  # dead-flat coil → box = (1005, 995)
    # breakout: close > box high, 5× volume, wide bar (no sweep below 995 → BREAK, not SPRING)
    rows.append((1090.0, 1100.0, 1000.0, 1_000_000))
    # forward path to the +12% target (entry 1090 → target 1220.8) without touching the stop
    rows += [(1150.0, 1160.0, 1080.0, 200_000), (1230.0, 1240.0, 1140.0, 200_000),
             (1235.0, 1245.0, 1225.0, 200_000), (1238.0, 1248.0, 1230.0, 200_000),
             (1240.0, 1250.0, 1232.0, 200_000)]
    closes, highs, lows, vols = (list(x) for x in zip(*rows))
    return pd.DataFrame({"date": dates[:len(rows)], "open": closes, "high": highs,
                         "low": lows, "close": closes, "volume": vols})


def test_degated_ladder_and_fuel_cohorts(tmp_path):
    """Two identical breakouts, one short-crowded and one with no margin print: both must reach
    IGNITING (fuel no longer gates), and the study must separate them into the A/B cohorts."""
    import duckdb

    s = Store(Settings(data_dir=tmp_path, duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    dates = pd.bdate_range("2024-01-04", periods=141)  # breakout bar lands at index 135

    for code in ("1301", "6502"):
        bars = _coil_breakout_bars(dates)
        bars["code"] = code
        s.upsert_prices(bars)
    s.upsert_topix(pd.DataFrame({
        "date": dates, "open": 2000.0, "high": 2000.0, "low": 2000.0, "close": 2000.0}))
    s.upsert_listed_info(pd.DataFrame([
        {"code": "1301", "name": "FUELED", "is_domestic_equity": True, "is_taishaku": True},
        {"code": "6502", "name": "NOFUEL", "is_domestic_equity": True, "is_taishaku": True},
    ]))
    # margin print for 1301 only: DtC = 2M/200k = 10, 倍率 = 0.05 → fuel-gated squeeze candidate
    s.upsert_weekly_margin(pd.DataFrame([{
        "code": "1301", "code5": "13010", "name": "FUELED", "is_taishaku": True,
        "short_total": 2_000_000, "long_total": 100_000,
        "neg_short": 0, "sys_short": 2_000_000, "neg_long": 0, "sys_long": 100_000,
    }]), as_of=dates[130].date(), published=dates[132].date())

    res = run_event_study(dates[133].date(), dates[136].date(), PARAMS, s, cadence="daily")

    assert res.n_events == 2
    assert res.label_counts.get("win") == 2 and res.win_rate == 1.0
    assert res.expectancy_r > 0
    assert res.splits["fuel-gated (squeeze)"]["n"] == 1
    assert res.splits["no-fuel breakout"]["n"] == 1
    assert res.splits["dtc_pctile Q4 (≥.75)"]["n"] == 1   # 1301: only valid DtC → rank 1.0
    assert res.splits["no margin print"]["n"] == 1        # 6502: NaN pctile cohort
    assert res.splits["break"]["n"] == 2                  # lows never swept the box → no springs

    # per-event records persisted for offline cohort re-slicing
    assert res.events_path and (tmp_path / res.events_path.split("/")[-1]).exists()
    ev = duckdb.connect().execute(f"SELECT * FROM '{res.events_path}'").df()
    assert set(ev["code"]) == {"1301", "6502"}
    assert ev.loc[ev["code"] == "1301", "has_fuel"].item() is True
    assert ev.loc[ev["code"] == "6502", "has_fuel"].item() is False
