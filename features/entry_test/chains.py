# -*- coding: utf-8 -*-
"""行为层：核算异常 —— 孤儿冲销。

只看一件事：**本年有冲销，本年找不到被冲的那一笔**。

冲销的两种形态（同一张凭证里既有反冲、又记了新账，也归到这里）：

- **闭环**：本年先有暂估、本年又冲掉。不管后面有没有实际计提/支付——链条再往下
  追是另一个层次的活（要合同、要付款流水），而"暂估 + 冲销都在本年"已经足以说明
  它对本年净影响为零，放过。
- **孤儿冲销**：本年只有冲销，找遍全账没有等额的母体。这才是要问的：冲一笔不存在
  的分录，费用/收入就凭空变了。

**不按摘要区分"是否明示冲销往年"**：摘要写了什么取决于制单人的习惯，同一件事
三个人能写出三种说法；而且跨期冲销本来就该看（尤其是跨年的收入确认与成本计提）。
所以规则只有一条：本年有没有母体。摘要只用来**识别这是不是冲销**（关键词），以及
在证据里如实记录"它提到了哪张往年凭证"，不参与判定。

两个已知的边界，写在证据里而不是藏起来：

1. 母体可能在**上一年**或别的系统（存货核算、固定资产），这份序时账里根本没有；
   所以"本年未找到"不等于"不存在"，线索说的是"值得翻一下上一年的账"。
2. 只认**红字冲销**（同方向负数）与本方向相反的等额反向；部分冲销（只冲一半）
   金额对不上，会落进孤儿——这是刻度问题，先把最干净的那批交出去。
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import pandas as pd

from . import leads as lead_mod

LAYER = "行为层"

# 识别"这是冲销凭证"的关键词。宁窄勿宽：把"调整""更正"也算进来，会把大量正常
# 的期末调整卷进来（本账 39 张），那是另一件事。
KEYWORDS = re.compile(r"冲销|红冲|冲回|冲帐|冲账")

# 金额对齐的容差：分位上的尾差（0.01）与千分之一的相对差都容忍
TOL_ABS = 0.02
TOL_REL = 0.001

# 摘要里点名的往年凭证（"冲销2024.12.31 记-1096号凭证"）：只写进证据，不做判定
PRIOR_REF = re.compile(r"(20\d{2})[年./-]\s*(\d{1,2})[月./-]?\s*(\d{1,2})?.*?记[-—]?\s*(\d+)")

# 优先级：冲销金额进前 10% 算高
HIGH_QUANTILE = 0.90


def _split_lines(group: pd.DataFrame) -> Tuple[Dict[Tuple[str, str], float], Dict[Tuple[str, str], float]]:
    """一张凭证拆成（负数行合计, 正数行合计），键是 (一级科目, 借/贷)。

    按"科目+方向"合计而不是逐行比：暂估可能写成 3 行、冲销写成 1 行，逐行比会
    把同一笔业务判成对不上。
    """
    negative: Dict[Tuple[str, str], float] = defaultdict(float)
    positive: Dict[Tuple[str, str], float] = defaultdict(float)
    for subject, debit, credit in zip(group["subject"], group["debit"], group["credit"]):
        name = str(subject or "").strip()
        debit, credit = float(debit or 0.0), float(credit or 0.0)
        if debit < -0.005:
            negative[(name, "借")] += abs(debit)
        elif credit < -0.005:
            negative[(name, "贷")] += abs(credit)
        elif debit > 0.005:
            positive[(name, "借")] += debit
        elif credit > 0.005:
            positive[(name, "贷")] += credit
    return dict(negative), dict(positive)


def _covers(need: Dict[Tuple[str, str], float], have: Dict[Tuple[str, str], float]) -> bool:
    """`have` 是否覆盖 `need`（同科目同方向、金额在容差内）。多余的科目不算错。"""
    for key, want in need.items():
        got = have.get(key)
        if got is None:
            return False
        if abs(got - want) > max(TOL_ABS, want * TOL_REL):
            return False
    return True


def _prior_hint(summary: str) -> str:
    hit = PRIOR_REF.search(str(summary or ""))
    if not hit:
        return ""
    year, month, day, seq = hit.groups()
    when = f"{year}.{int(month):02d}" + (f".{int(day):02d}" if day else "")
    return f"摘要指向 {when} 记-{seq} 号（不在本年）"


def detect(frame: pd.DataFrame) -> Dict[str, Any]:
    """跑一遍孤儿冲销。返回 {leads, stats}。"""
    if frame.empty:
        return {"leads": [], "stats": {"reversals": 0, "closed": 0, "orphan": 0}}

    by_vid = {str(vid): group for vid, group in frame.groupby("vid", sort=False)}
    signatures = {vid: _split_lines(group) for vid, group in by_vid.items()}
    summaries = {vid: str(group["summary"].iloc[0] or "") for vid, group in by_vid.items()}

    reversals = {vid: sig[0] for vid, sig in signatures.items()
                 if sig[0] and KEYWORDS.search(summaries.get(vid, ""))}

    # 候选索引：正数行的 (科目, 方向) → 按金额排好的 (金额, 凭证)。
    # 用冲销里**最大的一笔**去查（命中率最高），金额按容差取窗口而不是精确相等：
    # 母体与冲销差几分钱（税额拆分）时，精确匹配会把"有母体"误判成孤儿。
    buckets: Dict[Tuple[str, str], List[Tuple[float, str]]] = defaultdict(list)
    for vid, (_neg, pos) in signatures.items():
        for (subject, direction), amount in pos.items():
            buckets[(subject, direction)].append((round(amount, 2), vid))
    for items in buckets.values():
        items.sort()

    closed: List[str] = []
    orphans: List[Dict[str, Any]] = []
    for vid, need in reversals.items():
        (subject, direction), biggest = max(need.items(), key=lambda item: item[1])
        window = max(TOL_ABS, biggest * TOL_REL)
        items = buckets.get((subject, direction), [])
        low = bisect_left(items, (biggest - window, ""))
        matched = ""
        for _amount, cand in items[low:]:
            if _amount > biggest + window:
                break
            if cand == vid:
                continue
            if _covers(need, signatures[cand][1]):
                matched = cand
                break
        if matched:
            closed.append(vid)
            continue
        total = sum(need.values())
        hint = _prior_hint(summaries.get(vid, ""))
        evidence = (f"本年未找到等额母体（按 {len(need)} 个科目方向核对）；"
                    f"冲销金额 {total:,.2f}")
        evidence += f"；{hint}" if hint else "；摘要未指向往年凭证"
        orphans.append({
            "vid": vid, "amount": total, "summary": summaries.get(vid, ""),
            "lines": len(by_vid[vid]), "evidence": evidence,
        })

    # 优先级按金额分档：冲销金额本身已经是"影响多大"的直接度量
    amounts = sorted(item["amount"] for item in orphans)
    cut = amounts[int(len(amounts) * HIGH_QUANTILE)] if amounts else 0.0

    out: List[Dict[str, Any]] = []
    for item in sorted(orphans, key=lambda x: -x["amount"]):
        out.append(lead_mod.lead(
            layer=LAYER, kind="孤儿冲销",
            subject=item["vid"],
            evidence=(item["evidence"]
                      + f"；涉及 {item['lines']} 行：{item['summary'][:40]}"),
            vouchers=[item["vid"]],
            count=1,
            amount=item["amount"],
            priority="高" if item["amount"] >= cut and item["amount"] > 0 else "中",
        ))

    # 盲区写进统计：有负数金额但没有冲销关键词的凭证，本层看不到
    negative_vids = {vid for vid, (neg, _pos) in signatures.items() if neg}
    blind = negative_vids - set(reversals)

    return {
        "leads": out,
        "stats": {
            "vouchers": len(by_vid),
            "reversals": len(reversals),
            "closed": len(closed),
            "orphan": len(orphans),
            "orphan_amount": round(sum(item["amount"] for item in orphans), 2),
            "negative_without_keyword": len(blind),
        },
    }
