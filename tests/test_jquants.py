"""J-Quants V2 client tests.

The mappers are pinned against *real* response rows captured from the live API on 2026-06-11,
and `derive_published_date` is pinned against the free-path PDF overlap week (2026-06-05 →
published 2026-06-09) plus the holiday shapes a hand-rolled rule would botch (Golden Week,
Happy-Monday, the exchange's year-end closure).
"""

from datetime import date

import pandas as pd
import pytest

from tse_scanner.config import Params, Settings
from tse_scanner.data import jquants as jq

duckdb = pytest.importorskip("duckdb")

# --- real rows captured from the live V2 API (probe, 2026-06-11) --------------------------------
MASTER_ROW = {"Date": "2026-06-11", "Code": "97550", "CoName": "応用地質", "CoNameEn": "OYO",
              "S17": "10", "S17Nm": "情報通信・サービスその他", "S33": "9050", "S33Nm": "サービス業",
              "ScaleCat": "TOPIX Small 2", "Mkt": "0111", "MktNm": "プライム", "Mrgn": "2",
              "MrgnNm": "貸借", "ProdCat": "011"}
MARGIN_ROW = {"Date": "2026-06-05", "Code": "13010", "ShrtVol": 2800.0, "LongVol": 132400.0,
              "ShrtNegVol": 0.0, "LongNegVol": 42100.0, "ShrtStdVol": 2800.0,
              "LongStdVol": 90300.0, "IssType": "2"}
BAR_ROW = {"Date": "2026-06-01", "Code": "97550", "O": 2862.0, "H": 2862.0, "L": 2817.0,
           "C": 2820.0, "UL": "0", "LL": "0", "Vo": 84400.0, "Va": 238989000.0, "AdjFactor": 1.0,
           "AdjO": 2862.0, "AdjH": 2862.0, "AdjL": 2817.0, "AdjC": 2820.0, "AdjVo": 84400.0}
TOPIX_ROW = {"Date": "2026-06-01", "Code": "0000", "O": 3946.07, "H": 3971.78, "L": 3929.19,
             "C": 3940.7}


# --- publication-date derivation ------------------------------------------------------------
def test_published_date_matches_free_pdf_overlap_week():
    # The week stored from the free PDF: as-of Fri 2026-06-05, header said published 2026-06-09.
    assert jq.derive_published_date(date(2026, 6, 5)) == date(2026, 6, 9)


def test_published_date_plain_week():
    assert jq.derive_published_date(date(2026, 5, 29)) == date(2026, 6, 2)  # Fri → Tue


def test_published_date_slips_over_happy_monday():
    # 2025-07-21 is 海の日 (Mon): Fri 7/18 → Tue 7/22 is the 1st b-day, published Wed 7/23.
    assert jq.derive_published_date(date(2025, 7, 18)) == date(2025, 7, 23)


def test_published_date_golden_week():
    # 2025: 5/3 Sat 憲法記念日, 5/5 Mon こどもの日, 5/6 Tue 振替休日 → Fri 5/2 publishes 5/8.
    assert jq.derive_published_date(date(2025, 5, 2)) == date(2025, 5, 8)


def test_published_date_yearend_exchange_closure():
    # TSE closes 12/31–1/3 even when not national holidays: Fri 2024-12-27 → Mon 12/30 is the
    # 1st b-day, then 1/6 Mon 2025 (12/31–1/3 closed, 1/4–1/5 weekend) → published 2025-01-06.
    assert jq.derive_published_date(date(2024, 12, 27)) == date(2025, 1, 6)


# --- mappers --------------------------------------------------------------------------------------
def test_rows_to_master_maps_and_filters():
    etf = dict(MASTER_ROW, Code="13050", CoName="ETF", MktNm="その他", Mrgn="1", ProdCat="014")
    pfd = dict(MASTER_ROW, Code="97555", CoName="優先株")  # preferred share class → dropped
    df = jq.rows_to_master([MASTER_ROW, etf, pfd])
    assert list(df["code"]) == ["9755", "1305"]
    r = df.iloc[0]
    assert r["name"] == "応用地質" and r["segment"] == "prime" and r["segment_ja"] == "プライム"
    assert r["sector33"] == "サービス業" and bool(r["is_taishaku"]) and bool(r["is_domestic_equity"])
    e = df.iloc[1]
    assert e["segment"] == "other" and not bool(e["is_domestic_equity"]) and not bool(e["is_taishaku"])


def test_rows_to_margin_matches_free_pdf_columns():
    # Same week/name as the free PDF docstring sample (極洋 2026-06-05) — fields must agree 1:1.
    df = jq.rows_to_margin([MARGIN_ROW])
    r = df.iloc[0]
    assert r["code"] == "1301" and r["code5"] == "13010"
    assert r["as_of_date"] == date(2026, 6, 5)
    assert int(r["short_total"]) == 2800 and int(r["long_total"]) == 132400
    assert int(r["neg_short"]) == 0 and int(r["sys_short"]) == 2800       # 一般売 / 制度売
    assert int(r["neg_long"]) == 42100 and int(r["sys_long"]) == 90300    # 一般買 / 制度買
    assert bool(r["is_taishaku"])  # IssType 2 = 貸借


