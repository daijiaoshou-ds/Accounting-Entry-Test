# -*- coding: utf-8 -*-
"""个体层：业务活动里的科目组合（群 × 科目）。

一个业务活动有它"本来长什么样"：采购就是原材料、应付账款、应交税费；销售就是
应收账款、主营业务收入。某类科目在一个群里只出现一两次、金额却很大，就是这个
群里混进了不属于它的东西。

**先分群，再比。** 单个业务桶太细（采购 vs 生产制造天然相邻），单看桶会把"产成品
入库被判进采购"这种分类问题报成账务问题。所以先把业务桶并成四个业务群（供应链 /
销售 / 费用 / 筹资），只在群里比。群与对抗关系写在桶表 `groups` 里，运行时不聚类、
不随账变化。

**合并前先归一化，再等权平均。** 直接按凭证数相加，3,858 张的采购桶会压过 85 张的
生产制造桶——生产成本在供应链里只剩 0.57%，被当成"稀疏"。做法是先在桶内算占比，
再对群内各桶取算术平均：业务性质是平等的，桶不该因为张数多就说话大声。

**"家"由点乘矩阵定。** 列剖面问"这个桶多依赖它"，行剖面问"它有多少落在这个桶"，
两者相乘再按科目归一化成份额：

```
份额(桶, 科目) = 列 × 行 ÷ Σ_桶(列 × 行)
有效桶数       = 1 / Σ_桶(份额²)
```

判据只有一条：**有效桶数 ≤ 2**。它的含义是"这个科目的份额相当于摊在几个桶里"——
1 就是独占，2 是有主场，3 以上就不能说属于谁了。这条自带份额下限：因为
`HHI = Σp² ≤ max(p)`，所以有效桶数 ≤2 时最大份额必然 ≥50%，不用再单设份额阈值。

于是高频科目也能是特色：应付账款 66.6% 落在供应链（有效桶数 1.35），不会因为它
"到处都是"被排除；小桶也照样有家：实收资本 4 笔全在筹资（有效桶数 1.00）。
银行存款（8.61）、应交税费（3.45）、应收票据（3.08）、财务费用（2.50）都在这条线外，
它们不专属任何群。

两条检测：

- **纵向（稀疏大额）**：这个科目在群里各桶平均占比 ≤1%、金额 ≥100 万。不看它属于谁，
  只看"这么少、这么大"——1,911 万的其他应收款只有 1 张凭证，就是这种形状。
- **横向（跨对抗群）**：它有家，家是别的群，而本群与那个群性质对抗（供应链↔销售，
  三者↔筹资）。跨对抗群的组合是结构性的错位，不看金额。

费用群天然模糊（群内第一名只占 51%，其他群都在 82% 以上），它允许出现供应链、销售
的科目——费用部门领料、低值易耗品摊销都是这种形状。所以横向只在"对抗群"之间成立。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 桶内占比向全账基线收缩的先验张数（只影响"有出现"的格子，防 1/1=100%）
PRIOR = 10.0
# 进群平均的最低门槛：低于它的桶，占比就是 1 张 = 100%，跟大桶等权会把群口径拽歪
MEMBER_MIN = 10
# 有效桶数 = 1 / Σ份额²。≤2 才有家：相当于摊在 2 个桶以内。
# 这条自带份额下限——HHI ≤ max(份额)，所以有效桶数 ≤2 时最大份额必然 ≥50%。
HOME_EFFECTIVE = 2.0
# 这三类桶是机械凭证，科目构成由记账规则决定，不进点乘矩阵
EXCLUDE = ("资金内部往来", "期末结转", "汇兑损益")
# 群内占比低于它算"稀疏"
RARE_SHARE = 0.01
# 稀疏还要够大才算一条线索
MIN_AMOUNT = 1_000_000.0
# 到这个数以上算高优先级
HIGH_AMOUNT = 10_000_000.0


def config() -> Dict[str, Dict[str, Any]]:
    """从桶表读四个群。读不到就返回空——这一层宁可不跑，也不瞎猜分组。"""
    try:
        from core.classify.bucket_registry_v10 import load_taxonomy
        cfg = load_taxonomy()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for name, info in (cfg.get("groups") or {}).items():
        if str(name).startswith("_"):
            continue
        out[str(name)] = {"buckets": list(info.get("buckets") or []),
                          "rivals": set(info.get("rivals") or [])}
    return out


def group_of(activity: Any, groups: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """业务桶 → 群。子桶（`费用·办公与行政运营`）折到父级再找。"""
    name = str(activity or "").strip()
    if not name:
        return None
    for group, info in groups.items():
        if name in info["buckets"]:
            return group
    if "·" in name:
        base = name.split("·")[0]
        for group, info in groups.items():
            if base in info["buckets"]:
                return group
    return None


def homes_of(rows: pd.DataFrame,
             groups: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """点乘矩阵：哪个桶是哪个科目的家。

    三步：

    1. **列剖面** `P(桶, 科目) = 这个科目在这个桶的凭证数 ÷ 这个桶所有科目的凭证数之和`
       （不是桶的凭证数——一张凭证有多个科目，分母是各科目凭证数相加，这样列合计才是 1）。
    2. **行剖面** `P(科目, 桶) = 这个科目在这个桶的凭证数 ÷ 这个科目的总凭证数`。
    3. **点乘**：`列 × 行`。列表达"这个桶多依赖它"，行表达"它有多少落在这个桶"。

    点乘后再按科目归一化，得到"这个科目的点乘值在各桶的份额"——份额最高的桶就是家。
    判据：有效桶数 `1 / Σ份额²` ≤ 2（`HOME_EFFECTIVE`）。份额 ≥50% 是它的推论，不另设。

    为什么点乘而不是单看一边：应交税费在采购 1,111 笔（量最大）、在税费桶 113 笔（纯度最高）。
    列剖面税费桶 58.6% 远高于采购 11.5%，行剖面采购 53.5% 远高于税费桶 5.4%，
    点乘下来采购 6.17%、税费 3.19%——但按份额看，采购 42%、销售 24%、税费 22%，
    三家差不多，所以它没有家。银行存款更散（采购 15.2%、结算手续费 15.0%），也没家。
    """
    block = rows[["vid", "act", "subject"]].drop_duplicates()
    block = block[block["act"].astype(str) != ""]
    if block.empty:
        return {}
    matrix = (block.groupby(["act", "subject"])["vid"].nunique()
              .unstack(fill_value=0))
    if matrix.empty:
        return {}
    col = matrix.div(matrix.sum(axis=1), axis=0)     # 列剖面，列合计 = 1
    row = matrix.div(matrix.sum(axis=0), axis=1)     # 行剖面，行合计 = 1
    dot = col * row
    total = dot.sum(axis=0)
    share = dot.div(total.replace(0, 1), axis=1)

    out: Dict[str, str] = {}
    for subject in share.columns:
        values = share[subject]
        hhi = float((values ** 2).sum())
        if hhi <= 0 or 1.0 / hhi > HOME_EFFECTIVE:
            continue
        group = group_of(values.idxmax(), groups)
        if group:
            out[str(subject)] = group
    return out


def detect(frame: pd.DataFrame, bucket_of: Optional[Dict[str, str]] = None,
           groups: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """群 × 科目 → 稀疏大额 / 跨对抗群。`bucket_of` 是 {凭证号: 业务活动}。"""
    groups = groups or config()
    if frame is None or frame.empty or not bucket_of or not groups:
        return {"leads": [], "stats": {"groups": 0, "cells": 0,
                                       "skipped": "没有分组配置"}}

    work = pd.DataFrame({
        "vid": frame["vid"].astype(str),
        "subject": frame["subject"].astype(str).str.strip(),
        "amount": (frame["debit"].abs() + frame["credit"].abs()),
    })
    work["act"] = work["vid"].map(lambda vid: bucket_of.get(vid))
    work["group"] = work["act"].map(lambda act: group_of(act, groups))
    work = work[work["act"].notna() & (work["subject"] != "")]
    if work.empty:
        return {"leads": [], "stats": {"groups": 0, "cells": 0,
                                       "skipped": "没有可用的业务分类"}}

    # 点乘矩阵用全量（排除三类机械凭证）：只在四群里算会被大群带偏
    every = work[~work["act"].astype(str).isin(EXCLUDE)][
        ["vid", "act", "subject"]].drop_duplicates()

    # 列剖面：桶内占比（平滑），群内各桶等权平均
    local = work[work["group"].notna()]
    if local.empty:
        return {"leads": [], "stats": {"groups": 0, "cells": 0,
                                       "skipped": "四群内没有数据"}}

    home = homes_of(every, groups)

    pair = local[["vid", "act", "subject"]].drop_duplicates()
    bucket_n = pair.groupby("act")["vid"].nunique()
    cell_n = (pair.groupby(["act", "subject"])["vid"].nunique()
              .rename("n").reset_index())
    base_q = (pair.groupby("subject")["vid"].nunique()
              / int(pair["vid"].nunique())).to_dict()
    cell_n["N"] = cell_n["act"].map(bucket_n)
    cell_n["q"] = cell_n["subject"].map(base_q).fillna(0.0)
    cell_n["p"] = (cell_n["n"] + PRIOR * cell_n["q"]) / (cell_n["N"] + PRIOR)

    subjects = sorted(cell_n["subject"].unique())
    rows: List[Dict[str, Any]] = []
    for group, info in groups.items():
        members = [b for b in info["buckets"]
                   if b in bucket_n.index and bucket_n[b] >= MEMBER_MIN]
        if not members:
            continue
        for subject in subjects:
            values: List[float] = []
            for member in members:
                hit = cell_n[(cell_n["act"] == member) & (cell_n["subject"] == subject)]
                # 这个桶一次都没用过：记 0。填先验会让 11 张的桶凭空长出 28% 的应付账款
                values.append(float(hit["p"].iloc[0]) if len(hit) else 0.0)
            rows.append({"群": group, "科目": subject,
                         "占比": float(np.mean(values))})

    share = pd.DataFrame(rows)
    money = (local.groupby(["group", "subject"])["amount"].sum()
             .rename("金额").reset_index()
             .rename(columns={"group": "群", "subject": "科目"}))
    share = share.merge(money, on=["群", "科目"], how="left").fillna({"金额": 0.0})
    subject_money = local.groupby("subject")["amount"].sum().to_dict()
    share["金额占比"] = [float(a) / float(subject_money.get(s, 0) or 1)
                         for a, s in zip(share["金额"], share["科目"])]

    out: List[Dict[str, Any]] = []
    for item in share.itertuples(index=False):
        group, subject = str(item.群), str(item.科目)
        if float(item.金额) <= 0:          # 群里没发生过，谈不上稀疏或外来
            continue
        info = groups[group]
        owner = home.get(subject)
        crossed = (owner is not None and owner != group
                   and owner in info["rivals"])
        # 本群有某个桶把它当主力（≥50%），它就是这个群的科目：
        # 利润分配桶只有 2 张、够不上群平均，但利润分配就是它的科目，不该报
        mine = cell_n[(cell_n["act"].isin(info["buckets"]))
                      & (cell_n["subject"] == subject)]
        owned = bool(((mine["n"] >= 1) & (mine["n"] / mine["N"] >= 0.50)).any())
        sparse = (not owned and owner != group
                  and float(item.占比) <= RARE_SHARE
                  and float(item.金额) >= MIN_AMOUNT)
        if not crossed and not sparse:
            continue

        vids = (local[(local["group"] == group) & (local["subject"] == subject)]
                .groupby("vid")["amount"].sum().sort_values(ascending=False).index.tolist())
        why: List[str] = []
        if crossed:
            why.append(f"「{subject}」的家在「{owner}」群，而{owner}与「{group}」性质对抗")
        if sparse:
            why.append(f"它在「{group}」里各桶平均只占 {item.占比:.2%}，"
                       f"金额却有 {float(item.金额):,.0f} 元"
                       f"（占该科目全账金额的 {item.金额占比:.0%}）")

        out.append(lead_mod.lead(
            layer=LAYER, kind="科目组合孤立",
            subject=f"{group} · {subject}",
            evidence="；".join(why) + "。",
            vouchers=vids, count=len(vids), amount=float(item.金额),
            priority=("高" if float(item.金额) >= HIGH_AMOUNT
                      else "中" if float(item.金额) >= MIN_AMOUNT else "低")))

    share_map = {(str(r.群), str(r.科目)): float(r.占比)
                 for r in share.itertuples(index=False)}
    features: Dict[str, List[List[str]]] = {}
    for group in groups:
        items = [(s, share_map.get((group, s), 0.0))
                 for s, owner in home.items() if owner == group]
        items = [(s, v) for s, v in items if v > 0]
        items.sort(key=lambda pair: -pair[1])
        if items:
            features[group] = [[s, f"{v:.1%}"] for s, v in items[:6]]

    stats = {
        "groups": len(groups),
        "cells": int(len(share)),
        "features": features,
        "homes": dict(sorted(home.items())),
        "sizes": {group: int(sum(bucket_n.get(b, 0) for b in groups[group]["buckets"]))
                  for group in groups},
        "prior": PRIOR,
        "rare_share": RARE_SHARE,
        "min_amount": MIN_AMOUNT,
    }
    return {"leads": out, "stats": stats}
