"""测试最小平扫区域分块"""
import pytest
from bank_statement_matcher.src.core.block_splitter import split_into_blocks
from bank_statement_matcher.src.models.journal import JournalEntry
from bank_statement_matcher.src.models.bank_statement import BankEntry
from datetime import date


def make_gl(idx, amount, dt=None):
    return JournalEntry(
        idx=idx, entry_date=dt or date(2024, 1, 1),
        voucher_no="V001", abstract="test",
        level1_account="银行存款", detail_account="建行",
        debit=amount if amount > 0 else 0,
        credit=-amount if amount < 0 else 0,
    )


def make_bank(idx, amount, dt=None):
    return BankEntry(
        idx=idx, tx_date=dt or date(2024, 1, 1),
        counter_party="A", abstract="test",
        income=amount if amount > 0 else 0,
        expense=-amount if amount < 0 else 0,
    )


def test_perfect_split():
    # GL: [100, 200, 300] => cumsum [100, 300, 600]
    # Bank: [100, 200, 300] => cumsum [100, 300, 600]
    gls = [make_gl(0, 100), make_gl(1, 200), make_gl(2, 300)]
    banks = [make_bank(0, 100), make_bank(1, 200), make_bank(2, 300)]
    blocks, log = split_into_blocks(gls, banks)
    assert len(blocks) == 3
    assert len(blocks[0][0]) == 1 and len(blocks[0][1]) == 1
    assert len(blocks[1][0]) == 1 and len(blocks[1][1]) == 1
    assert len(blocks[2][0]) == 1 and len(blocks[2][1]) == 1
    # 验证 log
    assert log["gl_total"] == 600
    assert log["bank_total"] == 600
    assert len(log["cut_points"]) == 4  # (0,0), (1,1), (2,2), (3,3)
    assert len(log["blocks"]) == 3


def test_combined_split():
    # GL: [100, 200] => cumsum [100, 300]
    # Bank: [50, 50, 200] => cumsum [50, 100, 300]
    gls = [make_gl(0, 100), make_gl(1, 200)]
    banks = [make_bank(0, 50), make_bank(1, 50), make_bank(2, 200)]
    blocks, log = split_into_blocks(gls, banks)
    # 应该在累计额 300 处切分
    total_gl = sum(len(b[0]) for b in blocks)
    total_bank = sum(len(b[1]) for b in blocks)
    assert total_gl == 2
    assert total_bank == 3
    assert len(blocks) == 2
    # 验证 block log: 第一个 block GL[0:1] vs Bank[0:2]
    assert log["blocks"][0]["gl_count"] == 1
    assert log["blocks"][0]["bank_count"] == 2
    assert log["blocks"][0]["gl_sum"] == 100
    assert log["blocks"][0]["bank_sum"] == 100


def test_multi_blocks():
    """测试多 block 场景，模拟用户数据中 1 月支出有 8 个 block 的情况"""
    # GL: [100, 200, 50, 150, 100, 100, 50, 50] => cumsum [100, 300, 350, 500, 600, 700, 750, 800]
    # Bank: [50, 50, 200, 50, 150, 100, 50, 50, 50, 50] => cumsum [50, 100, 300, 350, 500, 600, 650, 700, 750, 800]
    # 交集点: 100(GL[0]=Bank[1]), 300(GL[1]=Bank[2]), 350(GL[2]=Bank[3]), 500(GL[3]=Bank[4]), 600(GL[4]=Bank[5]), 700(GL[5]=Bank[7]), 750(GL[6]=Bank[8]), 800(GL[7]=Bank[9])
    # 共 8 个 block
    gls = [
        make_gl(0, 100), make_gl(1, 200), make_gl(2, 50), make_gl(3, 150),
        make_gl(4, 100), make_gl(5, 100), make_gl(6, 50), make_gl(7, 50),
    ]
    banks = [
        make_bank(0, 50), make_bank(1, 50), make_bank(2, 200), make_bank(3, 50),
        make_bank(4, 150), make_bank(5, 100), make_bank(6, 50), make_bank(7, 50),
        make_bank(8, 50), make_bank(9, 50),
    ]
    blocks, log = split_into_blocks(gls, banks)
    assert len(blocks) == 8, f"期望 8 个 block，实际 {len(blocks)} 个"
    assert log["gl_total"] == 800
    assert log["bank_total"] == 800
