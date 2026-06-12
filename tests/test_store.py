"""Store tests — the publication-date point-in-time read is the project's load-bearing invariant.

Friday's margin is published the following Tue–Wed; a read as-of a date strictly before the
publication date must NOT see that print. These tests need DuckDB (installed).
"""

import subprocess
import sys
from datetime import date

import pandas as pd
import pytest

from tse_scanner.config import Settings
from tse_scanner.data.store import Store

duckdb = pytest.importorskip("duckdb")


def _store(tmp_path) -> Store:
    return Store(Settings(duckdb_path=tmp_path / "t.duckdb"))


def _margin_row(code="1301", short=100_000, long=10_000):
    return pd.DataFrame([{
        "code": code, "code5": code + "0", "name": "TEST", "is_taishaku": True,
        "short_total": short, "long_total": long,
        "neg_short": 0, "sys_short": short, "neg_long": 0, "sys_long": long,
    }])


def test_margin_publication_date_point_in_time(tmp_path):
    s = _store(tmp_path)
    s.init_schema()
    # older print: as-of 2026-05-29, published 2026-06-02
    s.upsert_weekly_margin(_margin_row(short=80_000), as_of=date(2026, 5, 29), published=date(2026, 6, 2))
    # newer print: as-of 2026-06-05, published 2026-06-09
    s.upsert_weekly_margin(_margin_row(short=120_000), as_of=date(2026, 6, 5), published=date(2026, 6, 9))

    # On 2026-06-08 the newer print is NOT public yet → must see the older (80k) print.
    before = s.margin_asof("1301", date(2026, 6, 8))
    assert int(before.iloc[0]["short_total"]) == 80_000
    # On 2026-06-10 the newer print IS public → see 120k.
    after = s.margin_asof("1301", date(2026, 6, 10))
    assert int(after.iloc[0]["short_total"]) == 120_000


def test_collapses_share_classes_to_common(tmp_path):
    s = _store(tmp_path)
    s.init_schema()
    # common (25930) + preferred (25935) both truncate to code 2593 — keep the common.
    df = pd.DataFrame([
        {"code": "2593", "code5": "25935", "name": "PFD", "is_taishaku": True,
         "short_total": 1, "long_total": 1, "neg_short": 0, "sys_short": 1, "neg_long": 0, "sys_long": 1},
        {"code": "2593", "code5": "25930", "name": "COMMON", "is_taishaku": True,
         "short_total": 999, "long_total": 1, "neg_short": 0, "sys_short": 999, "neg_long": 0, "sys_long": 1},
    ])
    s.upsert_weekly_margin(df, as_of=date(2026, 6, 5), published=date(2026, 6, 9))
    got = s.margin_asof("2593", date(2026, 6, 10))
    assert len(got) == 1 and int(got.iloc[0]["short_total"]) == 999  # common stock kept


def test_margin_trends_matches_per_code_history(tmp_path):
    """margin_trends batches margin_history for the scan loop (was a per-code query — the scan's
    bottleneck). It must return identical rows/order per code: publication-date filtered,
    oldest→newest, capped to `prints`."""
    s = _store(tmp_path)
    s.init_schema()
    weeks = [  # (as_of Fri, published Tue, 信用売残)
        (date(2026, 5, 15), date(2026, 5, 19), 60_000),
        (date(2026, 5, 22), date(2026, 5, 26), 80_000),
        (date(2026, 5, 29), date(2026, 6, 2), 100_000),
        (date(2026, 6, 5), date(2026, 6, 9), 120_000),
    ]
    for code in ("1301", "1305"):
        for as_of, pub, short in weeks:
            s.upsert_weekly_margin(_margin_row(code=code, short=short), as_of=as_of, published=pub)

    trends = s.margin_trends(["1301", "1305"], date(2026, 6, 10), prints=3)
    assert set(trends) == {"1301", "1305"}
    for code in ("1301", "1305"):
        batched = trends[code].reset_index(drop=True)
        per_code = s.margin_history(code, date(2026, 6, 10), prints=3).reset_index(drop=True)
        pd.testing.assert_frame_equal(batched, per_code)
        assert list(batched["short_total"]) == [80_000, 100_000, 120_000]  # last 3, oldest→newest

    # point-in-time: as-of 2026-06-08 the 06-05 print (pub 06-09) is not public yet
    pit = s.margin_trends(["1301"], date(2026, 6, 8), prints=3)["1301"]
    assert list(pit["short_total"]) == [60_000, 80_000, 100_000]
    assert s.margin_trends([], date(2026, 6, 10)) == {}  # empty codes → empty dict, no query


def test_prices_roundtrip_and_metrics(tmp_path):
    s = _store(tmp_path)
    dates = pd.bdate_range("2026-01-01", periods=30)
    px = pd.DataFrame({
        "code": "1301", "date": dates,
        "open": 1000.0, "high": 1010.0, "low": 990.0, "close": 1000.0, "volume": 5_000,
    })
    assert s.upsert_prices(px) == 30
    # idempotent: re-upsert same rows → still 30 in store, not 60
    s.upsert_prices(px)
    out = s.prices("1301")
    assert len(out) == 30 and "turnover" in out.columns

    pm = s.price_metrics().set_index("code")
    assert pm.loc["1301", "n_sessions"] == 30
    assert pm.loc["1301", "last_close"] == 1000.0
    assert pm.loc["1301", "adv_yen"] == 1000.0 * 5_000  # close × volume


def test_connect_retries_through_transient_lock(tmp_path):
    """A second process briefly holding the rw lock must not surface an IOException —
    the API reads and the research CLI collide exactly like this in normal use."""
    s = _store(tmp_path)
    s.init_schema()
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import duckdb, time; con = duckdb.connect({str(s.path)!r}); "
         "print('locked', flush=True); time.sleep(1.5)"],
        stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "locked"
    try:
        with pytest.raises(duckdb.IOException, match="lock"):
            s.connect(retries=1)  # no retries → the collision surfaces
        with s.connect() as con:  # default retries → waits out the holder
            assert con.execute("SELECT 1").fetchone()[0] == 1
    finally:
        holder.wait(timeout=10)
