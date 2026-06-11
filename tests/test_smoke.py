"""Smoke tests — stdlib only, green before any dependency is installed.

They lock the two things the scaffold guarantees today: (1) Params stays in sync with the
DESIGN.md §10 table, and (2) the lifecycle ladder + CLI shell behave.
"""

import pytest

from tse_scanner.cli import build_parser, main
from tse_scanner.config import PARAMS
from tse_scanner.pipeline import Stage, classify


def test_params_match_design_table():
    # DESIGN.md §10 starting guesses — if these drift, update both places on purpose.
    assert PARAMS.credit_ratio_max == 1.0           # 信用倍率 gate
    assert PARAMS.days_to_cover_min == 5.0
    assert PARAMS.bb_width_pctile_max == 0.20
    assert PARAMS.volume_surge_mult == 2.0
    assert (PARAMS.baseline_sessions, PARAMS.setup_sessions) == (120, 14)
    assert (PARAMS.target_pct, PARAMS.stop_pct, PARAMS.horizon_sessions) == (0.12, 0.06, 10)
    assert PARAMS.score_weights["fuel"] == max(PARAMS.score_weights.values())  # fuel weighted highest


def test_stage_ladder_is_ordered_and_actionable():
    assert Stage.CROWDED < Stage.PRIMED < Stage.IGNITING < Stage.CONFIRMED
    assert Stage.IGNITING.is_actionable
    assert not Stage.PRIMED.is_actionable


@pytest.mark.parametrize("fuel,coil,ign,follow,expected", [
    (False, False, False, False, Stage.NONE),
    (True, False, False, False, Stage.CROWDED),
    (True, True, False, False, Stage.PRIMED),
    (True, True, True, False, Stage.IGNITING),
    (True, True, True, True, Stage.CONFIRMED),
])
def test_classify(fuel, coil, ign, follow, expected):
    assert classify(has_fuel=fuel, is_coiled=coil, has_ignition=ign, has_followthrough=follow) == expected


def test_cli_parser_has_all_subcommands():
    parser = build_parser()
    actions = [a for a in parser._actions if a.dest == "command"]
    assert actions, "expected a subcommand group"
    names = set(actions[0].choices)
    assert {"ingest", "universe", "scan", "backtest", "rescan", "research"} <= names
    assert "dashboard" not in names  # Streamlit dashboard retired 2026-06-11 (replaced by web/)


def test_rescan_always_refreshes():
    # rescan exists so the web 再スキャン button never re-parses a cached weekly PDF —
    # refresh must be forced on regardless of flags.
    args = build_parser().parse_args(["rescan"])
    assert args.refresh is True
    assert args.func.__name__ == "_rescan"
    # plain ingest keeps refresh opt-in
    assert build_parser().parse_args(["ingest"]).refresh is False


@pytest.mark.parametrize("env, src, expect", [
    ("paid", None, "jquants"),       # the user's .env value — bare ingest/rescan must hit V2
    ("jquants", None, "jquants"),    # canonical token
    ("free", None, "free"),          # default free path
    ("paid", "free", "free"),        # explicit --source free overrides the env
    ("free", "jquants", "jquants"),  # explicit --source jquants overrides the env
])
def test_ingest_source_resolution(monkeypatch, env, src, expect):
    # Regression: TSE_DATA_SOURCE=paid was silently falling through to the free Yahoo path
    # because the dispatch only matched "jquants". The web 再スキャン button spawns a bare
    # `rescan`, so 'paid' must resolve to the J-Quants path.
    from tse_scanner.cli import _resolve_source

    monkeypatch.setenv("TSE_DATA_SOURCE", env)
    args = build_parser().parse_args(["ingest"] + (["--source", src] if src else []))
    assert _resolve_source(args) == expect


def test_cli_runs_clean():
    assert main([]) == 0          # no command → help, exit 0
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
