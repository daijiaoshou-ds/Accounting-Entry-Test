# -*- coding: utf-8 -*-
"""会计分录测试 —— 引擎适配层。

`features/jet/stages.py` 只跟这个文件打交道；`entry_test` 的列名口径
（voucher_unique_id / first_level_subject / counter_subject）不出这个文件。

三件事：

    ① detect()   序时账 → 逐行打分（业务群、综合得分、风险等级、异常类型）
    ② vouchers() 逐行 → 逐凭证（复核与导出都按凭证，不按行）
    ③ entries()  给复核用的整张凭证分录（判"这笔是不是真异常"要看全貌）

为什么全程 `redirect_stdout`：`AnomalyDetector` 里有 40 多行 `[Debug] 距离矩阵…`
的 print。命令行跑的时候它们会混进 agent 看到的 stderr 末尾（插件只留最后 40 行，
等于把真正有用的进度挤掉）。

**这个引擎不是完全确定的**：风险分档会随 set/dict 的字符串迭代顺序浮动（同一份账
两次跑，高风险可能是 52 张也可能是 54 张），原因是分数累加顺序影响浮点结果、边界
上的凭证就跨档了。插件起进程时固定了 `PYTHONHASHSEED=0`，所以"从页面/agent 跑"
是可复现的；命令行直接跑要复现也得带上这个环境变量。
"""

from __future__ import annotations

import contextlib
import io
from typing import Any, Dict, Iterable, List

import pandas as pd

from entry_test.anomaly_detector import AnomalyDetector
from entry_test.cluster_engine import ClusterEngine
from entry_test.data_processor import DataProcessor

# 契约见 `journal_io/fields.py` 的 FEATURE_REQUIRES["entry_test"]
REQUIRES = ("voucher_id", "date", "subject_l1", "debit", "credit")

# 复核结论（决策取值）
DECISIONS = ("确认异常", "疑似", "误报")

# 后果自负的默认：只问"高/中"，其余不打断
REVIEW_RISKS = ("高风险", "中风险")

VCOL = "voucher_unique_id"


# ============================================================================
# ① 跑引擎
# ============================================================================

def _mapping(journal) -> Dict[str, str]:
    """canonical 映射 → `DataProcessor` 认的那几个键。"""
    m = journal.schema.legacy()
    out = {
        "date": m.get("date", ""),
        "voucher_no": m.get("voucher_no", ""),
        "first_level_subject": m.get("subject", ""),
        "debit": m.get("debit", ""),
        "credit": m.get("credit", ""),
    }
    for extra, key in (("subject_name", "subject_name"), ("summary", "summary")):
        if m.get(key):
            out[extra] = m[key]
    return {k: v for k, v in out.items() if v}


def detect(journal, pareto: float = 0.95) -> pd.DataFrame:
    """跑完整条异常检测，返回**逐行**结果（原列 + 风险等级/综合得分/异常类型…）。

    `pareto` 是重要性水平的帕累托分位（网页默认 0.95）：该群前 N% 金额的凭证中，
    最后一张的金额就是重要性水平；低于它的凭证 α 系数小于 1，得分会被压低。
    """
    mapping = _mapping(journal)
    for key in ("date", "voucher_no", "first_level_subject", "debit", "credit"):
        if not mapping.get(key):
            raise ValueError(f"会计分录测试缺少「{key}」字段的列（读表阶段没认出来）")

    raw = journal.raw.copy()
    with contextlib.redirect_stdout(io.StringIO()):
        processed = DataProcessor(raw, column_mapping=mapping).preprocess()
        clustered = ClusterEngine().classify_all(processed, voucher_col=VCOL)
        result = AnomalyDetector().detect_anomalies(
            clustered,
            group_col="primary_group",
            feature_col="voucher_feature",
            voucher_col=VCOL,
            subject_col="first_level_subject",
            counter_col="counter_subject",
            credit_col="credit",
            involved_col="involved_groups",
            amount_col="voucher_abs_amount",
            pareto_percent=pareto,
            original_cols=list(mapping.values()),
        )
    return result


