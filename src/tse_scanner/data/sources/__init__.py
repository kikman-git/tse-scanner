"""Free data sources (no subscription) — verified runnable 2026-06-10 (DESIGN.md §5).

- jpx_files    : discover + download JPX's free published statistics files
- jpx_universe : 東証上場銘柄一覧 (data_j.xls) → tradeable universe
- jpx_margin   : 銘柄別信用取引週末残高 (syumatsu PDF) → per-issue 売残/買残 + 貸借(B) gate
- yahoo        : daily adjusted OHLCV via yfinance

Paid J-Quants (jquants.py) is a last-resort add-on for deep *historical* margin only.
"""
