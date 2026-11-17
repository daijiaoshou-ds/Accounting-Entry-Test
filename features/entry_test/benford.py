# -*- coding: utf-8 -*-
"""总体层：本福特。

**它不是判决，是定位器。** 整账 MAD 超标只说明"数字不像自然生成的"，真正有用的是
接下来四步把人领到具体凭证面前：

1. **分桶归因**：哪个首位数字超了（首位 9 超出 312 笔，而不是"整账异常"）；
2. **贡献度分解**：这个超额是谁贡献的（按业务活动）——数学上是精确分解：
   全账某桶的（观察−期望）= 各活动该桶（观察−期望）之和，不存在分不干净的余数；
3. **下钻**：该活动该桶的占比 vs 全账基线；
4. **留一确认**：剔掉这个活动重算 MAD，看是否回落到可接受区间——回落才说明
   "定位成立"，不回落说明超额是散的。

两个口径决定成败，写在这里免得下次又吵：

- **借、贷分开跑**。两边业务性质不同（收款 vs 付款），混在一起两个分布互相污染。
- **只取 ≥10 元的金额**，取绝对值。金额首位数字要成立，数本身至少得两位；红字
  冲销取绝对值——我们关心的是"金额长什么样"，不是方向。

样本量：首位 ≥1,000 条、次位 ≥5,000 条才有意义（Nigrini）。不够就不跑，写进 facts。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "总体层"

# 首位/次位的本福特期望
FIRST_EXPECT = {d: float(np.log10(1 + 1 / d)) for d in range(1, 10)}
SECOND_EXPECT = {d: float(sum(np.log10(1 + 1 / (10 * k + d)) for k in range(1, 10)))
                 for d in range(0, 10)}

# Nigrini 的 MAD 判读档：接近 / 可接受 / 不可接受
FIRST_MAD = (0.006, 0.012, 0.015)
SECOND_MAD = (0.008, 0.010, 0.012)

MIN_AMOUNT = 10.0
MIN_SAMPLES_FIRST = 1_000
MIN_SAMPLES_SECOND = 5_000
MAX_VOUCHERS = 30


def _digits(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """金额 → (首位, 次位)。次位对 <10 的数没有定义，用 -1 占位。"""
    first = np.zeros(len(values), dtype=int)
    second = np.full(len(values), -1, dtype=int)
    for index, value in enumerate(values):
        text = f"{abs(float(value)):.10g}"
        digits = text.replace(".", "").lstrip("0")
        if not digits:
            continue
        first[index] = int(digits[0])
        if len(digits) > 1:
            second[index] = int(digits[1])
    return first, second


def _mad(observed: Counter, expected: Dict[int, float], total: int) -> float:
    if not total:
        return 0.0
    return float(np.mean([abs(observed.get(d, 0) / total - expected[d]) for d in expected]))


def _verdict(mad: float, thresholds: Tuple[float, float, float]) -> str:
    close, acceptable, unacceptable = thresholds
    if mad <= close:
        return "接近"
    if mad <= acceptable:
        return "可接受"
    if mad <= unacceptable:
        return "边缘"
    return "不可接受"


def _side(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """某一侧（借/贷）的金额样本：非零、≥10 元、取绝对值。"""
    values = frame[frame[column].abs() >= MIN_AMOUNT].copy()
    values["样本金额"] = values[column].abs()
    return values


def insane(value: Any) -> str:
    """笔数加千分位。写成一个函数只是因为 f-string 里不能直接塞格式化的表达式嵌套。"""
    return f"{int(value):,}"


def _attribute(sample: pd.DataFrame, digits: np.ndarray, expect: Dict[int, float],
               digit: int, total: int, bucket_of: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """对某个数字做归因：谁贡献了超额、剔掉它 MAD 会怎样。超额不为正就返回 None。"""
    observed = int((digits == digit).sum())
    excess = observed - expect[digit] * total
    if excess <= 0:
        return None

    work = sample.assign(数字=digits)
    work["活动"] = [bucket_of.get(str(vid)) or "未分类" for vid in work["vid"]]
    by_activity: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "hit": 0, "amount": 0.0, "vids": []})
    for row in work.itertuples(index=False):
        slot = by_activity[row.活动]
        slot["count"] += 1
        slot["amount"] += float(row.样本金额)
        if int(row.数字) == digit:
            slot["hit"] += 1
            if len(slot["vids"]) < MAX_VOUCHERS:
                slot["vids"].append(str(row.vid))

    contribution = {name: slot["hit"] - slot["count"] * expect[digit]
                    for name, slot in by_activity.items()}
    top_name = max(contribution, key=contribution.get)
    if contribution[top_name] <= 0:
        return None

    rest = work[work["活动"] != top_name]
    rest_obs = Counter(rest["数字"].tolist())
    rest_total = sum(rest_obs.values())
    rest_mad = _mad(rest_obs, expect, rest_total) if rest_total else 0.0
    return {
        "observed": observed, "excess": excess, "top_name": top_name,
        "top": by_activity[top_name], "contribution": contribution[top_name],
        "rest_mad": rest_mad,
    }


def detect(frame: pd.DataFrame, bucket_of: Dict[str, str],
           min_samples_first: int = MIN_SAMPLES_FIRST) -> Dict[str, Any]:
    """跑两侧的本福特。返回 {leads, stats}。"""
    out: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {"sides": {}, "skipped": []}

    for column, name in (("debit", "借方"), ("credit", "贷方")):
        sample = _side(frame, column)
        values = sample["样本金额"].to_numpy(dtype=float)
        if values.size < min_samples_first:
            stats["skipped"].append(
                f"{name}只有 {values.size:,} 条 ≥{MIN_AMOUNT:.0f} 元的金额，"
                f"不够 {min_samples_first:,} 条，本福特不跑")
            continue

        first, second = _digits(values)
        first_obs = Counter(first[first > 0].tolist())
        second_obs = Counter(second[second >= 0].tolist())
        first_total, second_total = sum(first_obs.values()), sum(second_obs.values())
        first_mad = _mad(first_obs, FIRST_EXPECT, first_total)
        second_mad = _mad(second_obs, SECOND_EXPECT, second_total)

        side_stats = {
            "samples": int(values.size),
            "first": {"mad": round(first_mad, 5), "verdict": _verdict(first_mad, FIRST_MAD),
                      "obs": {str(k): v for k, v in sorted(first_obs.items())}},
            "second": {"mad": round(second_mad, 5),
                       "verdict": _verdict(second_mad, SECOND_MAD),
                       "obs": {str(k): v for k, v in sorted(second_obs.items())}},
        }
        stats["sides"][name] = side_stats

        # 首位：超标就往下走四步；次位：只在"边缘"以上才提一句（整数化金额的指纹）
        for label, digits, expect, mad, thresholds in (
                ("首位", first, FIRST_EXPECT, first_mad, FIRST_MAD),
                ("次位", second, SECOND_EXPECT, second_mad, SECOND_MAD)):
            if label == "首位":
                if mad <= thresholds[0]:
                    continue
            elif mad <= thresholds[1]:      # 次位只报"边缘/不可接受"，"可接受"不占篇幅
                continue
            obs = Counter(int(value) for value in digits
                          if (value > 0 if label == "首位" else value >= 0))
            total = sum(obs.values())
            if not total:
                continue
            digit = max(expect, key=lambda d: obs.get(d, 0) / total - expect[d])
            found = _attribute(sample, digits, expect, digit, total, bucket_of)
            if not found:
                continue
            top = found["top"]
            inside = top["hit"] / top["count"] if top["count"] else 0.0
            # 一句话说清三件事：偏在哪个数字、偏多少、偏在谁身上
            evidence = (
                f"「{found['top_name']}」的金额有 {insane(top['hit'])} 笔{label}是 {digit}"
                f"（占该活动 {inside:.0%}，全账只有 {expect[digit]:.1%}），"
                f"比该有的多 {found['excess']:,.0f} 笔；"
                f"把这块拿掉，整账的偏离就从{_verdict(mad, thresholds)}回到"
                f"{_verdict(found['rest_mad'], thresholds)}。"
            )
            out.append(lead_mod.lead(
                layer=LAYER, kind=f"本福特·{name} {label} {digit}",
                subject=found["top_name"],
                evidence=evidence,
                vouchers=top["vids"], count=int(top["hit"]),
                amount=float(top["amount"] * top["hit"] / top["count"] if top["count"] else 0),
                priority=("高" if label == "首位" and mad > thresholds[1]
                          and found["rest_mad"] < mad * 0.7 else "中"),
            ))

    return {"leads": out, "stats": stats}
