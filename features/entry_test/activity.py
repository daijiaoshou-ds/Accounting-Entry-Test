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

**桶内占比先向全账基线收缩**（`PRIOR=10`，相当于"任何桶至少按全账比例见过 10 张"）：
否则 4 张的权益筹资桶会把"银行存款 100%"带进群里，让银行存款在每个群都变成第一名。

两条判据：

- **纵向（稀疏大额）**：这个科目在群里占比 ≤1%，但金额 ≥100 万。不看它属于谁，
  只看"这么少、这么大"——1,911 万的其他应收款只有 1 张凭证，就是这种形状。
- **横向（跨对抗群）**：这个科目是**别的群**的特征科目（桶级 z ≥3），而本群与那个群
  性质对抗（供应链 ↔ 销售，三者 ↔ 筹资）。跨对抗群的组合是结构性的错位，不看金额。

费用群天然模糊（群内第一名只占 51%，其他群都在 82% 以上），它允许出现供应链、销售
的科目——费用部门领料、低值易耗品摊销都是这种形状。所以横向只在"对抗群"之间成立，
费用与供应链、销售不算对抗。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 桶内占比向全账基线收缩的先验张数
PRIOR = 10.0
# 群内占比低于它算"稀疏"
RARE_SHARE = 0.01
# 稀疏还要够大才算一条线索
MIN_AMOUNT = 1_000_000.0
# 到这个数以上算高优先级
HIGH_AMOUNT = 10_000_000.0
# 桶级 z：这个科目在这个桶里显著多于全账比例
Z_FEATURE = 3.0
# 桶太小，z 会虚高（4 张全是银行存款，z 能上百）：特征判定要求桶有这个张数
MIN_BUCKET = 30
# 进群平均的最低门槛：低于它的桶，占比就是 1 张 = 100%，跟大桶等权会把群口径拽歪
MEMBER_MIN = 10
# 全账占比超过这个数的科目是“公共科目”（银行存款 28%、应交税费 26%、应付账款 59%），
# 它们出现在哪都正常，不能拿来当“这个群特有”的证据
GENERIC_Q = 0.20


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
    work = work[work["subject"] != ""]
    if work.empty:
        return {"leads": [], "stats": {"groups": 0, "cells": 0,
                                       "skipped": "四群内没有数据"}}

    # 「家」在全账范围找：结算手续费、期末结转这些不在四群里的桶，也是科目的真实用武之地。
    # 只在四群里找会把财务费用算成"筹资专有"（它其实在结算手续费桶里更常见）。
    all_pair = work[["vid", "act", "subject"]].drop_duplicates()
    bucket_n = all_pair.groupby("act")["vid"].nunique()
    cell_n = (all_pair.groupby(["act", "subject"])["vid"].nunique()
              .rename("n").reset_index())
    total_v = int(all_pair["vid"].nunique())
    base_q = (all_pair.groupby("subject")["vid"].nunique() / total_v).to_dict()

    # 群占比只用四群
    work = work[work["group"].notna()]
    if work.empty:
        return {"leads": [], "stats": {"groups": 0, "cells": 0,
                                       "skipped": "四群内没有数据"}}

    cell_n["N"] = cell_n["act"].map(bucket_n)
    cell_n["q"] = cell_n["subject"].map(base_q).fillna(0.0)
    # 桶内占比（平滑）：(n + PRIOR·q) / (N + PRIOR)
    cell_n["p"] = ((cell_n["n"] + PRIOR * cell_n["q"])
                   / (cell_n["N"] + PRIOR))
    cell_n["z"] = _z(cell_n["n"], cell_n["N"], cell_n["q"])

    # 补全：群内每个桶 × 每个科目都要有值，没出现的按平滑值算，
    # 否则"等权平均"只平均了出现过的桶，小桶消失就没人发现。
    subjects = sorted(cell_n["subject"].unique())
    rows: List[Dict[str, Any]] = []
    for group, info in groups.items():
        # 凭证太少的桶不进群平均：1 张的桶归一化后就是 100%，跟 177 张的桶等权
        # 会把整个群的口径拽歪（筹资费用桶 1 张，就能让筹资群长出 34% 的应付账款）。
        members = [b for b in info["buckets"]
                   if b in bucket_n.index and bucket_n[b] >= MEMBER_MIN]
        if not members:
            continue
        for subject in subjects:
            values: List[float] = []
            for member in members:
                hit = cell_n[(cell_n["act"] == member) & (cell_n["subject"] == subject)]
                if len(hit):
                    values.append(float(hit["p"].iloc[0]))
                else:
                    # 这个桶一次都没用过这个科目：记 0，不填先验值。
                    # 填先验会让 11 张的桶凭空长出 28% 的应付账款（先验 59% × 10 张
                    # 压过 11 张实际），销售群里一张应付账款都没有却算出 14.6%。
                    values.append(0.0)
            if values:
                rows.append({"群": group, "科目": subject,
                             "占比": float(np.mean(values))})

    share = pd.DataFrame(rows)
    money = (work.groupby(["group", "subject"])["amount"].sum()
             .rename("金额").reset_index()
             .rename(columns={"group": "群", "subject": "科目"}))
    share = share.merge(money, on=["群", "科目"], how="left").fillna({"金额": 0.0})

    # 科目金额占它全账金额的比例：判断"这条路走了它多少钱"，比绝对额稳
    subject_total = work.groupby("subject")["amount"].sum().to_dict()
    share["金额占比"] = [float(a) / float(subject_total.get(s, 0) or 1)
                         for a, s in zip(share["金额"], share["科目"])]

    # 桶级 z → 特征科目 → 家映射到群。
    # 三条约束：桶要够大（否则 z 虚高）、科目不能是公共科目（银行存款到哪都正常）、
    # 家只能落在一个群（两个群都算特征＝它就是跨群通用的，不构成“外来”）。
    # 不在四群里的桶记作 `__other__`——财务费用在「结算手续费」桶也是常客，
    # 那只说明它是通用科目，不该因为结算汇兑不在四群就算成“筹资专有”。
    home: Dict[str, set] = {}
    for subject, block in cell_n[cell_n["z"] >= Z_FEATURE].groupby("subject"):
        if float(base_q.get(subject, 0.0)) >= GENERIC_Q:
            continue
        block = block[block["N"] >= MIN_BUCKET]
        if block.empty:
            continue
        home[str(subject)] = {group_of(act, groups) or "__other__" for act in block["act"]}

    out: List[Dict[str, Any]] = []
    for item in share.itertuples(index=False):
        group, subject = str(item.群), str(item.科目)
        # 这一格在群里根本没发生（补全时填了平滑值）：既谈不上稀疏，也谈不上外来
        if float(item.金额) <= 0:
            continue
        info = groups[group]
        homes = home.get(subject, set())
        # 在多个群都是特征 → 通用科目，不构成“外来”
        rivals = (homes - {group}) if len(homes) == 1 else set()
        crossed = sorted(r for r in rivals if r in info["rivals"])
        # 本群里有某个桶把它当主力（≥30% 且 ≥2 笔）：这是本群的科目，再稀疏也不是“外来”。
        # 利润分配桶只有 2 张，够不上群平均的门槛，但利润分配就是它的科目。
        local = cell_n[(cell_n["act"].isin(info["buckets"]))
                       & (cell_n["subject"] == subject)]
        owned = bool(((local["n"] >= 1) & (local["n"] / local["N"] >= 0.50)).any())
        sparse = (not owned and float(item.占比) <= RARE_SHARE
                  and float(item.金额) >= MIN_AMOUNT)
        if not sparse and not crossed:
            continue

        vids = (work[(work["group"] == group) & (work["subject"] == subject)]
                .groupby("vid")["amount"].sum().sort_values(ascending=False).index.tolist())
        why: List[str] = []
        if crossed:
            why.append(f"「{subject}」是「{'、'.join(crossed)}」群的特征科目，"
                       f"而{'、'.join(crossed)}与「{group}」性质对抗")
        if sparse:
            why.append(f"它在「{group}」里各桶平均只占 {item.占比:.2%}，"
                       f"金额却有 {float(item.金额):,.0f} 元"
                       f"（占该科目全账金额的 {item.金额占比:.0%}）")
        if not why:
            why.append("这一格的金额与业务群不匹配")

        out.append(lead_mod.lead(
            layer=LAYER, kind="科目组合孤立",
            subject=f"{group} · {subject}",
            evidence="；".join(why) + "。",
            vouchers=vids, count=len(vids), amount=float(item.金额),
            priority=("高" if float(item.金额) >= HIGH_AMOUNT
                      else "中" if float(item.金额) >= MIN_AMOUNT else "低")))

    # 群画像只列“特色科目”：家唯一落在本群的才算。公共科目（银行存款、应交税费）
    # 不在这里出现——它们到哪都是常客，写进来只会让人以为这是这个群的特征。
    share_map = {(str(r.群), str(r.科目)): float(r.占比)
                 for r in share.itertuples(index=False)}
    features: Dict[str, List[List[str]]] = {}
    for group in groups:
        items = [(s, share_map.get((group, s), 0.0))
                 for s in home if home[s] == {group}]
        items = [(s, v) for s, v in items if v >= 0.03]
        items.sort(key=lambda pair: -pair[1])
        if items:
            features[group] = [[s, f"{v:.1%}"] for s, v in items[:6]]

    stats = {
        "groups": len(groups),
        "cells": int(len(share)),
        "features": features,
        "sizes": {group: int(sum(bucket_n.get(b, 0) for b in groups[group]["buckets"]))
                  for group in groups},
        "prior": PRIOR,
        "rare_share": RARE_SHARE,
        "min_amount": MIN_AMOUNT,
    }
    return {"leads": out, "stats": stats}


def _z(count: pd.Series, total: pd.Series, base: pd.Series) -> pd.Series:
    """桶级 z：这个科目在这个桶里的占比，比全账基线高几个标准差。"""
    q = base.astype(float).clip(1e-6, 1 - 1e-6)
    n = count.astype(float)
    size = total.astype(float).clip(lower=1)
    return (n / size - q) / np.sqrt(q * (1 - q) / size)
