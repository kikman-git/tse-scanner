"""Revision-setup tests: /fins/summary converter, store point-in-time reads, and the
signals/revision feature logic (sandbagger / progress anomaly / lowball / announcement window).

Fixtures mirror the live V2 field shapes probed 2026-06-12 (codes 72030/91040): yen amounts as
strings, empty string = not disclosed, DiscTime format drift, EarnForecastRevision rows carrying
only the revised forecast fields.
"""

from datetime import date

import pandas as pd
import pytest

from tse_scanner.config import PARAMS, Settings
from tse_scanner.data.jquants import rows_to_fins_summary
from tse_scanner.signals import revision

pytest.importorskip("duckdb")


# --- converter ---------------------------------------------------------------------------------

def _api_row(**over) -> dict:
    base = {
        "DiscDate": "2024-05-10", "DiscTime": "15:00:00", "Code": "13010",
        "DiscNo": "20240510000001", "DocType": "FYFinancialStatements_Consolidated_JP",
        "CurPerType": "FY", "CurPerSt": "2023-04-01", "CurPerEn": "2024-03-31",
        "CurFYSt": "2023-04-01", "CurFYEn": "2024-03-31",
        "NxtFYSt": "2024-04-01", "NxtFYEn": "2025-03-31",
        "Sales": "100000000000", "OP": "10000000000", "OdP": "9500000000",
        "NP": "7000000000", "EPS": "120.5",
        "FSales": "", "FOP": "", "FOdP": "", "FNP": "", "FEPS": "",
        "FSales2Q": "", "FOP2Q": "", "FOdP2Q": "", "FNP2Q": "", "FEPS2Q": "",
        "NxFSales": "110000000000", "NxFOP": "9000000000", "NxFOdP": "8500000000",
        "NxFNp": "6500000000", "NxFEPS": "111.0",
    }
    base.update(over)
    return base


def test_rows_to_fins_summary_maps_types_and_codes():
    rows = [
        _api_row(),
        _api_row(DiscDate="2024-11-01", DiscTime="11:30", DiscNo="20241101000002",
                 DocType="EarnForecastRevision", CurPerType="FY",
                 CurPerEn="2025-03-31", CurFYEn="2025-03-31", NxtFYSt="", NxtFYEn="",
                 Sales="", OP="", OdP="", NP="", EPS="",
                 FOP="12000000000", FOdP="11000000000",
                 NxFSales="", NxFOP="", NxFOdP="", NxFNp="", NxFEPS=""),
    ]
    df = rows_to_fins_summary(rows)
    assert list(df["code"]) == ["1301", "1301"]
    fy = df.iloc[0]
    assert fy["op"] == 10_000_000_000 and fy["eps"] == 120.5
    assert fy["nxf_op"] == 9_000_000_000          # next-FY guidance kept
    assert fy["nxt_fy_end"] == date(2025, 3, 31)  # …and joinable to the FY it guides
    assert pd.isna(fy["f_op"])                     # empty string → NA, not 0
    rev = df.iloc[1]
    assert rev["doc_type"] == "EarnForecastRevision"
    assert pd.isna(rev["op"]) and rev["f_op"] == 12_000_000_000
    assert rev["disc_time"] == "11:30"             # verbatim, format drift preserved


def test_rows_to_fins_summary_empty():
    assert rows_to_fins_summary([]).empty


# --- store point-in-time reads ------------------------------------------------------------------

def test_fins_history_strict_excludes_same_day(tmp_path):
    from tse_scanner.data.store import Store

    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    df = rows_to_fins_summary([
        _api_row(DiscDate="2024-05-10", DiscNo="A1"),
        _api_row(DiscDate="2024-11-01", DiscNo="A2", DocType="EarnForecastRevision"),
    ])
    assert s.upsert_fins_summary(df) == 2
    assert s.upsert_fins_summary(df) == 2  # idempotent on (code, disc_no)

    # strict (default): a print disclosed *on* as-of day is not yet knowable (after-close norm)
    assert len(s.fins_history("1301", date(2024, 11, 1))) == 1
    assert len(s.fins_history("1301", date(2024, 11, 1), strict=False)) == 2
    assert len(s.fins_history("1301", date(2024, 11, 2))) == 2

    ev = s.revision_events(date(2024, 1, 1), date(2024, 12, 31))
    assert len(ev) == 1 and ev.iloc[0]["doc_type"] == "EarnForecastRevision"
    assert s.fins_codes() == {"1301"}


# --- revision feature logic ----------------------------------------------------------------------

def _hist_row(disc_date, doc_type, per_type, fy_end, *, op=None, f_op=None, nxf_op=None,
              nxt_fy_end=None, odp=None, f_odp=None, nxf_odp=None) -> dict:
    return {
        "code": "1301", "disc_date": date.fromisoformat(disc_date), "disc_time": "15:00:00",
        "disc_no": f"{disc_date}-{doc_type}-{per_type}", "doc_type": doc_type,
        "per_type": per_type, "per_end": None, "fy_end": date.fromisoformat(fy_end),
        "nxt_fy_end": date.fromisoformat(nxt_fy_end) if nxt_fy_end else None,
        "op": op, "f_op": f_op, "nxf_op": nxf_op,
        "odp": odp, "f_odp": f_odp, "nxf_odp": nxf_odp,
    }


