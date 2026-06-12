"""J-Quants REST client — **API V2**.

Official JPX API, relaunched 2025-12-22 as V2. Auth is a single dashboard-issued key
(jpx-jquants.com) sent as ``x-api-key`` on every request; no V1 refresh/ID-token dance.

Verified against the live API 2026-06-11 (Standard plan, window 2016-06-11 →):

- ``GET /equities/master`` — listed master, current or point-in-time via ``date=``; one call,
  no pagination (4,449 rows). Fields: Code (5-digit), CoName, MktNm (プライム/スタンダード/
  グロース/その他/TOKYO PRO MARKET), S33Nm, Mrgn (``2``=貸借, ``1``=信用, ``3``=その他),
  ProdCat (``011``=domestic common stock). Delisted names resolve via historical ``date=``.
- ``GET /markets/margin-interest`` — weekly 信用取引週末残高 by ``code=`` (full history, one
  call) or ``date=`` (one as-of week, all ~4,300 issues, IssType included). Fields map 1:1 to
  the store/free-PDF schema: ShrtVol/LongVol totals + Neg(一般)/Std(制度) splits — verified
  equal to the JPX PDF for the 2026-06-05 overlap week. **The payload carries the as-of Friday
  only — no publication date** → `derive_published_date`.
- ``GET /equities/bars/daily`` — by ``code=`` (full 10y in one call, 2,441 rows, no
  pagination) or ``date=`` (one day, all issues). AdjO/H/L/C/AdjVo (adjusted), Va (actual ¥
  traded), UL/LL price-limit flags. Delisted names keep their history (83690 ✓).
- ``GET /indices/bars/daily?code=0000`` — TOPIX OHLC (event-study benchmark).

429/5xx → exponential backoff honoring ``Retry-After``; ``pagination_key`` looped defensively
(never observed in practice, but V1 paginated).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

BASE_URL = "https://api.jquants.com/v2"
TOPIX_CODE = "0000"

# MktNm → segment, mirroring the free path's data_j.xls mapping (jpx_universe._SEGMENT).
_SEGMENT = {"プライム": "prime", "スタンダード": "standard", "グロース": "growth"}

# Codes whose weekly margin tape exists for the whole window — used to discover the weekly
# as-of calendar in one call each (union of two, so one name's odd week can't drop a date).
_CALENDAR_PROBES = ("13010", "72030")  # 極洋, トヨタ


# --- publication-date derivation ----------------------------------------------------------
def _is_tse_business_day(d: date) -> bool:
    """TSE trading day: Mon–Fri, not a national holiday, not the exchange's 12/31–1/3 closure."""
    import jpholiday

    if d.weekday() >= 5:
        return False
    if (d.month, d.day) in ((12, 31), (1, 2), (1, 3)):  # 1/1 is a national holiday anyway
        return False
    return not jpholiday.is_holiday(d)


def derive_published_date(as_of: date) -> date:
    """Publication date of a weekly margin print: **2nd TSE business day after the as-of date**.

    The V2 payload has no publication field, so we derive it. JPX publishes
    Friday's 残高 the following Tue (slipping to Wed across JP holidays) — i.e. as-of +2 business
    days. Cross-checked against the free-path PDF header for the overlap week:
    2026-06-05 → 2026-06-09 ✓. When the rule is ever in doubt, deriving *later* is the
    conservative direction (no look-ahead); national holidays come from `jpholiday` (handles
    振替休日 and the 2020/21 Olympics moves a hand table would botch).
    """
    d, remaining = as_of, 2
    while remaining:
        d += timedelta(days=1)
        if _is_tse_business_day(d):
            remaining -= 1
    return d


# --- pure response-row → store-schema mappers (unit-testable, no network) -----------------------
def _code4(code5: "pd.Series") -> "pd.Series":
    """5-char issue code → the 4-char base code the rest of the stack keys on (97550→9755)."""
    return code5.astype(str).str.strip().str[:4]


def _common_only(df: "pd.DataFrame") -> "pd.DataFrame":
    """Keep common stock only: code5 ends in '0' (preferred shares etc. end 5/9 — same rule as
    the free-PDF path). Alphanumeric codes ('130A0') end in '0' too."""
    return df[df["code5"].astype(str).str.endswith("0")]


