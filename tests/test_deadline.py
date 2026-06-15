"""Deadline (期日スイング) signal — FIFO vintage decomposition + due-window selection.

Pure-function tests (no store): the FIFO accounting is the load-bearing, error-prone part, so it
is pinned directly on hand-checkable synthetic balance series.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from tse_scanner.config import PARAMS
from tse_scanner.signals import deadline as dl


def _fridays(start: date, n: int) -> list[date]:
    return [start + timedelta(weeks=i) for i in range(n)]


def _margin(dates, short, long_=None):
    long_ = long_ if long_ is not None else [0.0] * len(dates)
    return pd.DataFrame({
        "as_of_date": dates,
        "published_date": [d + timedelta(days=4) for d in dates],  # ~Tue after Friday
        "sys_short": short,
        "sys_long": long_,
    })


# A vintage is "due" when its deadline (open + deadline_days) lands in
# [as_of − overdue_grace, as_of + due_window_days]. With defaults 182 / 28 / 7 that is roughly
# 22–27 weeks after the open.
DUE_AFTER = timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days + 1)  # safely inside


def test_single_vintage_due_only_inside_window():
    dates = _fridays(date(2020, 1, 3), 40)
    short = [0.0] + [100.0] * 39  # open 100 at week 1, hold
    open_date = dates[1]

    due_early, _ = dl._fifo_due_shares(dates, short, asof_idx=3, params=PARAMS)  # weeks after open
    assert due_early == 0.0  # deadline still ~6 months away

    # find the week whose date sits in the open vintage's due window
    lo = open_date + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    hi = open_date + timedelta(days=PARAMS.deadline_days + PARAMS.deadline_overdue_grace_days)
    idx = next(i for i, d in enumerate(dates) if lo <= d <= hi)
    due_now, total = dl._fifo_due_shares(dates, short, asof_idx=idx, params=PARAMS)
    assert due_now == 100.0
    assert total == 100.0


def test_fifo_peels_oldest_layer_first():
    # w0 +100 (A), w1 +50 (B), w2 −30 cover → FIFO peels A to 70, B intact at 50.
    dates = _fridays(date(2020, 1, 3), 40)
    short = [100.0, 150.0, 120.0] + [120.0] * 37
    a_open, b_open = dates[0], dates[1]

    # A's due window opens before B's (A opened a week earlier). Pick a week inside A's window
    # but before B's so only the (peeled) A layer counts.
    a_lo = a_open + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    b_lo = b_open + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    idx = next(i for i, d in enumerate(dates) if a_lo <= d < b_lo)
    due, total = dl._fifo_due_shares(dates, short, asof_idx=idx, params=PARAMS)
    assert due == 70.0          # A was peeled 100 → 70, not the full 100
    assert total == 120.0       # outstanding balance unchanged by the accounting


def test_cover_spans_multiple_layers():
    # w0 +40 (A), w1 +40 (B), w2 +40 (C), w3 −70 cover → peels A(40) fully + B down to 10; C intact.
    dates = _fridays(date(2020, 1, 3), 40)
    short = [40.0, 80.0, 120.0, 50.0] + [50.0] * 36
    b_open, c_open = dates[1], dates[2]
    # window where B is due but C not yet, and A already gone
    b_lo = b_open + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    c_lo = c_open + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    idx = next(i for i, d in enumerate(dates) if b_lo <= d < c_lo)
    due, _ = dl._fifo_due_shares(dates, short, asof_idx=idx, params=PARAMS)
    assert due == 10.0  # only the surviving slice of B (40 − 30 consumed after A's 40)


def test_due_dtc_deflates_by_adv():
    assert dl._due_dtc(1000.0, 500.0) == 2.0
    assert dl._due_dtc(0.0, 0.0) == 0.0
    assert dl._due_dtc(100.0, 0.0) == float("inf")  # can never be covered


def test_pressure_timeline_and_long_side():
    dates = _fridays(date(2020, 1, 3), 40)
    short = [0.0] + [200.0] * 39
    long_ = [0.0, 0.0] + [300.0] * 38
    tl = dl.pressure_timeline(_margin(dates, short, long_), adv_of=lambda _d: 100.0)
    assert {"short_due_dtc", "long_due_dtc"}.issubset(tl.columns)
    # in the short vintage's due window: 200 shares / 100 ADV = 2.0 due-DtC
    s_open = dates[1]
    lo = s_open + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    row = tl[tl["as_of_date"] >= lo].iloc[0]
    assert row["short_due_dtc"] == 2.0
    # the long vintage (opened a week later, 300 shares) is forced *selling* — tracked separately
    assert (tl["long_due_dtc"] == 3.0).any()


def test_assess_gate_on_absolute_floor():
    dates = _fridays(date(2020, 1, 3), 40)
    short = [0.0] + [100.0] * 39
    # make the latest row land inside the due window so assess sees a live deadline
    s_open = dates[1]
    lo = s_open + timedelta(days=PARAMS.deadline_days - PARAMS.deadline_due_window_days)
    hi = s_open + timedelta(days=PARAMS.deadline_days + PARAMS.deadline_overdue_grace_days)
    keep = [d for d in dates if d <= hi]
    assert lo <= keep[-1] <= hi
    m = _margin(keep, short[:len(keep)])

    hot = dl.assess(m, adv=50.0)       # 100/50 = 2.0 due-DtC ≥ floor
    assert hot.has_deadline and hot.short_due_dtc == 2.0 and hot.score > 0
    cold = dl.assess(m, adv=10_000.0)  # 100/10000 = 0.01 due-DtC < floor
    assert not cold.has_deadline
