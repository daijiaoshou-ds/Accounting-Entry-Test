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

**"家"由行剖面决定。** 列剖面是"这个桶里各科目怎么分"（桶内占比）；行剖面是反过来的
——"这个科目的笔数分在哪些群"。原材料的 510 张里 506 张在供应链（99.2%），供应链
就是它的家；银行存款的 1,944 张里最多的一群只占 45.9%、第二大群 36.3%，它没有家。

判据只有两条（都不设绝对占比门槛）：

- 某群占该科目 **≥60%**，或 **≥50% 且是第二名的 2 倍** → 这个群是它的家；
- 科目全账不足 3 笔不判（一笔落哪都是 100%，说明不了归属）。

于是高频科目也能是特色：应付账款全账 4,633 张，84.2% 落在供应链，它就是供应链的
特色科目——不会因为它"到处都是"被排除。小桶也照样有家：实收资本 4 笔全在筹资。

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
# 科目全账不足这么多笔不判归属（一笔落哪都是 100%）
SUBJECT_MIN = 3
# 家的判据：行剖面（笔数分布）某群 ≥50%，列剖面（群内使用率）是次高群的 2 倍，
# 且两个剖面指向同一个群
HOME_ROW_SHARE = 0.50
# 本群用它的比例，至少是"其他群里最高的那个"的 3 倍，才算特有。
# 2 倍挡不住业务伴随科目：应交税费在销售 40.3%、供应链 21.7%（1.86 倍），
# 但销售本来就要计销项税，它不是任何群的特有科目。
HOME_RATIO = 3.0
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


def homes_of(rows: pd.DataFrame, cols: pd.DataFrame,
             groups: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """一个科目的"家"：行剖面和列剖面必须指向同一个群。

    - 行剖面（rows）：这个科目的笔数分在哪些群。原材料的 510 张里 506 张在供应链。
    - 列剖面（cols）：这个群的凭证里有多少比例用它。

    两个都看，是因为单看任一个都会被带偏：只按笔数，应交税费 59% 落在供应链（采购
    团量大），可它在销售群的使用率更高（销项税）；只按使用率，银行存款在筹资群 79%
    （借款、付息都过银行），可它全账 28% 的凭证都在用。

    要求：行剖面最大群与列剖面最大群一致、行占比 ≥50%、列占比是次高群的 2 倍。
    `cols` 用加权（群内该科目凭证数 ÷ 群凭证数），不用等权——权益筹资桶只有 4 张，
    等权平均会把它整个抹掉，实收资本就没家了。
    """
    block = rows[["subject", "group", "vid"]].drop_duplicates()
    by = (block.groupby(["subject", "group"])["vid"].nunique()
          .unstack(fill_value=0))
    total = by.sum(axis=1)

    out: Dict[str, str] = {}
    for subject in by.index:
        name = str(subject)
        if float(total[subject]) < SUBJECT_MIN:
            continue
        row = by.loc[subject] / float(total[subject])
        row_groups = [g for g in groups if g in row.index]
        if not row_groups:
            continue
        row_top = max(row_groups, key=lambda g: float(row[g]))
        row_share = float(row[row_top])
        if row_share < HOME_ROW_SHARE:
            continue
        if name not in cols.columns or row_top not in cols.index:
            continue
        col = cols.loc[row_top]
        col_share = float(col.get(name, 0.0))
        others = [float(cols.loc[g].get(name, 0.0)) for g in groups
                  if g != row_top and g in cols.index]
        second = max(others) if others else 0.0
        # 列剖面也必须指向同一个群，且明显高于次高
        col_top = max(groups, key=lambda g: float(cols.loc[g].get(name, 0.0))
                      if g in cols.index else 0.0)
        if col_top == row_top and col_share >= HOME_RATIO * max(second, 1e-9):
            out[name] = row_top
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

    # 行剖面用全量；不属于四群的桶记 __other__，照样计入分母
    every = work[["vid", "act", "subject"]].drop_duplicates()
    every["group"] = every["act"].map(
        lambda act: group_of(act, groups) or "__other__")

    # 列剖面：桶内占比（平滑），群内各桶等权平均
    local = work[work["group"].notna()]
    if local.empty:
        return {"leads": [], "stats": {"groups": 0, "cells": 0,
                                       "skipped": "四群内没有数据"}}

    # 列剖面（加权）：该群凭证里有多少比例用这个科目。判定"家"用它，
    # 等权平均会把只有 4 张的权益筹资桶抹掉，实收资本就没家了。
    by_group = local.pivot_table(index="group", columns="subject", values="vid",
                                 aggfunc="nunique", fill_value=0)
    group_size = local.groupby("group")["vid"].nunique()
    cols = by_group.div(group_size, axis=0)
    home = homes_of(every, cols, groups)

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
