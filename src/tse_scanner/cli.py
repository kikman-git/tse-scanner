"""Command-line entry point.

A stdlib-only `argparse` shell so the skeleton runs *today* (`tse-scanner --help`,
`python -m tse_scanner --help`) before any dependency is installed — heavy imports (pandas, duckdb,
yfinance) live *inside* the handlers, never at module top. Each subcommand maps to a build phase
:

    ingest    Phase 0  — free pull (JPX universe + weekly margin + Yahoo OHLCV) → DuckDB store
    universe  Phase 0  — build the point-in-time 貸借銘柄 tradeable universe
    scan      Phase 2  — run the lifecycle pipeline, bucket by stage, score, persist
    backtest  Phase 1  — point-in-time event study with first-touch labels (GO/NO-GO)
    research       — on-demand deep fundamental memo for one ticker (headless claude run)

The view layer is the FastAPI service (tse_scanner.api) + Next.js app in web/.
"""

from __future__ import annotations

import argparse

from tse_scanner import __version__
from tse_scanner.config import PARAMS, load_settings
from tse_scanner.pipeline import Stage

_PENDING = "  (not built yet — {phase})\n  Will: {what}"


def _resolve_source(args: argparse.Namespace) -> str:
    """Resolve the ingest source to canonical 'jquants' | 'free'.

    --source wins; otherwise TSE_DATA_SOURCE decides (so the bare `rescan`/`ingest` the web
    再スキャン button spawns follows the.env switch). 'paid' and 'jquants'
    are aliases for the J-Quants V2 (Standard) path; the user's.env uses TSE_DATA_SOURCE=paid,
    so it must NOT silently fall through to the free JPX+Yahoo path.
    """
    source = getattr(args, "source", None) or load_settings().data_source
    return "jquants" if source in ("jquants", "paid") else "free"


def _ingest(args: argparse.Namespace) -> int:
    if _resolve_source(args) == "jquants":
        return _ingest_jquants(args)

    from pathlib import Path

    import pandas as pd

    from tse_scanner.data.sources import jpx_files, jpx_margin, jpx_universe, yahoo
    from tse_scanner.data.store import Store

    print("ingest --source free — JPX published files + Yahoo, no subscription.")
    raw = Path("data/raw")
    raw.mkdir(parents=True, exist_ok=True)
    uni_xls, margin_pdf = raw / "data_j.xls", raw / "syumatsu.pdf"

    try:
        if args.refresh or not uni_xls.exists():
            jpx_files.download(jpx_files.universe_xls_url(), uni_xls)
        if args.refresh or not margin_pdf.exists():
            jpx_files.download(jpx_files.latest_weekly_margin_url(), margin_pdf)
        uni = jpx_universe.parse_universe(uni_xls)
        asof, pub = jpx_margin.parse_dates(margin_pdf)
        mg = jpx_margin.parse_weekly_margin(margin_pdf)
    except Exception as e:  # noqa: BLE001
        print(f"  fetch/parse failed: {e}")
        return 1
    print(f"  universe: {len(uni)} issues ({int(uni.is_domestic_equity.sum())} domestic equities)")
    print(f"  margin:   {len(mg)} issues, {int(mg.is_taishaku.sum())} 貸借(B); "
          f"as-of {asof}, published {pub}")

    store = Store()
    store.init_schema()
    taishaku = set(mg.loc[mg.is_taishaku, "code"])
    store.upsert_listed_info(uni, taishaku_codes=taishaku)
    store.upsert_weekly_margin(mg, as_of=asof, published=pub)
    print(f"  → store: listed_info + weekly_margin written ({store.settings.duckdb_path})")

    if args.no_prices:
        print("  prices: skipped (--no-prices)")
    else:
        # Fuel is computable from the margin tape alone, so pre-rank short-heavy names (信用倍率 < 1)
        # and pull prices only for the top `limit` — bounded, polite, and exactly the scan's candidates.
        m = mg.copy()
        m["credit_ratio"] = m["long_total"] / m["short_total"].replace(0, pd.NA)
        cand = m[(m.is_taishaku) & (m.short_total > 0) & (m.credit_ratio < PARAMS.credit_ratio_max)]
        limit = args.limit if args.limit is not None else 250  # Yahoo politeness cap (free default)
        codes = cand.sort_values("short_total", ascending=False)["code"].head(limit).tolist()
        print(f"  prices: pulling {len(codes)} short-heavy 貸借 candidates (period={args.period}) via Yahoo…")
        try:
            px = yahoo.daily_ohlcv_batch(codes, period=args.period)
        except Exception as e:  # noqa: BLE001
            print(f"  price pull failed: {e}")
            return 1
        n = store.upsert_prices(px)
        print(f"  → store: {n} price rows for {px['code'].nunique() if not px.empty else 0} codes")

    print("  next: `tse-scanner scan` → bucket by lifecycle stage and rank.")
    return 0


