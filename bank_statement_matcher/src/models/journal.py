"""序时账数据模型"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, List


@dataclass
class JournalEntry:
    """序时账单条记录"""
    idx: int                          # 原始行号/索引
    entry_date: date                  # 记账日期
    voucher_no: str                   # 凭证号
    abstract: str                     # 摘要
    level1_account: str               # 一级科目
    detail_account: str               # 明细科目
    debit: float                      # 借方金额
    credit: float                     # 贷方金额
    customer_name: Optional[str] = None  # 客商名称（可选）
    counterparties: List[str] = field(default_factory=list)  # 对方科目分析后的客商列表（变形后填充）

    @property
    def amount(self) -> float:
        """返回有效金额（借方正数，贷方负数，用于收支判断）"""
        if self.debit > 0:
            return self.debit
        if self.credit > 0:
            return -self.credit
        return 0.0

    @property
    def is_debit(self) -> bool:
        """是否为借方（支出）"""
        return self.debit > 0

    @property
    def is_credit(self) -> bool:
        """是否为贷方（收入）"""
        return self.credit > 0

    def __hash__(self):
        return hash(self.idx)

    def __eq__(self, other):
        if not isinstance(other, JournalEntry):
            return False
        return self.idx == other.idx
