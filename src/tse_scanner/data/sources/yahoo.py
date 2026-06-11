"""Free daily adjusted OHLCV via Yahoo Finance / yfinance (DESIGN.md §5).

TSE code ``7203`` → Yahoo symbol ``7203.T``. (Stooq's free CSV is now behind a JS proof-of-work
anti-bot wall, so we use Yahoo, which works.) For the backtest, prefer storing once into DuckDB and
reading locally rather than re-hitting Yahoo per run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


def to_yahoo(code: str) -> str:
    code = str(code).strip()
    return code if code.endswith(".T") else f"{code}.T"


def daily_ohlcv_batch(codes, period: str = "1y", start=None, end=None, chunk: int = 50):
    """Batched OHLCV for many codes → **long** DataFrame: code, date, open, high, low, close, volume.

    yfinance downloads many tickers per request (grouped by ticker); we melt the wide MultiIndex
    frame to one row per (code, date). Chunked to keep each request modest and polite.
    """
    import pandas as pd
    import yfinance as yf

    codes = [str(c).strip() for c in codes]
    frames: list[pd.DataFrame] = []
    for i in range(0, len(codes), chunk):
        batch = codes[i:i + chunk]
        syms = [to_yahoo(c) for c in batch]
        raw = yf.download(syms, period=None if start else period, start=start, end=end,
                          progress=False, auto_adjust=True, group_by="ticker", threads=True)
        if raw is None or raw.empty:
            continue
        for code, sym in zip(batch, syms):
            try:
                sub = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            sub = sub.dropna(how="all")
            if sub.empty:
                continue
            sub = sub.rename(columns={c: c.lower() for c in sub.columns})
            keep = [c for c in ("open", "high", "low", "close", "volume") if c in sub.columns]
            sub = sub[keep].reset_index().rename(columns={"Date": "date", "index": "date"})
            sub["code"] = code
            frames.append(sub)
    if not frames:
        return pd.DataFrame(columns=["code", "date", "open", "high", "low", "close", "volume"])
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out[["code", "date", "open", "high", "low", "close", "volume"]]
