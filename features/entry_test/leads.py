# -*- coding: utf-8 -*-
"""线索（Lead）：各层检测共用的那一种东西。

一条线索 = 一个"值得看一眼"的发现，带两样东西：

1. **统计证据**：为什么可疑（robust z、超额的笔数、两端份额…），能被人复核对错；
2. **涉及凭证**：这件事落在哪些凭证上——交付时按凭证分发，只给统计量等于没交。

**不给建议程序**：报告只负责把风险摆出来，查什么、怎么查是项目组自己的判断。
写死的"建议"会变成两种坏东西——套话（每张凭证都建议"查合同"），或者越界
（替审计员下结论）。

优先级只有三档（高/中/低），按各层自己的口径定，**不做跨层可比**：一条孤儿冲销
和一条本福特超标不是同一种东西，硬排成一个序会给出"第 3 名比第 7 名更该查"的错觉。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# 层的顺序 = 报告里的顺序：先总体、再个体、再行为
LAYERS = ("总体层", "个体层", "行为层")

# 交付用的凭证清单最多带这么多个号（线索表里只列前几个，其余在凭证表里）
PREVIEW_VOUCHERS = 3


def lead(*, layer: str, kind: str, subject: str, evidence: str,
         vouchers: Optional[Iterable[str]] = None, count: int = 0,
         amount: float = 0.0, priority: str = "中") -> Dict[str, Any]:
    """造一条线索。凭证去重但保序（顺序就是发现顺序，方便按时间翻）。"""
    seen: List[str] = []
    for vid in vouchers or []:
        text = str(vid or "").strip()
        if text and text not in seen:
            seen.append(text)
    return {
        "层": layer,
        "类型": kind,
        "主体": subject,
        "笔数": int(count),
        "金额": round(float(amount), 2),
        "证据": evidence,
        "凭证": seen,
        "优先级": priority,
    }


def sort_leads(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """层内排序：优先级高的在前，同优先级按金额从大到小。"""
    weight = {"高": 0, "中": 1, "低": 2}
    order = {name: index for index, name in enumerate(LAYERS)}
    return sorted(leads, key=lambda item: (
        order.get(item["层"], 9), weight.get(item["优先级"], 3),
        -abs(float(item["金额"] or 0)), item["主体"]))


def _vouchers_text(vouchers: List[str]) -> str:
    if not vouchers:
        return "—"
    head = "、".join(vouchers[:PREVIEW_VOUCHERS])
    rest = len(vouchers) - PREVIEW_VOUCHERS
    return f"{head} 等 {len(vouchers)} 张" if rest > 0 else head


def lead_rows(leads: List[Dict[str, Any]]) -> List[List[str]]:
    """线索表：一条一行。凭证只列前几张，全量在凭证表里。"""
    return [[item["层"], item["类型"], item["主体"], f"{item['笔数']:,}",
             f"{item['金额']:,.2f}" if item["金额"] else "—",
             item["证据"], _vouchers_text(item["凭证"]), item["优先级"]]
            for item in leads]


LEAD_COLUMNS = ["层", "类型", "主体", "笔数", "金额", "证据", "涉及凭证", "优先级"]


def summary_rows(leads: List[Dict[str, Any]]) -> List[List[str]]:
    """按「层 + 类型」汇总：报告里每层只放这一张，明细留给 Excel。

    一张一行：这一类信号几条、涉及几张凭证、多少钱、最高优先级是几档。
    为什么不把线索本身放进报告：一条线索的证据要占三四行，几十条就把报告变成
    一张没人读完的表；报告要回答的是"哪一类问题最多"，"具体是哪几张"归 Excel。
    """
    weight = {"高": 0, "中": 1, "低": 2}
    book: Dict[tuple, Dict[str, Any]] = {}
    for item in leads:
        key = (item["层"], item["类型"])
        slot = book.setdefault(key, {"count": 0, "vouchers": set(), "amount": 0.0,
                                     "priority": "低"})
        slot["count"] += 1
        slot["vouchers"].update(item["凭证"])
        slot["amount"] += abs(float(item["金额"] or 0))
        if weight.get(item["优先级"], 3) < weight.get(slot["priority"], 3):
            slot["priority"] = item["优先级"]
    order = {name: index for index, name in enumerate(LAYERS)}
    rows = [(key, slot) for key, slot in book.items()]
    rows.sort(key=lambda pair: (order.get(pair[0][0], 9), weight.get(pair[1]["priority"], 3),
                                -pair[1]["amount"]))
    return [[key[0], key[1], f"{slot['count']}", f"{len(slot['vouchers']):,}",
             f"{slot['amount']:,.2f}", slot["priority"]]
            for key, slot in rows]


SUMMARY_COLUMNS = ["层", "类型", "条数", "涉及凭证", "涉及金额", "最高优先级"]


def voucher_rows(leads: List[Dict[str, Any]]) -> List[List[str]]:
    """凭证表：一张凭证一行，把命中的线索合成一句"为什么看它"。

    这是**交付用**的那张表：审计员拿到的是凭证号，不是统计量。同一张凭证被多条
    线索命中（比如既在跳变指纹里、又在本福特超标的活动里）就并在一行，不重复列。
    """
    book: Dict[str, Dict[str, Any]] = {}
    for item in leads:
        for vid in item["凭证"]:
            slot = book.setdefault(vid, {"lines": [], "priority": "低", "amount": 0.0})
            # 主体本身就是凭证号时（孤儿冲销）别在括号里再抄一遍
            label = item["类型"] if item["主体"] == vid else f"{item['类型']}（{item['主体']}）"
            slot["lines"].append(label)
            if _weight(item["优先级"]) < _weight(slot["priority"]):
                slot["priority"] = item["优先级"]
            slot["amount"] = max(slot["amount"], float(item["金额"] or 0))
    order = {"高": 0, "中": 1, "低": 2}
    entries = [(vid, slot) for vid, slot in book.items()]
    # 优先级高的在前；同优先级里"被多条线索命中"的在前，再看金额
    entries.sort(key=lambda pair: (order.get(pair[1]["priority"], 3),
                                   -len(pair[1]["lines"]), -pair[1]["amount"], pair[0]))
    return [[vid, "；".join(slot["lines"]),
             f"{slot['amount']:,.2f}" if slot["amount"] else "—", slot["priority"]]
            for vid, slot in entries]


VOUCHER_COLUMNS = ["凭证号", "命中的线索", "线索金额", "优先级"]


def _weight(priority: str) -> int:
    return {"高": 0, "中": 1, "低": 2}.get(priority, 3)