def rows_to_master(rows: list[dict]) -> "pd.DataFrame":
    """/equities/master rows → listed_info columns (one row per 4-digit code, common stock)."""
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "code5": raw["Code"].astype(str),
            "name": raw["CoName"],
            "segment": raw["MktNm"].map(_SEGMENT).fillna("other"),
            "segment_ja": raw["MktNm"],
            "sector33": raw["S33Nm"],
            # mirrors jpx_universe: 内国普通株式 on Prime/Standard/Growth
            "is_domestic_equity": (raw["ProdCat"].astype(str) == "011")
            & raw["MktNm"].isin(_SEGMENT),
            "is_taishaku": raw["Mrgn"].astype(str) == "2",
        }
)
    df["code"] = _code4(df["code5"])
    df = _common_only(df).drop(columns="code5")
    return df.drop_duplicates("code").reset_index(drop=True)


def rows_to_margin(rows: list[dict]) -> "pd.DataFrame":
    """/markets/margin-interest rows → weekly_margin columns (+code5 + as_of_date).

    Std(制度)→sys_*, Neg(一般)→neg_* — the same 制度/一般 split the free PDF carries; verified
    equal for 13010 on the 2026-06-05 overlap week. One *upsert* call must cover one as-of week
    (Store.upsert_weekly_margin collapses code5→code per week); multi-week frames (by-code
    pulls) are for calendar/history reads, not upserts.
    """
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "code5": raw["Code"].astype(str),
            "as_of_date": pd.to_datetime(raw["Date"]).dt.date,
            "short_total": pd.to_numeric(raw["ShrtVol"], errors="coerce").round().astype("Int64"),
            "long_total": pd.to_numeric(raw["LongVol"], errors="coerce").round().astype("Int64"),
            "neg_short": pd.to_numeric(raw["ShrtNegVol"], errors="coerce").round().astype("Int64"),
            "sys_short": pd.to_numeric(raw["ShrtStdVol"], errors="coerce").round().astype("Int64"),
            "neg_long": pd.to_numeric(raw["LongNegVol"], errors="coerce").round().astype("Int64"),
            "sys_long": pd.to_numeric(raw["LongStdVol"], errors="coerce").round().astype("Int64"),
            "is_taishaku": raw["IssType"].astype(str) == "2",
        }
)
    df["code"] = _code4(df["code5"])
    return df.reset_index(drop=True)


