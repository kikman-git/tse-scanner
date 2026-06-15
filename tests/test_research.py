"""Deep-research wrapper tests — run against a FAKE `claude` binary.

The wrapper's job is lifecycle bookkeeping: build the pack, spawn the agent, enforce rails,
parse artifacts, own the DuckDB row. None of that needs a real (expensive) agent run, so the
binary is stubbed: it parses `out=` from the prompt, writes (or doesn't write) the artifact
files, and prints a canned `--output-format json` envelope.
"""

import json
import sys
import types
from pathlib import Path

import pytest

from tse_scanner.config import Settings
from tse_scanner.data.store import Store
from tse_scanner.research import build_pack, run_research

duckdb = pytest.importorskip("duckdb")

_FAKE_OK = """#!/usr/bin/env python3
import json, re, sys
out = re.search(r"out=(\\S+)", " ".join(sys.argv)).group(1)
verdict = {"verdict": "SUPPORTS", "confidence": "high", "one_liner": "bear thesis broken",
           "bear_thesis": "broken", "squeeze_killers": [],
           "catalysts": [{"date": "2026-06-20", "event": "1Q決算", "inside_horizon": True}],
           "analyst_targets": [{"house": "野村", "target": 4200, "rating": "Buy", "date": "2026-05-30"}],
           "sector_view": {"trend": "tailwind", "one_liner": "半導体ペアも軒並みPrimed"},
           "sources": ["https://example.com/a"]}
open(out + "/verdict.json", "w").write(json.dumps(verdict, ensure_ascii=False))
open(out + "/memo.md", "w").write("# 9999 TEST — deep research\\n\\nmemo body")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 1.23, "num_turns": 17, "duration_ms": 61000}))
"""

_FAKE_API_ERROR = """#!/usr/bin/env python3
import json
print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                  "result": "You've hit your session limit",
                  "total_cost_usd": 0.4, "num_turns": 2, "duration_ms": 1000}))
"""

_FAKE_NO_ARTIFACTS = """#!/usr/bin/env python3
import json
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "total_cost_usd": 0.4, "num_turns": 50, "duration_ms": 1000}))
"""

_FAKE_HANG = """#!/usr/bin/env python3
import time
time.sleep(30)
"""


def _fake_claude(tmp_path: Path, script: str) -> str:
    p = tmp_path / "claude"
    p.write_text(script)
    p.chmod(0o755)
    return str(p)


def _settings(tmp_path: Path, script: str = _FAKE_OK, **kw) -> Settings:
    return Settings(duckdb_path=tmp_path / "t.duckdb", data_dir=tmp_path / "data",
                    claude_bin=_fake_claude(tmp_path, script), **kw)


@pytest.fixture(autouse=True)
def _no_network_yfinance(monkeypatch):
    """build_pack's yfinance snapshot is best-effort; tests must never hit the network."""
    class _Offline:
        def __init__(self, *_a, **_k):
            raise RuntimeError("offline test")

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Offline))


def test_run_research_done_roundtrip(tmp_path):
    s = _settings(tmp_path)
    res = run_research("9999", settings=s)
    assert res.status == "done" and res.verdict == "SUPPORTS"
    assert res.cost_usd == 1.23 and res.num_turns == 17

    row = Store(s).latest_research("9999").iloc[0]
    assert row["status"] == "done" and row["verdict"] == "SUPPORTS"
    assert "memo body" in row["memo_md"]
    assert json.loads(row["catalysts"])[0]["event"] == "1Q決算"
    assert json.loads(row["analyst_targets"])[0]["target"] == 4200
    assert json.loads(row["sector_view"])["trend"] == "tailwind"
    # artifacts on disk: pack written by wrapper, memo/verdict by the (fake) agent
    run_dir = Path(row["memo_path"]).parent
    assert (run_dir / "pack.md").exists() and (run_dir / "verdict.json").exists()


def test_ttl_skips_fresh_memo_unless_forced(tmp_path):
    s = _settings(tmp_path)
    assert run_research("9999", settings=s).status == "done"
    again = run_research("9999", settings=s)
    assert again.status == "skipped" and again.verdict == "SUPPORTS"
    forced = run_research("9999", settings=s, force=True)
    assert forced.status == "done"


def test_failed_when_agent_writes_no_artifacts(tmp_path):
    s = _settings(tmp_path, script=_FAKE_NO_ARTIFACTS)
    res = run_research("9999", settings=s)
    assert res.status == "failed" and "verdict.json" in res.error
    row = Store(s).latest_research("9999").iloc[0]
    assert row["status"] == "failed" and row["verdict"] is None


def test_failed_surfaces_api_error_message(tmp_path):
    # API-side death (429/limit): the envelope's own message beats "no verdict.json"
    s = _settings(tmp_path, script=_FAKE_API_ERROR)
    res = run_research("9999", settings=s)
    assert res.status == "failed" and "session limit" in res.error


def test_failed_on_timeout(tmp_path):
    s = _settings(tmp_path, script=_FAKE_HANG, research_timeout_s=2)
    res = run_research("9999", settings=s)
    assert res.status == "failed" and "timed out" in res.error


def test_build_pack_surfaces_gaps_not_errors(tmp_path):
    s = Settings(duckdb_path=tmp_path / "t.duckdb", data_dir=tmp_path / "data")
    store = Store(s)
    store.init_schema()
    md, warnings = build_pack("9999", store)
    assert "Context pack — 9999" in md and "+12%" in md
    assert any("no scan row" in w for w in warnings)
    # empty store → the new computed blocks degrade to warnings, never crash the pack
    assert any("technical exit map unavailable" in w for w in warnings)
    assert any("sector context unavailable" in w for w in warnings)


def test_build_pack_exit_map_and_sector_blocks(tmp_path):
    """With bars + listed_info in the store the pack gains both computed blocks — the exit map
    via the no-scan-row fallback (recomputed from bars) and the sector header from sector33."""
    import pandas as pd

    s = Settings(duckdb_path=tmp_path / "t.duckdb", data_dir=tmp_path / "data")
    store = Store(s)
    store.init_schema()
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=60)
    store.upsert_prices(pd.DataFrame({
        "code": "7777", "date": dates, "open": 900.0, "high": 910.0, "low": 890.0,
        "close": 900.0, "volume": 10_000,
    }))
    store.upsert_listed_info(pd.DataFrame([
        {"code": "7777", "name": "テスト", "segment": "Standard", "segment_ja": "スタンダード",
         "sector33": "電気機器", "is_domestic_equity": True, "is_taishaku": True},
        {"code": "8888", "name": "ピア", "segment": "Standard", "segment_ja": "スタンダード",
         "sector33": "電気機器", "is_domestic_equity": True, "is_taishaku": True},
    ]))
    md, warnings = build_pack("7777", store)
    assert "## Technical exit map" in md and "T1" in md
    assert "## Sector context" in md and "電気機器" in md
    # only the (deliberately offline) yfinance snapshot may warn — never our computed blocks
    assert not any("exit map" in w or "sector context" in w for w in warnings)


def test_research_verdicts_join(tmp_path):
    s = _settings(tmp_path)
    run_research("9999", settings=s)
    v = Store(s).research_verdicts()
    assert len(v) == 1 and v.iloc[0]["verdict"] == "SUPPORTS"
