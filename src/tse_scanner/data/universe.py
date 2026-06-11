"""Universe builder (Phase 0 — DESIGN.md §5).

Point-in-time, survivorship-free tradeable universe. As-of any date, a name is in the universe iff:
  - **貸借銘柄** (loan-marginable) — *thesis requirement*; 逆日歩 and the 制度信用 short-trap exist
    only here. Hard gate, not a filter.
  - listed & not 監理・整理, not ETF/REIT/ETN, not freshly listed (needs the ~120-session baseline).
  - turnover ≥ Params.turnover_floor_yen (20d avg) and price ≥ Params.min_price_yen.

"Point-in-time" is the whole game: membership must be reconstructed from listed/eligibility history
as it was *on that date*. The **free** path gives only the current eligibility snapshot (no per-day
history), so de-listed / de-margined names are *not* reconstructable for free — that survivorship
caveat (DESIGN.md §6, §12) is surfaced, not hidden, and is resolved only by the paid J-Quants
`listed/info` history. The publication-date margin read here is fully point-in-time regardless.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params

if TYPE_CHECKING:
    import pandas as pd

    from tse_scanner.data.store import Store


def build_universe(as_of: date, params: Params = PARAMS, store: "Store | None" = None) -> "pd.DataFrame":
    """Return the set of codes tradeable as-of `as_of`, keyed by code.

    Joins the eligibility snapshot (listed_info) + publication-date margin + price gates from the
    store, applies the §5 gates, and returns one row per surviving code with the fields the scan
    needs (margin balances, ADV, last close, eligibility, margin dates).
    """
    import pandas as pd

    from tse_scanner.data.store import Store

    store = store or Store()
    listed = store.listed()
    metrics = store.price_metrics(as_of, params.turnover_window)
    margin = store.latest_margin(as_of)

    if listed.empty or metrics.empty:
        return pd.DataFrame()

    df = listed.merge(metrics, on="code", how="inner")
    if not margin.empty:
        mcols = ["code", "short_total", "long_total", "is_taishaku",
                 "as_of_date", "published_date"]
        df = df.merge(margin[mcols], on="code", how="left", suffixes=("", "_mg"))
        # prefer the margin file's B-flag (authoritative §5 gate) over the listed snapshot.
        # Cast to the nullable boolean dtype first so the NA-fill chain stays on a boolean
        # dtype and avoids pandas' object-downcast FutureWarning (fires once per replay date).
        df["is_taishaku"] = (
            df["is_taishaku_mg"].astype("boolean")
            .fillna(df["is_taishaku"].astype("boolean"))
            .fillna(False)
            .astype(bool)
        )
        df = df.drop(columns=[c for c in df.columns if c.endswith("_mg")])
    else:
        df["short_total"] = pd.NA
        df["long_total"] = pd.NA
        df["as_of_date"] = pd.NaT
        df["published_date"] = pd.NaT

    gates = pd.Series(True, index=df.index)
    if params.require_taishaku:
        gates &= df["is_taishaku"].fillna(False)
    gates &= df["is_domestic_equity"].fillna(False)
    gates &= df["last_close"].fillna(0) >= params.min_price_yen
    gates &= df["adv_yen"].fillna(0) >= params.turnover_floor_yen
    gates &= df["n_sessions"].fillna(0) >= params.baseline_sessions
    # Point-in-time delisting guard: with deep history (Track B), a name delisted mid-window keeps
    # stale prices + a stale margin print forever; require a recent bar or it leaves the universe.
    staleness = (pd.Timestamp(as_of) - pd.to_datetime(df["last_date"])).dt.days
    gates &= staleness.fillna(10**6) <= params.max_price_staleness_days

    out = df[gates].copy()
    out = out.rename(columns={"as_of_date": "margin_as_of", "published_date": "margin_published"})
    keep = ["code", "name", "segment", "is_taishaku", "last_close", "last_date",
            "adv_yen", "adv_shares", "n_sessions", "short_total", "long_total",
            "margin_as_of", "margin_published"]
    return out[[c for c in keep if c in out.columns]].reset_index(drop=True)
