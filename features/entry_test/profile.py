# -*- coding: utf-8 -*-
"""第 1 步：画像。

只回答三个问题，别的都不做：

- 这份账都做了哪些**经济活动**（what）——业务活动清单，来自上游清洗的业务分类
- 这些活动的**金额**怎么分布（how）——笔数/金额/占比
- 记录是**谁做的**（who）——每个活动的主要制单人

再加一件顺带的事：**集中度 + 抽凭建议**。集中度用 HHI（Σ 份额²），不是为了下
结论，而是为了回答"这个活动是不是少数几张凭证撑起来的"——如果是，抽样就必须
覆盖那几张，否则抽了等于没抽。帕累托按活动各自取到累计 80%，不让"销售"把一个
配额吃光。

维度刻意压到最少：HHI 只算两个（金额、对手方），制单人只给"主要那位 + 占比"。
维度一多，人就不看了。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

# 抽样的目标覆盖率与上限：按活动取到累计 80%，单个活动最多 15 张（再多就不叫抽样了）
PARETO_SHARE = 0.80
PER_ACTIVITY_CAP = 15
TOTAL_CAP = 300

# 活动内少于这么多张时不谈"抽样"：全都在样本里，说明写清就行
SMALL_ACTIVITY = 3

# HHI 的判读档（业内常用的 0.15/0.25 分界，仅作参考，不下结论）
HHI_BANDS = ((0.25, "高度集中"), (0.15, "中等集中"), (0.0, "分散"))

_DOMAIN_CACHE: Optional[Dict[str, str]] = None


def _domains() -> Dict[str, str]:
    """业务桶 → 数据域（经营/投资/筹资/非现金/待定）。读不到就不显示这一列。"""
    global _DOMAIN_CACHE
    if _DOMAIN_CACHE is not None:
        return _DOMAIN_CACHE
    try:
        from summary_cleaner.v2.bucket_registry_v10 import load_taxonomy
        cfg = load_taxonomy()
        _DOMAIN_CACHE = {name: str(info.get("domain") or "")
                         for name, info in (cfg.get("buckets") or {}).items()}
    except Exception:
        _DOMAIN_CACHE = {}
    return _DOMAIN_CACHE


def hhi(shares: Iterable[float]) -> float:
    """赫芬达尔指数：Σ 份额²。份额按总额归一化，0–1，越大越集中。"""
    values = [float(x) for x in shares if x and x > 0]
    total = sum(values)
    if total <= 0:
        return 0.0
    return round(sum((v / total) ** 2 for v in values), 4)


def band(value: float) -> str:
    for threshold, word in HHI_BANDS:
        if value >= threshold:
            return word
    return "分散"


def _pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "—"
    return f"{part / whole * 100:.1f}%"


def _top_text(counter: Counter, whole: float, limit: int = 3) -> str:
    """`银行存款 42.1% · 应付账款 31.0% · 其他应收款 8.9%`"""
    items = [item for item in counter.most_common(limit) if item[1] > 0]
    if not items:
        return "—"
    return " · ".join(f"{name} {_pct(value, whole)}" for name, value in items)


def _main(counter: Counter) -> Tuple[str, int]:
    if not counter:
        return "—", 0
    name, count = counter.most_common(1)[0]
    return name, count


def build(frame: pd.DataFrame, vouchers: pd.DataFrame,
          bucket_of: Dict[str, str]) -> Dict[str, Any]:
    """画像。`bucket_of` 是 {凭证号: 业务活动}，来自上游清洗的业务分类。

    返回 {activities: 表行, picks: 抽凭建议, stats: 汇总}。
    """
    if vouchers.empty:
        return {"activities": [], "picks": [], "stats": {}}

    work = vouchers.copy()
    work["活动"] = [bucket_of.get(str(vid)) or "未分类" for vid in work["vid"]]

    # 行表挂上活动：对手方（一级科目）的分布要从行上看，凭证级只有一个列表
    acts_of_row = work.set_index("vid")["活动"]
    rows = frame.copy()
    rows["活动"] = [acts_of_row.get(str(vid), "未分类") for vid in rows["vid"]]

    total_amount = float(work["金额"].sum())
    total_vouchers = int(len(work))
    domains = _domains()

    activities: List[Dict[str, Any]] = []
    for activity, group in work.groupby("活动", sort=False):
        count = int(len(group))
        amount = float(group["金额"].sum())
        # 金额集中度：份额按凭证算——"是不是少数几张凭证撑起这个活动"
        amount_hhi = hhi(group["金额"].tolist())

        # 对手方：按凭证去重计数（一张 60 行的凭证不该把某科目数 60 次）
        counterpart = Counter()
        for subjects in group["科目"]:
            for name in subjects:
                counterpart[name] += 1
        subject_total = sum(counterpart.values())

        maker, maker_count = _main(Counter(group["制单人"].tolist()))
        top3 = float(group["金额"].nlargest(3).sum())

        activities.append({
            "业务活动": str(activity),
            "域": domains.get(str(activity), ""),
            "凭证": count,
            "笔数占比": _pct(count, total_vouchers),
            "金额": round(amount, 2),
            "金额占比": _pct(amount, total_amount),
            "前3张占比": _pct(top3, amount),
            "主要制单人": maker,
            "该人占比": _pct(maker_count, count),
            "金额HHI": amount_hhi,
            "对手方HHI": hhi(counterpart.values()),
            "判读": f"金额{band(amount_hhi)} · 对手方{band(hhi(counterpart.values()))}",
            "主要对手方": _top_text(counterpart, subject_total),
        })

    # 抽凭建议按"活动金额从大到小"排：小活动的一两张沉在后面，不挡视线
    activities.sort(key=lambda item: (-item["金额"], item["业务活动"]))
    picks = _pareto(work, [item["业务活动"] for item in activities])

    # 每个活动实际被抽走多少：抽到 80% 就停，但活动很大时 15 张也只够一半——
    # 这一列必须写出来，否则"抽凭建议"看着像"已经覆盖"。
    picked_amount: Dict[str, float] = {}
    picked_count: Dict[str, int] = {}
    for pick in picks:
        name = pick["业务活动"]
        picked_amount[name] = picked_amount.get(name, 0.0) + float(pick["金额"])
        picked_count[name] = picked_count.get(name, 0) + 1
    for item in activities:
        name = item["业务活动"]
        item["抽凭"] = f"{picked_count.get(name, 0)} 张"
        item["抽凭覆盖"] = _pct(picked_amount.get(name, 0.0), float(item["金额"]))

    return {
        "activities": activities,
        "picks": picks,
        "stats": {
            "vouchers": total_vouchers,
            "amount": round(total_amount, 2),
            "activities": len(activities),
            "picks": len(picks),
            "activities_picked": len({item["业务活动"] for item in picks}),
            "unclassified": int((work["活动"] == "未分类").sum()),
        },
    }


def _pareto(work: pd.DataFrame, order: List[str]) -> List[Dict[str, Any]]:
    """每个活动各自按金额降序取到累计 80%，给抽凭建议。

    为什么按活动而不是按全账：全账取到 95% 的话，几个大活动就把配额吃光，
    小活动一张都进不去——而小活动里的孤笔恰恰是审计最该看的。
    """
    groups = {str(name): group for name, group in work.groupby("活动", sort=False)}
    picks: List[Dict[str, Any]] = []
    for activity in order:
        group = groups.get(str(activity))
        if group is None:
            continue
        ordered = group.sort_values("金额", ascending=False)
        activity_total = float(ordered["金额"].sum())
        if activity_total <= 0:
            continue
        small = len(ordered) <= SMALL_ACTIVITY
        cumulative = 0.0
        taken = 0
        for rank, (_, row) in enumerate(ordered.iterrows(), start=1):
            cumulative += float(row["金额"])
            share = cumulative / activity_total
            picks.append({
                "业务活动": str(activity),
                "凭证号": str(row["凭证号"]),
                "日期": str(row["日期"])[:10] if pd.notna(row["日期"]) else "",
                "摘要": str(row["摘要"])[:80],
                "制单人": str(row["制单人"]),
                "金额": round(float(row["金额"]), 2),
                "活动内累计占比": f"{share * 100:.1f}%",
                "说明": (f"本活动只有 {len(ordered)} 张，全看"
                         if small else f"前 {rank} 张 · 覆盖本活动 {share * 100:.0f}% 金额"),
            })
            taken += 1
            if not small and (share >= PARETO_SHARE or taken >= PER_ACTIVITY_CAP):
                break
    return picks[:TOTAL_CAP]
