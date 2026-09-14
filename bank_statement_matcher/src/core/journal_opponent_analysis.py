"""序时账对方科目分析：将原始序时账变形为只含银行存款行的变形序时账

核心逻辑（Modify.md 理论实现）：
1. 按凭证号(voucher_no)分组
2. 每个凭证内识别 bank_rows（一级科目含"银行存款"）和 other_rows（其余科目）
3. 根据科目复杂度判定类型：
   - 简单：非银行只有一个一级科目，且只有一个银行账户明细
     → 按金额拆分 bank_rows，每行携带对应 other_row 的客商
   - 多银行账户：非银行只有一个一级科目，但有多个银行账户明细
     → 尝试按金额一一对应拆分；若无法拆分，退化为复杂聚合
   - 复杂：非银行有多个一级科目
     → 每个 bank_row 携带该凭证下所有 other_rows 的客商列表

输出：只包含变形后银行存款行的 DataFrame
"""
import pandas as pd
from typing import List, Dict, Tuple


# 客商分隔符（用于在 DataFrame 单元格内存储多个客商）
PARTY_DELIMITER = "，"


def transform_journal_df(df: pd.DataFrame, col_map: Dict[str, str]) -> Tuple[pd.DataFrame, dict]:
    """
    对原始序时账进行对方科目分析，返回变形后的 DataFrame（仅含银行存款行）

    参数:
        df: 原始序时账 DataFrame（含所有科目）
        col_map: 字段映射 {field_name: column_name}

    返回:
        (变形后的 DataFrame, 分析统计)
        统计字段: simple_count, complex_count, multi_bank_count, no_counterparty_count
    """
    stats = {"simple_count": 0, "complex_count": 0, "multi_bank_count": 0, "no_counterparty_count": 0}

    if df.empty:
        return df.copy(), stats

    voucher_col = col_map.get("voucher_no")
    entry_date_col = col_map.get("entry_date")
    level1_col = col_map.get("level1_account")
    debit_col = col_map.get("debit")
    credit_col = col_map.get("credit")
    customer_col = col_map.get("customer_name")
    detail_col = col_map.get("detail_account")

    # 缺少关键列则无法分析，直接返回原始 df（保留银行行）
    if not all([voucher_col, level1_col, debit_col, credit_col]):
        mask = df[level1_col].astype(str).str.contains("银行存款", na=False)
        return df[mask].copy(), stats

    # 构建复合分组键：年月 + 凭证号
    # 凭证号在不同月份会重复（如每月都有记-0001），单纯按凭证号分组会把不同月份的凭证混在一起
    if entry_date_col and entry_date_col in df.columns:
        year_month = pd.to_datetime(df[entry_date_col], errors='coerce').dt.strftime('%Y-%m')
        group_keys = [year_month, df[voucher_col]]
    else:
        group_keys = [df[voucher_col]]

    transformed_rows: List[dict] = []

    for _, group in df.groupby(group_keys, sort=False):
        # 保留原始行顺序（groupby 默认 sort=False）
        bank_mask = group[level1_col].astype(str).str.contains("银行存款", na=False)
        bank_rows = group[bank_mask]
        other_rows = group[~bank_mask]

        if bank_rows.empty:
            continue

        # 提取非银行科目的一级科目集合
        other_subjects = set()
        if not other_rows.empty:
            other_subjects = set(
                str(v).strip() for v in other_rows[level1_col].dropna().unique() if str(v).strip()
            )

        # 提取银行账户明细集合
        bank_accounts = set()
        if detail_col and detail_col in bank_rows.columns:
            bank_accounts = set(
                str(v).strip() for v in bank_rows[detail_col].dropna().unique() if str(v).strip()
            )

        # 判定类型并处理
        if len(other_subjects) == 0:
            # 只有银行存款，无对方科目
            stats["no_counterparty_count"] += 1
            for _, row in bank_rows.iterrows():
                new_row = row.to_dict()
                if customer_col and customer_col in new_row:
                    new_row[customer_col] = ""
                transformed_rows.append(new_row)

        elif len(other_subjects) == 1 and len(bank_accounts) <= 1:
            # 简单类型
            stats["simple_count"] += 1
            transformed_rows.extend(_split_simple(
                bank_rows, other_rows, debit_col, credit_col, customer_col
            ))

        elif len(other_subjects) == 1 and len(bank_accounts) > 1:
            # 多银行账户：尝试金额对应拆分
            stats["multi_bank_count"] += 1
            split_result = _try_split_multi_bank(
                bank_rows, other_rows, debit_col, credit_col, customer_col
            )
            if split_result:
                transformed_rows.extend(split_result)
            else:
                # 金额无法对应，退化为复杂聚合
                transformed_rows.extend(_aggregate_complex(
                    bank_rows, other_rows, customer_col
                ))

        else:
            # 复杂类型
            stats["complex_count"] += 1
            transformed_rows.extend(_aggregate_complex(
                bank_rows, other_rows, customer_col
            ))

    if not transformed_rows:
        return df.iloc[0:0].copy(), stats

    result_df = pd.DataFrame(transformed_rows)
    result_df = result_df[[c for c in df.columns if c in result_df.columns]]
    return result_df, stats


