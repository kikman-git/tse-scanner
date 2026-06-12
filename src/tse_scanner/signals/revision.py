"""Revision — "is an 上方修正 loaded?" — the anticipatory arm of the revision setup (2026-06-12).

Why this is predictable at all (TSE-specific structure):
  - TSE timely-disclosure rules **mandate** a 業績予想修正 filing once internal forecasts deviate
    from published guidance (sales ±10%, profits ±30%) — the revision is a *legal threshold*, not
    a PR choice.
  - Japanese companies ritually sandbag initial guidance (firm-sticky behavior), so "actual runs
    ahead of lowballed guidance" is a recurring, observable buildup with a mandated release.
  - Most 修正/決算 land at/after the 15:00–15:30 close → the surprise prints as an opening gap
    that can't be bought (often straight to ストップ高). Hence this module: estimate *whether a
    revision is loaded* and *when it must land*, position at the coil/sweep entry **before** it,
    with a structural stop. The reactive arm (buy the post-print drift) is the baseline cohort
    the event study compares against.

All features read only `fins_summary` rows disclosed strictly before as-of (store.fins_history
strict=True) — same point-in-time discipline as the margin tape, one day tighter because of the
after-close convention. 経常利益 (odp) falls back when 営業利益 (op) is not guided — JP small caps
often headline 経常 (and banks/shippers guide it exclusively).

Score weights are PLACEHOLDERS until the event study earns them (same philosophy as fuel's
boosters): the verdict comes from cohort splits over probability deciles, never from this file.
NOT wired into the squeeze scan score — this is a separate setup sharing the store and the coil.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from tse_scanner.config import PARAMS, Params

if TYPE_CHECKING:
    import pandas as pd

_QUARTER_DOCS = ("1Q", "2Q", "3Q")


def _pick_metric(hist: "pd.DataFrame") -> str:
    """Prefer 営業利益 (op) when the name guides it; fall back to 経常利益 (odp)."""
    if hist.empty:
        return "op"
    f_op_prints = hist["f_op"].notna().sum()
    f_odp_prints = hist["f_odp"].notna().sum()
    return "op" if f_op_prints >= f_odp_prints else "odp"


def _statements(hist: "pd.DataFrame") -> "pd.DataFrame":
    """Quarterly/FY 決算短信 rows only (the actuals carriers), disc-date ordered."""
    return hist[hist["doc_type"].astype(str).str.contains("FinancialStatements", na=False)]


def guidance_timeline(hist: "pd.DataFrame", fy_end, metric: str = "op") -> "pd.DataFrame":
    """All guidance prints for the FY ending `fy_end`, disc-date ordered: (disc_date, value).

    Two sources, merged:
      - rows of that FY carrying a full-year forecast (`f_<metric>`) — quarterly 短信 restate it,
        standalone 修正 update it;
      - the *prior* FY's FY-doc carrying next-FY guidance (`nxf_<metric>`) — usually the true
        initial print, joined exactly via its stored `nxt_fy_end`.
    """
    import pandas as pd

    f_col, nxf_col = f"f_{metric}", f"nxf_{metric}"
    own = hist[(hist["fy_end"] == fy_end) & hist[f_col].notna()][["disc_date", f_col]]
    own = own.rename(columns={f_col: "value"})
    prior = hist[(hist["nxt_fy_end"] == fy_end) & hist[nxf_col].notna()][["disc_date", nxf_col]]
    prior = prior.rename(columns={nxf_col: "value"})
    tl = pd.concat([prior, own], ignore_index=True).sort_values("disc_date")
    return tl.reset_index(drop=True)


def sandbagger_score(hist: "pd.DataFrame", params: Params = PARAMS) -> float:
    """Fraction of recent completed FYs where the actual beat *initial* guidance by ≥ the margin.

    Initial guidance = earliest print in `guidance_timeline` (usually the prior FY's FY-doc).
    FYs with non-positive initial guidance are skipped (beat-margin semantics break across zero).
    NaN with < 2 usable FYs — "no track record" must not read as "never sandbags".
    """
    metric = _pick_metric(hist)
    stmts = _statements(hist)
    fy_docs = stmts[(stmts["per_type"] == "FY") & stmts[metric].notna()]
    if fy_docs.empty:
        return float("nan")
    fy_docs = fy_docs.sort_values("disc_date").tail(params.rev_sandbag_lookback_fys)

    usable = beats = 0
    for r in fy_docs.itertuples(index=False):
        tl = guidance_timeline(hist, r.fy_end, metric)
        if tl.empty:
            continue
        initial = float(tl.iloc[0]["value"])
        if initial <= 0:
            continue
        usable += 1
        if float(getattr(r, metric)) >= initial * (1.0 + params.rev_beat_margin):
            beats += 1
    return beats / usable if usable >= 2 else float("nan")


def progress_anomaly(hist: "pd.DataFrame") -> float:
    """Latest interim progress vs this name's own seasonal pace: >1 = running ahead of guidance.

    progress  = cumulative actual (latest 1Q/2Q/3Q print of the current FY) ÷ current FY guidance
    baseline  = median over past FYs of (same-quarter cumulative actual ÷ that FY's final actual)
    anomaly   = progress ÷ baseline

    The ±30% mandatory-revision rule operates on the *guidance* denominator, so progress >> its
    seasonal norm means the filing threshold is being approached mechanically.
    """
    metric = _pick_metric(hist)
    stmts = _statements(hist)
    qs = stmts[stmts["per_type"].isin(_QUARTER_DOCS) & stmts[metric].notna()]
    if qs.empty:
        return float("nan")
    last_q = qs.sort_values("disc_date").iloc[-1]
    fy_end, q = last_q["fy_end"], last_q["per_type"]

    tl = guidance_timeline(hist, fy_end, metric)
    if tl.empty:
        return float("nan")
    guidance = float(tl.iloc[-1]["value"])  # latest guidance in force
    if guidance <= 0:
        return float("nan")
    progress = float(last_q[metric]) / guidance

    fy_actuals = stmts[(stmts["per_type"] == "FY") & stmts[metric].notna()]
    fy_actuals = fy_actuals[fy_actuals["fy_end"] != fy_end]
    ratios: list[float] = []
    for r in fy_actuals.itertuples(index=False):
        actual = float(getattr(r, metric))
        if actual <= 0:
            continue
        same_q = qs[(qs["fy_end"] == r.fy_end) & (qs["per_type"] == q)]
        if same_q.empty:
            continue
        ratios.append(float(same_q.iloc[-1][metric]) / actual)
    if not ratios:
        return float("nan")
    ratios.sort()
    baseline = ratios[len(ratios) // 2] if len(ratios) % 2 else (
        (ratios[len(ratios) // 2 - 1] + ratios[len(ratios) // 2]) / 2)
    if baseline <= 0.05:  # degenerate seasonality (e.g. all-H2 earner) — anomaly unreliable
        return float("nan")
    return progress / baseline


def lowball_ratio(hist: "pd.DataFrame") -> float:
    """Current FY's *initial* guidance vs prior FY's actual − 1. Negative = guiding below run-rate."""
    metric = _pick_metric(hist)
    stmts = _statements(hist)
    fy_docs = stmts[(stmts["per_type"] == "FY") & stmts[metric].notna()].sort_values("disc_date")
    if fy_docs.empty:
        return float("nan")
    last_fy = fy_docs.iloc[-1]
    prior_actual = float(last_fy[metric])
    if prior_actual <= 0:
        return float("nan")
    # current FY = the one the last FY-doc guides next (exact join via nxt_fy_end)
    cur_fy_end = last_fy["nxt_fy_end"]
    if cur_fy_end is None or cur_fy_end != cur_fy_end:
        return float("nan")
    tl = guidance_timeline(hist, cur_fy_end, metric)
    if tl.empty:
        return float("nan")
    return float(tl.iloc[0]["value"]) / prior_actual - 1.0


def days_to_next_announcement(hist: "pd.DataFrame", asof: date) -> float:
    """Estimated days to the next scheduled 決算 print, from this name's own filing anniversaries.

    The official forward calendar has no history (live probe), so the backtestable estimate is
    "last year's same disclosure + 1 year" (companies keep stable reporting cadences; JP filing
    dates drift only days year-to-year). Negative ≤ −7 (long past) → NaN.
    """
    stmts = _statements(hist)
    if stmts.empty:
        return float("nan")
    cands = sorted(d + timedelta(days=365) for d in stmts["disc_date"])
    future = [d for d in cands if d >= asof - timedelta(days=7)]
    if not future:
        return float("nan")
    return float((future[0] - asof).days)


@dataclass
class Revision:
    """Computed revision-pressure state for one name as-of a date."""

    metric: str               # 'op' or 'odp' — which P&L line the features used
    sandbag: float            # [0,1] fraction of FYs beating initial guidance (NaN = no record)
    anomaly: float            # progress vs own seasonal pace (>1 = ahead; NaN = not computable)
    lowball: float            # initial guidance ÷ prior actual − 1 (< 0 = lowballed)
    days_to_announce: float   # estimated days to the next 決算 print (NaN = unknown)
    score: float              # composite [0,1] — PLACEHOLDER weights until the event study


def assess(hist: "pd.DataFrame", asof: date, params: Params = PARAMS) -> Revision:
    """Compose the features into one transparent score. `hist` must already be point-in-time
    (store.fins_history(code, asof, strict=True)) — this function never filters dates itself."""
    metric = _pick_metric(hist)
    sb = sandbagger_score(hist, params)
    an = progress_anomaly(hist)
    lb = lowball_ratio(hist)
    dta = days_to_next_announcement(hist, asof)

    sb_c = sb if sb == sb else 0.0
    an_c = max(0.0, min(1.0, an - 1.0)) if an == an else 0.0      # 2× ahead of pace → 1.0
    lb_c = 1.0 if (lb == lb and lb < -params.rev_beat_margin) else 0.0
    wp_c = max(0.0, min(1.0, 1.0 - dta / params.rev_window_days)) if dta == dta else 0.0
    score = 0.35 * sb_c + 0.30 * an_c + 0.20 * lb_c + 0.15 * wp_c
    return Revision(metric=metric, sandbag=sb, anomaly=an, lowball=lb,
                    days_to_announce=dta, score=max(0.0, min(1.0, score)))