def _ingest_jquants(args: argparse.Namespace) -> int:
    """Sync the store to the J-Quants subscription window (backfill + weekly are one path)."""
    from tse_scanner.data.jquants import JQuantsClient
    from tse_scanner.data.jquants_sync import sync
    from tse_scanner.data.store import Store

    s = load_settings()
    if not s.jquants_api_key:
        print("ingest --source jquants — JQUANTS_API_KEY not set (issue one at jpx-jquants.com).")
        return 1
    print("ingest --source jquants — V2 sync: master + weekly margin + TOPIX + daily bars.")
    client = JQuantsClient(api_key=s.jquants_api_key)
    store = Store()
    try:
        res = sync(store, client, prices=not args.no_prices, limit=args.limit)
    except Exception as e:  # noqa: BLE001
        print(f"  sync failed: {e}")
        return 1
    print(f"  → store: +{res['weeks_added']} margin weeks ({res['margin_rows']} rows), "
          f"{res['codes_pulled']} codes backfilled (+{res['bars_rows']} bars), "
          f"+{res['topix_rows']} TOPIX sessions ({store.settings.duckdb_path})")
    if args.no_prices:
        print("  prices: skipped (--no-prices)")
    if getattr(args, "fins", False):
        from tse_scanner.data.jquants_sync import sync_fins

        print("  fins: syncing 決算短信サマリー + 業績予想修正 (/fins/summary)…")
        try:
            fres = sync_fins(store, client, limit=args.limit)
        except Exception as e:  # noqa: BLE001
            print(f"  fins sync failed: {e}")
            return 1
        print(f"  → store: {fres['fins_codes']} codes pulled (+{fres['fins_rows']} disclosure rows)")
    print("  next: `tse-scanner scan`, then `tse-scanner backtest` (the GO/NO-GO).")
    return 0


def _universe(_: argparse.Namespace) -> int:
    from tse_scanner.data.store import Store
    from tse_scanner.data.universe import build_universe

    store = Store()
    pm = store.price_metrics()
    if pm.empty:
        print("universe — no prices in store. Run `tse-scanner ingest` first.")
        return 1
    import pandas as pd

    as_of = pd.to_datetime(pm["last_date"]).max().date()
    uni = build_universe(as_of, PARAMS, store)
    print(f"universe — point-in-time 貸借銘柄 tradeable set as-of {as_of}")
    print(f"  gates: 貸借 only, turnover ≥ ¥{PARAMS.turnover_floor_yen:,.0f}/day, "
          f"price ≥ ¥{PARAMS.min_price_yen:,.0f}, ≥{PARAMS.baseline_sessions} sessions")
    print(f"  → {len(uni)} names pass.")
    if not uni.empty:
        cols = ["code", "name", "segment", "last_close", "adv_yen", "short_total", "long_total"]
        print(uni[[c for c in cols if c in uni.columns]].head(15).to_string(index=False))
    return 0


def _scan(_: argparse.Namespace) -> int:
    from tse_scanner.scan import run_scan

    print("scan — run the lifecycle pipeline and bucket the universe by stage.")
    res = run_scan()
    if res.table is None or res.table.empty:
        print("  no names evaluated — run `tse-scanner ingest` first (need prices + margin).")
        return 1
    print(f"  scan date: {res.scan_date}   evaluated: {len(res.table)} names")
    order = [s.name for s in sorted(Stage, reverse=True)]
    buckets = " · ".join(f"{s}={res.counts.get(s, 0)}" for s in order if res.counts.get(s, 0))
    print(f"  buckets: {buckets or '(all NONE)'}")
    print(f"  act on: {Stage.IGNITING.name}   watch from: {Stage.PRIMED.name}")
    top = res.table.head(15)
    cols = ["code", "name", "stage", "score", "fuel", "coil", "ignition", "days_to_cover", "credit_ratio"]
    print(top[[c for c in cols if c in top.columns]].to_string(index=False))
    print("  → written to scan_results. View in the web UI (tse-web,:3002).")
    return 0


