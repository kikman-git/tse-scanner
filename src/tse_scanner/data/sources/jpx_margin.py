"""Free per-issue weekly margin (信用取引残高) from JPX published PDFs.

Source: JPX 銘柄別信用取引週末残高 (free) → ``syumatsu<date>NN.pdf``. `extract_tables()` is useless
(no ruled borders), but `extract_text()` yields regular rows. One data row:

    B 極洋 普通株式 13010 JP3257200000 2,800 ▲ 1,300 132,400 13,400 0 ▲ 100 2,800 ▲ 1,200 42,100 1,200 90,300 12,200
    ^   ^name ^type  ^code5 ^ISIN      └─ 12 numeric cols: short,Δ, long,Δ, [一般売,Δ 制度売,Δ  一般買,Δ 制度買,Δ] ─┘
    │
    └ flag: 'B' = 貸借銘柄 (loan-marginable) — doubles as the universe gate.

The header carries BOTH the as-of (申込み現在) and publication date → free lag handling.
PDF parsing is fragile by nature; `parse_margin_line` is pure (unit-tested) and we validate that
short_total == 一般売 + 制度売.  TODO: capture 増担保/規制 prefix markers (e.g. '*') if present.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

_ISIN = re.compile(r"^JP[0-9A-Z]{10}$")
_CODE5 = re.compile(r"^\d{4}[0-9A-Z]$")
_DATE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_TYPES = ("普通株式", "優先株式", "優先出資証券", "出資証券", "新株予約権", "投資証券", "受益証券")


def _to_numbers(tokens: list[str]) -> list[int]:
    """Tokens → ints. '▲'/'△'/'-' negate the next number; commas are thousands separators."""
    out: list[int] = []
    sign = 1
    for t in tokens:
        if t in ("▲", "△", "-", "−"):
            sign = -1
            continue
        c = t.replace(",", "")
        if c.isdigit():
            out.append(sign * int(c))
        sign = 1
    return out


def parse_margin_line(line: str) -> dict | None:
    """Parse one issue row; return None for headers/section/blank lines. Pure & stdlib-only."""
    tok = line.split()
    idx = next((i for i, t in enumerate(tok) if _ISIN.match(t)), None)
    if idx is None or idx < 1:
        return None
    code5 = tok[idx - 1]
    if not _CODE5.match(code5):
        return None
    flag = tok[0] if (len(tok[0]) == 1 and tok[0].isalpha()) else ""
    n = _to_numbers(tok[idx + 1:])
    if len(n) < 4:
        return None
    front = tok[(1 if flag else 0): idx - 1]  # name + 種別
    name = next((" ".join(front[:i]) for i, t in enumerate(front) if t in _TYPES), " ".join(front))
    rec = {
        "code": code5[:4],
        "code5": code5,
        "name": name,
        "is_taishaku": flag == "B",
        "short_total": n[0],
        "long_total": n[2],
    }
    if len(n) >= 12:
        rec.update(neg_short=n[4], sys_short=n[6], neg_long=n[8], sys_long=n[10])
    return rec


def parse_dates(pdf_path: str | Path) -> tuple[date | None, date | None]:
    """(as_of 申込み現在, published) from the PDF header — the lag, for free."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        head = pdf.pages[0].extract_text() or ""
    ds = [date(int(y), int(m), int(d)) for y, m, d in _DATE.findall(head)]
    if not ds:
        return None, None
    # as-of (申込み現在, Fri) is the earliest header date; publication (Tue) is the latest.
    return min(ds), (max(ds) if len(set(ds)) > 1 else None)


def parse_weekly_margin(pdf_path: str | Path) -> "pd.DataFrame":
    """Parse a full syumatsu PDF into a per-issue DataFrame (one row per code)."""
    import pandas as pd
    import pdfplumber

    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").splitlines():
                rec = parse_margin_line(line)
                if rec:
                    rows.append(rec)
    return pd.DataFrame(rows).drop_duplicates("code5").reset_index(drop=True)