def _split_simple(
    bank_rows: pd.DataFrame,
    other_rows: pd.DataFrame,
    debit_col: str,
    credit_col: str,
    customer_col: str
) -> List[dict]:
    """
    简单类型：将单个 bank_row 按 other_rows 的金额拆分
    新行数 = other_rows 行数，每行金额和客商与对应 other_row 一致
    新行保持原始 bank_row 的借贷方向
    """
    result = []
    # 以第一行 bank_row 为模板
    bank_template = bank_rows.iloc[0].to_dict() if not bank_rows.empty else {}

    # 确定原始 bank_row 的借贷方向
    bank_debit = _to_float(bank_template.get(debit_col, 0))
    bank_credit = _to_float(bank_template.get(credit_col, 0))
    bank_is_debit = bank_debit > 0

    for _, other in other_rows.iterrows():
        new_row = dict(bank_template)
        # other_row 的金额（取非零值）
        other_debit = _to_float(other.get(debit_col, 0))
        other_credit = _to_float(other.get(credit_col, 0))
        other_amt = other_debit if other_debit > 0 else other_credit

        # 新行保持 bank 的借贷方向
        if bank_is_debit:
            new_row[debit_col] = other_amt
            new_row[credit_col] = 0
        else:
            new_row[debit_col] = 0
            new_row[credit_col] = other_amt

        # 客商
        if customer_col:
            party = other.get(customer_col, "")
            new_row[customer_col] = str(party) if pd.notna(party) else ""
        result.append(new_row)

    return result


def _try_split_multi_bank(
    bank_rows: pd.DataFrame,
    other_rows: pd.DataFrame,
    debit_col: str,
    credit_col: str,
    customer_col: str
) -> List[dict]:
    """
    多银行账户：尝试按金额一一对应拆分
    如果能完全对应（每个 bank 金额唯一匹配一个 other 金额），返回拆分后的行
    否则返回 None（退化为复杂聚合）
    """
    # 收集 other_rows 的金额和客商
    other_entries: List[Tuple[float, str]] = []
    for _, other in other_rows.iterrows():
        debit = _to_float(other.get(debit_col, 0))
        credit = _to_float(other.get(credit_col, 0))
        amt = debit if debit > 0 else credit
        party = str(other.get(customer_col, "")) if customer_col and pd.notna(other.get(customer_col)) else ""
        other_entries.append((amt, party))

    # 尝试为每个 bank_row 匹配一个 other_row（金额相等）
    matched: List[dict] = []
    used_other = set()

    for _, bank in bank_rows.iterrows():
        bank_debit = _to_float(bank.get(debit_col, 0))
        bank_credit = _to_float(bank.get(credit_col, 0))
        bank_amt = bank_debit if bank_debit > 0 else bank_credit

        found = False
        for i, (amt, party) in enumerate(other_entries):
            if i in used_other:
                continue
            if abs(amt - bank_amt) < 0.01:
                new_row = bank.to_dict()
                if customer_col:
                    new_row[customer_col] = party
                matched.append(new_row)
                used_other.add(i)
                found = True
                break

        if not found:
            return None  # 有 bank_row 无法匹配，整体失败

    # 验证：所有 other_rows 都被匹配了
    if len(used_other) != len(other_entries):
        return None

    return matched


def _aggregate_complex(
    bank_rows: pd.DataFrame,
    other_rows: pd.DataFrame,
    customer_col: str
) -> List[dict]:
    """
    复杂类型：每个 bank_row 携带所有 other_rows 的客商名称
    """
    # 收集所有客商（去重，保留顺序）
    all_parties = []
    seen = set()
    if customer_col and customer_col in other_rows.columns:
        for party in other_rows[customer_col].dropna():
            p = str(party).strip()
            if p and p not in seen:
                seen.add(p)
                all_parties.append(p)

    party_str = PARTY_DELIMITER.join(all_parties)

    result = []
    for _, bank in bank_rows.iterrows():
        new_row = bank.to_dict()
        if customer_col:
            new_row[customer_col] = party_str
        result.append(new_row)

    return result


def _to_float(val) -> float:
    """安全转浮点数"""
    if val is None:
        return 0.0
    if isinstance(val, float) and pd.isna(val):
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
