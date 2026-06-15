"""Revision event-study integration test (hermetic tmp store).

Seeds one name with a chained disclosure tape (FY + 2Q docs guiding the next FY at 100, beaten
every year) plus one up-revision, and verifies both arms fire: the REACTIVE arm classifies the
修正 as up and enters after it, the ANTICIPATORY arm fires ahead of statement anniversaries once
enough history exists. Asserts the cohort plumbing the A/B is read from.
"""

from datetime import date

import pandas as pd
import pytest

from tse_scanner.backtest.revision_study import run_revision_study
from tse_scanner.config import PARAMS, Settings
from tse_scanner.data.store import Store

pytest.importorskip("duckdb")

_STMT = "FinancialStatements_Consolidated_JP"


def _row(disc, doc, per, fy, *, op=None, f_op=None, nxf_op=None, nxt_fy=None):
    return {
        "code": "1301", "code5": "13010", "disc_date": date.fromisoformat(disc),
        "disc_time": "15:00:00", "disc_no": f"{disc}-{per}-{doc[:2]}", "doc_type": doc,
        "per_type": per, "fy_end": date.fromisoformat(fy),
        "nxt_fy_end": date.fromisoformat(nxt_fy) if nxt_fy else None,
        "op": op, "f_op": f_op, "nxf_op": nxf_op,
    }


def _seed(tmp_path) -> Store:
    s = Store(Settings(data_dir=tmp_path, duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    rows = [
        _row("2022-05-10", "FY" + _STMT, "FY", "2022-03-31", op=130, nxf_op=100, nxt_fy="2023-03-31"),
        _row("2022-11-01", "2Q" + _STMT, "2Q", "2023-03-31", op=65, f_op=100),
        _row("2023-05-10", "FY" + _STMT, "FY", "2023-03-31", op=125, nxf_op=100, nxt_fy="2024-03-31"),
        _row("2023-11-01", "2Q" + _STMT, "2Q", "2024-03-31", op=70, f_op=100),
        _row("2024-05-10", "FY" + _STMT, "FY", "2024-03-31", op=140, nxf_op=100, nxt_fy="2025-03-31"),
        _row("2024-11-01", "2Q" + _STMT, "2Q", "2025-03-31", op=85, f_op=100),
        # the up-revision: FY2025 guidance hiked 100 → 130 (a +30% 上方修正)
        _row("2024-08-01", "EarnForecastRevision", "FY", "2025-03-31", f_op=130),
    ]
    assert s.upsert_fins_summary(pd.DataFrame(rows)) == len(rows)

    bdays = pd.bdate_range("2021-01-04", "2025-12-31")
    close = pd.Series(1000.0 * (1.0 + 0.0008) ** pd.RangeIndex(len(bdays)))  # gentle uptrend
    s.upsert_prices(pd.DataFrame({
        "code": "1301", "date": bdays, "open": close.values,
        "high": (close * 1.02).values, "low": (close * 0.99).values,
        "close": close.values, "volume": 300_000}))
    s.upsert_topix(pd.DataFrame({
        "date": bdays, "open": 2000.0, "high": 2000.0, "low": 2000.0, "close": 2000.0}))
    return s


def test_both_arms_fire_and_classify(tmp_path):
    import duckdb

    s = _seed(tmp_path)
    res = run_revision_study(date(2022, 1, 1), date(2025, 12, 31), PARAMS, s)

    assert res.n_events >= 2
    assert res.splits["reactive 上方修正 drift (up)"]["n"] >= 1   # the +30% hike, classified up
    assert res.splits["anticipatory (all windows)"]["n"] >= 1    # fired ahead of anniversaries

    ev = duckdb.connect().execute(f"SELECT * FROM '{res.events_path}'").df()
    assert "anticipatory" in set(ev["cohort"])
    up = ev[ev["cohort"] == "reactive 上方修正 (up)"]
    assert len(up) >= 1 and up.iloc[0]["rev_mag"] == pytest.approx(0.30)
    # `date` is the signal (the after-close 修正 print); the entry bar is the strictly-next session
    # by construction (_label strict=True), so the trade can't see the print's own day.
    assert pd.to_datetime(up.iloc[0]["date"]) == pd.Timestamp("2024-08-01")
