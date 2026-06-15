"""Deadline event-study integration test (hermetic tmp store).

Seeds one 貸借 name carrying a single 制度信用 short vintage that ages into its 6-month deadline
window, with a price run-up over that window. The study must (a) fire a TREATMENT event in the
due window that labels WIN, and (b) sample a PLACEBO event from the same name at a non-due week —
the A/B that isolates deadline *timing* from name-selection.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from tse_scanner.backtest.deadline_study import run_deadline_study
from tse_scanner.config import PARAMS, Settings
from tse_scanner.data.store import Store

pytest.importorskip("duckdb")


def _seed(tmp_path) -> Store:
    s = Store(Settings(data_dir=tmp_path, duckdb_path=tmp_path / "t.duckdb"))
    s.init_schema()
    s.upsert_listed_info(pd.DataFrame([
        {"code": "1301", "name": "DUE", "is_domestic_equity": True, "is_taishaku": True}]))

    # weekly margin: a single short vintage opens at week 1 (2024-01-12) and holds at 500k.
    # Its deadline ≈ 2024-07-12; the due window is ~2024-06-14 .. 2024-07-19.
    weeks = [date(2024, 1, 5) + timedelta(weeks=i) for i in range(30)]
    for i, w in enumerate(weeks):
        sys_short = 0 if i == 0 else 500_000
        s.upsert_weekly_margin(pd.DataFrame([{
            "code": "1301", "code5": "13010", "name": "DUE", "is_taishaku": True,
            "short_total": sys_short, "long_total": 0,
            "neg_short": 0, "sys_short": sys_short, "neg_long": 0, "sys_long": 0,
        }]), as_of=w, published=w + timedelta(days=4))

    # prices: flat 1000 (ADV 200k×1000 = ¥200M, passes the liquidity gate) until the deadline
    # window opens, then a run to +12% (entry ~1000 → high ≥ 1120 = WIN within the horizon).
    bdays = pd.bdate_range("2024-01-08", "2024-08-30")
    ramp = [1000.0, 1020.0, 1060.0, 1100.0, 1130.0]
    cut = pd.Timestamp("2024-06-17")
    closes, k = [], 0
    for d in bdays:
        if d <= cut:
            closes.append(1000.0)
        else:
            closes.append(ramp[k] if k < len(ramp) else 1130.0)
            k += 1
    closes = pd.Series(closes)
    s.upsert_prices(pd.DataFrame({
        "code": "1301", "date": bdays, "open": closes,
        "high": closes + 15.0, "low": closes - 10.0, "close": closes, "volume": 200_000}))
    s.upsert_topix(pd.DataFrame({
        "date": bdays, "open": 2000.0, "high": 2000.0, "low": 2000.0, "close": 2000.0}))
    return s


def test_deadline_treatment_wins_placebo_separates(tmp_path):
    import duckdb

    s = _seed(tmp_path)
    res = run_deadline_study(date(2024, 1, 1), date(2024, 8, 31), PARAMS, s)

    assert res.n_events >= 2
    treat = res.splits["期日向かい treatment (due≥floor)"]
    placebo = res.splits["placebo (same names, no deadline)"]
    assert treat["n"] >= 1
    assert placebo["n"] >= 1                      # same name, sampled from non-due weeks
    assert res.label_counts.get("win", 0) >= 1   # the deadline-window run-up

    # per-event parquet carries the cohort tag for offline A/B re-slicing
    assert res.events_path
    ev = duckdb.connect().execute(f"SELECT * FROM '{res.events_path}'").df()
    assert set(ev["cohort"]) == {"treatment", "placebo"}
    # a treatment event fired inside the due window and won (point-in-time). The placebo may also
    # win here — a single price spike is caught by any entry within the horizon, which is exactly
    # the name-selection confound the placebo exists to expose; the A/B is decided on *average*
    # expectancy across many events, not one synthetic spike.
    twins = ev[(ev["cohort"] == "treatment") & (ev["label"] == "win")]
    assert len(twins) >= 1
    w = twins.iloc[0]
    assert pd.Timestamp(w["date"]) >= pd.Timestamp("2024-06-14")
    assert w["short_due_dtc"] >= PARAMS.deadline_dtc_floor
