# -*- coding: utf-8 -*-
"""
训练数据提取 — V3.0（BGE 微调）专用

与旧版（legacy/data.py，jieba 分词 + 关键词白名单）完全不同：
V3.0 输入是「整句摘要 + 科目开关向量」，不需要分词、关键词、白名单。

规则:
  - 按凭证号分组
  - 桶取组内首个「业务分类」（V2.1 classify() 输出）
  - 科目开关 = 一级科目+方向，格式 '科目[方向]'（如 '应付账款[借]'、'银行存款[贷]'）
  - 摘要 = 组内全部非空摘要去重后以空格拼接（整句保留，供 BGE 编码）

本模块顶层禁止 import torch/transformers —— V2.1 的 classify() 在
无 torch 机器上也能正常导出训练数据（classifier.py 直接引用本模块）。
"""

import re
from typing import Any, Dict, List, Optional, Set

import pandas as pd

# ============================================================================
# 常量
# ============================================================================

# 硬规则桶：由 V2.1 规则预分配（模式固定，无学习价值），不进入训练数据
HARD_RULE_BUCKETS: Set[str] = {"其他业务", "资金内部往来", "汇兑损益"}

# 默认跳过的桶 = 未分类 + 硬规则桶
DEFAULT_SKIP_BUCKETS: Set[str] = {"未分类", "无法分类"} | HARD_RULE_BUCKETS

# 摘要长度上限（防御超长摘要；BGE 截断在 tokenizer 层做，这里是字符级防御）
SUMMARY_MAX_CHARS = 200

_WHITESPACE_RE = re.compile(r"\s+")


# ============================================================================
# 科目开关提取
# ============================================================================

def extract_subjects_from_group(
    group: pd.DataFrame,
    subject_col: str,
    debit_col: str,
    credit_col: str,
) -> List[str]:
    """提取一组凭证行的（一级科目, 方向）开关列表。

    格式: '科目[方向]'，如 ['应付账款[借]', '银行存款[贷]']。
    方向判定（沿用 V2.1 legacy 逻辑）:
      - debit > credit        → 借
      - credit > 0（否则）     → 贷
      - 两者都不是             → 跳过该行

    Args:
        group: 单张凭证的所有分录行
        subject_col: 科目列名
        debit_col: 借方金额列名
        credit_col: 贷方金额列名

    Returns:
        去重后的开关列表（'科目[方向]'），按出现顺序
    """
    switches: Set[str] = set()

    for _, row in group.iterrows():
        subject = row.get(subject_col)
        if subject is None or (isinstance(subject, float) and pd.isna(subject)):
            continue
        subject = str(subject).strip()
        if not subject:
            continue

        # 注意: NaN 是 truthy（bool(nan)==True），`x or 0` 不能把 NaN 变成 0！
        # 必须用 pd.isna() 显式归一化，否则借方行被跳过（NaN > 0 恒为 False）
        debit = pd.to_numeric(row.get(debit_col), errors="coerce")
        credit = pd.to_numeric(row.get(credit_col), errors="coerce")
        if pd.isna(debit):
            debit = 0.0
        if pd.isna(credit):
            credit = 0.0

        if debit > credit:
            switches.add(f"{subject}[借]")
        elif credit > 0:
            switches.add(f"{subject}[贷]")

    return sorted(switches)


# ============================================================================
# 训练记录提取
# ============================================================================

def extract_training_records(
    df: pd.DataFrame,
    column_mapping: Dict[str, str],
    skip_buckets: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """从 V2.1 分类结果提取训练记录 [整句摘要, 科目组合, 桶]。

    Args:
        df: V2.1 classify() 输出（须含「业务分类」列）
        column_mapping: {voucher_no, subject, debit, credit, summary}
        skip_buckets: 跳过的桶集合，默认 DEFAULT_SKIP_BUCKETS

    Returns:
        [{"summary": str, "subjects": List[str], "bucket": str}, ...]

    规则:
      - 按凭证号分组，桶取组内首个「业务分类」，在 skip_buckets 内则跳过
      - subjects 为空 → 跳过（没有科目信号的凭证无法学习）
      - 摘要为空 → 跳过（BGE 需要文本输入）
    """
    if skip_buckets is None:
        skip_buckets = DEFAULT_SKIP_BUCKETS

    v_col = column_mapping["voucher_no"]
    s_col = column_mapping["subject"]
    d_col = column_mapping.get("debit", "")
    c_col = column_mapping.get("credit", "")
    sum_col = column_mapping.get("summary", "")

    if "业务分类" not in df.columns:
        raise ValueError("输入 DataFrame 缺少「业务分类」列（请先运行 V2.1 classify()）")

    records: List[Dict[str, Any]] = []
    skipped_no_bucket = 0
    skipped_no_subject = 0
    skipped_no_summary = 0

    for voucher_id, group in df.groupby(v_col, sort=False):
        bucket = group["业务分类"].iloc[0]
        if bucket in skip_buckets:
            skipped_no_bucket += 1
            continue

        subjects = extract_subjects_from_group(group, s_col, d_col, c_col)
        if not subjects:
            skipped_no_subject += 1
            continue

        # 摘要：组内非空摘要去重后拼接（整句保留）
        summaries = []
        if sum_col and sum_col in group.columns:
            for text in group[sum_col].dropna().astype(str):
                text = _WHITESPACE_RE.sub(" ", text).strip()
                if text and text not in summaries:
                    summaries.append(text)
        summary = " ".join(summaries)
        if not summary:
            skipped_no_summary += 1
            continue
        summary = summary[:SUMMARY_MAX_CHARS]

        records.append({
            "summary": summary,
            "subjects": subjects,
            "bucket": bucket,
        })

    print(f"[OK] 训练记录提取: {len(records)} 条")
    print(f"   跳过: {skipped_no_bucket} 凭证(桶被排除), "
          f"{skipped_no_subject} 凭证(无科目), "
          f"{skipped_no_summary} 凭证(无摘要)")

    return records
