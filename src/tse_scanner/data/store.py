"""Point-in-time data store (Phase 0 — DESIGN.md §6, §8): DuckDB + Parquet.

The single most important correctness property of this whole project lives here:

  **Weekly margin is stored with its as-of date AND its publication date, and every point-in-time
  read filters on publication date.** Friday's 信用取引残高 is published the following Tue–Wed;
  using it before then is trading on data that didn't exist and makes the backtest lie (DESIGN.md
  §6.1). Prices are EOD and need no such lag.

Tables:
  prices(code, date, open, high, low, close, volume, turnover)
  weekly_margin(code, as_of_date, published_date, short_total, long_total,
                neg_short, sys_short, neg_long, sys_long, is_taishaku)
  listed_info(code, name, segment, segment_ja, sector33, is_domestic_equity, is_taishaku, ingested_at)
  scan_results(scan_date, code, name, segment, stage, score, fuel, coil, ignition,
               days_to_cover, credit_ratio, short_total, long_total, bb_width_pct,
               box_high, box_low, last_close, adv_yen, margin_as_of, margin_published)
  research(code, research_date, status, verdict, confidence, one_liner, squeeze_killers,
           catalysts, sources, memo_md, memo_path, model, cost_usd, num_turns, duration_s,
           error, started_at, finished_at, pid)   -- §15 deep-research memos; agent never writes here

NOTE on point-in-time universe: the *free* path (JPX `data_j.xls`) publishes only the **current**
listed/eligibility snapshot — there is no free per-day eligibility history — so `listed_info` is a
current snapshot, not a valid_from/valid_to history. Survivorship-free reconstruction (DESIGN.md
§12) needs the paid J-Quants `listed/info` history; until then the backtest universe is
present-membership and that caveat is surfaced, not hidden.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from tse_scanner.config import Settings, load_settings

if TYPE_CHECKING:
    import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    code        VARCHAR NOT NULL,
    date        DATE    NOT NULL,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      BIGINT,
    turnover    DOUBLE,              -- close * volume (¥ proxy for traded value)
    PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS weekly_margin (
    code           VARCHAR NOT NULL,
    as_of_date     DATE    NOT NULL, -- 申込み現在 (Friday)
    published_date DATE,             -- public the following Tue–Wed (DESIGN.md §6.1)
    short_total    BIGINT,           -- 信用売残
    long_total     BIGINT,           -- 信用買残
    neg_short      BIGINT,
    sys_short      BIGINT,
    neg_long       BIGINT,
    sys_long       BIGINT,
    is_taishaku    BOOLEAN,          -- 貸借銘柄 (B flag) — DESIGN.md §5 universe gate
    PRIMARY KEY (code, as_of_date)
);
CREATE TABLE IF NOT EXISTS listed_info (
    code               VARCHAR NOT NULL,
    name               VARCHAR,
    segment            VARCHAR,
    segment_ja         VARCHAR,
    sector33           VARCHAR,
    is_domestic_equity BOOLEAN,
    is_taishaku        BOOLEAN,
    ingested_at        TIMESTAMP,
    PRIMARY KEY (code)
);
CREATE TABLE IF NOT EXISTS scan_results (
    scan_date        DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    name             VARCHAR,
    segment          VARCHAR,
    stage            VARCHAR,
    score            DOUBLE,
    fuel             DOUBLE,
    coil             DOUBLE,
    ignition         VARCHAR,
    days_to_cover    DOUBLE,
    credit_ratio     DOUBLE,
    short_total      BIGINT,
    long_total       BIGINT,
    bb_width_pct     DOUBLE,
    box_high         DOUBLE,
    box_low          DOUBLE,
    last_close       DOUBLE,
    adv_yen          DOUBLE,
    margin_as_of     DATE,
    margin_published DATE,
    PRIMARY KEY (scan_date, code)
);
CREATE TABLE IF NOT EXISTS topix (
    date  DATE NOT NULL,             -- TOPIX OHLC — event-study benchmark (DESIGN.md §6)
    open  DOUBLE,
    high  DOUBLE,
    low   DOUBLE,
    close DOUBLE,
    PRIMARY KEY (date)
);
CREATE TABLE IF NOT EXISTS research (
    code            VARCHAR NOT NULL,
    research_date   DATE    NOT NULL,
    status          VARCHAR NOT NULL,  -- running | done | failed
    verdict         VARCHAR,           -- SUPPORTS | NEUTRAL | OPPOSES (DESIGN.md §15)
    confidence      VARCHAR,           -- low | medium | high
    one_liner       VARCHAR,
    squeeze_killers VARCHAR,           -- JSON array (text)
    catalysts       VARCHAR,           -- JSON array of {date, event} (text)
    sources         VARCHAR,           -- JSON array of URLs (text)
    memo_md         VARCHAR,           -- full memo — the web UI renders from here, not the fs
    memo_path       VARCHAR,
    model           VARCHAR,
    cost_usd        DOUBLE,
    num_turns       INTEGER,
    duration_s      DOUBLE,
    error           VARCHAR,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    pid             INTEGER,           -- wrapper PID while status='running'; lets any reader
                                       -- check real process liveness, not just a wall-clock timer
    PRIMARY KEY (code, research_date)
);

-- Migrate DBs created before `pid` existed (CREATE IF NOT EXISTS above won't add a column to an
-- existing table). ADD COLUMN IF NOT EXISTS is idempotent, so this is a no-op once applied.
ALTER TABLE research ADD COLUMN IF NOT EXISTS pid INTEGER;
"""


