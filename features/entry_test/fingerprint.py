# -*- coding: utf-8 -*-
"""个体层：特征孤立 —— 指纹。

**指纹 = 摘要 + 科目组合（带方向）**。每月计提工资的凭证都长一个样：摘要"计提工资"、
借费用/贷应付职工薪酬。指纹相同的凭证，业务上是同一件事的不同期，它们的金额应该
在一个量级上、月份应该连续、第一次出现通常不该是年末——这三条各自对应一个信号：

- **跳变**：同一指纹，某一张（或某个月）的金额离群。用的是稳健 z（中位数 + MAD）：
  `z = 0.6745 × (x − 中位数) / MAD`，阈值 3.5。为什么不用均值/标准差：一条 96.8 万
  就能把均值和方差一起拉走，把一个离群点变成"正常波动"。
- **断档**：指纹某个月该有却没有（折旧少提一个月）。金额小，性质重——这是完整性
  问题不是金额问题。**只有"本来就该每月都有"的指纹才谈得上断档**：租金、工资、
  计提、摊销、结转这类；销售/采购流水本来就是时有时无，缺一个月不代表漏记，所以
  断档只看摘要命中这些周期性口径的指纹。
- **新增**：指纹第一次出现就在最后一个期间，且金额很大。弱信号，单看不足以说明
  什么，叠上"期末 + 大额"才值得翻。

摘要归一化与清洗的"业务等价组"同一口径：**数字一律换成 `#`**
（`summary_cleaner/nn/training_data.py::_normalize_summary`）。不共用同一段代码，
但规则必须一致——两处各写一套，同一张凭证在这两个功能里会是两种指纹。测试里有一条
断言盯着这件事（能导入清洗那份时，两边必须逐字符相等）。

样本量闸门（写进 facts，报告里也说明）：跳变要 ≥ 4 张（MAD 在 3 张上没有意义）、
断档要 ≥ 4 个月且该指纹至少 3 个月出现、新增要 ≥ 3 个月。低于门槛就不跑，而不是
硬跑出一堆"每张都是离群"的噪声。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 跳变的稳健 z 阈值（Iglewicz & Hoaglin：3.5 是常用的"离群"起点）
Z_THRESHOLD = 3.5
# 金额下限：低于它的跳变不值得占一条线索（几十块的波动在任何账上都有）
MIN_JUMP_AMOUNT = 5_000.0
# 跳变：要够"周期性"才谈得上离群——偶发的供应商入库单里出现一笔大额不是跳变，
# 那只是两笔不同的货。要求出现次数 ≥6、覆盖其头尾月份的 60%。
MIN_JUMP_SAMPLES = 6
MIN_JUMP_PRESENCE = 0.60
MIN_JUMP_SPAN = 4
# 跳变的"影响"下限：金额与中位数之差不到这个数就不占一条线索
MIN_JUMP_DELTA = 50_000.0

# 断档：跨度 ≥6 个月，且最多缺 2 个月（缺 3 个月就不是"漏"，是停了）
MIN_GAP_SPAN = 6
MAX_GAP_MISSING = 2
MIN_MONTHS_GAP = 4
MIN_MONTHS_NEW = 3

# 断档只认"应该周期性发生"的指纹（口径词）
PERIODIC = re.compile(r"计提|摊销|折旧|结转|预提|待摊|租金|利息|工资|薪酬|社保|公积金|税金|附加")
# 新增信号要求金额进全账前 5%
NEW_QUANTILE = 0.95

_DIGITS = re.compile(r"\d+")


def normalise_summary(text: Any) -> str:
    """数字 → `#`，并压掉空白。与清洗的 `_normalize_summary` 同口径。"""
    return re.sub(r"\s+", "", _DIGITS.sub("#", str(text or ""))).strip()


def fingerprint_of(subjects: List[str], summary: str) -> str:
    """指纹：归一化摘要 + 方向化的科目组合（排序后拼串，保证同组合同串）。"""
    return f"{normalise_summary(summary)}｜{'、'.join(sorted(subjects))}"


def _subject_signature(group: pd.DataFrame) -> List[str]:
    out = []
    for subject, debit, credit in zip(group["subject"], group["debit"], group["credit"]):
        name = str(subject or "").strip()
        if not name:
            continue
        if float(debit or 0) > 0.005:
            out.append(f"{name}[借]")
        elif float(credit or 0) > 0.005:
            out.append(f"{name}[贷]")
    return sorted(set(out))


def _month_range(periods: List[str]) -> List[str]:
    """把实际有凭证的月份补成**连续的日历月份**（首月到末月）。

    为什么不能直接用"有凭证的那些月份"：某个月整本账一张凭证都没有时，它不在
    列表里，断档就永远发现不了——而"这个月这张计提凭证没了"恰恰要看那个空位。
    """
    if not periods:
        return []
    try:
        first = pd.Period(periods[0], freq="M")
        last = pd.Period(periods[-1], freq="M")
    except (ValueError, TypeError):
        return list(periods)
    return [str(period) for period in pd.period_range(first, last, freq="M")]


def _span(items: List[Dict[str, Any]], periods: List[str]):
    """（出现过的月份, 首尾之间的所有月份）。用来算"周期性"与"缺哪几个月"。"""
    seen = sorted({item["period"] for item in items if item["period"]})
    if not seen:
        return [], []
    return seen, [p for p in periods if seen[0] <= p <= seen[-1]]


def robust_z(values: List[float], target: float) -> float:
    """稳健 z：中位数 + MAD。MAD 为 0（金额都一样）时退化成"偏离即异常"。"""
    data = np.asarray(values, dtype=float)
    if data.size < 2:
        return 0.0
    median = float(np.median(data))
    mad = float(np.median(np.abs(data - median)))
    if mad <= 0:
        return 99.0 if abs(target - median) > 1e-6 else 0.0
    return 0.6745 * (target - median) / mad


def detect(frame: pd.DataFrame, vouchers: pd.DataFrame) -> Dict[str, Any]:
    """跑指纹的三个信号。返回 {leads, stats}。"""
    if frame.empty or vouchers.empty:
        return {"leads": [], "stats": {"fingerprints": 0}}

    # 凭证级：指纹只依赖"整张凭证的科目组合"，所以先把行合成凭证再算
    signatures = {str(vid): _subject_signature(group)
                  for vid, group in frame.groupby("vid", sort=False)}
    missing_summary = 0
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in vouchers.itertuples(index=False):
        vid = str(getattr(row, "vid"))
        summary = str(getattr(row, "摘要") or "")
        if not summary.strip():
            missing_summary += 1
            continue
        key = fingerprint_of(signatures.get(vid, []), summary)
        groups[key].append({
            "vid": vid,
            "amount": float(getattr(row, "金额") or 0.0),
            "period": str(getattr(row, "期间") or ""),
        })

    periods = sorted({str(p) for p in vouchers["期间"] if str(p or "").strip()})
    calendar = _month_range(periods)
    amounts = np.asarray([float(a) for a in vouchers["金额"]], dtype=float)
    high_amount = float(np.quantile(amounts, NEW_QUANTILE)) if amounts.size else 0.0
    last_period = periods[-1] if periods else ""

    out: List[Dict[str, Any]] = []
    stats = {
        "fingerprints": len(groups),
        "singletons": sum(1 for items in groups.values() if len(items) == 1),
        "missing_summary": missing_summary,
        "months": len(periods),
        "jumps": 0, "gaps": 0, "new": 0,
        "skipped": [],
    }

    for key, items in groups.items():
        label = key.split("｜", 1)[0] or "（无摘要）"
        subjects = key.split("｜", 1)[1] if "｜" in key else ""

        seen_months, span_months = _span(items, calendar)
        presence = len(seen_months) / len(span_months) if span_months else 0.0

        # ── 跳变 ──
        if (len(items) >= MIN_JUMP_SAMPLES and span_months
                and len(span_months) >= MIN_JUMP_SPAN and presence >= MIN_JUMP_PRESENCE):
            values = [abs(item["amount"]) for item in items]
            worst = max(items, key=lambda item: abs(item["amount"]))
            z = robust_z(values, abs(worst["amount"]))
            delta = abs(worst["amount"]) - float(np.median(values))
            if z >= Z_THRESHOLD and abs(worst["amount"]) >= MIN_JUMP_AMOUNT and delta >= MIN_JUMP_DELTA:
                median = float(np.median(values))
                ratio = abs(worst["amount"]) / median if median else 0.0
                stats["jumps"] += 1
                out.append(lead_mod.lead(
                    layer=LAYER, kind="指纹跳变",
                    subject=label,
                    evidence=(f"同指纹 {len(items)} 张，金额中位数 {median:,.2f}，"
                              f"这张 {abs(worst['amount']):,.2f}（{ratio:.1f} 倍，"
                              f"稳健 z={z:.1f}）；科目组合 {subjects}"),
                    action=("先分辨是「正常的年度特例」（年终奖、一次性大单）还是"
                            "「把费用在期末堆进来」；对工资/折旧这类要拿计算表核对。"),
                    vouchers=[worst["vid"]],
                    count=1, amount=abs(worst["amount"]),
                    priority="高" if z >= 10 or abs(worst["amount"]) >= high_amount else "中",
                ))
        elif len(items) == 1:
            pass  # 只出现一次的指纹没有"同指纹"参照，跳变无从谈起

        # ── 断档 ──
        if len(periods) >= MIN_MONTHS_GAP and PERIODIC.search(label):
            seen = seen_months
            span = span_months
            missing = [p for p in span if p not in set(seen)]
            if (missing and len(span) >= MIN_GAP_SPAN and len(missing) <= MAX_GAP_MISSING
                    and len(seen) >= len(span) - MAX_GAP_MISSING):
                    stats["gaps"] += 1
                    out.append(lead_mod.lead(
                        layer=LAYER, kind="指纹断档",
                        subject=label,
                        evidence=(f"该指纹在 {len(seen)} 个月出现（{seen[0]}–{seen[-1]}），"
                                  f"中间缺 {len(missing)} 个月：{'、'.join(missing[:6])}；"
                                  f"科目组合 {subjects}"),
                        action="查这几个月的计提/摊销表：是漏记、被并到别的凭证，"
                               "还是业务真的停了（看期后有没有补提）。",
                        vouchers=[item["vid"] for item in items],
                        count=len(missing),
                        amount=float(np.median([abs(i["amount"]) for i in items])),
                        priority="中",
                    ))

        # ── 新增（弱信号）──
        if len(periods) >= MIN_MONTHS_NEW and last_period:
            seen = set(seen_months)
            if len(seen) == 1 and last_period in seen:
                big = [item for item in items if abs(item["amount"]) >= high_amount]
                if big:
                    target = max(big, key=lambda item: abs(item["amount"]))
                    stats["new"] += 1
                    out.append(lead_mod.lead(
                        layer=LAYER, kind="新指纹",
                        subject=label,
                        evidence=(f"全年只在 {last_period} 出现（{len(items)} 张），"
                                  f"最大一张 {abs(target['amount']):,.2f}，"
                                  f"进全账前 {int((1 - NEW_QUANTILE) * 100)}%；"
                                  f"科目组合 {subjects}"),
                        action="期末新出现的、金额进前列的分录：看合同/协议与期后处理，"
                               "收入类和往来挂账的优先。",
                        vouchers=[item["vid"] for item in items],
                        count=len(items), amount=abs(target["amount"]),
                        priority="中",
                    ))

    if len(periods) < MIN_MONTHS_GAP:
        stats["skipped"].append(f"只有 {len(periods)} 个月，断档不判（要 ≥{MIN_MONTHS_GAP}）")
    if len(periods) < MIN_MONTHS_NEW:
        stats["skipped"].append(f"只有 {len(periods)} 个月，新增不判（要 ≥{MIN_MONTHS_NEW}）")
    if missing_summary:
        stats["skipped"].append(f"{missing_summary} 张凭证没有摘要，指纹层看不到它们")

    return {"leads": out, "stats": stats}
