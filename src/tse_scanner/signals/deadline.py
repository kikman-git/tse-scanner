"""Deadline (期日スイング) — "is a 制度信用 vintage forced to settle soon?" — a *structural* flow edge.

Why this is an edge of a different kind than the squeeze pattern:
  - 制度信用 (system-margin) buys and sells carry a **legal 6-month settlement deadline** (返済期限).
    A short opened on 1/15 *must* be bought back by ~7/15 — 現渡し aside, the cover is forced buying
    on a known clock. A margin **long** hitting its deadline is forced *selling* (an overhang).
  - This is the opposite failure mode to a *predictive* pattern: we are not forecasting behaviour,
    we are reading a calendar of obligations the exchange's own rules impose. The squeeze backtest
    showed "high short interest" alone has no edge; the question here is whether the **timing** of
    the forced flow does.

The hard part — the tape gives *balances*, not *ages*:
  The weekly margin print reports the outstanding 制度信用売残/買残, never when each tranche was
  opened. So we reconstruct vintages by **FIFO-decomposing the weekly deltas**:
    - a week where the balance *rises* opens a new vintage layer (age 0, deadline = as_of + 182d);
    - a week where it *falls* covers shares off the **oldest** surviving layer first — the oldest
      tranche is the one nearest its own deadline, so forced/disciplined covering is FIFO by
      construction (voluntary covering may not be, but the deadline binds the front of the queue).
  The surviving layers then carry a date-stamped age distribution. The slice whose deadline falls
  inside the next `deadline_due_window_days` is the **forced-flow fuel**, deflated by ADV into a
  "due days-to-cover" (cross-sectionally comparable, same discipline as fuel.days_to_cover).

Honest approximations (why this is a *measured* hypothesis — the event study decides, not this file):
  - **Rolls** (期日越え: cover the old tranche and reopen) reset the clock; on the tape a roll can
    net to ≈0 delta and never register, so FIFO ages of long-lived books drift slightly young.
  - **現渡し** (deliver owned stock against the short) settles a deadline with *no* buying.
  - Weekly granularity blurs the open date (and thus the deadline) by ±1 print.
  These all *blunt* the signal rather than fake one, which is the safe direction for a falsification
  test. NOT wired into the squeeze scan score — a separate setup sharing the store and the coil.

All reads are publication-date point-in-time (the caller passes margin history already filtered by
store.margin_history/published_date); the FIFO pass never looks past the as-of week.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Callable

from tse_scanner.config import PARAMS, Params

if TYPE_CHECKING:
    import pandas as pd


def _fifo_due_shares(
    as_of_dates: list[date], balances: list[float], asof_idx: int, params: Params
) -> tuple[float, float]:
    """FIFO-decompose `balances[:asof_idx+1]` into vintages; return (due_shares, total) at asof_idx.

    `due_shares` = shares whose 6-month deadline lands within
    [as_of − overdue_grace, as_of + due_window] — i.e. forced to settle imminently. `total` is the
    outstanding balance at asof_idx (so callers can form a concentration fraction). Oldest tranche
    covers first (the binding-deadline assumption). Negative balances / NaNs are treated as 0.
    """
    stack: "deque[list]" = deque()  # FIFO: [open_date, shares] oldest at the left
    prev = 0.0
    for i in range(asof_idx + 1):
        b = balances[i]
        b = float(b) if b is not None and b == b and b > 0 else 0.0
        delta = b - prev
        if delta > 1e-9:  # new shorts opened this week → new vintage at the back
            stack.append([as_of_dates[i], delta])
        elif delta < -1e-9:  # covered → peel the oldest layers first (FIFO)
            need = -delta
            while need > 1e-9 and stack:
                layer = stack[0]
                if layer[1] <= need + 1e-9:
                    need -= layer[1]
                    stack.popleft()
                else:
                    layer[1] -= need
                    need = 0.0
        prev = b

    asof = as_of_dates[asof_idx]
    lo = asof - timedelta(days=params.deadline_overdue_grace_days)
    hi = asof + timedelta(days=params.deadline_due_window_days)
    due = 0.0
    for open_date, shares in stack:
        deadline = open_date + timedelta(days=params.deadline_days)
        if lo <= deadline <= hi:
            due += shares
    return due, prev


def _due_dtc(due_shares: float, adv: float) -> float:
    """Forced-cover shares ÷ ADV (shares) = ADV-days that *must* trade. adv ≤ 0 → inf if any due."""
    if adv is None or adv <= 0:
        return float("inf") if due_shares > 0 else 0.0
    return float(due_shares) / float(adv)


def pressure_timeline(
    margin: "pd.DataFrame", adv_of: "Callable[[date], float]", params: Params = PARAMS
) -> "pd.DataFrame":
    """Per-week forced-flow pressure for one name — the panel the event study consumes.

    `margin` is that name's weekly history (oldest→newest) with columns `as_of_date`,
    `published_date`, `sys_short`, `sys_long`. `adv_of(d)` returns the trailing ADV in **shares**
    known as-of `d` (the caller owns the point-in-time ADV). Returns one row per week with the
    short (期日向かい / K1) and long (期日明け / K2) due slices and their ADV-deflated DtC.
    """
    import pandas as pd

    if margin is None or margin.empty:
        return pd.DataFrame()
    m = margin.sort_values("as_of_date").reset_index(drop=True)
    dates = [d.date() if hasattr(d, "date") else d for d in m["as_of_date"]]
    short = m["sys_short"].tolist() if "sys_short" in m else [0.0] * len(m)
    long_ = m["sys_long"].tolist() if "sys_long" in m else [0.0] * len(m)

    rows = []
    for i in range(len(m)):
        s_due, s_tot = _fifo_due_shares(dates, short, i, params)
        l_due, l_tot = _fifo_due_shares(dates, long_, i, params)
        adv = adv_of(dates[i])
        rows.append({
            "as_of_date": dates[i],
            "published_date": (m["published_date"].iloc[i].date()
                               if hasattr(m["published_date"].iloc[i], "date")
                               else m["published_date"].iloc[i]),
            "sys_short": s_tot,
            "short_due_shares": s_due,
            "short_due_dtc": _due_dtc(s_due, adv),
            "short_due_frac": (s_due / s_tot if s_tot > 0 else 0.0),
            "long_due_shares": l_due,
            "long_due_dtc": _due_dtc(l_due, adv),
        })
    return pd.DataFrame(rows)


@dataclass
class Deadline:
    """Forced-flow state for one name as-of its latest weekly print."""

    short_due_dtc: float    # ADV-days of 制度 short forced to cover in the window (K1 — forced buying)
    short_due_frac: float   # due_shares ÷ outstanding 制度 short (concentration of the wave)
    short_due_shares: float
    long_due_dtc: float     # K2 — 制度 long forced to sell (a *headwind*, recorded for the cohort)
    has_deadline: bool      # gate: short_due_dtc ≥ floor (percentile gate is cross-sectional, caller)
    score: float            # normalized [0,1] — PLACEHOLDER until the event study earns weights


def assess(margin: "pd.DataFrame", adv: float, params: Params = PARAMS) -> Deadline:
    """Latest-week forced-flow snapshot for one name (mirrors fuel.assess / coil.assess).

    `adv` is the trailing ADV in shares known as-of the latest print. The percentile half of the
    gate is cross-sectional and therefore owned by the caller (like fuel's dtc_pctile); `has_deadline`
    here is only the absolute floor. Score is a transparent placeholder: due-DtC capped at a few
    ADV-days, lightly boosted by concentration — weights are not believed until the study speaks.
    """
    tl = pressure_timeline(margin, lambda _d: adv, params)
    if tl.empty:
        return Deadline(0.0, 0.0, 0.0, 0.0, False, 0.0)
    last = tl.iloc[-1]
    s_dtc = float(last["short_due_dtc"])
    s_frac = float(last["short_due_frac"])
    has = s_dtc >= params.deadline_dtc_floor
    base = min(1.0, s_dtc / 3.0) if s_dtc == s_dtc and s_dtc != float("inf") else 1.0
    score = max(0.0, min(1.0, 0.8 * base + 0.2 * min(1.0, s_frac)))
    if not has:
        score *= 0.5
    return Deadline(short_due_dtc=s_dtc, short_due_frac=s_frac,
                    short_due_shares=float(last["short_due_shares"]),
                    long_due_dtc=float(last["long_due_dtc"]), has_deadline=has, score=score)
