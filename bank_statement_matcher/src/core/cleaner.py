"""数据清洗模块"""
import pandas as pd
from typing import List, Tuple

from bank_statement_matcher.src.models.journal import JournalEntry
from bank_statement_matcher.src.models.bank_statement import BankEntry
from bank_statement_matcher.src.utils.date_parser import robust_parse_date


def clean_journal(
    df: pd.DataFrame,
    col_map: dict,
) -> Tuple[List[JournalEntry], List[dict]]:
    """
    清洗序时账数据
    1. 筛选一级科目为"银行存款"的记录
    2. 解析日期
    3. 转换为 JournalEntry 对象
    返回: (有效记录列表, 清洗失败记录列表)
    """
    entries = []
    errors = []

    # 筛选一级科目包含"银行存款"的记录
    level1_col = col_map.get("level1_account")
    if level1_col and level1_col in df.columns:
        mask = df[level1_col].astype(str).str.contains("银行存款", na=False)
        df = df[mask].copy()

    for idx, row in df.iterrows():
        try:
            d = row.to_dict()
            entry_date = robust_parse_date(d.get(col_map.get("entry_date")))
            if entry_date is None:
                errors.append({"index": idx, "reason": "日期解析失败", "raw": d})
                continue

            debit = _parse_amount(d.get(col_map.get("debit")))
            credit = _parse_amount(d.get(col_map.get("credit")))

            customer_name = _str_or_empty(d.get(col_map.get("customer_name")))
            # 解析多个客商（由对方科目分析生成，用"，"分隔）
            counterparties = [p.strip() for p in customer_name.split("，") if p.strip()] if customer_name else []

            entry = JournalEntry(
                idx=int(idx),
                entry_date=entry_date,
                voucher_no=_str_or_empty(d.get(col_map.get("voucher_no"))),
                abstract=_str_or_empty(d.get(col_map.get("abstract"))),
                level1_account=_str_or_empty(d.get(col_map.get("level1_account"))),
                detail_account=_str_or_empty(d.get(col_map.get("detail_account"))),
                debit=debit,
                credit=credit,
                customer_name=customer_name,
                counterparties=counterparties,
            )
            entries.append(entry)
        except Exception as e:
            errors.append({"index": idx, "reason": str(e), "raw": row.to_dict()})

    # 严格保留 Excel 原始行序，不排序
    return entries, errors


def clean_bank(
    df: pd.DataFrame,
    col_map: dict,
) -> Tuple[List[BankEntry], List[dict]]:
    """
    清洗银行流水数据
    1. 解析日期
    2. 转换为 BankEntry 对象
    返回: (有效记录列表, 清洗失败记录列表)
    """
    entries = []
    errors = []

    for idx, row in df.iterrows():
        try:
            d = row.to_dict()
            tx_date = robust_parse_date(d.get(col_map.get("tx_date")))
            if tx_date is None:
                errors.append({"index": idx, "reason": "日期解析失败", "raw": d})
                continue

            income = _parse_amount(d.get(col_map.get("income")))
            expense = _parse_amount(d.get(col_map.get("expense")))

            entry = BankEntry(
                idx=int(idx),
                tx_date=tx_date,
                counter_party=_str_or_empty(d.get(col_map.get("counter_party"))),
                income=income,
                expense=expense,
                abstract=_str_or_empty(d.get(col_map.get("abstract"))),
                serial_no=_str_or_empty(d.get(col_map.get("serial_no"))),
            )
            entries.append(entry)
        except Exception as e:
            errors.append({"index": idx, "reason": str(e), "raw": row.to_dict()})

    # 严格保留 Excel 原始行序，不排序
    return entries, errors


def split_by_detail_account(entries: List[JournalEntry]) -> dict:
    """
    按明细科目分账，一个明细科目对应一份银行流水
    返回: {detail_account: [JournalEntry, ...]}
    """
    result = {}
    for e in entries:
        key = e.detail_account or "未分类"
        result.setdefault(key, []).append(e)
    return result


def split_by_month_and_direction(entries: list):
    """
    通用拆分：按月份 + 收支方向拆分
    
    银行存款科目逻辑：
    - 序时账借方 = 存款增加 = 银行收入 → income 组
    - 序时账贷方 = 存款减少 = 银行支出 → expense 组
    - 银行流水收入 → income 组
    - 银行流水支出 → expense 组
    
    返回: {
        (year, month, 'income'): [entries...],
        (year, month, 'expense'): [entries...],
    }
    """
    from collections import defaultdict
    from bank_statement_matcher.src.models.journal import JournalEntry
    from bank_statement_matcher.src.models.bank_statement import BankEntry

    result = defaultdict(list)
    for e in entries:
        if hasattr(e, 'entry_date'):
            d = e.entry_date
        elif hasattr(e, 'tx_date'):
            d = e.tx_date
        else:
            continue

        key_month = (d.year, d.month)

        if isinstance(e, JournalEntry):
            # 银行存款：借方 = 收入，贷方 = 支出
            if e.is_debit:
                result[(key_month, 'income')].append(e)
            elif e.is_credit:
                result[(key_month, 'expense')].append(e)
        elif isinstance(e, BankEntry):
            if e.is_income:
                result[(key_month, 'income')].append(e)
            elif e.is_expense:
                result[(key_month, 'expense')].append(e)
    return result


def _parse_amount(val) -> float:
    """解析金额，处理各种格式，强制保留2位小数消除浮点精度问题"""
    if val is None:
        return 0.0
    if isinstance(val, float) and pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = str(val).strip().replace(",", "").replace("，", "")
    # 处理括号负数 (100.00) -> -100.00
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return round(float(s), 2)
    except ValueError:
        return 0.0


def _str_or_empty(val) -> str:
    """安全转字符串"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()
