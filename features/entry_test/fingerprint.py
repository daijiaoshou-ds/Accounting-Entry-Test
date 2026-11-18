# -*- coding: utf-8 -*-
"""个体层：金额特征孤立。

**指纹 = 摘要 + 科目组合（带方向）**。每月计提工资的凭证都长一个样：摘要"计提工资"、
借费用/贷应付职工薪酬。指纹相同的凭证，业务上是同一件事的不同期，金额应该在一个
量级上。某一张突然跳出去，就去问它为什么。

判定用稳健 z（中位数 + MAD）：`z = 0.6745 × (x − 中位数) / MAD`，阈值 3.5。
不用均值/标准差：一条 96.8 万就能把均值和方差一起拉走，把一个离群点变成
"正常波动"。

**这一层只报金额孤立**。指纹本来能出三个信号（跳变 / 断档 / 新增），另外两个不做：

- **新增指纹**（以前从没出现过的科目组合）在「科目对」那一块已经报了——
  新的组合必然是一对低频的边，两边说的是同一件事，各报一遍只会让人以为抓到了
  两个问题。
- **断档**（某个月该有却没有）要判"该有"，就得先知道哪些事是月度必发的。这份清单
  只能靠摘要关键词猜（计提/摊销/折旧…），猜错的代价是把"这个月真的没发生"报成
  "漏记了"；误报要人去翻凭证确认，比漏报更费时间。完整性另有一条路：抽一个月
  核对计提表。

样本量闸门：跳变要求该指纹 ≥6 张、覆盖其头尾月份的 60%——偶发的供应商入库单里
出现一笔大额不是"跳"，那只是两笔不同的货。金额下限 5 万、与中位数的差 ≥5 万，
几十块的波动在任何账上都有。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 跳变的稳健 z 阈值（Iglewicz & Hoaglin：3.5 是常用的"离群"起点）
Z_THRESHOLD = 3.5
# 金额下限：低于它的跳变不值得占一条线索
MIN_JUMP_AMOUNT = 5_000.0
# 与中位数之差的下限：不到这个数就不占一条线索
MIN_JUMP_DELTA = 50_000.0
# 跳变：要够"周期性"才谈得上离群。要求出现次数 ≥6、覆盖其头尾月份的 60%
MIN_JUMP_SAMPLES = 6
MIN_JUMP_PRESENCE = 0.60
MIN_JUMP_SPAN = 4
# 金额进全账前这么多名才叫"高优先级"
HIGH_QUANTILE = 0.99

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
    """实际有凭证的月份 → 连续的日历月份。

    判"周期性"要用日历月：某个月整本账没有凭证时，它在"有凭证的月份"里缺席，
    3 个月就会被当成"只覆盖了 2 个月"，是不是周期性就判错了。
    """
    if not periods:
        return []
    try:
        first = pd.Period(periods[0], freq="M")
        last = pd.Period(periods[-1], freq="M")
    except (ValueError, TypeError):
        return list(periods)
    return [str(period) for period in pd.period_range(first, last, freq="M")]


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
    """跑金额孤立。返回 {leads, stats}。"""
    if frame.empty or vouchers.empty:
        return {"leads": [], "stats": {"fingerprints": 0, "jumps": 0}}

    signatures = {str(vid): _subject_signature(group)
                  for vid, group in frame.groupby("vid", sort=False)}
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    missing_summary = 0
    for row in vouchers.itertuples(index=False):
        vid = str(getattr(row, "vid"))
        summary = str(getattr(row, "摘要") or "")
        if not summary.strip():
            missing_summary += 1
        groups[fingerprint_of(signatures.get(vid, []), summary)].append({
            "vid": vid,
            "amount": abs(float(getattr(row, "金额") or 0)),
            "period": str(getattr(row, "期间") or ""),
            # 原始摘要留着当"主体名"：归一化后的 `#年#月份货款` 在报告里没法读，
            # 归一化只用于分组。
            "summary": summary.strip(),
        })

    periods = _month_range(sorted({str(p) for p in vouchers["期间"]
                                   if str(p or "").strip()}))
    amounts = np.asarray([abs(float(x)) for x in vouchers["金额"]], dtype=float)
    high_amount = float(np.quantile(amounts, HIGH_QUANTILE)) if amounts.size else 0.0

    out: List[Dict[str, Any]] = []
    stats = {"fingerprints": len(groups), "jumps": 0, "singletons": 0, "skipped": []}

    for key, items in groups.items():
        label, _, subjects = key.partition("｜")
        if len(items) == 1:
            stats["singletons"] += 1
            continue
        if len(items) < MIN_JUMP_SAMPLES:
            continue
        seen = sorted({item["period"] for item in items if item["period"]})
        span = [p for p in periods if seen and seen[0] <= p <= seen[-1]]
        if not span or len(span) < MIN_JUMP_SPAN:
            continue
        if len(seen) / len(span) < MIN_JUMP_PRESENCE:
            continue

        values = [item["amount"] for item in items]
        worst = max(items, key=lambda item: item["amount"])
        z = robust_z(values, worst["amount"])
        median = float(np.median(values))
        delta = worst["amount"] - median
        if z < Z_THRESHOLD or worst["amount"] < MIN_JUMP_AMOUNT or delta < MIN_JUMP_DELTA:
            continue

        stats["jumps"] += 1
        named = next((item["summary"] for item in items if item["summary"]), "")
        out.append(lead_mod.lead(
            layer=LAYER, kind="金额孤立",
            subject=named[:40] or label or "（无摘要）",
            evidence=(f"同类 {len(items)} 张、金额中位数 {median:,.2f}；"
                      f"{worst['period'] or '某一期'} 这张 {worst['amount']:,.2f}，"
                      f"是中位数的 {worst['amount'] / median:.1f} 倍。"),
            vouchers=[worst["vid"]],
            count=1, amount=worst["amount"],
            priority="高" if z >= 10 or worst["amount"] >= high_amount else "中",
        ))

    if missing_summary:
        stats["skipped"].append(
            f"{missing_summary} 张凭证没有摘要，算不出指纹，这一层看不到它们")
    stats["pairs_checked"] = sum(1 for items in groups.values()
                                 if len(items) >= MIN_JUMP_SAMPLES)
    return {"leads": out, "stats": stats}
