"""Free TSE listed-issue universe from JPX 東証上場銘柄一覧 ``data_j.xls``.

Columns (Shift-JIS.xls): 日付, コード, 銘柄名, 市場・商品区分, 33業種コード/区分, 17業種…, 規模…
Gives code, name, segment, sector. The 貸借銘柄 gate itself comes from the margin file's `B` flag
(jpx_margin), not from here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

_SEGMENT = {
    "プライム（内国株式）": "prime",
    "スタンダード（内国株式）": "standard",
    "グロース（内国株式）": "growth",
}


def parse_universe(xls_path: str | Path) -> "pd.DataFrame":
    import pandas as pd

    u = pd.read_excel(xls_path)  #.xls → xlrd engine
    u = u.rename(columns={"コード": "code", "銘柄名": "name",
                          "市場・商品区分": "segment_ja", "33業種区分": "sector33"})
    u["code"] = u["code"].astype(str).str.strip()
    u["segment"] = u["segment_ja"].map(_SEGMENT).fillna("other")
    u["is_domestic_equity"] = u["segment"].isin(["prime", "standard", "growth"])
    return u[["code", "name", "segment", "segment_ja", "sector33", "is_domestic_equity"]]
