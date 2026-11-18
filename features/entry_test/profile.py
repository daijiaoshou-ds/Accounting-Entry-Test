# -*- coding: utf-8 -*-
"""第 1 步：画像。

只回答三个问题，别的都不做：

- 这份账都做了哪些**经济活动**（what）——业务活动清单，来自上游清洗的业务分类
- 这些活动的**金额**怎么分布（how）——笔数/金额/占比
- 记录是**谁做的**（who）——每个活动的主要制单人

集中度用 HHI（Σ 份额²）：不是下结论，而是回答"这个活动是不是少数几张凭证撑起来
的""这个活动的钱主要往来于几个科目"。两个维度就够，制单人只给"主要那位 + 占比"。

**不做抽凭建议**：真抽凭要先定重要性水平、样本量、覆盖哪些认定，靠"金额前 80%"
拼出来的清单不是抽样、只是排序，交出去反而误导。抽样留给细节测试。

维度刻意压到最少：维度一多，人就不看了。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

# HHI 的判读档（业内常用的 0.15/0.25 分界，仅作参考，不下结论）
HHI_BANDS = ((0.25, "高度集中"), (0.15, "中等集中"), (0.0, "分散"))

_DOMAIN_CACHE: Optional[Dict[str, str]] = None


def _domains() -> Dict[str, str]:
    """业务桶 → 数据域（经营/投资/筹资/非现金/待定）。读不到就不显示这一列。"""
    global _DOMAIN_CACHE
    if _DOMAIN_CACHE is not None:
        return _DOMAIN_CACHE
    try:
        from core.classify.bucket_registry_v10 import load_taxonomy
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

    返回 {activities: 每个活动一行, stats: 汇总}。
    """
    if vouchers.empty:
        return {"activities": [], "stats": {}}

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

        activities.append({
            "业务活动": str(activity),
            "域": domains.get(str(activity), ""),
            "凭证": count,
            "笔数占比": _pct(count, total_vouchers),
            "金额": round(amount, 2),
            "金额占比": _pct(amount, total_amount),
            "主要制单人": maker,
            "该人占比": _pct(maker_count, count),
            "金额HHI": amount_hhi,
            "对手方HHI": hhi(counterpart.values()),
            "判读": f"金额{band(amount_hhi)} · 对手方{band(hhi(counterpart.values()))}",
            "主要对手方": _top_text(counterpart, subject_total),
        })

    activities.sort(key=lambda item: (-item["金额"], item["业务活动"]))

    return {
        "activities": activities,
        "stats": {
            "vouchers": total_vouchers,
            "amount": round(total_amount, 2),
            "activities": len(activities),
            "unclassified": int((work["活动"] == "未分类").sum()),
        },
    }
