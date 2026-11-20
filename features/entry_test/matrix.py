# -*- coding: utf-8 -*-
"""个体层的第二块：科目对孤立（借×贷 矩阵 + 两端份额）。

逐笔看凭证看不出"这一对科目不该一起出现"——一笔装修费挂其他应付款，单看很正常。
**整本账的边**才看得出来：原材料一年动两千次，从不走预计负债；而预计负债一年只
登台三次，两次是给原材料当对手方。

矩阵的每一格是「借 X → 贷 Y」的凭证数，于是有两种份额：

    σ_row = 这条边 / X 作借方出现的凭证数   —— 这笔钱在 X 的往来里占多少
    σ_col = 这条边 / Y 作贷方出现的凭证数   —— Y 的收入里有多少来自 X

**判据只有一条**：σ_row 低（对 X 是罕见通道）且 σ_col 高（对 Y 是主要来源）。
两个缺一不可——只看 σ_row，所有一次性往来都算（借方对谁都很罕见）；只看 σ_col，
会选出"某个科目的唯一用途"（那只是它本来的用途，不是异常）。

**不设"枢纽杂音""孤立双边"这类分档**：它们是同一套份额的另外两种阈值组合，不是
三种不同的现象；给每一档起个名字，读者要多记三个词，而这三个词不携带信息——
两端都稀有就是两端都稀有，样本量 1 的"专享"是废话。

**不用 E 与 lift**：E 假设"一张凭证有 X"和"一张凭证有 Y"互相独立，而分录是业务
驱动的、从来不独立。更硬的问题是它会在两端规模悬殊时失效——若原材料出现在所有
凭证里，它的概率是 100%，于是 E 恒等于实际、lift 恒等于 1；反之 E 趋近 0 时 lift
会爆到几千（实测 E<0.5 的边 lift 中位数 24、最大 2,619）。lift 是"关联度"，不是
"异常度"，这里不拿它当判据，也不在报告里展示。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 稀有：全年出现这么多次以内才谈得上"一条通道"
RARE_MAX = 5
# 借方端要够活跃，它的"份额极低"才有信息量（一年只动两次的科目，份额 100% 也不稀奇）
ACTIVE_MIN = 100
# 贷方端要够冷门，它的"被专享"才说明问题
COLD_MAX = 50
# 对借方：这条边占它全部往来的比例要低于这个数（罕见通道）
SIGMA_ROW = 0.02
# 对贷方：这条边占它全部来源的比例要高于这个数（主要来源）
SIGMA_COL = 0.30
# 边金额下限：不到 10 万的边不进线索
MIN_EDGE_AMOUNT = 100_000.0
# 大额：边内最大单笔要在全账 P98 以上
BIG_QUANTILE = 0.98
BIG_MIN = 50_000.0

# 边级白名单：这些科目对天然稀有，不是疑点
_CLOSING = ("本年利润", "利润分配")
_COST = ("主营业务成本", "其他业务成本")
_INVENTORY = ("库存商品", "发出商品", "生产成本", "劳务成本", "合同履约成本",
              "开发成本", "消耗性生物资产", "半成品", "委托加工物资")


def _structural(borrow: str, lend: str) -> bool:
    """这条边是不是记账规则本身就会造出来的。"""
    if borrow == lend:
        return True                      # 自环：同一科目借贷对冲，不是"两边"
    if borrow in _CLOSING or lend in _CLOSING:
        return True                      # 结转到本年利润/利润分配
    if borrow in _COST and lend in _INVENTORY:
        return True                      # 结转销售成本
    if lend in _COST and borrow in _INVENTORY:
        return True
    return False


def build_edges(rows: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """对方科目行表 → 边表（借, 贷, 金额, 凭证号），**每张凭证每对科目一条**。

    一行只描述一个端点视角，所以同一条配对会被写两遍（「借 存货跌价准备 / 贷 管理
    费用」两行都指向对方）。直接按行建边，一张凭证就会长出 A→B 和 B→A 各一条，
    其中一条账上根本不存在。做法是三步：

    1. 每行出一条边：借方行是 subj→对方，贷方行反过来；红字（负数）把方向翻过来，
       金额保留符号。
    2. 去掉"同一笔被写两遍"的重复（同一凭证、同一对、同一金额）。
    3. 同一凭证同一对求和——蓝字入库 512 万 + 红字冲回 512 万，净额才是这张凭证
       真正在这对科目之间发生的数。

    为什么不用"净额法"（按科目先算净借额再配对）：过路科目会失效。一张凭证里
    应付账款先收 6,690 又付 6,690，净额为 0，整张凭证的边会全部消失——实测那样
    会漏掉 12% 的借方发生额。

    **已知近似**：多借多贷（借 A / 贷 B、C）时两端各生成一次，边金额会比实际偏高
    （实测全账合计比借方发生额多约 9%）。笔数与两端份额是准的，检测只看这两样，
    所以不修；但报告里那个"金额"是上限、不是净值。
    """
    v_col = mapping.get("voucher_no") or "凭证编号"
    s_col = mapping.get("subject") or "一级科目"
    d_col = mapping.get("debit") or "借方金额"
    c_col = mapping.get("credit") or "贷方金额"

    for need, name in ((v_col, "凭证号"), (s_col, "一级科目"), (d_col, "借方金额")):
        if need not in rows.columns:
            raise ValueError(f"对方科目行表缺「{name}」列：{need}")
    if "对方科目-一级" not in rows.columns:
        raise ValueError("对方科目行表缺「对方科目-一级」列")

    debit = pd.to_numeric(rows[d_col], errors="coerce").fillna(0.0)
    credit = pd.to_numeric(rows.get(c_col), errors="coerce").fillna(0.0)

    records: List[Dict[str, Any]] = []
    for vid, subj, other, d, c in zip(rows[v_col].astype(str), rows[s_col].astype(str),
                                      rows["对方科目-一级"].astype(str), debit, credit):
        subj, other = subj.strip(), other.strip()
        if not subj or not other or "nan" in (subj, other):
            continue
        amount = abs(float(d)) if abs(float(d)) > 0 else abs(float(c))
        if amount <= 0:
            continue

        # 本科目在这笔里算贷方吗？正数照列判断，红字把方向翻过来
        # （借方的红字是贷方，贷方的红字是借方）。红字的方向反过来之后，
        # 金额就取正——负号已经在"方向"里表达过一次了。
        as_credit = float(c) > 0 or float(d) < 0
        signed = amount

        parts = [part.strip() for part in other.split("、") if part.strip()]
        if not parts:
            continue
        share = signed / len(parts)
        for part in parts:
            if part == subj:
                continue
            if as_credit:
                records.append({"借": part, "贷": subj, "金额": share, "凭证号": vid})
            else:
                records.append({"借": subj, "贷": part, "金额": share, "凭证号": vid})

    if not records:
        return pd.DataFrame(columns=["借", "贷", "金额", "凭证号"])

    frame = pd.DataFrame(records)
    # 同一笔被两端各写了一遍：凭证 + 方向 + 金额绝对值相同就是同一条
    frame["键"] = frame["金额"].abs().round(2)
    frame = frame.drop_duplicates(["凭证号", "借", "贷", "键"])

    grouped = (frame.groupby(["凭证号", "借", "贷"], sort=False)["金额"]
               .sum().reset_index())
    grouped["金额"] = grouped["金额"].abs()
    grouped = grouped[grouped["金额"] > 0.005]

    # 同一张凭证里 A→B 与 B→A 同时存在时，有两种可能：
    #   · 正负两笔互相冲抵（暂估 500、红字冲回 480）→ 实际净额 20
    #   · 两笔方向相反但各自独立的业务（收退款 1,015、付款 49,530）→ 都该留着
    # 差别只在金额差多少：接近（<20%）的合并成净额，差得远的分开留。
    merged: List[Dict[str, Any]] = []
    grouped["小"] = [min(a, b) for a, b in zip(grouped["借"], grouped["贷"])]
    grouped["大"] = [max(a, b) for a, b in zip(grouped["借"], grouped["贷"])]
    for (_vid, low, high), block in grouped.groupby(["凭证号", "小", "大"], sort=False):
        forward = block[block["借"] == low]["金额"].sum()
        backward = block[block["借"] == high]["金额"].sum()
        if forward > 0 and backward > 0 and abs(forward - backward) / max(forward, backward) < 0.2:
            net = abs(forward - backward)
            if net > 0.005:
                merged.append({"借": low if forward > backward else high,
                               "贷": high if forward > backward else low,
                               "金额": net, "凭证号": _vid})
            continue
        for row in block.itertuples(index=False):
            merged.append({"借": row.借, "贷": row.贷,
                           "金额": row.金额, "凭证号": row.凭证号})

    out = pd.DataFrame(merged, columns=["借", "贷", "金额", "凭证号"])
    # 自环先剔：招行→建行这类内部账户互转，借和贷都是"银行存款"。它不是"两个科目
    # 的关系"，进矩阵只会把银行存款的行合计撑大、把别的边的份额压小。
    out = out[(out["借"] != out["贷"]) & (out["金额"] > 0.005)]
    return out.reset_index(drop=True)


def _hhi(values: List[float]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    return round(sum((v / total) ** 2 for v in values), 4)


def vids_of(edges: pd.DataFrame, borrow: str, lend: str) -> List[str]:
    """这条边落在哪些凭证上，按金额从大到小。"""
    sub = edges[(edges["借"] == borrow) & (edges["贷"] == lend)]
    return sub.sort_values("金额", ascending=False)["凭证号"].tolist()


def _grid(rows: pd.DataFrame, mapping: Dict[str, str]) -> Dict[str, Any]:
    """投入产出方阵：行 = 产出（钱从哪来），列 = 投入（钱到哪去）。

    与边表的区别：边表是「借 → 贷」，只能表示一边借一边贷；借方对借方
    （原材料借方 ↔ 库存商品借方）在边表里没有位置。这里不区分借贷方向，
    只问「钱从哪个科目流到哪个科目」，所以任意科目对都能落进方阵，自环
    落在对角线上。

    只取**借方行**构建：每笔借方都完整列出了它的对方科目，而贷方行在一贷
    多借时只写了一个对方（实测两端口径有 149 格不一致，全是这种情形）。
    借方行的金额是配对金额，同一科目各行之恒等于它的借方发生额，直接累加
    即可，不需要按凭证分摊。
    """
    src_col, dst_col = "对方科目-一级", mapping.get("subject") or "一级科目"
    amt_col = mapping.get("debit") or "借方金额"
    if src_col not in rows.columns or amt_col not in rows.columns:
        return {}

    work = pd.DataFrame({
        "src": rows[src_col].astype(str).str.strip(),
        "dst": rows[dst_col].astype(str).str.strip(),
        "amt": pd.to_numeric(rows[amt_col], errors="coerce").fillna(0.0),
    })
    work = work[(work["amt"] != 0) & (work["src"] != "") & (work["dst"] != "")
                & (~work["src"].isin(["nan"])) & (~work["dst"].isin(["nan"]))]
    if work.empty:
        return {}

    matrix = work.groupby(["src", "dst"])["amt"].sum()
    col_sum = matrix.groupby(level=1).sum()
    row_sum = matrix.groupby(level=0).sum()
    # 按「投入额」（列合计 = 该科目借方发生额）从大到小排，大科目在前
    subjects = sorted(set(matrix.index.get_level_values(0)) | set(matrix.index.get_level_values(1)),
                      key=lambda name: -abs(float(col_sum.get(name, 0.0))))
    position = {name: index for index, name in enumerate(subjects)}
    cells = [[position[src], position[dst], round(float(value), 2)]
             for (src, dst), value in matrix.items()]

    def _sums(series) -> List[float]:
        return [round(float(series.get(name, 0.0)), 2) for name in subjects]

    return {"subjects": subjects, "cells": cells,
            "col_sum": _sums(col_sum), "row_sum": _sums(row_sum)}


def detect(edges: pd.DataFrame, rows: Optional[pd.DataFrame] = None,
           mapping: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """边表 → {leads, stats, matrix}。`rows` 给了就顺带算投入产出方阵。"""
    if edges is None or edges.empty:
        return {"leads": [], "stats": {"edges": 0, "pairs": 0}, "matrix": []}

    group = edges.groupby(["借", "贷"], sort=False)
    table = group.agg(笔数=("金额", "size"), 金额=("金额", "sum"),
                      最大单笔=("金额", "max")).reset_index()

    row_total = table.groupby("借")["笔数"].sum().to_dict()
    col_total = table.groupby("贷")["笔数"].sum().to_dict()
    grand = float(table["笔数"].sum())
    big = float(np.quantile(edges["金额"].to_numpy(), BIG_QUANTILE)) if len(edges) else 0.0
    big = max(big, BIG_MIN)

    table["σ_row"] = [count / max(row_total.get(base, 0), 1)
                      for base, count in zip(table["借"], table["笔数"])]
    table["σ_col"] = [count / max(col_total.get(base, 0), 1)
                      for base, count in zip(table["贷"], table["笔数"])]
    table["row_all"] = [row_total.get(base, 0) for base in table["借"]]
    table["col_all"] = [col_total.get(base, 0) for base in table["贷"]]

    out: List[Dict[str, Any]] = []
    skipped_structural = 0
    for item in table.itertuples(index=False):
        if _structural(str(item.借), str(item.贷)):
            skipped_structural += 1
            continue
        if float(item.最大单笔) < BIG_MIN or float(item.金额) < MIN_EDGE_AMOUNT:
            continue

        count, row_all, col_all = int(item.笔数), int(item.row_all), int(item.col_all)
        sigma_row, sigma_col = float(item.σ_row), float(item.σ_col)

        # 唯一判据：对借方是罕见通道，对贷方却是主要来源。
        # 两个条件缺一不可——只看前者会把所有一次性往来都算进来（借方对谁都很罕见），
        # 只看后者会选出"某个科目的唯一用途"（那只是它本来的用途）。
        if not (count <= RARE_MAX and sigma_row <= SIGMA_ROW
                and row_all >= ACTIVE_MIN and sigma_col >= SIGMA_COL
                and col_all <= COLD_MAX):
            continue

        priority = "高" if sigma_col >= 0.8 else "中"
        out.append(lead_mod.lead(
            layer=LAYER, kind="科目对孤立",
            subject=f"{item.借} → {item.贷}",
            evidence=(f"全年 {count} 笔、合计 {float(item.金额):,.2f}"
                      f"（最大一笔 {float(item.最大单笔):,.2f}）。"
                      f"对「{item.借}」是罕见通道：一年 {row_all:,} 笔里只占 {sigma_row:.2%}；"
                      f"对「{item.贷}」却是主要来源：一年 {col_all} 笔里 {sigma_col:.0%} 走这里。"),
            vouchers=vids_of(edges, item.借, item.贷),
            count=count, amount=float(item.金额), priority=priority))

    stats = {
        "edges": int(len(table)),
        "rows": int(len(edges)),
        "subjects": int(table["借"].nunique()),
        "big_threshold": round(big, 2),
        "structural_skipped": skipped_structural,
        # 常见边一起带走：报告要拿它当"常规长什么样"的对照，单独返回一个字段
        # 会在阶段层被丢掉（那里只搬 stats）。
        "matrix_preview": _top_matrix(table),
    }
    # 投入产出方阵：报告 2.4 的画像用它，只在 reports 里展示，不参与判定
    grid = _grid(rows, mapping or {}) if rows is not None else {}
    if grid:
        stats["grid"] = grid
    return {"leads": out, "stats": stats}


def _top_matrix(table: pd.DataFrame, limit: int = 12) -> List[List[str]]:
    """报告里的矩阵预览：按笔数取前若干条边，让人看到"常规长什么样"。

    矩阵不看常规就没有对照——只给异常边，读的人不知道这是不是正常。
    """
    # 自环与机械结转的边不进预览：它们在判定时已经被跳过，摆在"常规"里只会让人问
    # "应付账款为什么对自己付款"。
    clean = table[[not _structural(str(b), str(c)) for b, c in zip(table["借"], table["贷"])]]
    top = clean.sort_values("笔数", ascending=False).head(limit)
    return [[str(item.借), str(item.贷), f"{int(item.笔数):,}",
             f"{item.σ_row:.1%}", f"{item.σ_col:.1%}"]
            for item in top.itertuples(index=False)]
