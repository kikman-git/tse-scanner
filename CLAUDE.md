# CLAUDE.md

Working manual for this repo. **Code is the source of truth**; this file captures the *why* the
code can't — the thesis, the discipline that keeps the backtest honest, and the rails. Read
`README.md` for the front-page pitch and quickstart, `PLAYBOOK.md` for how the human trades the
output.

## The thesis (踏み上げ short-squeeze scanner)

Find TSE names where **shorts are crowded and trapped** (the *fuel* — read off Japan's published
margin tape, not inferred from chart shape) **and** price is **wound into a tight volatility coil**
(the *timing*), then catch the **upside break that runs the shorts** (the *ignition*). The edge over
SMC/ICT "liquidity sweep" trading: Japan *publishes* the trapped positioning instead of making us
guess it from equal highs/lows.

**Direction is long-only** — in a squeeze the trapped party is the shorts, so short-covering is
forced buying and the move is self-reinforcing upside.

## Architecture — one pipeline, a lifecycle state machine

Setup and trigger are the *same* pipeline at different stages (you can't detect the break without
already knowing where the fuel and coil are). Every universe name is bucketed by stage.

**De-gated ladder (2026-06-12).** The original ladder required fuel to reach Primed/Igniting; over
10y of history that joint AND fired **once** (unfalsifiable — see Validation). Now **coil+ignition
drive the actionable stages and fuel is a ranking overlay** (`Params.require_fuel=False`; `True`
restores the legacy squeeze-only ladder). Whether fuel adds edge is *measured* by the event study's
fuel-quartile cohort splits, not assumed.

| Stage | Condition | Use |
|---|---|---|
| **Crowded** | Fuel present (top-decile DtC, 信用倍率<1), not yet coiled | Squeeze background watch |
| **Primed** | Coiled (BB squeeze + tight box) — fuel optional, ranks within stage | Active watchlist |
| **Igniting** | Coiled **+** confirmed upside break *(spring variant = premium tag)* | **Act** |
| **Confirmed** | Follow-through **+** (lagging) 売残 dropping = shorts covering | Confirmation / post-trade |

`pipeline.py` composes the three signal booleans into this ladder; `scan.py` runs it over the
universe and writes `scan_results`.

## Signals — the three booleans `scan` composes

- **Fuel — "are shorts crowded and trapped?" (the edge hypothesis, under test).** `signals/fuel.py`
  - Primary gauge: **days-to-cover** = 信用売残 ÷ avg daily volume. High = shorts can't all exit.
    DTC is ADV-deflated (cross-sectionally comparable), so the gate is its **percentile within the
    day's universe**: `dtc_pctile ≥ 0.90` + an absolute floor `DtC ≥ 1.0`. (The old absolute
    `DtC ≥ 5` sat above the 90th pctile of its own pool — 13/1,633 names — and starved the study.)
  - Gate: **信用倍率 < 1.0** (買残 ÷ 売残 < 1 → more shorts than longs).
  - Boosters (weights earned by the event study, *not* assumed): 逆日歩 present/rising; 売残 rising
    across the ~3 setup-window prints.
  - Raw 売残 is still meaningless cross-sectionally — only deflated measures (DtC) get ranked.
- **Coil — "is the spring wound?" (timing).** `signals/coil.py`
  - BB-width at a **low percentile vs the ~120-session baseline** AND price contained in a **tight
    box** over the ~14-session setup window. (A run of small bars is a *symptom*; we want the state.)
- **Ignition — "has it fired?" (trigger, long-only).** `signals/ignition.py`
  - Confirmed break: **close above box high** + **volume surge** + **wide-range bar** (close not an
    intraday poke; volume+width reject fakeouts).
  - **Spring sub-type (premium):** dips below box low first (sweeps stops, validates shorts so they
    add), then reclaims and breaks up — maximal trap, ranks higher.
  - **Sweep sub-type (early entry, 2026-06-12):** the sweep-*reversal bar itself* — low takes the
    box low, close reclaims it, long lower wick (≥50% of range, close in upper 40%). Entry at the
    reversal, **structural stop under the wick low** (labels use per-event barriers, target scaled
    to keep the R:R). Confirmation-vs-earliness is an event-study cohort question (sweep vs break
    vs spring), not an assumption. Short mirror deliberately not built (borrow reality — see
    PLAYBOOK; user is long-mostly).

Two lookback windows: **~120 sessions** (baseline = "compressed vs *this name's* normal") and **~14
sessions** (the active coil + fuel build ≈ 3 weekly margin prints).

## Data & universe — point-in-time discipline is sacred