def rows_to_fins_summary(rows: list[dict]) -> "pd.DataFrame":
    """/fins/summary rows → fins_summary columns (decision-relevant subset).

    One row per disclosure (決算短信 quarterly/FY summaries AND standalone 業績予想修正). Field
    notes from live probes (2026-06-12, codes 72030/91040):
      - Yen amounts arrive as strings, empty string = not disclosed → NA (Int64).
      - `EarnForecastRevision` rows leave the actuals empty and carry the *revised* forecasts —
        sometimes FY fields (FOP…), sometimes interim ones (FOP2Q…), sometimes only 経常 (FOdP,
        e.g. 9104 2020-06-17). Keep both field families plus 経常利益 throughout, or revisions
        that only touch one of them look empty.
      - `DiscTime` formats vary ('11:30' vs '15:00:00') — stored verbatim; ~15:00+ means
        after-close, the next session is the first tradable bar.
      - `NxtFYEn` is stored so a prior FY doc's next-FY guidance (NxF*) can be joined to the FY
        it guides *exactly*, not by +1-year inference (fiscal-year changes would break that).
    """
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)

    def _yen(col: str) -> "pd.Series":
        s = raw.get(col, pd.Series([None] * len(raw)))
        return pd.to_numeric(s.replace("", None), errors="coerce").round().astype("Int64")

    def _flt(col: str) -> "pd.Series":
        s = raw.get(col, pd.Series([None] * len(raw)))
        return pd.to_numeric(s.replace("", None), errors="coerce")

    df = pd.DataFrame({
        "code5": raw["Code"].astype(str),
        "disc_date": pd.to_datetime(raw["DiscDate"]).dt.date,
        "disc_time": raw["DiscTime"].astype(str),
        "disc_no": raw["DiscNo"].astype(str),
        "doc_type": raw["DocType"].astype(str),
        "per_type": raw["CurPerType"].astype(str),
        "per_end": pd.to_datetime(raw["CurPerEn"].replace("", None)).dt.date,
        "fy_end": pd.to_datetime(raw["CurFYEn"].replace("", None)).dt.date,
        "nxt_fy_end": pd.to_datetime(raw.get("NxtFYEn", pd.Series([None] * len(raw)))
.replace("", None)).dt.date,
        "sales": _yen("Sales"), "op": _yen("OP"), "odp": _yen("OdP"), "np": _yen("NP"),
        "eps": _flt("EPS"),
        "f_sales": _yen("FSales"), "f_op": _yen("FOP"), "f_odp": _yen("FOdP"),
        "f_np": _yen("FNP"), "f_eps": _flt("FEPS"),
        "f_sales_2q": _yen("FSales2Q"), "f_op_2q": _yen("FOP2Q"), "f_odp_2q": _yen("FOdP2Q"),
        "f_np_2q": _yen("FNP2Q"), "f_eps_2q": _flt("FEPS2Q"),
        "nxf_sales": _yen("NxFSales"), "nxf_op": _yen("NxFOP"), "nxf_odp": _yen("NxFOdP"),
        "nxf_np": _yen("NxFNp"), "nxf_eps": _flt("NxFEPS"),
    })
    df["code"] = _code4(df["code5"])
    return df.reset_index(drop=True)


def rows_to_bars(rows: list[dict]) -> "pd.DataFrame":
    """/equities/bars/daily rows → prices columns.

    Adjusted OHLCV (AdjO/H/L/C/AdjVo — split-adjusted, same convention as the Yahoo path);
    turnover = Va (actual ¥ traded; falls back to close×volume = 0 on no-trade days, matching
    the Yahoo path). No-trade rows (null close) are dropped — Yahoo emits no bar there either.
    """
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    df = pd.DataFrame(
        {
            "code5": raw["Code"].astype(str),
            "date": pd.to_datetime(raw["Date"]).dt.date,
            "open": pd.to_numeric(raw["AdjO"], errors="coerce"),
            "high": pd.to_numeric(raw["AdjH"], errors="coerce"),
            "low": pd.to_numeric(raw["AdjL"], errors="coerce"),
            "close": pd.to_numeric(raw["AdjC"], errors="coerce"),
            "volume": pd.to_numeric(raw["AdjVo"], errors="coerce"),
            "turnover": pd.to_numeric(raw.get("Va"), errors="coerce"),
        }
)
    df["code"] = _code4(df["code5"])
    df = _common_only(df)
    df = df[df["close"].notna()].copy()
    df["volume"] = df["volume"].fillna(0).round().astype("int64")
    df["turnover"] = df["turnover"].fillna(df["close"] * df["volume"])
    return df.drop(columns="code5").reset_index(drop=True)


def rows_to_topix(rows: list[dict]) -> "pd.DataFrame":
    """/indices/bars/daily (code=0000) rows → topix table columns (no volume on indices)."""
    import pandas as pd

    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            "date": pd.to_datetime(raw["Date"]).dt.date,
            "open": pd.to_numeric(raw["O"], errors="coerce"),
            "high": pd.to_numeric(raw["H"], errors="coerce"),
            "low": pd.to_numeric(raw["L"], errors="coerce"),
            "close": pd.to_numeric(raw["C"], errors="coerce"),
        }
).dropna(subset=["close"]).reset_index(drop=True)


