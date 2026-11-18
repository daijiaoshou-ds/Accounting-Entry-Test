# -*- coding: utf-8 -*-
"""个体层的第一块：业务活动里的科目组合（活动 × 科目）。

一个业务活动有它"本来长什么样"：采购就是原材料、应付账款、应交税费；销售就是
应收账款、主营业务收入。某类科目在这个活动里只出现一两次、金额却很大，就是这个
活动的账里混进了不属于它的东西——采购里冒出生产成本，销售里冒出其他应收款。

这跟"科目对孤立"问的不是同一件事，两者不能互相替代：

- 科目对问「这两个科目之间的通道正不正常」，不看业务语境。所以它会报出"银行存款
  → 实收资本"这种结构稀有但业务正常的组合（股东出资）。
- 活动内问「这个活动里该不该出现这个科目」，用业务活动当先验。股东出资在"权益
  筹资"活动里是常客，不会被报；只有在采购里出现才算。

判据：某科目在这个活动里出现的**凭证占比 ≤ 1%**，且这一格的金额 ≥ 100 万。

为什么不看全账频次：预计负债全账只有 3 笔，在哪个活动里都少，"少"本身不说明
问题；只有拿它跟"这个活动通常用什么科目"比，才分得出"这个活动偶尔用一下"和
"这个科目本来就罕见"。所以分母是活动自己的凭证数。

金额门槛的作用：活动内的长尾很长——每个活动都有一堆只出现一次的科目（押金、
赔偿、零星调整）。没有金额门槛，这一层会变成一张几百行的科目清单。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 这个科目在该活动里的凭证占比，低于它算"罕见"
RARE_SHARE = 0.01
# 这一格的金额下限：不到 100 万不值得占一条线索
MIN_AMOUNT = 1_000_000.0
# 金额到这个数以上，优先级提到"高"
HIGH_AMOUNT = 10_000_000.0


def detect(frame: pd.DataFrame, bucket_of: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """活动 × 科目 → 罕见组合。`bucket_of` 是 {凭证号: 业务活动}（上游清洗的结论）。"""
    if frame is None or frame.empty or not bucket_of:
        return {"leads": [], "stats": {"activities": 0, "cells": 0}}

    work = pd.DataFrame({
        "vid": frame["vid"].astype(str),
        "subject": frame["subject"].astype(str).str.strip(),
        "amount": (frame["debit"].abs() + frame["credit"].abs()),
    })
    work["活动"] = work["vid"].map(lambda vid: bucket_of.get(vid))
    work = work[work["活动"].notna() & (work["subject"] != "")]
    if work.empty:
        return {"leads": [], "stats": {"activities": 0, "cells": 0}}

    # 一个科目在一张凭证里可能有多行，只算一次凭证
    uniq = work[["vid", "活动", "subject"]].drop_duplicates()
    per_activity = uniq.groupby("活动")["vid"].nunique().rename("活动凭证")
    cell = (uniq.groupby(["活动", "subject"])["vid"].nunique()
            .rename("科目凭证").reset_index())
    cell["活动凭证"] = cell["活动"].map(per_activity)
    cell["占比"] = cell["科目凭证"] / cell["活动凭证"]

    money = work.groupby(["活动", "subject"])["amount"].sum().rename("金额").reset_index()
    cell = cell.merge(money, on=["活动", "subject"], how="left")

    rare = cell[(cell["占比"] <= RARE_SHARE) & (cell["金额"] >= MIN_AMOUNT)]
    rare = rare.sort_values("金额", ascending=False)

    out: List[Dict[str, Any]] = []
    for item in rare.itertuples(index=False):
        vids = (work[(work["活动"] == item.活动) & (work["subject"] == item.subject)]
                .groupby("vid")["amount"].sum().sort_values(ascending=False).index.tolist())
        out.append(lead_mod.lead(
            layer=LAYER, kind="活动科目孤立",
            subject=f"{item.活动} · {item.subject}",
            evidence=(f"「{item.活动}」的 {int(item.活动凭证):,} 张凭证里，只有 "
                      f"{int(item.科目凭证)} 张用到「{item.subject}」（{item.占比:.2%}），"
                      f"合计 {float(item.金额):,.2f}。这个活动通常不用这个科目。"),
            vouchers=vids, count=int(item.科目凭证),
            amount=float(item.金额),
            priority="高" if float(item.金额) >= HIGH_AMOUNT else "中"))

    return {
        "leads": out,
        "stats": {
            "activities": int(cell["活动"].nunique()),
            "cells": int(len(cell)),
            "rare": int(len(rare)),
            "threshold": RARE_SHARE,
            "min_amount": MIN_AMOUNT,
        },
    }