_STMT = "FinancialStatements_Consolidated_JP"


def _sandbagger_hist() -> pd.DataFrame:
    """A serial sandbagger's chained tape (Mar-end FYs), one FY-doc per year that reports the
    actual AND guides the next FY at 100 via nxf_op (as the real 短信 does):

      FY2024–FY2026 all guided 100 initially, delivered 130/125/140 (3/3 beats);
      every 2Q print lands at exactly half the eventual actual (seasonal baseline = 0.50);
      FY2027 in progress: guided 100 (lowball vs 140 prior actual), 2Q cumulative already 80
      → progress 0.80 vs baseline 0.50 = anomaly 1.6.
    """
    actuals = {"2024-03-31": 130.0, "2025-03-31": 125.0, "2026-03-31": 140.0}
    rows = [  # seed: FY2023 doc only guides FY2024 (its own tl is empty → skipped as unusable)
        _hist_row("2023-05-10", "FY" + _STMT, "FY", "2023-03-31",
                  op=90.0, nxf_op=100.0, nxt_fy_end="2024-03-31")]
    for fy, actual in actuals.items():
        y = int(fy[:4])
        rows.append(_hist_row(f"{y - 1}-11-01", "2Q" + _STMT, "2Q", fy,
                              op=actual * 0.5, f_op=100.0))
        rows.append(_hist_row(f"{y}-05-10", "FY" + _STMT, "FY", fy, op=actual,
                              nxf_op=100.0, nxt_fy_end=f"{y + 1}-03-31"))
    # current FY 2027/3: 2Q cumulative 80 vs guidance 100 → progress 0.80
    rows.append(_hist_row("2026-11-01", "2Q" + _STMT, "2Q", "2027-03-31", op=80.0, f_op=100.0))
    return pd.DataFrame(rows).sort_values("disc_date").reset_index(drop=True)


def test_sandbagger_score_counts_beats():
    hist = _sandbagger_hist()
    assert revision.sandbagger_score(hist, PARAMS) == 1.0  # 3/3 FYs beat initial by ≥5%
    # chronic misser: the same tape but every FY actual lands at 90 vs 100 guided
    miss = hist.copy()
    miss.loc[(miss["per_type"] == "FY") & miss["op"].notna(), "op"] = 90.0
    assert revision.sandbagger_score(miss, PARAMS) == 0.0


def test_progress_anomaly_vs_own_seasonality():
    hist = _sandbagger_hist()
    # progress 80/100 = 0.80; baseline median(65/130, 62.5/125, 70/140) = 0.50 → anomaly 1.6
    assert revision.progress_anomaly(hist) == pytest.approx(1.6)


def test_lowball_ratio_against_prior_actual():
    hist = _sandbagger_hist()
    # FY2026 actual 140, FY2027 initial guidance 100 → −28.6%
    assert revision.lowball_ratio(hist) == pytest.approx(100.0 / 140.0 - 1.0)


def test_days_to_next_announcement_anniversary():
    hist = _sandbagger_hist()
    # last year's 2Q print 2026-11-01 (+365d = 2027-11-01) ← asof 2027-10-20 → 12 days
    assert revision.days_to_next_announcement(hist, date(2027, 10, 20)) == 12.0
    assert revision.days_to_next_announcement(hist.head(0), date(2027, 10, 20)) != \
        revision.days_to_next_announcement(hist.head(0), date(2027, 10, 20))  # NaN


def test_assess_composes_score():
    hist = _sandbagger_hist()
    r = revision.assess(hist, date(2026, 11, 20), PARAMS)
    assert r.metric == "op"
    assert r.sandbag == 1.0 and r.anomaly == pytest.approx(1.6) and r.lowball < -0.05
    assert 0.7 <= r.score <= 1.0  # serial sandbagger, ahead of pace, lowballed → high pressure
    # no history → every component degrades to NaN/0, never raises
    empty = revision.assess(hist.head(0), date(2026, 11, 20), PARAMS)
    assert empty.score == 0.0 and empty.sandbag != empty.sandbag


def test_metric_fallback_to_odp():
    # a name that guides 経常利益 only (banks/shippers): f_op all NaN, f_odp populated
    rows = [
        _hist_row("2025-05-10", "FY" + _STMT, "FY", "2025-03-31",
                  odp=120.0, nxf_odp=100.0, nxt_fy_end="2026-03-31"),
        _hist_row("2025-11-01", "2Q" + _STMT, "2Q", "2026-03-31", odp=70.0, f_odp=100.0),
    ]
    hist = pd.DataFrame(rows)
    assert revision._pick_metric(hist) == "odp"
    assert revision.lowball_ratio(hist) == pytest.approx(100.0 / 120.0 - 1.0)
