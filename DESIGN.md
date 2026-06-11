# 踏み上げ Scanner — Design

**A short-squeeze setup scanner for the Tokyo Stock Exchange (TSE).**
Status: Tracks A + B built (2026-06-11) — FastAPI + Next.js view layer; 10y J-Quants history
backfilled. The §6 GO/NO-GO event study is the remaining gate. Last updated: 2026-06-11.

---

## 1. Thesis

Find TSE stocks where **shorts are crowded and trapped** (the *fuel*, read from Japan's
published margin-trading data) **and** price is **wound into a tight volatility coil**
(the *timing*), then catch the **upside break that runs the shorts** (the *ignition*).

This started as a Smart-Money-Concepts "liquidity sweep" idea (find low-range candles
coiling toward a manipulation move that sweeps resting liquidity). We deliberately pivoted:
a **short squeeze (踏み上げ) *is* that move** — trapped traders forced to act, a violent
run — except Japan lets us **read the trapped positioning off the margin tape** instead of
*inferring* it from chart shape (equal highs/lows) the way SMC/ICT traders do and are often
wrong about. Same mental model; stronger, TSE-native edge.

Direction is **long-only**: in a squeeze the trapped party is the shorts, so the move is
upside continuation (short covering = forced buying = self-reinforcing).

---

## 2. Architecture — lifecycle state machine

The scanner is **one pipeline** that buckets every universe name by the stage it's in. Setup
and trigger are the same pipeline at different stages (you can't detect the break without
already knowing where the fuel and the coil are).

| Stage | Condition | Use |
|---|---|---|
| **Crowded** | Fuel present (high days-to-cover, 信用倍率<1), not yet coiled | Background watch |
| **Primed** | Fuel **+** coiled (BB squeeze + tight box) | Active watchlist |
| **Igniting** | Primed **+** confirmed upside break *(spring variant = premium tag)* | **Act** |
| **Confirmed** | Follow-through **+** (lagging) 売残 dropping = shorts covering | Confirmation / post-trade |

A natural validation by-product: measure how often a **Primed** name actually progresses to
**Igniting**, and how **Igniting** resolves.

---

## 3. Signal definitions

### 3.1 Fuel — "are shorts crowded and trapped?" (the edge)
- **Primary gauge: days-to-cover** = 信用売残 ÷ average daily volume. High = shorts can't all
  exit quickly = trapped. This is the cleanest operationalization of the thesis.
- **Gate: 信用倍率 < 1.0** (買残 ÷ 売残 < 1 → more shorts than longs; "取り組み good").
- **Boosters (weights set by the backtest, not assumed):**
  - **逆日歩** present / rising (lending scarcity → acute squeeze pressure; fires *late*).
  - **信用売残 rising** across the ~3 weekly prints in the setup window (fuel *building*).
- Normalize against the name's *own* history (raw 売残 is meaningless cross-sectionally).

### 3.2 Coil — "is the spring wound?" (timing)
> Reframe: "low-spread candles" is a *symptom*; what precedes a squeeze is a *state*. A run of
> small bars can be a quiet drift; a tight range can be choppy big bars. We want the wound
> spring: volatility unusually low **for this name** *and* price contained in a tight band.
- **Bollinger-band squeeze:** BB-width at a **low percentile vs the ~120-session baseline**
  (TTM-squeeze convention: lowest band width in ~6 months).
