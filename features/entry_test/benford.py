# -*- coding: utf-8 -*-
"""总体层：本福特，只作背景判断，**不出线索**。

它回答的问题只有一个：这份账的金额，像不像自然长出来的。这是整账一个判读，
不是一条一条的发现——所以它不进异常清单、不标任何凭证行，只出现在报告第一节
「总体完整性」里，作为"后面那些检验值不值得信"的背景。

为什么退出异常清单：本福特在统计上很有说服力（大样本、分布明确、MAD 有公认档位），
在审计上却指不出具体哪一笔有问题。把一条"费用这类业务金额偏整数"的结论摊成
几十张凭证的标记，会让审计员拿着一张没有指向性的清单去翻账——这正是过去
标记表里本福特行数最多的原因（一条线索覆盖 17 张凭证的全部明细行）。

**只做首位，不做次位。** 次位的本意是查"凑整/整数化"，但它在本账套里没有信息量：
整数报销、整万转账、按天计息让次位 0 天然偏高，样本门槛又高（≥5,000），几乎
每份账都会落到"边缘"，报出来全是噪声。首位的 1→9 递减才是真正稳定的规律。

判定仍用 Nigrini 的 MAD 档位。只有落到"边缘 / 不可接受"（首位 MAD > 0.012）
才往下走三步：找偏离最大的数字、按业务活动分解贡献、留一确认（剔掉它 MAD 应当
回落）。归因结果是一句背景说明，仍然不是凭证。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

# 层名保留：线索排序里这一层已经不会出现，但常量被别处引用
LAYER = "总体层"

# 首位数字的本福特期望
FIRST_EXPECT = {d: float(np.log10(1 + 1 / d)) for d in range(1, 10)}

# Nigrini 的 MAD 判读档：接近 / 可接受 / 不可接受
FIRST_MAD = (0.006, 0.012, 0.015)

MIN_AMOUNT = 10.0
MIN_SAMPLES_FIRST = 1_000


def verdict(mad: float) -> str:
    """MAD → 档位。报告与检测共用这一处，免得两边阈值各写一份。"""
    close, acceptable, unacceptable = FIRST_MAD
    if mad <= close:
        return "接近"
    if mad <= acceptable:
        return "可接受"
    if mad <= unacceptable:
        return "边缘"
    return "不可接受"


def _first_digit(values: np.ndarray) -> np.ndarray:
    """金额 → 首位。0 表示读不出（金额为 0），调用方会剔掉。"""
    first = np.zeros(len(values), dtype=int)
    for index, value in enumerate(values):
        text = f"{abs(float(value)):.10g}".replace(".", "").lstrip("0")
        if text:
            first[index] = int(text[0])
    return first


def _mad(observed: Counter, total: int) -> float:
    if not total:
        return 0.0
    return float(np.mean([abs(observed.get(d, 0) / total - FIRST_EXPECT[d])
                          for d in FIRST_EXPECT]))


def _side(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """某一侧（借/贷）的金额样本：非零、≥10 元、取绝对值。"""
    values = frame[frame[column].abs() >= MIN_AMOUNT].copy()
    values["样本金额"] = values[column].abs()
    return values


def _attribute(sample: pd.DataFrame, digits: np.ndarray, digit: int, total: int,
               bucket_of: Dict[str, str], mad: float) -> Optional[Dict[str, Any]]:
    """谁把这个数字抬高了。**剔除后必须变好**，否则不算定位成立。

    只看"贡献为正"是不够的：一个大活动哪怕完全符合本福特，只要它笔数多，
    它对每个数字的贡献都可能为正。真正说明问题的是——把它拿掉，整账的偏离
    要回落。回不落说明偏离是散的，这条归因就不该写出来。
    """
    observed = int((digits == digit).sum())
    excess = observed - FIRST_EXPECT[digit] * total
    if excess <= 0:
        return None

    work = sample.assign(数字=digits)
    work["活动"] = [bucket_of.get(str(vid)) or "未分类" for vid in work["vid"]]
    by_activity: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "hit": 0, "amount": 0.0})
    for row in work.itertuples(index=False):
        slot = by_activity[row.活动]
        slot["count"] += 1
        slot["amount"] += float(row.样本金额)
        if int(row.数字) == digit:
            slot["hit"] += 1

    contribution = {name: slot["hit"] - slot["count"] * FIRST_EXPECT[digit]
                    for name, slot in by_activity.items()}
    top_name = max(contribution, key=contribution.get)
    if contribution[top_name] <= 0:
        return None

    rest = work[work["活动"] != top_name]
    rest_obs = Counter(rest["数字"].tolist())
    rest_total = sum(rest_obs.values())
    rest_mad = _mad(rest_obs, rest_total) if rest_total else 0.0
    if rest_mad >= mad:                 # 剔掉它偏离没变好：偏离不是它造成的
        return None

    top = by_activity[top_name]
    return {
        "excess": round(float(excess), 1),
        "top_name": top_name,
        "top_hit": int(top["hit"]),
        "top_count": int(top["count"]),
        "top_share": round(top["hit"] / top["count"], 4) if top["count"] else 0.0,
        "top_amount": round(float(top["amount"]), 2),
        "rest_mad": round(float(rest_mad), 5),
    }


def detect(frame: pd.DataFrame, bucket_of: Dict[str, str],
           min_samples_first: int = MIN_SAMPLES_FIRST) -> Dict[str, Any]:
    """跑两侧的首位检验。返回 {leads: 恒空, stats}。

    `leads` 恒空是设计，不是遗漏：这一层没有"凭证级发现"这种东西。
    """
    stats: Dict[str, Any] = {"sides": {}, "found": [], "skipped": []}

    for column, name in (("debit", "借方"), ("credit", "贷方")):
        sample = _side(frame, column)
        values = sample["样本金额"].to_numpy(dtype=float)
        if values.size < min_samples_first:
            stats["skipped"].append(
                f"{name}只有 {values.size:,} 条 ≥{MIN_AMOUNT:.0f} 元的金额，"
                f"不够 {min_samples_first:,} 条，本福特不跑")
            continue

        digits = _first_digit(values)
        obs = Counter(digits[digits > 0].tolist())
        total = sum(obs.values())
        mad = _mad(obs, total)
        mark = verdict(mad)
        stats["sides"][name] = {
            "samples": int(values.size),
            "total": int(total),
            "first": {"mad": round(float(mad), 5), "verdict": mark,
                      "obs": {str(k): v for k, v in sorted(obs.items())}},
        }

        # 接近 / 可接受都是正常范围：不做归因，免得把正常账也说成"有来源"
        if mad <= FIRST_MAD[1]:
            continue
        digit = max(FIRST_EXPECT, key=lambda d: obs.get(d, 0) / total - FIRST_EXPECT[d])
        found = _attribute(sample, digits, digit, total, bucket_of, mad)
        if found:
            stats["found"].append({"side": name, "digit": int(digit),
                                   "mad": round(float(mad), 5), "verdict": mark,
                                   **found})

    return {"leads": [], "stats": stats}
