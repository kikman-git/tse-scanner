"""Scan-loop integration: the exit map rides every scan row and never touches stage/score."""

from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from tse_scanner.config import PARAMS, Settings
from tse_scanner.data.store import Store
from tse_scanner.scan import run_scan

pytest.importorskip("duckdb")


def _seed(tmp_path) -> tuple[Store, date]:
    """One liquid 貸借 name on a flat tape: closes 1000, boxed 990–1010, 140 sessions."""
    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    s.upsert_listed_info(pd.DataFrame([{
        "code": "1301", "name": "テスト", "segment": "Prime", "segment_ja": "プライム",
        "sector33": "水産・農林業", "is_domestic_equity": True,
    }]), taishaku_codes={"1301"})
    dates = pd.bdate_range("2024-01-02", periods=140)
    s.upsert_prices(pd.DataFrame({
        "code": "1301", "date": dates,
        "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1000.0,
        "volume": 200_000,  # ×¥1,000 = ¥200M turnover — clears the ADV floor
    }))
    s.upsert_weekly_margin(pd.DataFrame([{
        "code": "1301", "code5": "13010", "short_total": 400_000, "long_total": 100_000,
        "is_taishaku": True,
    }]), as_of=date(2024, 6, 28), published=date(2024, 7, 2))
    return s, dates[-1].date()


def test_scan_attaches_exit_map_and_persists_it(tmp_path):
    store, as_of = _seed(tmp_path)
    res = run_scan(as_of=as_of, store=store, write=True)
    assert len(res.table) == 1
    row = res.table.iloc[0]

    # box 990–1010 (height 20) → T1 = 1030, T2 = 1050; neither is near a 節目 (>2% from 1000)
    assert row["box_high"] == pytest.approx(1010.0) and row["box_low"] == pytest.approx(990.0)
    assert row["t1"] == pytest.approx(1030.0)
    assert row["t2"] == pytest.approx(1050.0)
    assert row["rr_t1"] == pytest.approx(1.0)  # (1030−1010) / (1010−990)
    # the box high IS the 52w high on a flat tape → breaking it = blue sky
    assert row["high_52w"] == pytest.approx(1010.0)
    assert bool(row["blue_sky"])
    assert row["atr"] == pytest.approx(20.0)   # constant 20-yen true range

    # the write round-trips through scan_results (schema + migrations agree with the row dict)
    persisted = store.read_scan().iloc[0]
    for col in ("t1", "t2", "atr", "rr_t1", "next_resistance", "high_52w", "blue_sky"):
        assert col in store.read_scan().columns
    assert persisted["t1"] == pytest.approx(1030.0)


def test_exit_map_never_moves_stage_or_score(tmp_path):
    """Red line: the exit map is a display overlay — wildly different exit params must produce
    byte-identical stage/score (only the exit columns may differ)."""
    store, as_of = _seed(tmp_path)
    base = run_scan(as_of=as_of, store=store, write=False).table
    bent = run_scan(
        as_of=as_of, store=store, write=False,
        params=replace(PARAMS, exit_box_mult_t1=5.0, exit_box_mult_t2=9.0,
                       exit_round_snap_pct=0.5),
    ).table
    assert base.iloc[0]["stage"] == bent.iloc[0]["stage"]
    assert base.iloc[0]["score"] == bent.iloc[0]["score"]
    assert bent.iloc[0]["t1"] != base.iloc[0]["t1"]  # the overlay itself did move
