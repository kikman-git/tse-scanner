"""Unit tests for the free JPX weekly-margin PDF line parser (pure; no PDF/network)."""

from tse_scanner.data.sources.jpx_margin import parse_margin_line


def test_parses_taishaku_row():
    line = ("B 極洋 普通株式 13010 JP3257200000 2,800 ▲ 1,300 132,400 13,400 "
            "0 ▲ 100 2,800 ▲ 1,200 42,100 1,200 90,300 12,200")
    r = parse_margin_line(line)
    assert r["code"] == "1301" and r["code5"] == "13010"
    assert r["is_taishaku"] is True
    assert r["short_total"] == 2800 and r["long_total"] == 132400
    # breakdown consistency: 一般売 + 制度売 == 売残合計 ; same for 買
    assert r["neg_short"] + r["sys_short"] == r["short_total"]
    assert r["neg_long"] + r["sys_long"] == r["long_total"]


def test_negative_change_and_4digit_code():
    line = ("B ニッスイ 普通株式 13320 JP3718800000 164,500 10,300 192,000 ▲ 9,200 "
            "128,900 900 35,600 9,400 76,900 1,100 115,100 ▲ 10,300")
    r = parse_margin_line(line)
    assert r["code"] == "1332"
    assert r["short_total"] == 164500 and r["long_total"] == 192000


def test_section_and_header_lines_return_none():
    assert parse_margin_line("貸借銘柄 loan trading issue 2675 銘柄") is None
    assert parse_margin_line("プライム Prime 1482 銘柄") is None
    assert parse_margin_line("") is None
