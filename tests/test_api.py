"""FastAPI view-layer tests (PLAN.md Track A).

The API is a thin skin over Store: reads must be JSON-safe (NaN→null, dates→ISO) and the two
mutation endpoints use detached-spawn semantics (202 + idempotent; state polled from the store /
log, never held in the request).
"""

import json
from datetime import date

import pandas as pd
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tse_scanner.api as api_mod  # noqa: E402
from tse_scanner.config import Settings  # noqa: E402
from tse_scanner.data.store import Store  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TSE_DUCKDB_PATH", str(tmp_path / "t.duckdb"))
    monkeypatch.setattr(api_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setitem(api_mod._procs, "rescan", None)
    monkeypatch.setitem(api_mod._procs, "rescan_started", 0.0)
    monkeypatch.setitem(api_mod._procs, "rescan_failed", None)
    monkeypatch.setitem(api_mod._procs, "research", {})
    return TestClient(api_mod.app)


def _seed(tmp_path) -> Store:
    s = Store(Settings(duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    days = pd.bdate_range(end=date(2026, 6, 10), periods=40)
    s.upsert_prices(pd.DataFrame([
        {"code": "9755", "date": d.date(), "open": 2800, "high": 2860, "low": 2790,
         "close": 2820, "volume": 84_400} for d in days
    ]))
    s.upsert_weekly_margin(pd.DataFrame([{
        "code": "9755", "code5": "97550", "name": "応用地質", "is_taishaku": True,
        "short_total": 211_100, "long_total": 112_100,
        "neg_short": 192_200, "sys_short": 18_900, "neg_long": 38_700, "sys_long": 73_400,
    }]), as_of=date(2026, 6, 5), published=date(2026, 6, 9))
    s.write_scan(pd.DataFrame([{
        "scan_date": date(2026, 6, 10), "code": "9755", "name": "応用地質", "segment": "prime",
        "stage": "CROWDED", "score": 0.61, "fuel": 0.8, "coil": 0.3, "ignition": "none",
        "days_to_cover": 15.4, "credit_ratio": 0.53, "short_total": 211_100,
        "long_total": 112_100, "bb_width_pct": 0.18, "box_high": 2900.0, "box_low": 2700.0,
        "last_close": 2820.0, "adv_yen": 2.4e8, "margin_as_of": date(2026, 6, 5),
        "margin_published": date(2026, 6, 9),
    }]))
    return s


def test_scan_empty_store(client):
    r = client.get("/scan")
    assert r.status_code == 200
    assert r.json() == {"scan_date": None, "rows": [], "counts": {}, "margin_as_of": None,
                        "margin_published": None, "verdicts": {}}


def test_scan_freshness_and_rows(client, tmp_path):
    _seed(tmp_path)
    j = client.get("/scan").json()
    assert j["scan_date"] == "2026-06-10"
    assert j["margin_as_of"] == "2026-06-05" and j["margin_published"] == "2026-06-09"
    assert j["counts"] == {"CROWDED": 1}
    row = j["rows"][0]
    assert row["code"] == "9755" and row["days_to_cover"] == 15.4
    json.dumps(j)  # strictly JSON-serializable (no NaN leakage)


def test_prices_and_margin_endpoints(client, tmp_path):
    _seed(tmp_path)
    bars = client.get("/prices/9755", params={"sessions": 5}).json()["bars"]
    assert len(bars) == 5 and bars[-1]["date"] == "2026-06-10" and bars[-1]["close"] == 2820
    prints = client.get("/margin/9755").json()["prints"]
    assert prints[-1]["as_of_date"] == "2026-06-05" and prints[-1]["neg_short"] == 192_200


def test_research_states_none_done_and_stale_running(client, tmp_path):
    s = _seed(tmp_path)
    assert client.get("/research/9755").json()["state"] == "none"
    s.upsert_research({"code": "9755", "research_date": date(2026, 6, 10), "status": "done",
                       "verdict": "NEUTRAL", "confidence": "medium", "one_liner": "ok",
                       "memo_md": "# memo"})
    j = client.get("/research/9755").json()
    assert j["state"] == "done" and j["row"]["verdict"] == "NEUTRAL"
    s.upsert_research({"code": "8179", "research_date": date(2026, 6, 10), "status": "running",
                       "started_at": pd.Timestamp("2026-06-10T00:00:00")})
    j = client.get("/research/8179").json()
    assert j["state"] == "failed"  # stale 'running' row = crashed run
    assert "stale" in j["row"]["error"]


class _FakeProc:
    def __init__(self, rc=None):
        self.returncode = rc

    def poll(self):
        return self.returncode


def test_rescan_spawn_idempotent_and_status(client, monkeypatch, tmp_path):
    spawned = []

    def fake_popen(cmd, **kw):
        spawned.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(api_mod.subprocess, "Popen", fake_popen)
    assert client.post("/rescan").status_code == 202
    assert client.post("/rescan").status_code == 200  # already running — no second spawn
    assert len(spawned) == 1 and spawned[0][-1] == "rescan"

    (tmp_path / "data" / "rescan.log").write_text("ingest...\n  scan date: 2026-06-10\n")
    st = client.get("/rescan/status").json()
    assert st["running"] is True and st["last_log_line"].startswith("scan date")
    assert st["failed_rc"] is None

    api_mod._procs["rescan"] = _FakeProc(rc=1)  # child exited non-zero
    st = client.get("/rescan/status").json()
    assert st["running"] is False and st["failed_rc"] == 1


def test_research_spawn_validates_code(client, monkeypatch):
    monkeypatch.setattr(api_mod.subprocess, "Popen", lambda *a, **k: _FakeProc())
    assert client.post("/research/9755").status_code == 202
    assert client.post("/research/not-a-code").status_code == 422
