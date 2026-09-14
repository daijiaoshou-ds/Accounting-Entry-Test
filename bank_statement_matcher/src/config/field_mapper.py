"""字段智能识别与映射"""
import pandas as pd
from typing import Dict, List, Optional, Tuple


# 序时账字段关键词库
JOURNAL_KEYWORDS = {
    "entry_date": ["日期", "记账日期", "会计日期", "凭证日期", "date"],
    "voucher_no": ["凭证号", "凭证编号", "记字号", "voucher"],
    "abstract": ["摘要", "说明", "备注", "abstract", "summary"],
    "level1_account": ["一级科目", "科目", "会计科目", "account"],
    "detail_account": ["明细科目", "二级科目", "科目明细", "detail account"],
    "debit": ["借方", "借方金额", "借", "debit"],
    "credit": ["贷方", "贷方金额", "贷", "credit"],
    "customer_name": ["客商", "客户", "供应商", "对方单位", "往来单位", "customer", "vendor"],
}

# 银行流水字段关键词库
BANK_KEYWORDS = {
    "tx_date": ["日期", "交易日期", "记账日期", "交易日", "date", "time"],
    "counter_party": ["交易方", "对方户名", "对方账户", "对手方", "counterparty", "对方"],
    "income": ["收入", "收入金额", "贷", "income", "dedit", "转入金额"],
    "expense": ["支出", "支出金额", "借", "expense", "crebit", "转出金额"],
    "abstract": ["摘要", "用途", "备注", "abstract", "summary", "交易说明"],
    "serial_no": ["流水号", "交易流水号", "serial", "transaction id", "交易号"],
}


def _score_column(col_name: str, keywords: List[str]) -> int:
    """计算列名与关键词的匹配得分"""
    col_lower = str(col_name).lower().strip()
    score = 0
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if kw_lower == col_lower:
            score += 100  # 完全匹配
        elif kw_lower in col_lower:
            score += 50   # 包含匹配
        elif any(part in col_lower for part in kw_lower.split()):
            score += 20   # 部分匹配
    return score


class FieldMapper:
    """字段映射器：自动识别 Excel/CSV 表头，支持手动覆盖"""

    def __init__(self):
        self.journal_map: Dict[str, str] = {}               # key -> col_name
        self.bank_maps: Dict[str, Dict[str, str]] = {}      # filename -> {key: col_name}

    # ---------- 序时账 ----------

    def auto_map_journal(self, df: pd.DataFrame) -> Dict[str, str]:
        """自动识别序时账字段"""
        cols = list(df.columns)
        mapping = {}
        for field, keywords in JOURNAL_KEYWORDS.items():
            best_col = None
            best_score = 0
            for col in cols:
                score = _score_column(col, keywords)
                if score > best_score:
                    best_score = score
                    best_col = col
            if best_col:
                mapping[field] = best_col
        self.journal_map = mapping
        return mapping

    def set_journal_field(self, field: str, col_name: str):
        """手动设置序时账字段映射"""
        self.journal_map[field] = col_name

    def get_journal_col(self, field: str) -> Optional[str]:
        return self.journal_map.get(field)

    def validate_journal(self) -> List[str]:
        """返回缺失的必填字段"""
        required = ["entry_date", "voucher_no", "abstract", "level1_account",
                    "detail_account", "debit", "credit"]
        return [f for f in required if f not in self.journal_map]

    # ---------- 银行流水（按文件） ----------

    def auto_map_bank(self, filename: str, df: pd.DataFrame) -> Dict[str, str]:
        """自动识别指定银行流水文件的字段"""
        cols = list(df.columns)
        mapping = {}
        for field, keywords in BANK_KEYWORDS.items():
            best_col = None
            best_score = 0
            for col in cols:
                score = _score_column(col, keywords)
                if score > best_score:
                    best_score = score
                    best_col = col
            if best_col:
                mapping[field] = best_col
        self.bank_maps[filename] = mapping
        return mapping

    def set_bank_field(self, filename: str, field: str, col_name: str):
        """手动设置指定银行流水文件的字段映射"""
        if filename not in self.bank_maps:
            self.bank_maps[filename] = {}
        self.bank_maps[filename][field] = col_name

    def get_bank_col(self, filename: str, field: str) -> Optional[str]:
        return self.bank_maps.get(filename, {}).get(field)

    def validate_bank(self, filename: str) -> List[str]:
        """返回指定银行流水文件缺失的必填字段"""
        required = ["tx_date", "counter_party", "income", "expense", "abstract"]
        return [f for f in required if f not in self.bank_maps.get(filename, {})]

    def validate_all_banks(self) -> List[Tuple[str, List[str]]]:
        """返回所有银行流水文件缺失的必填字段
        返回: [(filename, [missing_fields...]), ...]
        """
        result = []
        for filename in self.bank_maps:
            missing = self.validate_bank(filename)
            if missing:
                result.append((filename, missing))
        return result