# --- client -------------------------------------------------------------------------------------
_RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class JQuantsClient:
    api_key: str | None = None
    max_retries: int = 6
    timeout_s: float = 60.0

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise RuntimeError(
                "JQUANTS_API_KEY not set — issue one from the jpx-jquants.com dashboard"
)
        return {"x-api-key": self.api_key}

    def get(self, path: str, params: dict | None = None) -> list[dict]:
        """GET with 429/5xx exponential backoff (honoring Retry-After) + pagination_key loop."""
        import httpx

        params = {k: (v.isoformat() if isinstance(v, date) else v)
                  for k, v in (params or {}).items() if v is not None}
        out: list[dict] = []
        while True:
            attempt = 0
            while True:
                try:
                    r = httpx.get(BASE_URL + path, headers=self._headers(), params=params,
                                  timeout=self.timeout_s)
                except httpx.TransportError:
                    if attempt >= self.max_retries:
                        raise
                    time.sleep(min(2.0 ** attempt, 30.0))
                    attempt += 1
                    continue
                if r.status_code in _RETRY_STATUSES and attempt < self.max_retries:
                    wait = float(r.headers.get("Retry-After") or min(2.0 ** attempt, 30.0))
                    time.sleep(wait)
                    attempt += 1
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"J-Quants GET {path} {params} -> {r.status_code}: {r.text[:300]}")
                break
            j = r.json()
            out.extend(j.get("data", []))
            if not j.get("pagination_key"):
                return out
            params["pagination_key"] = j["pagination_key"]

    # --- endpoints (paths verified live 2026-06-11) ---
    def listed_master(self, *, code: str | None = None, date: date | str | None = None) -> "pd.DataFrame":
        """GET /equities/master — current (no args) or point-in-time (`date=`) listed snapshot."""
        return rows_to_master(self.get("/equities/master", {"code": code, "date": date}))

    def weekly_margin_interest(self, *, code: str | None = None,
                               date: date | str | None = None) -> "pd.DataFrame":
        """GET /markets/margin-interest — by `code` (full history) or `date` (one as-of week)."""
        return rows_to_margin(self.get("/markets/margin-interest", {"code": code, "date": date}))

    def daily_bars(self, *, code: str | None = None, date: date | str | None = None,
                   date_from: date | str | None = None,
                   date_to: date | str | None = None) -> "pd.DataFrame":
        """GET /equities/bars/daily — by `code` (full 10y, one call) or `date` (one day, all)."""
        return rows_to_bars(self.get(
            "/equities/bars/daily", {"code": code, "date": date, "from": date_from, "to": date_to}
))

    def topix_bars(self, *, date_from: date | str | None = None,
                   date_to: date | str | None = None) -> "pd.DataFrame":
        """GET /indices/bars/daily code=0000 — TOPIX OHLC (benchmark)."""
        return rows_to_topix(self.get(
            "/indices/bars/daily", {"code": TOPIX_CODE, "from": date_from, "to": date_to}
))

    def margin_asof_dates(self) -> list[date]:
        """The weekly as-of calendar available to this subscription, discovered in 2 calls
        (union of two always-listed probes' tapes — robust to one name's odd week)."""
        dates: set[date] = set()
        for c in _CALENDAR_PROBES:
            df = self.weekly_margin_interest(code=c)
            if not df.empty:
                dates.update(df["as_of_date"])
        return sorted(dates)

    def fins_summary(self, *, code: str | None = None,
                     date: date | str | None = None) -> "pd.DataFrame":
        """GET /fins/summary — 決算短信サマリー + 業績予想修正, one row per disclosure.

        By `code` (full history, one call — verified 41–53 rows for 10y) or by `date`. This is
        the revision setup's event source AND its feature history (guidance trajectory).
        """
        return rows_to_fins_summary(self.get("/fins/summary", {"code": code, "date": date}))

    def earnings_calendar(self) -> list[dict]:
        """GET /equities/earnings-calendar — *forward snapshot only* (live probe 2026-06-12: all
        params ignored, ~14 rows). Useless for backtests — historical announcement timing is
        derived per-name from fins_summary disclosure dates instead. Live-overlay use only."""
        return self.get("/equities/earnings-calendar", {})

    def short_sale_report(self, *, code: str | None = None, date_from: str | None = None,
                          date_to: str | None = None):
        """GET /markets/short-sale-report — 空売り残高報告 (≥0.5% fund positions). Not wired yet:
        systematic bear-case/crowding history is a later phase."""
        raise NotImplementedError("not needed for; wire when crowding history is wanted")
