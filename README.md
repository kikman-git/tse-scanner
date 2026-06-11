# tse-scanner

**踏み上げ (fumiage) short-squeeze setup scanner for the Tokyo Stock Exchange.**

Find TSE names where shorts are **crowded and trapped** (read off Japan's published margin tape)
**and** price is **wound into a tight coil**, then catch the **upside break that runs the shorts**.

Three docs drive this repo — read them first:

| Doc | Owns |
|---|---|
| **[`DESIGN.md`](./DESIGN.md)** | How the **scanner** detects & validates the setup (signals, event study, stack). |
| **[`PLAYBOOK.md`](./PLAYBOOK.md)** | How I **trade** the names it surfaces (sizing, entry, stops, exits, journal). |
| **[`PLAN.md`](./PLAN.md)** | What's **next**: FastAPI+Next.js view split (Track A) · J-Quants ingestion + GO/NO-GO event study (Track B). |

> **Status: Tracks A + B built (2026-06-11).** The view layer is a FastAPI service + Next.js app
> (Track A); the 10y J-Quants history is backfilled (Track B). The edge is still a *hypothesis*
> until the `DESIGN.md` §6 point-in-time event study returns its GO/NO-GO verdict. **No live size
> before that gate passes** (PLAYBOOK graduation ladder).

## Quickstart

```bash
uv sync --extra dev --extra api         # create .venv and install deps (DESIGN.md §8 stack)
uv run tse-scanner rescan               # ingest --refresh + scan in one shot (the web 再スキャン button)
uv run uvicorn tse_scanner.api:app --host 127.0.0.1 --port 8602   # API; then `cd web && npm run dev`
```

On the VPS the UI runs as two systemd user services — `tse-api` (uvicorn, loopback `:8602`) and
`tse-web` (Next.js, `:3002`): `systemctl --user restart tse-api tse-web` · logs via
`journalctl --user -u tse-web` · Tailscale `http://100.89.178.115:3002`.

The free path needs **no credentials**. `.env` is only for the paid J-Quants V2 history pull
(`JQUANTS_API_KEY`, dashboard-issued — V2 retired the email/password token flow) and for tuning
the §15 deep-research knobs (`TSE_RESEARCH_MODEL`, TTL, timeout). See `.env.example`.

The CLI shell uses only the stdlib, so `python -m tse_scanner --help` works before any install.

## Layout

```
src/tse_scanner/
  config.py          # Params — mirrors the DESIGN.md §10 starting-guess table (single source of truth)
  cli.py             # argparse CLI: ingest · universe · scan · rescan · backtest · research
  pipeline.py        # lifecycle state machine: Crowded → Primed → Igniting → Confirmed (DESIGN.md §2)
  scan.py            # composes fuel/coil/ignition → stage + score → scan_results (Phase 2)
  research.py        # on-demand deep fundamental memo via headless claude run (DESIGN.md §15)
  api.py             # FastAPI view layer over the store — loopback :8602 (PLAN.md Track A)
  data/
    jquants.py       # J-Quants REST client            (Phase 0 — DESIGN.md §5)
    universe.py      # 貸借銘柄 universe builder        (Phase 0 — DESIGN.md §5)
    store.py         # DuckDB + Parquet point-in-time store, publication-date aware (Phase 0 — §6.1)
  signals/
    fuel.py          # days-to-cover, 信用倍率, boosters (Phase 1 — DESIGN.md §3.1)
    coil.py          # BB-width squeeze + tight box     (Phase 1 — DESIGN.md §3.2)
    ignition.py      # confirmed break + spring variant (Phase 1 — DESIGN.md §3.3)
  backtest/
    labels.py        # first-touch triple-barrier labels (Phase 1 — DESIGN.md §6)
    event_study.py   # point-in-time event study, GO/NO-GO (Phase 1 — DESIGN.md §6)
.claude/
  skills/deep-research/   # §15 research playbook: memo template, source checklist, contract
  agents/research-collector.md  # Sonnet ingest subagent (raw pages never reach the synthesis model)
tests/
```

## Deep research (`DESIGN.md` §15)

The scanner reads *positioning*; it can't know **why** shorts are short or whether a 公募増資 is
about to kill the squeeze. `tse-scanner research <code>` (or the 🔬 button in the web UI)
spawns one headless `claude` run — Sonnet collectors fetch and distill filings/news/short
positions in parallel, the synthesis model writes a squeeze-specific memo and a
`SUPPORTS / NEUTRAL / OPPOSES` verdict. Display-only overlay: it never feeds the scan score.

## What's left to build

Both **[`PLAN.md`](./PLAN.md)** tracks are built (2026-06-11): the FastAPI + Next.js view layer
(Track A — replaced the original Streamlit dashboard) and the 10y J-Quants Standard ingestion
(Track B — derived publication dates, full-universe daily bars). The one open gate is the
`DESIGN.md` §6 event study's **GO/NO-GO verdict** — no live size until it passes (PLAYBOOK ladder).

## Two traps that invalidate most JP-margin backtests (`DESIGN.md` §6) — handled from day one

1. **Publication lag** — Friday's margin balance is *published* the following Tue–Wed. Use the print
   at its **publication date**, not its as-of date, or the backtest trades on data that didn't exist.
2. **Stop-high no-fill** — the best squeezes lock ストップ高 and you *cannot buy a locked stock*. The
   backtest must assume those fills are missed.