- **AND** price contained in a **tight box** over the ~14-session setup window.
- Recent narrow bars = optional *final-tightening* confirmation (the original "low-spread
  candle", demoted from definition to confirmation).

### 3.3 Ignition — "has it fired?" (trigger, long-only)
- **Confirmed upside break:** **close above the box high** + **volume surge** + **wide-range
  (band-expanding) bar**. (Close, not an intraday poke; volume + width reject fakeouts.)
- **Spring sub-type (premium tag):** price first **dips below the box low** (sweeps sell-side
  stops, validates shorts so they add), then **reclaims and breaks up** — maximal trap. This
  is where the original "liquidity sweep" intuition lives, correctly placed on the *downside*.

---

## 4. Lookback windows

Two windows, because one number can't be both the yardstick and the thing measured:
- **Baseline ~120 sessions (~6mo):** reference for "compressed vs *this name's* normal."
- **Setup window ~14 sessions:** the active coil + fuel build. ≈ 3 weeks ≈ **~3 weekly margin
  prints** — the original "14 sessions", relabeled to its correct job.

Both tunable in Phase 1.

---

## 5. Data & universe

**Primary source: free, official, no subscription** — verified runnable 2026-06-10:
- **Universe:** JPX 東証上場銘柄一覧 `data_j.xls` (segments/sectors) — free Excel (`sources/jpx_universe.py`).
- **Per-issue weekly margin (the fuel):** JPX 銘柄別信用取引週末残高 `syumatsu<date>.pdf` — free PDF,
  ~3,700 issues, parses via `pdfplumber` text + one regex (`sources/jpx_margin.py`). Each row carries a
  **`B` = 貸借銘柄 flag** (so it *also* supplies the §5 universe gate) and 売残/買残 split 一般/制度; the
  header gives both the as-of (申込み) and publication date → **§6.1 lag handled for free**.
- **Adjusted OHLCV:** Yahoo via `yfinance` (`<code>.T`, `sources/yahoo.py`). (Stooq is now behind a JS
  anti-bot wall.)
- **Fresher fuel (Phase 4):** 日証金 (JSF) daily 貸借 + 逆日歩 — *official*, free, 貸借銘柄 only (`sources/jsf.py`).

**Scraping policy (other JP sources).** kabutan / minkabu / Yahoo-JP were evaluated (2026-06-10) and are
**not scraped**: their ToS prohibits automated/bulk access & redistribution (a permissive robots.txt ≠
ToS permission), their 信用残 numbers are **JS-rendered** (would need a headless browser per stock —
fragile, heavy, ~3,900 pages/wk), and they don't cleanly solve the history gap anyway. They are used as
**human eyeball references** while trading (PLAYBOOK §N), not as automated feeds; anything automated uses
**official publishers** (JPX, JSF).

**The one gap — backtest history. Resolved by subscription (2026-06-10).** Free JPX keeps only ~5
recent weekly PDFs (06.html 過去推移表 is *aggregate*, not per-issue), so years of point-in-time
per-issue margin aren't free. **J-Quants API V2 Standard is now active**: per-issue weekly margin
`/markets/margin-interest` verified live at **~10yr depth** (508 prints for 9755, 2016→2026) with
制度/一般 splits mapping 1:1 onto `weekly_margin`; auth is one dashboard key as `x-api-key` against
`https://api.jquants.com/v2` (`JQUANTS_API_KEY` in `.env`; the V1 email/password→token flow is
retired). Caveat: the payload's `Date` is the **as-of Friday only** — `published_date` must be
*derived* (§6.1; derivation rule + full wiring plan in **PLAN.md Track B**). The free path stays the
default feed until Track B lands, and keeps accumulating prints forward as the fallback.

**Universe filters:**
- **貸借銘柄 only** — *thesis requirement* (逆日歩 and 制度信用 short-trapping only exist here).
- All segments (Prime / Standard / Growth) — squeezes live in mid/small caps, not just Prime.
- **Turnover floor** (~≥¥100M/day avg) + **min price** — cut untradeable junk.
- Exclude ETF / REIT / ETN, freshly-listed (no baseline/history), 監理・整理 issues.

---

## 6. Validation — the go/no-go gate

**Point-in-time event study.** For every historical *Igniting* signal, compute the outcome
using **only data available on that day**, then aggregate.

**Two traps that invalidate most JP-margin backtests — handle both from day one:**
1. **Publication lag (the silent killer):** Friday's margin balance is *published* the
   following **Tue–Wed**. Backtests must use the margin print at its **publication date**, not
   its as-of (Friday) date. Acting on Friday data on Monday = trading on data that didn't
   exist = fake results.
2. **Stop-high no-fill:** the best squeezes lock ストップ高, and you **cannot buy a locked
   stock**. The backtest must assume those fills are missed.

Also: **point-in-time universe** (include delisted / de-margined names → no survivorship
bias). **Walk-forward** to tune parameters. Benchmark vs **TOPIX**.

### Success label — first-touch barriers (triple-barrier)
From the ignition entry, within ~10 sessions: **+target% hit first = win**, **−stop% hit first
= loss**, **neither = scratch**. Yields a *tradeable expectancy* (win-rate × payoff) and
matches how you'd actually trade a squeeze (scale into the spike, cut if wrong). Handles
spike-and-fade. Target/stop/horizon tunable.

---

## 7. Ranking

**Gate by stage** (Igniting > Primed > Coiling > Crowded), then **transparent weighted
within-stage score** of normalized fuel + coil-tightness + ignition-quality, **fuel weighted
highest** (it's the edge). Weights informed by the event study. **No ML in v1** — squeeze
events are too rare (overfitting) and you want to understand why a name ranks. ML is a later
upgrade once years of labeled events exist.

---

## 8. Stack & output

- **Python** end-to-end: pandas, **DuckDB** (+ Parquet) store, free JPX/Yahoo sources
  (+ J-Quants V2 client — PLAN.md Track B).
- **Batch:** `tse-scanner rescan` (ingest `--refresh` + scan in one shot) — manual via CLI or the
  web 再スキャン button; runs on the Oracle VPS. EOD cron is a later nicety — fuel only
  changes on the weekly Tue/Wed margin publication; price-based coil/ignition recompute per run.
- **View layer (PLAN.md Track A, shipped 2026-06-11):** FastAPI service (`tse-api`, loopback
  :8602) over the store + Next.js app (`tse-web`, :3002 via Tailscale): ranked list, stage
  buckets, **per-name price + margin charts** (seeing the coil/box/売残-trend visually is how
  trust is built), §15 research panel, rescan control. Replaced the original Streamlit dashboard
  (decision history: §14).
- *Later:* push-on-fresh-Igniting (Slack/Discord webhook, LINE Messaging API, or email).

---

## 9. Build roadmap — status (2026-06-11)

- **Phase 0 — Plumbing. DONE.** Free ingestion (`sources/`: JPX universe xls + weekly-margin PDF +
  Yahoo OHLCV), DuckDB point-in-time store with §6.1 publication-date reads, 貸借 gate from the
  margin `B` flag.
- **Phase 1 — Validate. CODE DONE, VERDICT PENDING.** fuel/coil/ignition, first-touch labels and
  the point-in-time event study are implemented and unit-tested — but the store holds ~1 weekly
  print, so the study can't say anything yet. The GO/NO-GO runs after the 10y J-Quants backfill
  (**PLAN.md Track B**). **← Be willing to kill the project here if the edge isn't real.**
- **Phase 2 — Productionize. DONE.** Lifecycle pipeline, transparent scoring, `rescan` batch.
- **Phase 3 — View layer. DONE.** FastAPI (`tse-api`) + Next.js (`tse-web`) — the view-layer
  split of **PLAN.md Track A**, which replaced the original (hardened) Streamlit dashboard (§14).
- **Phase 4 (optional, unstarted):** push alerts, 日証金 daily fuel (`sources/jsf.py` stub),
  regime filter, ML ranking — only after Phase 1 passes.

Phases 2–3 shipped ahead of Phase 1's verdict (the cockpit was wanted early); the gate still
applies — **no live size until the event study passes** (PLAYBOOK graduation ladder).

---

## 10. Parameters left for the backtest (starting guesses — tune in Phase 1)

| Parameter | Starting guess |
|---|---|
| Turnover floor | ≥ ¥100M/day (20d avg) |
| Min price | ≥ ¥100–300 |
| Price staleness gate | last bar ≤ ~10 calendar days before as-of (drops delisted names point-in-time) |
| Days-to-cover threshold | ≥ ~5 days |
| 信用倍率 gate | < 1.0 |
| Booster weights (逆日歩, 売残 trend) | TBD by event study |
| BB-width percentile cutoff | ≤ ~20th pct of 120d |
| Box tightness | height ≤ ~N×ATR / ~X% of price |
| Volume-surge multiple | ≥ ~2× 20d avg |
| Wide-bar definition | range ≥ ~1.5× ATR |
| Baseline / setup windows | ~120 / ~14 sessions |
| Target / stop / horizon | +12% / −6% / 10 sessions |
| Within-stage score weights | fuel-heavy; TBD |

---

## 11. Risks & honest caveats

1. **The edge may not exist on TSE daily** — Phase 1 is a real gate, not a formality.
2. **Fuel is stale** (weekly + publication lag); the daily price trigger carries freshness.
3. **Stop-high lock** = you may be unable to enter the best squeezes (fill reality).
4. **Few squeeze events** → overfitting risk (mitigate: walk-forward + transparent rules).
5. **Regime-dependent** — squeezes cluster; expect lumpy, non-stationary performance.
6. **Scanner ≠ execution system** — risk sizing, exits, and order routing are out of v1 scope.

---

## 12. Open items

- ~~J-Quants tiers / pricing / depth / auth~~ — **RESOLVED, then superseded by subscription
  (2026-06-10):** Standard is active and verified live (V2 single-key auth, ~10yr weekly margin,
  `/markets/short-sale-report`). Current facts live in §5; wiring plan in **PLAN.md Track B**.
- ~~日証金 daily 貸借: Phase 0 or 4?~~ — **Phase 4** (official, free 逆日歩/daily 貸借;
  `sources/jsf.py` stub). Chosen over scraping kabutan/minkabu (§5 policy).
- **Still open (check during Track B):** does J-Quants expose usable *point-in-time*
  listed/margin-eligibility history? Needed for survivorship-free universe reconstruction (§6);
  the free path only has the current snapshot.
- **Still open (from the 2026-06 優待 finding):** can a high 一般信用 short share (`neg_short` /
  `short_total`) or 優待 record-date proximity serve as a quantitative "hedged-cross fake fuel"
  flag? Test as an event-study *split*, never a score input, once Track B data lands.

---

## 13. Glossary (JP terms)

| Term | Meaning |
|---|---|
| 踏み上げ (fumiage) | Short squeeze |
| 貸借銘柄 | Loan-marginable issue (制度信用 short selling possible; only place 逆日歩 occurs) |
| 信用売残 / 買残 | Margin short balance / margin long balance |
| 信用倍率 | Credit ratio = 買残 ÷ 売残 (< 1 = short-heavy) |
| 逆日歩 (gyaku-hibu) | Premium shorts pay when lendable shares are scarce |
| 信用取引残高 | Margin trading outstanding (TSE weekly, per issue) |
| 日証金 | Japan Securities Finance (daily 貸借 loan balances, 逆日歩) |
| ストップ高 / 安 | Limit-up / limit-down (price-limit lock) |
| 監理・整理 | Supervision / liquidation post (delisting-risk issues) |

---

## 14. Decision log (what we chose and why)

- **Detect the full sequence as a lifecycle state machine** (not setup-only or trigger-only) —
  setup and trigger share inputs; bucketing by stage gives both watchlist and trigger.
- **Pivoted SMC liquidity-sweep → TSE-native short squeeze** — Japan publishes the positioning
  ICT traders only infer; stronger edge, same mental model.
- **Short-squeeze + compression** (not price-limit follow-through / RS / PBR-reform) — keeps
  the original "trapped liquidity → violent move" intuition.
- **J-Quants paid** — only low-pain path to historical margin data needed to backtest.
- **貸借銘柄, liquid mid/small + turnover floor** — squeezes live in mid/small caps; floor cuts
  untradeable junk.
- **Days-to-cover primary + 倍率<1 gate** — best captures "trapped"; boosters' weights earned
  in the backtest, not assumed.
- **BB squeeze + box** (not ATR-only / NR7 / box-only) — state-based wound spring, not a
  bar-level symptom; pairs with a band-expansion ignition.
- **Split windows (~120d baseline + ~14d setup)** — "14" was doing two incompatible jobs.
- **Upside break + spring tag** — squeeze runs *up*; the sweep is a downside shakeout, fold it
  in as a premium sub-type.
- **Point-in-time event study, publication-date margin, stop-high no-fill** — or the backtest
  lies.
- **First-touch barriers** — tradeable expectancy, not a misleading fixed-horizon return.
- **Gate-by-stage + transparent score, no ML v1** — interpretable; too few events for ML.
- **Python + local batch + DuckDB + Streamlit** — one ecosystem. (The original notebooks-first
  validation plan was superseded: signals + event study went straight to `src/` with unit tests.)
- **Deep research = display-only overlay (§15), never a score input** — the quantitative scanner
  stays falsifiable; fundamentals are the human's discretionary layer at entry/exit.
- **Research agent writes files, wrapper owns DuckDB** — DuckDB is single-writer across
  processes; an agent holding a write connection mid-run would fight the API/scan.
- **Streamlit vs Next.js — re-decided 2026-06-11.** Original call (2026-06-10): stay on
  Streamlit (single-user Tailscale cockpit, UI in-process with the store), harden in place,
  re-open only on documented triggers — one of which was *trust in the cockpit*. **That trigger
  then fired:** two incidents in two days (a restart-killed websocket silently dropping clicks;
  Streamlit's rerun-resets-module-globals model hiding a live rescan). Both were root-caused,
  fixed and regression-tested — but both are Streamlit-execution-model failure modes, and the
  user asked for Next.js twice. Decision: **split the view layer** — FastAPI over `Store` +
  Next.js cockpit, Python core untouched. **Executed 2026-06-11:** `tse-api` + `tse-web` shipped
  and the Streamlit dashboard was fully retired after reaching parity (**PLAN.md Track A**).
  Hardening that carried over: systemd units, `Store.connect` lock-retry, the detached-spawn
  process registry, the stale-scan banner.

---

## 15. Deep research — on-demand fundamental memo per ticker

**Why.** The scanner reads *positioning* (fuel) and *price state* (coil/ignition) — it cannot know
*why* shorts are short, whether that thesis is broken, or whether a 公募増資 is about to kill the
squeeze. Entries/exits combine both: technicals say *when*, fundamentals say *whether to trust it and
how long to hold*. Research is **on-demand per ticker** (human-triggered), not batch.

**Architecture.** `tse-scanner research <code>` spawns one headless `claude -p` run (repo cwd):
- **Orchestrator** = `TSE_RESEARCH_MODEL` (default `claude-fable-5`; opus via env) — synthesis only.
- **Ingest** = up to **4 parallel Sonnet collector subagents** (`.claude/agents/research-collector.md`,
  `model: sonnet`) — raw pages stay in collector contexts; only distilled reports (persisted to
  `raw/`) return to the orchestrator. That's the token split: cheap model reads the web, expensive
  model only synthesizes.
- **Playbook** = `.claude/skills/deep-research/SKILL.md`: memo template, source checklist, collector
  briefs, output contract.
- The wrapper pre-builds a **context pack** (scan row, margin history, params, best-effort yfinance
  snapshot) so the agent starts from ground truth; un-scanned tickers run with a "no scan context"
  warning rather than failing.

**Memo template (squeeze-specific, not generic equity research).** ① snapshot; ② **the bear case —
why are shorts short, and is it intact/weakening/broken** (incl. 空売り残高報告 filers via karauri.net);
③ **squeeze-killers** — 公募増資/MSワラント/CB/shelf/cash-runway dilution risk; ④ catalyst calendar
(決算 date vs the 10-session horizon, revisions, 大量保有, buybacks, index events); ⑤ float & holders
reality; ⑥ fundamental trajectory; ⑦ valuation headroom; ⑧ **verdict** `SUPPORTS | NEUTRAL | OPPOSES`
+ confidence + explicit entry/exit implications keyed to the ±12%/−6%/10-session mechanics;
⑨ sources — **no URL, no claim**.

**Sources.** WebSearch/WebFetch with a curated checklist: official publishers first (TDnet, JPX,
company IR), then aggregators (karauri.net, IR BANK, 株探 news pages). This does **not** breach the
§5 scraping policy: §5 bans *bulk automated pipeline feeds* (~3,900 pages/wk); research is a
human-triggered, single-name, ~dozen-page read — the "human eyeball reference" tier with an AI
reading assistant. No new API keys, no headless browser; anything bulk stays official-only.

**Storage contract.** Agent writes **files only** under `data/research/<code>/<YYYY-MM-DD>/`:
`memo.md`, `verdict.json` (fixed schema: verdict, confidence, one_liner, squeeze_killers[],
catalysts[{date,event}], sources[]), `raw/<collector>.md`. The **wrapper owns all DuckDB writes**:
`research` table row `status='running'` on spawn → parse artifacts on exit → `done` (verdict fields +
full `memo_md`) or `failed` + error. One row per (code, research_date); history accumulates.

**Rails.** TTL: skip if a `done` memo is < `TSE_RESEARCH_TTL_DAYS` (5) old, `--force` overrides;
`--max-turns 50`; 15-min wall-clock kill; ≤4 collectors, bounded briefs; cost/turns/duration captured
from the `--output-format json` envelope onto the row; permissions allowlist
`WebSearch,WebFetch,Read,Write,Glob,Task` — **no Bash, no Edit, no DB access, no
--dangerously-skip-permissions**. Blast radius = write files + browse.

**Web UI.** Ranked table gains a display-only `research` column (💚/⚪/🔴, latest verdict per
code). Per-name detail, 4 states: none (Run button) → running (SWR polls `/research/{code}` every
5s; >20-min-old `running` rows surfaced as failed) → done (badge + one-liner + catalysts + memo +
cost caption + force re-run) / failed. Spawn = `POST /research/{code}` → detached
`Popen([... "research", code], start_new_session=True)`, double-spawn guarded by the running row.

**Testing.** Wrapper logic (pack build, TTL, ingest, failure paths) unit-tested against a **fake
`claude` stub binary** emitting a canned JSON envelope + fixture artifacts — zero API spend; one real
end-to-end run is the acceptance test.