def test_rows_to_bars_uses_adjusted_fields_and_va_turnover():
    no_trade = dict(BAR_ROW, Date="2026-06-02", AdjC=None, AdjO=None, AdjH=None, AdjL=None,
                    AdjVo=None, Va=None)
    halted = dict(BAR_ROW, Date="2026-06-03", AdjVo=0.0, Va=None)
    pfd = dict(BAR_ROW, Code="97555")
    df = jq.rows_to_bars([BAR_ROW, no_trade, halted, pfd])
    assert list(df["code"]) == ["9755", "9755"]          # preferred + no-trade rows dropped
    r = df.iloc[0]
    assert r["date"] == date(2026, 6, 1) and r["close"] == 2820.0 and r["volume"] == 84400
    assert r["turnover"] == 238989000.0                  # actual ¥ traded (Va), not close×volume
    assert df.iloc[1]["turnover"] == 0.0                 # Va missing → close × 0 volume


def test_rows_to_bars_keeps_alphanumeric_common_codes():
    row = dict(BAR_ROW, Code="130A0")
    df = jq.rows_to_bars([row])
    assert list(df["code"]) == ["130A"]


def test_rows_to_topix():
    df = jq.rows_to_topix([TOPIX_ROW])
    assert df.iloc[0]["date"] == date(2026, 6, 1) and df.iloc[0]["close"] == 3940.7


# --- client transport: pagination + backoff ------------------------------------------------------
class _Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        return self._payload


def test_get_loops_pagination_and_retries_429(monkeypatch):
    import httpx

    calls = []
    responses = [
        _Resp(429, headers={"Retry-After": "0"}),
        _Resp(200, {"data": [{"a": 1}], "pagination_key": "k2"}),
        _Resp(200, {"data": [{"a": 2}]}),
    ]

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(dict(params))
        return responses[len(calls) - 1]

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(jq.time, "sleep", lambda s: None)
    out = jq.JQuantsClient(api_key="k").get("/markets/margin-interest", {"date": date(2026, 6, 5)})
    assert out == [{"a": 1}, {"a": 2}]
    assert calls[0] == {"date": "2026-06-05"}            # date objects serialized
    assert calls[2]["pagination_key"] == "k2"            # second page requested


def test_get_raises_without_key():
    with pytest.raises(RuntimeError, match="JQUANTS_API_KEY"):
        jq.JQuantsClient().get("/x", {})


# --- store round-trip + staleness gate ------------------------------------------------------------
def test_margin_week_roundtrip_point_in_time(tmp_path):
    """API week → mapper → store → read with the *derived* publication date."""
    from tse_scanner.data.store import Store

    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    week = jq.rows_to_margin([MARGIN_ROW])
    as_of = week["as_of_date"].iloc[0]
    s.upsert_weekly_margin(week, as_of=as_of, published=jq.derive_published_date(as_of))
    assert s.margin_asof("1301", date(2026, 6, 8)).empty          # Mon: not public yet
    after = s.margin_asof("1301", date(2026, 6, 9))               # Tue: published
    assert int(after.iloc[0]["short_total"]) == 2800


def test_universe_drops_stale_priced_names(tmp_path):
    """Delisting guard: a name whose last bar is >N days before as-of leaves the universe."""
    from tse_scanner.data.store import Store
    from tse_scanner.data.universe import build_universe

    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    as_of = date(2020, 6, 12)
    bars = []
    for code, last in (("1111", as_of), ("2222", date(2020, 4, 30))):  # 2222 delisted weeks ago
        days = pd.bdate_range(end=last, periods=130)
        for d in days:
            bars.append({"code": code, "date": d.date(), "open": 1000, "high": 1010,
                         "low": 990, "close": 1000, "volume": 200_000})
    s.upsert_prices(pd.DataFrame(bars))
    s.upsert_listed_info(pd.DataFrame([
        {"code": "1111", "name": "LIVE", "segment": "prime", "is_domestic_equity": True},
        {"code": "2222", "name": "DEAD", "segment": "prime", "is_domestic_equity": True},
    ]), taishaku_codes={"1111", "2222"})
    mg = pd.DataFrame([
        {"code": c, "code5": c + "0", "name": n, "is_taishaku": True,
         "short_total": 1_000_000, "long_total": 100_000}
        for c, n in (("1111", "LIVE"), ("2222", "DEAD"))
    ])
    s.upsert_weekly_margin(mg, as_of=date(2020, 6, 5), published=date(2020, 6, 9))

    uni = build_universe(as_of, Params(), s)
    assert list(uni["code"]) == ["1111"]


def test_sync_skips_topix_when_store_current(tmp_path):
    """Store already holds today's TOPIX bar — sync must not request the inverted
    from>to range (J-Quants 400s it and the whole rescan dies)."""
    from datetime import timedelta

    from tse_scanner.data.jquants_sync import sync
    from tse_scanner.data.store import Store

    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    s.upsert_topix(pd.DataFrame([{"date": date.today(), "open": 1.0, "high": 1.0,
                                  "low": 1.0, "close": 1.0}]))

    topix_calls: list[tuple] = []

    class _Client:
        def margin_asof_dates(self):
            return [date.today() - timedelta(days=7)]

        def listed_master(self, date=None):
            return pd.DataFrame()

        def weekly_margin_interest(self, date=None):
            return pd.DataFrame()

        def topix_bars(self, date_from=None, date_to=None):
            topix_calls.append((date_from, date_to))
            return pd.DataFrame()

    out = sync(s, _Client(), prices=False, progress=lambda _m: None)
    assert topix_calls == []
    assert out["topix_rows"] == 0