class Store:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.path: Path = self.settings.duckdb_path

    def connect(self, *, retries: int = 6, base_delay: float = 0.15):
        """Open the DuckDB connection (lazy import so the stdlib smoke path stays clean).

        DuckDB allows only one read-write process on the file at a time, and both of our
        processes (API reads, research/ingest CLI) hold sub-second connections — so a
        "Conflicting lock" IOException is a transient collision, not a real fault. Retry with
        exponential backoff (~4.7s worst case) before surfacing it.
        """
        import duckdb

        self.path.parent.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            try:
                return duckdb.connect(str(self.path))
            except duckdb.IOException as exc:
                attempt += 1
                if "lock" not in str(exc).lower() or attempt >= retries:
                    raise
                time.sleep(base_delay * 2 ** (attempt - 1))

    def init_schema(self) -> None:
        with self.connect() as con:
            con.execute(_SCHEMA)

    # --- writes -------------------------------------------------------------
    @staticmethod
    def _upsert(con, table: str, df: "pd.DataFrame", keys: list[str]) -> int:
        """Idempotent upsert: delete rows matching `keys`, then insert. Returns row count."""
        if df is None or len(df) == 0:
            return 0
        cols = list(df.columns)
        con.register("_tmp", df)
        try:
            key_sql = ", ".join(keys)
            con.execute(
                f"DELETE FROM {table} WHERE ({key_sql}) IN (SELECT {key_sql} FROM _tmp)"
            )
            col_sql = ", ".join(cols)
            con.execute(f"INSERT INTO {table} ({col_sql}) SELECT {col_sql} FROM _tmp")
        finally:
            con.unregister("_tmp")
        return len(df)

    def upsert_prices(self, df: "pd.DataFrame") -> int:
        """Write adjusted OHLCV. Expects columns: code, date, open, high, low, close, volume[, turnover]."""
        import pandas as pd

        if df is None or df.empty:
            return 0
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if "turnover" not in df.columns:
            df["turnover"] = df["close"].astype(float) * df["volume"].astype(float)
        df = df[["code", "date", "open", "high", "low", "close", "volume", "turnover"]]
        self.init_schema()
        with self.connect() as con:
            return self._upsert(con, "prices", df, ["code", "date"])

    def upsert_weekly_margin(self, df: "pd.DataFrame", *, as_of: date, published: date | None) -> int:
        """Persist BOTH as_of_date and published_date (DESIGN.md §6.1).

        `df` is one weekly print as returned by jpx_margin.parse_weekly_margin (per-issue rows);
        `as_of`/`published` come from jpx_margin.parse_dates (the PDF header).
        """
        import pandas as pd

        if df is None or df.empty:
            return 0
        df = df.copy()
        # Collapse per-issue (code5) rows to the 4-digit `code` the rest of the stack keys on
        # (Yahoo uses 4-digit). A code can carry common + preferred (e.g. 2593 = 25930/25935);
        # keep the common stock (code5 ending in '0') — that's the security we trade.
        if "code5" in df.columns:
            df["_common"] = df["code5"].astype(str).str.endswith("0")
            df = df.sort_values("_common", ascending=False).drop_duplicates("code", keep="first")
            df = df.drop(columns="_common")
        df["as_of_date"] = as_of
        df["published_date"] = published
        for c in ("neg_short", "sys_short", "neg_long", "sys_long"):
            if c not in df.columns:
                df[c] = pd.NA
        cols = ["code", "as_of_date", "published_date", "short_total", "long_total",
                "neg_short", "sys_short", "neg_long", "sys_long", "is_taishaku"]
        df = df[cols]
        self.init_schema()
        with self.connect() as con:
            return self._upsert(con, "weekly_margin", df, ["code", "as_of_date"])

    def upsert_listed_info(self, df: "pd.DataFrame", *, taishaku_codes: set[str] | None = None) -> int:
        """Write the current listed/eligibility snapshot (free path = present-membership only)."""
        import pandas as pd

        if df is None or df.empty:
            return 0
        df = df.copy()
        df = df.drop_duplicates("code", keep="first")  # data_j can list multiple share classes
        if taishaku_codes is not None:
            df["is_taishaku"] = df["code"].isin(taishaku_codes)
        elif "is_taishaku" not in df.columns:
            df["is_taishaku"] = False
        df["ingested_at"] = pd.Timestamp.utcnow().tz_localize(None)
        for c in ("name", "segment", "segment_ja", "sector33"):
            if c not in df.columns:
                df[c] = None
        cols = ["code", "name", "segment", "segment_ja", "sector33",
                "is_domestic_equity", "is_taishaku", "ingested_at"]
        df = df[cols]
        self.init_schema()
        with self.connect() as con:
            return self._upsert(con, "listed_info", df, ["code"])

    def upsert_topix(self, df: "pd.DataFrame") -> int:
        """Write TOPIX OHLC rows (date, open, high, low, close) — the §6 benchmark series."""
        import pandas as pd

        if df is None or df.empty:
            return 0
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[["date", "open", "high", "low", "close"]]
        self.init_schema()
        with self.connect() as con:
            return self._upsert(con, "topix", df, ["date"])

    def write_scan(self, df: "pd.DataFrame") -> int:
        """Persist a scan run (one row per code for that scan_date)."""
        if df is None or df.empty:
            return 0
        self.init_schema()
        with self.connect() as con:
            return self._upsert(con, "scan_results", df, ["scan_date", "code"])

    # --- point-in-time reads ------------------------------------------------
    def margin_asof(self, code: str, asof: date):
        """Latest weekly margin print for `code` **published on or before** `asof` (no look-ahead)."""
        with self.connect() as con:
            return con.execute(
                """SELECT * FROM weekly_margin
                   WHERE code = ? AND published_date IS NOT NULL AND published_date <= ?
                   ORDER BY as_of_date DESC LIMIT 1""",
                [str(code), asof],
            ).df()

    def latest_margin(self, asof: date | None = None) -> "pd.DataFrame":
        """Each code's most-recent margin print published on/before `asof` (latest overall if None).

        The vectorized form of `margin_asof` — the publication-date point-in-time read the scan uses.
        """
        where = "published_date IS NOT NULL"
        params: list = []
        if asof is not None:
            where += " AND published_date <= ?"
            params = [asof]
        with self.connect() as con:
            return con.execute(
                f"""SELECT * FROM weekly_margin
                    WHERE {where}
                    QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of_date DESC) = 1""",
                params,
            ).df()

    def margin_history(self, code: str, asof: date | None = None, prints: int = 3) -> "pd.DataFrame":
        """Last `prints` weekly prints for `code` (publication-date filtered), oldest→newest."""
        with self.connect() as con:
            where = "code = ?"
            params: list = [str(code)]
            if asof is not None:
                where += " AND published_date IS NOT NULL AND published_date <= ?"
                params.append(asof)
            return con.execute(
                f"""SELECT * FROM weekly_margin WHERE {where}
                    ORDER BY as_of_date DESC LIMIT {int(prints)}""",
                params,
            ).df().iloc[::-1].reset_index(drop=True)

    def margin_trends(
        self, codes: list[str], asof: date | None = None, prints: int = 3
    ) -> dict[str, "pd.DataFrame"]:
        """Last `prints` prints per code (publication-date filtered), oldest→newest — keyed by code.

        Batched form of `margin_history` for the scan loop: one query instead of one-per-code. The
        scan evaluates ~1.6k names and the per-code connect+query dominated its runtime (≈195s →
        a few s). Returns the same rows/order `margin_history` would, per code.
        """
        import pandas as pd

        codes = [str(c) for c in codes]
        if not codes:
            return {}
        where = "code IN (SELECT code FROM _codes)"
        params: list = []
        if asof is not None:
            where += " AND published_date IS NOT NULL AND published_date <= ?"
            params.append(asof)
        with self.connect() as con:
            con.register("_codes", pd.DataFrame({"code": codes}))
            try:
                df = con.execute(
                    f"""SELECT * FROM weekly_margin WHERE {where}
                        QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of_date DESC)
                                <= {int(prints)}
                        ORDER BY code, as_of_date""",
                    params,
                ).df()
            finally:
                con.unregister("_codes")
        if df.empty:
            return {}
        return {c: g.reset_index(drop=True) for c, g in df.groupby("code")}

    def prices(self, code: str, start: date | None = None, end: date | None = None) -> "pd.DataFrame":
        """Daily OHLCV for `code` in [start, end], indexed by date ascending."""
        with self.connect() as con:
            where = "code = ?"
            params: list = [str(code)]
            if start is not None:
                where += " AND date >= ?"
                params.append(start)
            if end is not None:
                where += " AND date <= ?"
                params.append(end)
            df = con.execute(
                f"SELECT * FROM prices WHERE {where} ORDER BY date", params
            ).df()
        if not df.empty:
            import pandas as pd

            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    def price_metrics(self, asof: date | None = None, window: int = 20) -> "pd.DataFrame":
        """Per-code price gates as-of a date: last close, n_sessions, `window`-day ADV (¥ and shares).

        Window aggregates use only the most-recent `window` sessions on/before `asof` (point-in-time).
        """
        where = "" if asof is None else "WHERE date <= ?"
        params = [] if asof is None else [asof]
        sql = f"""
            WITH ranked AS (
                SELECT code, date, close, volume, turnover,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn,
                       COUNT(*)     OVER (PARTITION BY code)                    AS n
                FROM prices {where}
            )
            SELECT code,
                   MAX(n)                                          AS n_sessions,
                   MAX(CASE WHEN rn = 1 THEN close END)            AS last_close,
                   MAX(CASE WHEN rn = 1 THEN date END)             AS last_date,
                   AVG(CASE WHEN rn <= {int(window)} THEN turnover END) AS adv_yen,
                   AVG(CASE WHEN rn <= {int(window)} THEN volume   END) AS adv_shares
            FROM ranked GROUP BY code
        """
        with self.connect() as con:
            return con.execute(sql, params).df()

    def all_prices(self, codes: list[str], asof: date | None = None) -> "pd.DataFrame":
        """All OHLCV rows for `codes` (≤ asof), one frame ordered by (code, date) — for the scan."""
        import pandas as pd

        if not codes:
            return pd.DataFrame()
        codes = [str(c) for c in codes]
        cond = "code IN (SELECT code FROM _codes)"
        params: list = []
        if asof is not None:
            cond += " AND date <= ?"
            params = [asof]
        with self.connect() as con:
            con.register("_codes", pd.DataFrame({"code": codes}))
            try:
                df = con.execute(
                    f"SELECT * FROM prices WHERE {cond} ORDER BY code, date", params
                ).df()
            finally:
                con.unregister("_codes")
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def listed(self) -> "pd.DataFrame":
        with self.connect() as con:
            return con.execute("SELECT * FROM listed_info").df()

    def topix(self, start: date | None = None, end: date | None = None) -> "pd.DataFrame":
        """TOPIX OHLC in [start, end], indexed by date ascending (empty frame if none stored)."""
        self.init_schema()
        where, params = "1=1", []
        if start is not None:
            where += " AND date >= ?"
            params.append(start)
        if end is not None:
            where += " AND date <= ?"
            params.append(end)
        with self.connect() as con:
            df = con.execute(f"SELECT * FROM topix WHERE {where} ORDER BY date", params).df()
        if not df.empty:
            import pandas as pd

            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    def margin_weeks(self) -> list[date]:
        """Distinct as-of dates already in weekly_margin — the sync diff base (Track B)."""
        self.init_schema()
        with self.connect() as con:
            return [r[0] for r in con.execute(
                "SELECT DISTINCT as_of_date FROM weekly_margin ORDER BY as_of_date"
            ).fetchall()]

    def price_date_ranges(self) -> "pd.DataFrame":
        """Per-code min/max(date) in prices — drives the incremental bars sync (Track B)."""
        self.init_schema()
        with self.connect() as con:
            return con.execute(
                "SELECT code, min(date) AS first_date, max(date) AS last_date "
                "FROM prices GROUP BY code"
            ).df()

    def read_scan(self, scan_date: date | None = None) -> "pd.DataFrame":
        """Read a scan run (the latest if `scan_date` is None)."""
        with self.connect() as con:
            if not con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name='scan_results'"
            ).fetchone():
                import pandas as pd

                return pd.DataFrame()
            if scan_date is None:
                row = con.execute("SELECT max(scan_date) FROM scan_results").fetchone()
                scan_date = row[0] if row else None
                if scan_date is None:
                    import pandas as pd

                    return pd.DataFrame()
            # Gate-by-stage then score (DESIGN.md §7). `stage` is text, so rank it explicitly by the
            # lifecycle ladder (matches pipeline.Stage) rather than sorting the string alphabetically.
            return con.execute(
                """SELECT * FROM scan_results WHERE scan_date = ?
                   ORDER BY CASE stage
                              WHEN 'CONFIRMED' THEN 4 WHEN 'IGNITING' THEN 3
                              WHEN 'PRIMED'    THEN 2 WHEN 'CROWDED'  THEN 1
                              ELSE 0 END DESC,
                            score DESC""",
                [scan_date],
            ).df()

    # --- deep research (DESIGN.md §15) ---------------------------------------
    # The research agent writes files only; these methods are called solely by the
    # `tse-scanner research` wrapper (two brief touches) and the API (reads).

    def upsert_research(self, row: dict) -> None:
        """Insert/replace the (code, research_date) research row — used for both the
        'running' marker at spawn and the final 'done'/'failed' record."""
        import pandas as pd

        self.init_schema()
        cols = ["code", "research_date", "status", "verdict", "confidence", "one_liner",
                "squeeze_killers", "catalysts", "sources", "memo_md", "memo_path", "model",
                "cost_usd", "num_turns", "duration_s", "error", "started_at", "finished_at", "pid"]
        df = pd.DataFrame([{c: row.get(c) for c in cols}])
        with self.connect() as con:
            self._upsert(con, "research", df, ["code", "research_date"])

    def latest_research(self, code: str, status: str | None = None) -> "pd.DataFrame":
        """Most recent research row for `code` (optionally only rows with `status`)."""
        self.init_schema()
        where, params = "code = ?", [str(code)]
        if status is not None:
            where += " AND status = ?"
            params.append(status)
        with self.connect() as con:
            return con.execute(
                f"""SELECT * FROM research WHERE {where}
                    ORDER BY research_date DESC, started_at DESC LIMIT 1""",
                params,
            ).df()

    def research_verdicts(self) -> "pd.DataFrame":
        """Per-code latest *done* verdict — the web ranked-table join (display-only)."""
        self.init_schema()
        with self.connect() as con:
            return con.execute(
                """SELECT code, research_date, verdict, confidence, one_liner FROM research
                   WHERE status = 'done'
                   QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY research_date DESC) = 1"""
            ).df()

    def summary(self) -> dict:
        """Row counts + freshness — used by the CLI and the web freshness header."""
        self.init_schema()
        with self.connect() as con:
            out = {
                "prices": con.execute("SELECT count(*) FROM prices").fetchone()[0],
                "price_codes": con.execute("SELECT count(DISTINCT code) FROM prices").fetchone()[0],
                "margin_rows": con.execute("SELECT count(*) FROM weekly_margin").fetchone()[0],
                "listed": con.execute("SELECT count(*) FROM listed_info").fetchone()[0],
                "scans": con.execute("SELECT count(DISTINCT scan_date) FROM scan_results").fetchone()[0],
            }
            row = con.execute(
                "SELECT max(as_of_date), max(published_date) FROM weekly_margin"
            ).fetchone()
            out["margin_as_of"], out["margin_published"] = row[0], row[1]
            return out
