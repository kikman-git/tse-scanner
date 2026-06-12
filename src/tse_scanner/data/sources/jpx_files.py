"""Discover & download JPX's free published statistics files.

The attachment URLs carry a rotating hash segment (…/tvdivq0000001rnl-att/…), so we scrape the
listing page for the *current* link rather than hard-coding it. Polite, low-frequency use only:
these files refresh weekly (margin) / on listing changes (universe).
"""

from __future__ import annotations

import re
from pathlib import Path

BASE = "https://www.jpx.co.jp"
MARGIN_PAGE = f"{BASE}/markets/statistics-equities/margin/05.html"   # 銘柄別信用取引週末残高
UNIVERSE_PAGE = f"{BASE}/markets/statistics-equities/misc/01.html"   # 東証上場銘柄一覧
_HEADERS = {"User-Agent": "tse-scanner/0.0 (personal research)"}


def _get_text(url: str) -> str:
    import httpx

    r = httpx.get(url, headers=_HEADERS, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.text


def latest_weekly_margin_url() -> str:
    """Newest ``syumatsu<YYYYMMDD>NN.pdf`` link on the JPX weekly-margin page."""
    html = _get_text(MARGIN_PAGE)
    hits = re.findall(r'href="(/markets/[^"]*?syumatsu(\d{8})\d*\.pdf)"', html)
    if not hits:
        raise RuntimeError("no syumatsu PDF link found on JPX margin page (layout changed?)")
    path, _date = max(hits, key=lambda h: h[1])
    return BASE + path


def universe_xls_url() -> str:
    """``data_j.xls`` (東証上場銘柄一覧) link."""
    html = _get_text(UNIVERSE_PAGE)
    m = re.search(r'href="(/markets/[^"]*?data_j\.xls)"', html)
    if not m:
        raise RuntimeError("no data_j.xls link found on JPX universe page (layout changed?)")
    return BASE + m.group(1)


def download(url: str, dest: str | Path) -> Path:
    """Stream ``url`` to ``dest`` (creating parents). Returns the path."""
    import httpx

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, headers=_HEADERS, timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return dest