Sources (`data/sources/`): JPX 東証上場銘柄一覧 `data_j.xls` (universe), JPX 銘柄別信用取引週末残高 PDF
(**the fuel**; each row's `B` flag = 貸借銘柄, doubles as the universe gate), Yahoo `yfinance` OHLCV,
**J-Quants API V2 Standard** (paid — 10y weekly-margin history + full-universe bars + `/fins/summary`
決算短信+業績予想修正 for the revision setup; sync via `ingest --fins`), 日証金/JSF (daily 逆日歩,
stubbed Phase 4). Source switch: `TSE_DATA_SOURCE` = `free` (default) | `paid` | `jquants`.
`/equities/earnings-calendar` is a forward snapshot only (no history) — backtests derive expected
announcement windows from each name's own disclosure anniversaries (`signals/revision.py`).

**Universe filters:** 貸借銘柄 **only** (thesis requirement — 逆日歩/制度信用 short-trapping only exists
here), all segments (Prime/Standard/Growth — squeezes live in mid/small caps), turnover floor
(~≥¥100M/day) + min price, exclude ETF/REIT/ETN, freshly-listed, 監理・整理.

**Three traps that invalidate most JP backtests — handled from day one, never regress these:**
1. **Publication lag (the silent killer).** Friday's margin balance is *published* the following
   **Tue–Wed**. Every read must use the print at its **publication date**, not its as-of (Friday)
   date — acting on Friday data on Monday is trading on data that didn't exist. The store enforces
   this; J-Quants V2 has no publication field so it's *derived* (`data/jquants.py`
   `derive_published_date`: 2nd business day after as-of, slips on JP holidays). Same discipline
   for disclosures: `fins_history` defaults to **strictly-before** as-of (決算/修正 land at/after
   the close).
2. **Stop-high no-fill.** The best squeezes lock ストップ高 and **you cannot buy a locked stock** —
   `backtest/labels.py` assumes those fills are missed.
