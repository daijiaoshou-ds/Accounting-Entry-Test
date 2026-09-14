"""银行流水数据模型"""
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class BankEntry:
    """银行流水单条记录"""
    idx: int                          # 原始行号/索引
    tx_date: date                     # 交易日期
    counter_party: str                # 交易方
    income: float                     # 收入金额
    expense: float                    # 支出金额
    abstract: str                     # 摘要
    serial_no: Optional[str] = None   # 流水号

    @property
    def amount(self) -> float:
        """返回有效金额（收入为正，支出为负）"""
        if self.income > 0:
            return self.income
        if self.expense > 0:
            return -self.expense
        return 0.0

    @property
    def is_income(self) -> bool:
        """是否为收入"""
        return self.income > 0

    @property
    def is_expense(self) -> bool:
        """是否为支出"""
        return self.expense > 0

    def __hash__(self):
        return hash(self.idx)

    def __eq__(self, other):
        if not isinstance(other, BankEntry):
            return False
        return self.idx == other.idx