def vouchers(result: pd.DataFrame, journal) -> List[Dict[str, Any]]:
    """逐行 → 逐凭证（复核与导出都用这个粒度）。"""
    m = journal.schema.legacy()
    vcol, scol = m.get("voucher_no", ""), m.get("summary", "")

    out: List[Dict[str, Any]] = []
    for vid, group in result.groupby(VCOL, sort=False):
        first = group.iloc[0]
        out.append({
            "key": str(vid),
            "voucher": str(first[vcol]) if vcol in first.index else str(vid),
            "summary": str(first[scol])[:120] if scol in first.index else "",
            "group": str(first.get("primary_group") or ""),
            "groups": str(first.get("involved_groups") or ""),
            "risk": str(first.get("风险等级") or ""),
            "kind": str(first.get("异常类型") or ""),
            "score": round(float(first.get("综合得分") or 0), 2),
            "amount": round(float(first.get("凭证金额") or 0), 2),
            "rows": int(len(group)),
        })
    return out


def entries(result: pd.DataFrame, vids: Iterable[str]) -> Dict[str, List[str]]:
    """这些凭证的整张分录（按"科目+方向"聚合，借在前）。"""
    want = {str(v) for v in vids}
    if not want:
        return {}
    sub = result[result[VCOL].astype(str).isin(want)]

    out: Dict[str, List[str]] = {}
    for vid, group in sub.groupby(VCOL, sort=False):
        agg: Dict[tuple, float] = {}
        order: List[tuple] = []
        for subject, debit, credit in zip(group["first_level_subject"],
                                          group["debit"], group["credit"]):
            name = str(subject or "").strip() or "（空科目）"
            if abs(float(debit or 0)) > 0.001:
                k, amt = (name, "借"), float(debit)
            elif abs(float(credit or 0)) > 0.001:
                k, amt = (name, "贷"), float(credit)
            else:
                continue
            if k not in agg:
                agg[k] = 0.0
                order.append(k)
            agg[k] += amt
        order.sort(key=lambda k: (0 if k[1] == "借" else 1, -agg[k]))
        lines = []
        for k in order[:14]:
            lines.append(f"{k[0]} {k[1]} {agg[k]:,.2f}")
        if len(order) > 14:
            lines.append(f"……另有 {len(order) - 14} 个科目")
        out[str(vid)] = lines
    return out


# ============================================================================
# ② 复核与导出用的小工具
# ============================================================================

def risk_of(voucher: Dict[str, Any]) -> str:
    """风险等级去掉 emoji（"高风险 🔴" → "高风险"）。"""
    return str(voucher.get("risk") or "").split(" ")[0]


def needs_review(voucher: Dict[str, Any]) -> bool:
    return risk_of(voucher) in REVIEW_RISKS


def candidates(vouchers_: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """待复核的凭证：高/中风险，按综合得分从高到低。"""
    picked = [v for v in vouchers_ if needs_review(v)]
    picked.sort(key=lambda v: (-v["score"], -v["amount"]))
    return picked


def stats(result: pd.DataFrame, vouchers_: List[Dict[str, Any]]) -> Dict[str, Any]:
    """detect 阶段落进 facts 的数。"""
    dist: Dict[str, int] = {}
    for v in vouchers_:
        level = risk_of(v) or "未知"
        dist[level] = dist.get(level, 0) + 1

    kinds: Dict[str, int] = {}
    for v in vouchers_:
        kind = v["kind"] or "未知"
        kinds[kind] = kinds.get(kind, 0) + 1

    picked = candidates(vouchers_)
    return {
        "rows": int(len(result)),
        "vouchers": len(vouchers_),
        "risk_dist": dist,
        "kind_dist": kinds,
        "high": dist.get("高风险", 0),
        "medium": dist.get("中风险", 0),
        "review_candidates": len(picked),
    }


def apply_decisions(vouchers_: List[Dict[str, Any]],
                    choices: Dict[str, str]) -> Dict[str, Any]:
    """{凭证 key: 复核结论} → 给导出用的说明（不在这里改表）。"""
    by_key = {v["key"]: v for v in vouchers_}
    applied: Dict[str, str] = {}
    ignored: List[str] = []
    for key, value in (choices or {}).items():
        key, value = str(key), str(value).strip()
        if key not in by_key:
            ignored.append(key)
            continue
        if value not in DECISIONS:
            ignored.append(f"{key}:{value}")
            continue
        applied[key] = value
    return {
        "applied": len(applied),
        "ignored": ignored,
        "by_key": applied,
        # 一张凭证一行：导出要拼「复核结论」列
        "by_voucher": {by_key[k].get("voucher") or k: v for k, v in applied.items()},
    }
