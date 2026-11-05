# tse-scanner

**踏み上げ (fumiage) short-squeeze setup scanner for the Tokyo Stock Exchange.** Find TSE names where shorts are **crowded and trapped** (read off Japan's published margin tape) **and** price is **wound into a tight coil**, then catch the **upside break that runs the shorts**. A personal, single-user research tool for a discretionary, mostly-long TSE trader: Python core with a DuckDB store, a FastAPI view layer and a Next.js UI, run as two systemd user services on one VPS.

Two docs go with this repo:

| Doc | Owns |
|---|---|
| **[`CLAUDE.md`](./CLAUDE.md)** | How the **scanner** works: thesis, signals, the point-in-time validation gate, stack, deep research, and the red lines. |
| **[`PLAYBOOK.md`](./PLAYBOOK.md)** | How the human **trades** the names it surfaces (sizing, entry, stops, exits, journal). |

> **Status:** FastAPI + Next.js view layer shipped; the 10y J-Quants Standard history is backfilled.
> The point-in-time event study has returned its **GO/NO-GO verdict (2026-06-15): NO-GO** — squeeze, 期日 (deadline) and
> 上方修正 (revision) were all tested and none beats TOPIX (revision has positive expectancy, but it is beta, not alpha).
> **No live size** until a setup clears that gate (PLAYBOOK graduation ladder).

## How it works

```
 JPX universe + weekly margin PDF ─┐
 Yahoo OHLCV / J-Quants V2 bars  ──┴─► ingest ─► data/tse.duckdb ─► scan ─► scan_results
                                       (publication-date aware)                    │
 browser ◄─ Next.js :3002 ◄─ /api proxy ◄─ FastAPI 127.0.0.1:8602 ◄────────────────┘
                                              └─ spawns detached `rescan` / `research <code>` children
```

- **One pipeline, a lifecycle state machine.** Coil + ignition drive the actionable stages; fuel is a ranking overlay (`Params.require_fuel=False`, de-gated 2026-06-12).

  | Stage | Condition | Use |
  |---|---|---|
  | Crowded | fuel present (top-decile days-to-cover, 信用倍率 < 1), not yet coiled | background watch |
  | Primed | coiled (BB squeeze + tight box); fuel optional, ranks within the stage | active watchlist |
  | Igniting | coiled + confirmed upside break (spring variant = premium tag; sweep = early entry) | act |
  | Confirmed | follow-through + 売残 dropping (shorts covering) | confirmation |

- **Signals:** fuel (`signals/fuel.py`, days-to-cover percentile + 信用倍率), coil (`coil.py`, BB-width percentile + tight box), ignition (`ignition.py`), plus `revision.py` (上方修正), `deadline.py` (期日) and `targets.py` (technical exit map). Long-only. No ML in v1; ranking is a transparent weighted score within each stage.
- **Display-only overlays:** the exit map (T1/T2, 節目, 青天井) and the research verdict never feed stage or score.
- **API (loopback, no auth):** `GET /health /summary /scan /prices/{code} /margin/{code} /research /research/{code} /rescan/status /jobs /jobs/log /playbook`; `POST /rescan` and `POST /research/{code}` return 202 and spawn a detached CLI child. UI pages: scan view, per-name detail, jobs, playbook (renders `PLAYBOOK.md` verbatim).

### Deep research

The scanner reads *positioning*; it can't know **why** shorts are short or whether a 公募増資 is about to kill the squeeze. `tse-scanner research <code>` (or the 🔬 button in the web UI) spawns one headless `claude` run — Sonnet collectors fetch and distill filings/news/short positions in parallel, the synthesis model writes a squeeze-specific memo and a `SUPPORTS / NEUTRAL / OPPOSES` verdict (with the broker target-price cluster as an exit-ceiling reference). Display-only overlay: it never feeds the scan score. Rails: up to 4 parallel collectors, tool allowlist `WebSearch, WebFetch, Read, Write, Glob, Task/Agent` (no Bash, no Edit, no DB), `--max-turns`, wall-clock kill, TTL skip. The agent writes files under `data/research/<code>/<date>/`; the wrapper owns all DuckDB writes. See [`CLAUDE.md`](./CLAUDE.md) for the architecture and rails.

### Three traps that invalidate most JP-margin backtests — handled from day one

1. **Publication lag** — Friday's margin balance is *published* the following Tue–Wed. Use the print at its **publication date**, not its as-of date, or the backtest trades on data that didn't exist.
2. **Stop-high no-fill** — the best squeezes lock ストップ高 and you *cannot buy a locked stock*. The backtest must assume those fills are missed.
3. **Gap-through-barrier fills** — a bar that *opens* beyond a barrier fills at its open, so an overnight 決算 gap through the stop loses more than the stop; `backtest/labels.py` is gap-aware.

## Setup

Needs Python ≥ 3.12 with [uv](https://docs.astral.sh/uv/) (`python3 -m venv` is broken on this box), and Node.js + npm for the UI (Next.js 15 / React 19; the VPS runs Node 24). Deep research also needs the `claude` CLI installed and logged in.

```bash
uv sync --extra dev --extra api         # create .venv and install deps (= `make install`)
cp .env.example .env                    # optional: only for the paid J-Quants pull and the research knobs
cd web && npm install                   # UI dependencies
```

The free path needs **no credentials**. `.env` is only for the paid J-Quants V2 history pull (`JQUANTS_API_KEY`, dashboard-issued — V2 retired the email/password token flow) and for tuning the deep-research knobs (`TSE_RESEARCH_MODEL`, TTL, timeout). See `.env.example`.

The CLI shell uses only the stdlib, so `python -m tse_scanner --help` works before any install.

## Run / deploy

```bash
uv run tse-scanner rescan               # ingest --refresh + scan in one shot (the web 再スキャン button)
uv run uvicorn tse_scanner.api:app --host 127.0.0.1 --port 8602   # API; then `cd web && npm run dev` (UI on :3002)
```

| CLI command | Does |
|---|---|
| `ingest [--source free/jquants/paid] [--refresh] [--limit N] [--period 1y] [--no-prices] [--fins]` | free: JPX universe + weekly margin + Yahoo OHLCV; jquants/paid: J-Quants V2 sync (`--fins` adds 決算/業績予想修正, first run ≈ 4k calls) |
| `universe` / `scan` | build the 貸借銘柄 universe / bucket it by stage, score, persist to `scan_results` |
| `rescan` | `ingest --refresh` + `scan` (what the web button spawns) |
| `backtest [--setup squeeze/deadline/revision] [--cadence daily/weekly] [--start D] [--end D]` | point-in-time event study (the GO/NO-GO gate); `revision` needs `ingest --fins` |
| `research <code> [--force]` | deep fundamental memo for one ticker |

On the VPS the UI runs as two systemd user services (units live in `~/.config/systemd/user/`, not in this repo):

| Unit | Runs | Listens |
|---|---|---|
| `tse-api.service` | `.venv/bin/uvicorn tse_scanner.api:app --host 127.0.0.1 --port 8602`, cwd = repo root, `.env` loaded in-process | `127.0.0.1:8602` (loopback only) |
| `tse-web.service` | `npm start` in `web/` (= `next start -p 3002 -H 0.0.0.0`), `NODE_ENV=production`, `TSE_API_URL=http://127.0.0.1:8602`, `Wants=tse-api.service` | `0.0.0.0:3002` |

- URL: **https://openclaw-vps.tail0d53dc.ts.net:8450/** (tailscale serve `:8450` → `127.0.0.1:3002`; also a card on the `/apps` hub).
- `systemctl --user restart tse-api tse-web` · logs via `journalctl --user -u tse-web` (and `-u tse-api`). Both units are `Restart=always`.
- Deploy a UI change: `cd web && npm run build && systemctl --user restart tse-web`. Python change: `systemctl --user restart tse-api`.
- OpenClaw gateway restarts and reboots wipe tailscale serve mappings; `tailscale-serve-apps.timer` re-asserts them within ~3 minutes, `systemctl --user start tailscale-serve-apps.service` forces it.
- **`next start` needs `next build` first.** `tse-web` once crash-looped 476 times in a day because `web/.next` held no production build and nothing surfaced it. If the unit sits in `activating (auto-restart)`, check for a build before debugging anything else.

## Configuration

Read from `.env` (and the process environment) by `load_settings()`; names only here, values never. Strategy tunables are code, not env: see `Params` in `src/tse_scanner/config.py`.

| Variable | Default | Meaning |
|---|---|---|
| `TSE_DATA_SOURCE` | `free` | `free` (JPX files + Yahoo) or `jquants` (paid J-Quants V2); `paid` is an alias of `jquants`; `--source` overrides per run |
| `TSE_DATA_DIR` | `./data` | local data directory |
| `TSE_DUCKDB_PATH` | `./data/tse.duckdb` | DuckDB store (backtests point it at a snapshot, see Data) |
| `JQUANTS_API_KEY` | none | dashboard-issued key, sent as `x-api-key` to `api.jquants.com/v2`; only the paid pull needs it |
| `JSF_ENABLED` | `false` | optional fresher fuel: 日証金 / JSF daily 貸借 + 逆日歩 (Phase 4 stub, `src/tse_scanner/data/sources/jsf.py`) |
| `TSE_RESEARCH_MODEL` | `claude-fable-5` | orchestrator (synthesis) model for deep research; collectors are pinned to Sonnet |
| `TSE_RESEARCH_TTL_DAYS` | `5` | skip a re-run when a `done` memo is younger than this (`--force` overrides) |
| `TSE_RESEARCH_TIMEOUT_S` | `900` | wall-clock kill for one research run |
| `TSE_RESEARCH_MAX_TURNS` | `50` | `--max-turns` for the headless `claude` run |
| `TSE_CLAUDE_BIN` | `claude` | path to the `claude` CLI |
| `TSE_API_URL` | `http://127.0.0.1:8602` | web app only: where Next.js reaches the API; set by `tse-web.service`, not read from `.env` |

## Data

Everything below is under `data/` (1.4 GB, almost all of it the two DuckDB files) and **none of it is in git**: the stores are large and reproducible from ingestion (free: JPX + Yahoo; paid: J-Quants V2 backfill), the rest is run output.

| Path | What |
|---|---|
| `data/tse.duckdb` (719 MB) | live store: `prices`, `weekly_margin` (as-of and publication date), `listed_info`, `scan_results`, `topix`, `fins_summary`, `research`; DuckDB is single-writer |
| `data/tse_ro.duckdb` (719 MB) | static snapshot used by the long backtests (`TSE_DUCKDB_PATH=data/tse_ro.duckdb`) so the live API on `tse.duckdb` is untouched; how it is refreshed is not documented here |
| `data/raw/` | cached JPX downloads (`data_j.xls`, weekly margin PDF), reused until `ingest --refresh` |
| `data/research/<code>/<date>/` | per-run research archive: `pack.md`, `memo.md`, `verdict.json`, `raw/`, `run.log` |
| `data/backtest_*_events_*.parquet` | per-event event-study output for offline re-slicing |
| `data/*.log` | runtime logs (`rescan.log` is tailed by the UI; `/jobs` lists the rest) |
| `data/run_*_backtest.sh` | two driver scripts for the deadline / revision studies; ignored with the rest of `data/` |

Also not in git: `.env` (secrets), `.venv/`, `web/node_modules/`, `web/.next/` (rebuild with `npm run build`), and the `.pytest_cache` / `.ruff_cache` caches. Nothing in this repo backs up `data/tse.duckdb`, which holds the paid 10-year J-Quants backfill — back it up separately.

## Development

| `make` target | Runs |
|---|---|
| `install` / `sync` | `uv sync --extra dev --extra api` |
| `test` | `uv run pytest -q` |
| `lint` | `uv run ruff check src tests` (line length 100, py312) |
| `ingest` | `tse-scanner ingest --refresh` — without `--refresh` the cached weekly PDF in `data/raw/` is re-parsed forever and the new Tue/Wed print is never picked up |
| `universe` / `scan` / `backtest` | the matching `tse-scanner` subcommand |
| `research code=7203` | deep memo for one ticker |
| `clean` | removes `.pytest_cache`, `.ruff_cache`, `**/__pycache__` |

There is no `make rescan`; use `uv run tse-scanner rescan`. Typecheck the UI with `cd web && npx tsc --noEmit` before relying on a change. Tests live in `tests/` (pytest, `pythonpath = ["src"]`); the research wrapper is tested against a fake `claude` stub, so the suite spends no API credit.

```
src/tse_scanner/
  config.py          # Params — every tunable, with starting guesses (single source of truth); Settings (env)
  cli.py             # argparse CLI: ingest · universe · scan · rescan · backtest · research
  pipeline.py        # lifecycle state machine: Crowded → Primed → Igniting → Confirmed
  scan.py            # composes fuel/coil/ignition → stage + score → scan_results
  research.py        # on-demand deep fundamental memo via headless claude run
  api.py             # FastAPI view layer over the store — loopback :8602
  data/
    store.py         # DuckDB + Parquet point-in-time store, publication/disclosure-date aware
    universe.py      # 貸借銘柄 universe builder
    jquants.py       # J-Quants V2 REST client + published_date derivation
    jquants_sync.py  # idempotent backfill + weekly sync (margin, bars, TOPIX, fins)
    sources/         # free feeds: jpx_files, jpx_universe, jpx_margin (the fuel), yahoo, jsf (stub)
  signals/
    fuel.py          # days-to-cover, 信用倍率, boosters — the edge
    coil.py          # BB-width squeeze + tight box — timing
    ignition.py      # confirmed break + spring variant — trigger
    revision.py      # 上方修正 pressure (disclosure-anniversary windows)
    deadline.py      # 期日 forced-flow, FIFO-rebuilt vintages
    targets.py       # technical exit map (T1/T2, 節目, 青天井) — display-only
  backtest/
    labels.py        # first-touch triple-barrier labels (gap-aware; fixed / ATR / ATR-trail exit models)
    event_study.py   # point-in-time event study — the GO/NO-GO gate
    deadline_study.py · revision_study.py · exit_bakeoff.py   # the other setups' A/B studies; re-label under each exit model
web/                 # Next.js app (App Router); lib/types.ts mirrors the API payloads by hand
tests/               # pytest
.claude/
  skills/deep-research/SKILL.md   # research playbook: memo template, source checklist, contract
  agents/research-collector.md    # Sonnet ingest subagent (raw pages never reach the synthesis model)
```

## Related

- [`CLAUDE.md`](./CLAUDE.md) and [`PLAYBOOK.md`](./PLAYBOOK.md), above. `.claude/` is tracked on purpose: `research` runs `claude` from the repo root so it finds the skill and the collector agent.
- All apps on the VPS are listed with a live up/down dot at `https://openclaw-vps.tail0d53dc.ts.net/apps` (this one is "踏み上げ Scanner").
- The separate `deep-research` repo (`phat` harness) shares no code with this repo: the per-ticker research here is its own small wrapper in `src/tse_scanner/research.py`.

## Gotchas

- **DuckDB is single-writer across processes.** `Store.connect` retries transient lock collisions; the API only spawns detached children and never holds a long write connection. Long backtests read `tse_ro.duckdb` for the same reason.
- **The API has no auth:** it is loopback-only by design — never widen `--host`. `tse-web` does bind `0.0.0.0:3002` (not loopback), so only the host firewall keeps it off the public internet.
- **`next start` needs `next build` first** (see Run / deploy); `web/.next` is not in git.
- Code comments, `Makefile`, `.env.example` and the unit files' `Documentation=` still cite `DESIGN.md` / `PLAN.md`. Both were deleted in `db6c56b`; `CLAUDE.md` is the source of truth.
- Point-in-time discipline is sacred: publication-date reads, stop-high no-fill and gap-aware fills must not regress, and the verdict / exit map must never become score inputs (`CLAUDE.md` → Red lines).

## License

MIT. See [LICENSE](LICENSE).
