# Next session plan — written 2026-06-11

> **STATUS: both tracks executed 2026-06-11.** Track A (FastAPI `tse-api` + Next.js `tse-web`)
> shipped and the Streamlit dashboard was fully retired at parity. Track B (J-Quants Standard
> ingestion) wired + 10y backfilled; the §10 event-study GO/NO-GO is the one remaining gate.
> The text below is the original plan, kept as the historical record — operational state now
> lives in README.md / DESIGN.md.

Two tracks, both scoped and ready. **Decide order at session start** (see Open decisions).

## Where things stand (end of 2026-06-11 session)

- Dashboard: Streamlit on :8601 as systemd user service `tse-dashboard`; hardened (DuckDB
  lock-retry, cache_resource process registry, rescan button w/ live log tail, stale-scan
  banner, Deploy chrome hidden). 35 tests + ruff green. The two UI-trust incidents (dead
  websocket click, rerun-reset module global) are root-caused and regression-tested
  (tests/test_dashboard.py).
- User has signaled **twice** they want a Next.js front-end; offer made (parallel build,
  Streamlit stays until parity). DESIGN.md §14 trust-trigger has effectively fired —
  awaiting explicit "build it" before starting Track A.
- J-Quants **Standard paid since 2026-06-10**; V2 key verified live in `.env`
  (`JQUANTS_API_KEY`, header `x-api-key`, base `https://api.jquants.com/v2`). 9755
  margin-interest → 508 weekly rows 2016→2026. Ingestion still on the free path.
- Latest scan (2026-06-10 data + 6/11 partial bars): 13 CROWDED / 0 PRIMED. The June CROWDED
  cohort is dominated by 優待 restaurant/retail names into the 6/30 record date — research
  proved 8179's shorts are hedged 優待クロス (OPPOSES); 9755 NEUTRAL (activist supply).

## Track A — view-layer split: FastAPI + Next.js (user-requested)

Keep the entire Python core (store/signals/backtest/research). Swap only presentation.

1. `src/tse_scanner/api.py` — FastAPI over `Store`, loopback-only `127.0.0.1:8602`:
   - GET `/scan` (latest table + freshness dates), `/summary`, `/prices/{code}`,
     `/margin/{code}`, `/research/{code}`, `/research` (verdict list)
   - POST `/rescan` → 202, spawns the same detached `tse-scanner rescan` (idempotent if
     running); GET `/rescan/status` → `{running, started_at, last_log_line, failed_rc}`
   - POST `/research/{code}?force=` + status — mirror of the dashboard spawn pattern.
   - DuckDB single-writer is already handled (Store.connect retry); API spawns children,
     never holds long connections.
2. `web/` — Next.js (App Router) on `0.0.0.0:3002` (3001 + 8501 are taken by job-hunt);
   server components fetch the loopback API; SWR `refreshInterval` ~5s only while a
   rescan/research is running. Charts: TradingView lightweight-charts (candles + box lines
   + margin series). Keep deps minimal.
3. systemd user services `tse-api` + `tse-web` (pattern: existing `tse-dashboard.service`;
   linger already enabled). Tailscale URL `http://100.89.178.115:3002`.
4. Parity checklist before retiring Streamlit: freshness header + §6.1 publication-lag
   caption + stale-data banner; stage buckets; ranked table (TradingView links, verdict
   emoji, progress bars); per-name candles + coil box + 信用残 chart; research panel all 4
   states + force re-run; rescan control with live tail + failure banner.
5. Cutover: run both a few days → `systemctl --user disable --now tse-dashboard`. Keep
   app.py + its tests (they pin Store behavior).

## Track B — wire J-Quants Standard ingestion (the data edge)

Unblocks: full 貸借 universe (no 250-name Yahoo cap), 10y weekly margin history (rising
booster + real backtest), survivorship-correct listed history.

1. Implement `src/tse_scanner/data/jquants.py` (stubs exist): V2 client — `x-api-key`
   header, `pagination_key` loop, 429 backoff, 5-digit→4-digit code mapping (`97550`→`9755`).
   Endpoints: `/equities/master`, `/markets/margin-interest`, daily bars (**verify exact
   path/fields at https://jpx-jquants.com/en/spec before coding**).
2. Weekly margin backfill: **bulk by date** (`?date=` = one call per week ≈ 520 calls for
   10y), NOT per-code (~3.5k calls). Map `ShrtStdVol/ShrtNegVol/LongStdVol/LongNegVol` →
   `sys_short/neg_short/sys_long/neg_long` (verified 1:1 with store schema).
3. **published_date derivation** (§6.1 — payload has as-of Friday only, no publication
   field): rule = 2nd business day after as-of (Tue, slips to Wed on JP holidays).
   Cross-check against the free-path PDF for the overlap week (2026-06-05 → published
   2026-06-09 ✓). When in doubt derive LATER (conservative = no lookahead). Decide: small
   JP-holiday table vs `jpholiday` dep.
4. Daily bars backfill for the 貸借 universe (staged, respect rate limits), then
   `TSE_DATA_SOURCE=jquants` switch in config + `ingest --source jquants` handler
   (currently a stub print). Keep free path working as fallback.
5. Rerun scan (expect wider CROWDED set; rising booster live after 3+ prints accumulate —
   backfill gives it instantly), then **run the §10 event study over 10y** → win-rate /
   expectancy / PRIMED→IGNITING rates. This is the project's GO/NO-GO gate.
6. New signal idea from the 8179 finding, testable with this data: 優待クロス hedged shorts
   live mostly in 一般信用 → a high `neg_short` share (and/or 優待 record-date proximity)
   may be a cheap quantitative proxy for "fake fuel". Add to event study as a split, not a
   score input, until it earns weight.

## Quick wins (either session, ~minutes each)

- `make research code=2752` — フジオフード, top DtC (15.4) but suspect 優待クロス like 8179
  (~$5, decides whether the board's #2 is real).
- Optional: "intraday partial bar" badge when rescan ran during JST session.

## Open decisions (ask at session start)

1. **Track order.** A first if Streamlit-distrust is blocking daily use; B first if the
   trading edge matters more this June window (my lean: B — it's the falsifiable edge and
   the paid plan is burning; A is scoped and ready the moment you say go).
2. Track B step 3: holiday handling for published_date (tiny table vs `jpholiday` dep).
3. After A ships: retire Streamlit immediately at parity, or keep both indefinitely?