def _backtest(args: argparse.Namespace) -> int:
    from tse_scanner.backtest.event_study import run_event_study
    from tse_scanner.data.store import Store

    from datetime import date as _date

    setup = getattr(args, "setup", "squeeze")
    cadence = getattr(args, "cadence", "daily")
    store = Store()
    with store.connect() as con:
        row = con.execute("SELECT min(published_date), max(published_date) FROM weekly_margin "
                          "WHERE published_date IS NOT NULL").fetchone()
    if not row or row[0] is None:
        print("  no published margin in store — run `tse-scanner ingest` first.")
        return 1
    start, end = row[0], row[1]
    if getattr(args, "start", None):
        start = _date.fromisoformat(args.start)
    if getattr(args, "end", None):
        end = _date.fromisoformat(args.end)

    if setup == "deadline":
        from tse_scanner.backtest.deadline_study import run_deadline_study

        print("backtest (deadline / 期日スイング) — forced 制度信用 settlement-flow event study.")
        print(f"  label: +{PARAMS.target_pct:.0%} / -{PARAMS.stop_pct:.0%} within "
              f"{PARAMS.deadline_horizon_sessions} sessions; FIFO vintages; publication-date margin.")
        print(f"  scanning name-weeks in [{start}.. {end}] …", flush=True)
        res = run_deadline_study(start, end, PARAMS, store,
                                 progress=lambda m: print(m, flush=True))
    elif setup == "revision":
        from tse_scanner.backtest.revision_study import run_revision_study

        print("backtest (revision / 上方修正先回り) — reactive-drift vs anticipatory event study.")
        print(f"  label: +{PARAMS.target_pct:.0%} / -{PARAMS.stop_pct:.0%} within "
              f"{PARAMS.rev_horizon_sessions} sessions; fins disclosed strictly-before; gap-aware fills.")
        print(f"  scanning disclosures in [{start}.. {end}] …", flush=True)
        res = run_revision_study(start, end, PARAMS, store,
                                 progress=lambda m: print(m, flush=True))
    else:
        print("backtest — point-in-time event study with first-touch labels (the GO/NO-GO gate).")
        print(f"  label: +{PARAMS.target_pct:.0%} / -{PARAMS.stop_pct:.0%} within "
              f"{PARAMS.horizon_sessions} sessions; publication-date margin; stop-high no-fill.")
        clock = "every trading day" if cadence == "daily" else "margin publication dates"
        print(f"  replaying {clock} in [{start}.. {end}] ({cadence} cadence) …", flush=True)
        res = run_event_study(start, end, PARAMS, store,
                              progress=lambda m: print(m, flush=True), cadence=cadence)
    print(f"  events: {res.n_events}   labels: {res.label_counts}")
    if res.n_events:
        _p2i = res.primed_to_igniting_rate
        p2i = f"   Primed→Igniting: {_p2i:.1%}" if _p2i == _p2i else ""
        print(f"  win-rate: {res.win_rate:.1%}   expectancy: {res.expectancy_r:+.2f}R{p2i}")
        vt = f"{res.vs_topix:+.2f}R" if res.vs_topix == res.vs_topix else "n/a (no TOPIX stored)"
        print(f"  vs TOPIX (matched windows): {vt}")
        for name, c in res.splits.items():
            wr = f"{c['win_rate']:.1%}" if c["win_rate"] == c["win_rate"] else "n/a"
            er = f"{c['expectancy_r']:+.2f}R" if c["expectancy_r"] == c["expectancy_r"] else "n/a"
            print(f"  split · {name}: n={c['n']}  win-rate {wr}  expectancy {er}")
        if res.events_path:
            print(f"  events → {res.events_path}  (per-event records; re-slice cohorts offline)")
    print(f"  note: {res.note}")
    return 0


