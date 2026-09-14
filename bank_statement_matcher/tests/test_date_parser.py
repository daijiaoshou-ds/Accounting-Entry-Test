"""测试日期解析器"""
import pytest
from datetime import date
from bank_statement_matcher.src.utils.date_parser import robust_parse_date


def test_standard_date():
    assert robust_parse_date("2024/12/31") == date(2024, 12, 31)
    assert robust_parse_date("2024-12-31") == date(2024, 12, 31)
    assert robust_parse_date("2024.12.31") == date(2024, 12, 31)


def test_compact_date():
    assert robust_parse_date("20241231") == date(2024, 12, 31)
    assert robust_parse_date(20241231) == date(2024, 12, 31)


def test_datetime_with_time():
    assert robust_parse_date("2024/12/31 10:12:35") == date(2024, 12, 31)


def test_none_and_empty():
    assert robust_parse_date(None) is None
    assert robust_parse_date("") is None
    assert robust_parse_date("nan") is None


def test_date_object():
    d = date(2024, 6, 15)
    assert robust_parse_date(d) == d
