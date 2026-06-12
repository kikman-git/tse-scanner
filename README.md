# tse-scanner

**踏み上げ (fumiage) short-squeeze setup scanner for the Tokyo Stock Exchange.**

Find TSE names where shorts are **crowded and trapped** (read off Japan's published margin tape)
**and** price is **wound into a tight coil**, then catch the **upside break that runs the shorts**.

Two docs go with this repo:

| Doc | Owns |
|---|---|
| **[`CLAUDE.md`](./CLAUDE.md)** | How the **scanner** works: thesis, signals, the point-in-time validation gate, stack, deep research, and the red lines. |
| **[`PLAYBOOK.md`](./PLAYBOOK.md)** | How the human **trades** the names it surfaces (sizing, entry, stops, exits, journal). |

> **Status:** FastAPI + Next.js view layer shipped; the 10y J-Quants Standard history is backfilled.
> The edge is still a *hypothesis* until the point-in-time event study returns its **GO/NO-GO
> verdict** — **no live size before that gate passes** (PLAYBOOK graduation ladder).

## Quickstart

```bash
uv sync --extra dev --extra api         # create .venv and install deps
uv run tse-scanner rescan               # ingest --refresh + scan in one shot (the web 再スキャン button)
uv run uvicorn tse_scanner.api:app --host 127.0.0.1 --port 8602   # API; then `cd web && npm run dev`
```

On the VPS the UI runs as two systemd user services — `tse-api` (uvicorn, loopback `:8602`) and
`tse-web` (Next.js, `:3002`): `systemctl --user restart tse-api tse-web` · logs via
`journalctl --user -u tse-web` · Tailscale `http://100.89.178.115:3002`.

The free path needs **no credentials**. `.env` is only for the paid J-Quants V2 history pull
(`JQUANTS_API_KEY`, dashboard-issued — V2 retired the email/password token flow) and for tuning the
deep-research knobs (`TSE_RESEARCH_MODEL`, TTL, timeout). See `.env.example`.

The CLI shell uses only the stdlib, so `python -m tse_scanner --help` works before any install.

## Layout

```
src/tse_scanner/
  config.py          # Params — every tunable, with starting guesses (single source of truth)
  cli.py             # argparse CLI: ingest · universe · scan · rescan · backtest · research
  pipeline.py        # lifecycle state machine: Crowded → Primed → Igniting → Confirmed
  scan.py            # composes fuel/coil/ignition → stage + score → scan_results
  research.py        # on-demand deep fundamental memo via headless claude run
  api.py             # FastAPI view layer over the store — loopback :8602
  data/
    jquants.py       # J-Quants V2 REST client + published_date derivation
    universe.py      # 貸借銘柄 universe builder
    store.py         # DuckDB + Parquet point-in-time store, publication-date aware
  signals/
    fuel.py          # days-to-cover, 信用倍率, boosters — the edge
    coil.py          # BB-width squeeze + tight box — timing
    ignition.py      # confirmed break + spring variant — trigger
  backtest/
    labels.py        # first-touch triple-barrier labels
    event_study.py   # point-in-time event study — the GO/NO-GO gate
.claude/
  skills/deep-research/   # research playbook: memo template, source checklist, contract
  agents/research-collector.md  # Sonnet ingest subagent (raw pages never reach the synthesis model)
tests/
```

## Deep research

The scanner reads *positioning*; it can't know **why** shorts are short or whether a 公募増資 is
about to kill the squeeze. `tse-scanner research <code>` (or the 🔬 button in the web UI) spawns one
headless `claude` run — Sonnet collectors fetch and distill filings/news/short positions in
parallel, the synthesis model writes a squeeze-specific memo and a `SUPPORTS / NEUTRAL / OPPOSES`
verdict (with the broker target-price cluster as an exit-ceiling reference). Display-only overlay: it
never feeds the scan score. See [`CLAUDE.md`](./CLAUDE.md) for the architecture and rails.

## Two traps that invalidate most JP-margin backtests — handled from day one

1. **Publication lag** — Friday's margin balance is *published* the following Tue–Wed. Use the print
   at its **publication date**, not its as-of date, or the backtest trades on data that didn't exist.
2. **Stop-high no-fill** — the best squeezes lock ストップ高 and you *cannot buy a locked stock*. The
   backtest must assume those fills are missed.