def _research(args: argparse.Namespace) -> int:
    from tse_scanner.config import load_settings
    from tse_scanner.research import run_research

    s = load_settings()
    code = str(args.code)
    print(f"research {code} — deep fundamental memo.")
    print(f"  model: {s.research_model} (collectors: sonnet)   "
          f"caps: {s.research_max_turns} turns / {s.research_timeout_s // 60} min")
    print("  this spawns a headless claude run with web search — typically 5–12 minutes…")
    res = run_research(code, force=args.force)
    for w in res.pack_warnings:
        print(f"  ⚠️ {w}")
    if res.status == "skipped":
        print(f"  ⏭️  {res.error}")
        print(f"  verdict: {res.verdict} — {res.one_liner}")
        print(f"  memo: {res.memo_path}")
        return 0
    if res.status == "failed":
        print(f"  ❌ failed: {res.error}")
        if res.run_dir is not None:
            print(f"  inspect: {res.run_dir}/run.log")
        return 1
    cost = f"${res.cost_usd:.2f}" if res.cost_usd is not None else "n/a"
    mins = (res.duration_s or 0) / 60
    print(f"  ✅ {res.verdict} — {res.one_liner}")
    print(f"  memo: {res.memo_path}")
    print(f"  cost: {cost}   turns: {res.num_turns}   took: {mins:.1f} min")
    print("  → stored in the research table; visible in the web per-name view.")
    return 0


def _rescan(args: argparse.Namespace) -> int:
    """ingest (forced --refresh) then scan — one shot; what the web 再スキャン button spawns."""
    rc = _ingest(args)
    if rc != 0:
        return rc
    return _scan(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tse-scanner", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    for name, fn, help_ in [
        ("ingest", _ingest, "free pull: JPX universe + weekly margin + Yahoo OHLCV (Phase 0)"),
        ("universe", _universe, "build the 貸借銘柄 point-in-time universe (Phase 0)"),
        ("scan", _scan, "bucket the universe by lifecycle stage (Phase 2)"),
        ("backtest", _backtest, "run the point-in-time event study / GO-NO-GO (Phase 1)"),
        ("rescan", _rescan, "ingest (forced --refresh) + scan in one shot (the web 再スキャン button)"),
        ("research", _research, "deep fundamental memo for one ticker via headless claude"),
    ]:
        sp = sub.add_parser(name, help=help_)
        if name == "research":
            sp.add_argument("code", help="4-digit TSE ticker, e.g. 7203")
            sp.add_argument("--force", action="store_true",
                            help="re-run even if a memo younger than TSE_RESEARCH_TTL_DAYS exists")
        if name == "backtest":
            sp.add_argument("--setup", choices=["squeeze", "deadline", "revision"],
                            default="squeeze",
                            help="which setup's event study to run: squeeze (coil/fuel/ignition — "
                                 "the GO/NO-GO gate, default), deadline (期日スイング forced-flow), "
                                 "revision (上方修正先回り — needs `ingest --fins`).")
            sp.add_argument("--cadence", choices=["daily", "weekly"], default="daily",
                            help="replay clock: daily = every trading day (representative — "
                                 "ignition is a daily breakout); weekly = margin publication dates "
                                 "(fast but under-samples ignition). Default: daily.")
            sp.add_argument("--start", default=None, metavar="YYYY-MM-DD",
                            help="replay window start (default: first published margin print)")
            sp.add_argument("--end", default=None, metavar="YYYY-MM-DD",
                            help="replay window end (default: last published margin print)")
        if name in ("ingest", "rescan"):
            sp.add_argument("--source", choices=["free", "jquants", "paid"], default=None,
                            help="free = JPX files + Yahoo; jquants/paid = V2 sync "
                                 "(default: TSE_DATA_SOURCE, currently '%s')"
                                 % load_settings().data_source)
            sp.add_argument("--limit", type=int, default=None,
                            help="cap price pulls: free = top-N short-heavy via Yahoo (default "
                                 "250); jquants = cap full-history backfill pulls (default: all)")
            sp.add_argument("--period", default="1y",
                            help="Yahoo OHLCV period to pull (default 1y; need ≥120 sessions)")
            sp.add_argument("--no-prices", action="store_true", help="ingest universe + margin only")
            sp.add_argument("--refresh", action="store_true", help="re-download JPX files even if cached")
            sp.add_argument("--fins", action="store_true",
                            help="also sync /fins/summary (決算+業績予想修正 — the revision setup; "
                                 "jquants source only; first run ≈ 4k calls)")
        sp.set_defaults(func=fn)
        if name == "rescan":
            sp.set_defaults(refresh=True)  # always pull the fresh weekly print — that's the point
    return p


def main(argv: list[str] | None = None) -> int:
    import sys

    # The API spawns this CLI detached and live-tails its log file; block-buffered
    # stdout (the default when redirected) would leave those tails empty for minutes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0
    load_settings()  # populate env (no-op if python-dotenv absent)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
