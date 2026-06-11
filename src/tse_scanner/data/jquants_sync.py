"""J-Quants → store sync (PLAN.md Track B): one idempotent code path for backfill *and* weekly.

`sync` makes the local store converge to what the subscription window offers:

  1. **listed master** — current snapshot upsert; when backfilling (>8 missing weeks) also one
     snapshot per June 30 in the window, oldest→newest, so names delisted mid-window keep their
     last-seen attributes (the survivorship fix — universe membership itself still comes from
     the per-week margin tape's IssType, which is point-in-time by construction).
  2. **weekly margin** — diff the API's as-of calendar (2 calls) against stored weeks; pull each
     missing week bulk-by-date (~520 calls for 10y, not ~3.5k per-code) and upsert with the
     derived publication date (§6.1).
  3. **TOPIX** — incremental pull into the `topix` table (event-study benchmark, §6).
  4. **daily bars** — per-code full-history pulls (one call each — verified) for candidate codes
     (ever-貸借 in the margin tape ∪ currently 貸借) that have no rows yet; then bulk-by-date for
     recent missing trading days (covers everything already in the store, 1 call/day).

First run ≈ 520 + ~2,900 + a handful of calls (~20–40 min, sequential + 429 backoff); later
runs touch only the new week / new days. Free-path weeks already stored are left as-is (the API
matches the PDF 1:1 for the overlap week, so converged is converged).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Callable

from tse_scanner.data.jquants import JQuantsClient, derive_published_date

if TYPE_CHECKING:
    from tse_scanner.data.store import Store

_BACKFILL_THRESHOLD_WEEKS = 8   # more missing weeks than this → treat as a backfill run
_PRICE_FLUSH_CODES = 25         # buffer per-code bar pulls; flush in chunks (short DB locks)


def _master_snapshot_dates(weeks: list[date]) -> list[date]:
    """One June-30 snapshot per calendar year in the window (delisted names' attributes)."""
    years = sorted({w.year for w in weeks})
    return [date(y, 6, 30) for y in years]


def sync(store: "Store", client: JQuantsClient, *, prices: bool = True,
         limit: int | None = None, progress: Callable[[str], None] = print) -> dict:
    """Converge the store to the subscription window. Returns counters for the CLI report."""
    import pandas as pd

    store.init_schema()
    out = {"master_rows": 0, "weeks_added": 0, "margin_rows": 0,
           "topix_rows": 0, "codes_pulled": 0, "bars_rows": 0, "days_filled": 0}

    # --- 2) weekly margin calendar diff (do the diff first; it decides backfill-vs-weekly) ---
    calendar = client.margin_asof_dates()
    if not calendar:
        raise RuntimeError("J-Quants margin calendar came back empty — check the API key/plan")
    have = set(store.margin_weeks())
    missing = [w for w in calendar if w not in have]
    backfilling = len(missing) > _BACKFILL_THRESHOLD_WEEKS
    progress(f"  margin calendar: {len(calendar)} weeks available "
             f"({calendar[0]} → {calendar[-1]}), {len(missing)} missing")

    # --- 1) listed master (current last so surviving names carry today's attributes) ---
    snap_dates = _master_snapshot_dates(calendar) if backfilling else []
    for d in snap_dates:
        m = client.listed_master(date=d)
        out["master_rows"] += store.upsert_listed_info(m)
    cur = client.listed_master()
    out["master_rows"] += store.upsert_listed_info(cur)
    progress(f"  master: {len(cur)} current names upserted"
             + (f" (+{len(snap_dates)} historical snapshots for delisted attrs)" if snap_dates else ""))

    # --- 2b) pull missing margin weeks, oldest first ---
    for i, w in enumerate(missing, 1):
        mg = client.weekly_margin_interest(date=w)
        if mg.empty:
            continue
        pub = derive_published_date(w)
        out["margin_rows"] += store.upsert_weekly_margin(mg, as_of=w, published=pub)
        out["weeks_added"] += 1
        if i % 26 == 0 or i == len(missing):
            progress(f"  margin: {i}/{len(missing)} weeks (… as-of {w}, published {pub})")

    # --- 3) TOPIX incremental ---
    tpx = store.topix()
    tpx_from = tpx.index.max().date() + timedelta(days=1) if not tpx.empty else calendar[0]
    new_tpx = client.topix_bars(date_from=tpx_from, date_to=date.today())
    out["topix_rows"] = store.upsert_topix(new_tpx)
    if out["topix_rows"]:
        progress(f"  topix: +{out['topix_rows']} sessions")

    if not prices:
        return out

    # --- 4) daily bars ---
    with store.connect() as con:
        taishaku_hist = {r[0] for r in con.execute(
            "SELECT DISTINCT code FROM weekly_margin WHERE is_taishaku"
        ).fetchall()}
        master_taishaku = {r[0] for r in con.execute(
            "SELECT code FROM listed_info WHERE is_taishaku"
        ).fetchall()}
        first_margin = dict(con.execute(
            "SELECT code, min(as_of_date) FROM weekly_margin GROUP BY code"
        ).fetchall())
    cands = sorted(taishaku_hist | master_taishaku)

    ranges = store.price_date_ranges()
    had_any_prices = not ranges.empty
    max_stored = pd.to_datetime(ranges["last_date"]).max().date() if had_any_prices else None
    # Full-history pull list: candidates whose bars don't reach back to the window start *or* to
    # their own first margin week (bars start at IPO ≤ first margin print, so a post-2016 listing
    # whose bars reach its margin start is complete — no re-pull loop). The free-path Yahoo names
    # hold ~1y against a 2016 margin tape → correctly re-pulled at full depth.
    deep_enough: set[str] = set()
    if had_any_prices:
        for code, first in zip(ranges["code"], pd.to_datetime(ranges["first_date"]).dt.date):
            target = max(calendar[0], first_margin.get(code, calendar[0]))
            if first <= target + timedelta(days=40):
                deep_enough.add(code)
    new_codes = [c for c in cands if c not in deep_enough]
    if limit is not None and len(new_codes) > limit:
        progress(f"  bars: capping full-history pulls {len(new_codes)} → {limit} (--limit)")
        new_codes = new_codes[:limit]
    buf: list[pd.DataFrame] = []
    for i, c in enumerate(new_codes, 1):
        # 4-digit base code → common-stock code5 (the API keys on 5-char codes)
        bars = client.daily_bars(code=f"{c}0")
        if not bars.empty:
            buf.append(bars)
        if len(buf) >= _PRICE_FLUSH_CODES or (i == len(new_codes) and buf):
            out["bars_rows"] += store.upsert_prices(pd.concat(buf, ignore_index=True))
            buf = []
        if i % 100 == 0 or i == len(new_codes):
            progress(f"  bars: {i}/{len(new_codes)} new codes ({out['bars_rows']} rows)")
    out["codes_pulled"] = len(new_codes)

    # recent missing trading days for everything already stored (incl. the free-path names)
    if had_any_prices:
        tpx_all = store.topix()
        fill_days = [d.date() for d in tpx_all.index if d.date() > max_stored]
        for d in fill_days:
            n = store.upsert_prices(client.daily_bars(date=d))
            out["bars_rows"] += n
            out["days_filled"] += 1 if n else 0  # today's bars may not be published yet
        if fill_days:
            progress(f"  bars: filled {out['days_filled']}/{len(fill_days)} recent day(s)")

    return out