3. **Gap-through-barrier fills.** A bar that *opens* beyond a barrier fills at its open — an
   overnight 決算 gap down through the stop loses more than −stop%. `labels.py` is gap-aware;
   barriers are per-event overridable (the sweep trigger's structural stop).

Also: **point-in-time universe** (include delisted/de-margined names → no survivorship bias),
walk-forward tuning, benchmark vs **TOPIX**.

**Scraping policy:** only **official publishers** are automated (JPX, JSF). kabutan/minkabu/Yahoo-JP
are **not scraped** (ToS prohibits bulk automated access/redistribution; their 信用残 is JS-rendered).
Aggregators (kabutan, IR BANK, karauri, みんかぶ, トレーダーズ・ウェブ) are fine as **single-name,
human-eyeball reads** — that's the deep-research tier below, not a bulk feed.

## Validation — the GO/NO-GO gate (the one open item)

`backtest/event_study.py` + `labels.py`. A **point-in-time event study**: for every historical
*Igniting* signal, compute the outcome with only data available that day, then aggregate. Success =
**first-touch triple-barrier** (from entry, within ~10 sessions: +target% first = win, −stop% first
= loss, else scratch). Starting guesses: **+12% / −6% / 10 sessions** (all tunable; see `config.py`).

**First verdict (2026-06-12): UNTESTABLE at the original gates — n=1 event in 10y.** Not "edge
dead": the fuel∧coil∧break AND had near-zero base rate. Hence the de-gated ladder: the study now
runs over *all* coil breakouts and reports **cohort splits** (fuel-gated vs no-fuel, DtC-percentile
quartiles, spring vs break, 優待クロス proxy) plus a per-event parquet
(`data/backtest_events_*.parquet`) for offline re-slicing. The A/B question replaces the single
gate: *do fuel-rich breakouts outperform fuel-less ones?* If yes, fuel earns its weights; if no,
the squeeze overlay dies and the coil-breakout stands on the headline numbers.

**This is the project's GO/NO-GO gate.** The edge is still a *hypothesis* until a cohort clears a
win-rate/expectancy bar. **No live size until it passes** (PLAYBOOK graduation ladder) — be willing
to kill the project here if the edge isn't real.

## Ranking

Gate-by-stage (Igniting > Primed > Coiling > Crowded), then a **transparent weighted within-stage
score** of normalized fuel + coil-tightness + ignition-quality, **fuel weighted highest** (it's the
edge). **No ML in v1** — squeeze events are too rare (overfitting) and you want to see *why* a name
ranks.

## Stack, layout & the single-writer rule

Python end-to-end: pandas, **DuckDB (+ Parquet)** store. CLI: `tse-scanner ingest · universe · scan ·
rescan · backtest · research`. View layer = **FastAPI** (`tse-api`, loopback `:8602`) over the store
+ **Next.js** (`tse-web`, `:3002` via Tailscale); SWR polls every ~5s only while a rescan/research
runs.

**DuckDB is single-writer across processes.** `Store.connect` does lock-retry; spawners (the API)
launch detached children and **never hold long write connections**. This is why the research agent
writes *files* and the wrapper owns all DB writes (below).

```
src/tse_scanner/
  config.py          # Params — every tunable, with starting guesses (single source of truth)
  cli.py             # argparse CLI: ingest · universe · scan · rescan · backtest · research
  pipeline.py        # lifecycle state machine: Crowded → Primed → Igniting → Confirmed
  scan.py            # composes fuel/coil/ignition → stage + score → scan_results
  research.py        # on-demand deep fundamental memo via headless `claude` run
  api.py             # FastAPI view layer over the store — loopback :8602
  data/
    store.py         # DuckDB + Parquet point-in-time store, publication/disclosure-date aware
    universe.py      # 貸借銘柄 universe builder
    jquants.py       # J-Quants V2 client + published_date derivation + fins_summary converter
    jquants_sync.py  # idempotent backfill + weekly sync (margin, bars, TOPIX, fins)
    sources/         # free official feeds: jpx_universe, jpx_margin (the fuel), yahoo, jsf
  signals/           # fuel (the edge) · coil (timing) · ignition (trigger) · revision (上方修正圧)
  backtest/          # labels (gap-aware first-touch triple-barrier) · event_study (GO/NO-GO + cohorts)
.claude/
  skills/deep-research/SKILL.md   # research playbook: memo template, collector briefs, verdict schema
  agents/research-collector.md    # Sonnet ingest subagent (raw pages never reach the synthesis model)
web/                 # Next.js app (App Router); lib/types.ts mirrors the API payloads by hand
tests/               # pytest; research wrapper tested against a fake `claude` stub (zero API spend)
```

## Deep research — on-demand per-ticker fundamental memo

The scanner reads *positioning* + *price state*; it can't know **why** shorts are short or whether a
公募増資 is about to kill the squeeze. `tse-scanner research <code>` (or the 🔬 button) spawns one
headless `claude -p` run:

- **Orchestrator** = `TSE_RESEARCH_MODEL` (default `claude-fable-5`) — synthesis only. **Ingest** =
  up to **4 parallel Sonnet `research-collector` subagents**; raw web pages stay in their contexts,
  only distilled cited reports return. Cheap model reads the web, expensive model synthesizes.
- Output: files only under `data/research/<code>/<YYYY-MM-DD>/` — `memo.md`, `verdict.json`,
  `raw/<collector>.md`. The **wrapper owns all DuckDB writes** (running row on spawn → done/failed on
  exit). `verdict.json` schema: `verdict` (SUPPORTS|NEUTRAL|OPPOSES), `confidence`, `one_liner`,
  `squeeze_killers[]`, `catalysts[]`, `analyst_targets[]` (broker target-price cluster = the
  exit-ceiling reference), `sources[]`.
- **The verdict is a display-only overlay for the human — it NEVER feeds the scan score.** That's
  what keeps the quantitative scanner falsifiable; fundamentals are the discretionary entry/exit layer.
- **Rails:** TTL skip if a `done` memo is < `TSE_RESEARCH_TTL_DAYS` (5d) old (`--force` overrides),
  `--max-turns 50`, 15-min wall-clock kill, tool allowlist `WebSearch, WebFetch, Read, Write, Glob,
  Task/Agent` — **no Bash, no Edit, no DB, no `--dangerously-skip-permissions`**. Blast radius =
  write files + browse.

## Commands

```bash
make test        # uv run pytest -q
make lint        # uv run ruff check src tests
make scan        # tse-scanner scan   (or `make rescan` equivalent: ingest --refresh + scan)
make ingest      # tse-scanner ingest --refresh   (--refresh or the cached weekly PDF is reused forever)
make research code=7203          # deep memo for one ticker
cd web && npx tsc --noEmit       # typecheck the Next.js app before relying on a UI change
```

On the VPS the UI runs as two systemd user services: `systemctl --user restart tse-api tse-web`
(logs via `journalctl --user -u tse-web`). The free data path needs **no credentials**; `.env` holds
only `JQUANTS_API_KEY` (paid history) and the research knobs — see `.env.example`.

## Red lines (don't regress these)

- **Point-in-time / no-lookahead is sacred** — publication-date reads, stop-high no-fill. Breaking
  either makes the backtest lie.
- **DuckDB single-writer** — agents/UI spawn detached processes; never hold a long write connection.
- **Research verdict is display-only** — it must never become a scan-score input.
- **Only official sources are automated** — aggregators are single-name human-eyeball/research reads.
- **No live size until the event-study gate passes.**
