"""测试小 Block 匹配"""
import pytest
from bank_statement_matcher.src.core.small_block import solve_small_block
from bank_statement_matcher.src.models.journal import JournalEntry
from bank_statement_matcher.src.models.bank_statement import BankEntry
from datetime import date


def make_gl(idx, amount, dt=None, customer=None):
    return JournalEntry(
        idx=idx, entry_date=dt or date(2024, 1, 1),
        voucher_no="V001", abstract="test",
        level1_account="银行存款", detail_account="建行",
        debit=amount if amount > 0 else 0,
        credit=-amount if amount < 0 else 0,
        customer_name=customer,
    )


def make_bank(idx, amount, dt=None, party="A"):
    return BankEntry(
        idx=idx, tx_date=dt or date(2024, 1, 1),
        counter_party=party, abstract="test",
        income=amount if amount > 0 else 0,
        expense=-amount if amount < 0 else 0,
    )


def test_one_to_one():
    gls = [make_gl(0, 100), make_gl(1, 200)]
    banks = [make_bank(0, 100), make_bank(1, 200)]
    matches, ug, ub = solve_small_block(gls, banks)
    assert len(matches) == 2
    assert len(ug) == 0
    assert len(ub) == 0


def test_aggregation():
    # GL: 300
    # Bank: 100 + 200 (同一交易方，同一日期)
    gls = [make_gl(0, 300)]
    banks = [
        make_bank(0, 100, party="滴滴"),
        make_bank(1, 200, party="滴滴"),
    ]
    matches, ug, ub = solve_small_block(gls, banks)
    assert len(matches) == 1
    assert matches[0][2].startswith("agg")
    assert len(ug) == 0
    assert len(ub) == 0


def test_subset_sum():
    # GL: 300
    # Bank: 100, 150, 50 (设置不同交易方和摘要，避免被聚合策略提前匹配)
    gls = [make_gl(0, 300)]
    banks = [
        make_bank(0, 100, party="A"),
        make_bank(1, 150, party="B"),
        make_bank(2, 50, party="C"),
    ]
    # 修改摘要以避免聚合
    banks[0].abstract = "abs1"
    banks[1].abstract = "abs2"
    banks[2].abstract = "abs3"
    matches, ug, ub = solve_small_block(gls, banks)
    assert len(matches) == 1
    assert matches[0][2] in ("dp_subset", "backtrack_subset")
    assert len(ug) == 0
    assert len(ub) == 0
