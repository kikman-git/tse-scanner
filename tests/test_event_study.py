"""Event-study cadence tests (the GO/NO-GO replay clock — DESIGN.md §6).

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
