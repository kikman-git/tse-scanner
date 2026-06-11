"""日証金 (Japan Securities Finance / JSF) — official free daily 貸借 + 逆日歩 (DESIGN.md §3.1, §4).

The cleanest FREE source for the 逆日歩 booster and *daily* (fresher-than-weekly) loan balances.
Unlike kabutan / minkabu / Yahoo-JP, JSF is an **official publisher**, so use is defensible — no
commercial-ToS problem. Covers 貸借銘柄 only, which is exactly the scanner's universe (§5).

Why not scrape kabutan/minkabu/Yahoo-JP for 逆日歩 instead (DESIGN.md §5 policy):
  - their ToS prohibits automated / bulk scraping & redistribution (robots.txt permissiveness ≠ ToS),
  - the 信用残/逆日歩 numbers are JS-rendered (need a headless browser; fragile, heavy),
  - JSF publishes the same 逆日歩/貸借 data officially.

Status: Phase 4 (DESIGN.md §9). Stub — confirm the current download URL/format before wiring.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

JSF_BASE = "https://www.jsf.co.jp"  # 貸借取引残高 / 逆日歩(品貸料) published daily after the close


def daily_taishaku(as_of: date | None = None) -> "pd.DataFrame":
    """Daily 貸借取引残高 + 逆日歩 per 貸借銘柄, as-of `as_of` (latest if None)."""
    raise NotImplementedError("Phase 4: wire 日証金 daily 貸借/逆日歩 booster (DESIGN.md §3.1)")
