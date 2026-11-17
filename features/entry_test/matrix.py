# -*- coding: utf-8 -*-
"""个体层的第二块：科目组合孤立（科目对矩阵 + 份额 + 三条规则）。

逐笔看凭证看不出"这一对科目不该一起出现"——一笔装修费挂其他应付款，单看很正常。
**整本账的边**才看得出来：原材料一年动两千次，从不走预计负债；而预计负债一年只
登台三次，两次是给原材料当对手方。这种"非对称"不是单张凭证能说的。

矩阵的每一格是「借 X → 贷 Y」的笔数，于是有两种边际份额：

    σ_row = 边 / X 作借方出现的总笔数      —— 这笔钱在 X 的支付习惯里占多少
    σ_col = 边 / Y 作贷方出现的总笔数      —— Y 收到的钱里有多少来自 X

三条规则，按设计文档的口径：

1. **私人专线**（高）：稀有 ∧ 大额 ∧ 一端活跃且份额极低 ∧ 另一端冷门且被这条边
   专享。含义是"这个科目像是专门为这条通道准备的"。
2. **枢纽杂音**（低）：两端份额都极低，且贷方是个超大枢纽（银行、应付账款）。
   一次性买专利付现就是这种：正常世界的杂音，看一眼即可。
3. **孤立双边**（中）：两端都是冷门科目，这条边只出现过一两次。不是"专线"
   （样本量 1 的"专享"是废话），是"待解释"：这笔钱为什么这么走。

数据来自上游「对方科目分析」的行表（`contra_rows.parquet`，清洗的 link 阶段搬进
来的）。不用自己枚举"全部借 × 全部贷"：多借多贷拆对是对方科目分析已经做完的事，
这里再拆一遍只会造出一堆账上不存在的边。

白名单（汇兑损益 / 结转期末损益 / 资金内部往来）在调用前已经剔掉——这类凭证的
科目组合由业务形态决定，不是人选的，进矩阵只会把"常规"搅成"异常"。

另有一层**边级**白名单（`_structural`）：有些科目对天然就该稀有，因为那条通道
一年只会走几次。结算期末损益（一端是本年利润）和成本结转（主营业务成本 ↔ 存货
类）就是这种——它们的"稀有 ∧ 专享"是记账规则，不是疑点。这层不按凭证放过：
同一张凭证里别的边照样进矩阵。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import leads as lead_mod

LAYER = "个体层"

# 稀有边：全年出现这么多次以内才谈得上"专线"
RARE_MAX = 5
# 活跃端：借方科目全年至少动这么多笔，它的"份额极低"才有信息量
ACTIVE_MIN = 100
# 冷门端：贷方科目全年至多这么多笔，它的"专享"才说明问题
COLD_MAX = 50
# 私人专线的份额门槛。
# 冷门端不要求"过半"：一年只登台 6 次的科目，2 次给同一个对手方已经是它的三分之一，
# 而另一端（管理费用 4,780 笔）只走了 0.04%——这种非对称才是"专线"的本体。
# 门槛卡在 50% 会把「管理费用→存货跌价准备」这种真正该看的漏掉。
PRIVATE_SIGMA_ROW = 0.02
PRIVATE_SIGMA_COL = 0.30
# 枢纽：贷方科目是个超大枢纽（银行存款这类）
HUB_MIN = 500
HUB_SIGMA_ROW = 0.01
HUB_SIGMA_COL = 0.002
# 孤立双边：两端都冷门
ISOLATED_MAX = 10
# 边金额下限：不到 10 万的边不进线索（再稀有也不值得占一条）
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
    """对方科目行表 → 边表（借方科目, 贷方科目, 单笔金额, 凭证号）。

    每一行都是"我这端 → 对方那端"的一对：借方行说明钱进了本科目、对方是贷方；
    贷方行反过来。所以方向由借贷金额定，不由科目名猜。
    """
    v_col = mapping.get("voucher_no") or "凭证编号"
    s_col = mapping.get("subject") or "一级科目"
    d_col = mapping.get("debit") or "借方金额"
    c_col = mapping.get("credit") or "贷方金额"

    for need, name in ((v_col, "凭证号"), (s_col, "一级科目")):
        if need not in rows.columns:
            raise ValueError(f"对方科目行表缺「{name}」列：{need}")

    debit = pd.to_numeric(rows.get(d_col), errors="coerce").fillna(0.0)
    credit = pd.to_numeric(rows.get(c_col), errors="coerce").fillna(0.0)
    subject = rows[s_col].astype(str).str.strip()
    counter = rows["对方科目-一级"].astype(str).str.strip() if "对方科目-一级" in rows.columns else ""

    # 多借多贷拆出的行里，对方科目可能是"甲、乙"这种拼接值——一条边只能有一个
    # 端点，所以按「、」拆成多行，金额均摊。拆不了（空）就丢掉。
    records: List[Dict[str, Any]] = []
    for vid, subj, other, d, c in zip(
            rows[v_col].astype(str), subject, counter, debit, credit):
        if not subj or subj in ("", "nan") or not other or other in ("", "nan"):
            continue
        amount = abs(float(d)) if abs(float(d)) > 0 else abs(float(c))
        if amount <= 0:
            continue
        parts = [part.strip() for part in str(other).split("、") if part.strip()]
        if not parts:
            continue
        share = amount / len(parts)
        for part in parts:
            if abs(float(d)) > 0:
                records.append({"借": subj, "贷": part, "金额": share, "凭证号": vid})
            else:
                records.append({"借": part, "贷": subj, "金额": share, "凭证号": vid})

    return pd.DataFrame(records, columns=["借", "贷", "金额", "凭证号"])


def _hhi(values: List[float]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    return round(sum((v / total) ** 2 for v in values), 4)


def detect(edges: pd.DataFrame) -> Dict[str, Any]:
    """边表 → {leads, stats, matrix}。"""
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
    table["lift"] = [count * grand / max(row_total.get(b, 1) * col_total.get(c, 1), 1)
                     for b, c, count in zip(table["借"], table["贷"], table["笔数"])]

    out: List[Dict[str, Any]] = []
    weak = 0
    skipped_structural = 0
    for item in table.itertuples(index=False):
        if _structural(str(item.借), str(item.贷)):
            skipped_structural += 1
            continue
        if float(item.最大单笔) < BIG_MIN or float(item.金额) < MIN_EDGE_AMOUNT:
            continue
        count, row_all, col_all = int(item.笔数), int(item.row_all), int(item.col_all)
        sigma_row, sigma_col = float(item.σ_row), float(item.σ_col)

        kind, priority, read = "", "", ""
        if (count <= RARE_MAX and sigma_row <= PRIVATE_SIGMA_ROW
                and row_all >= ACTIVE_MIN and sigma_col >= PRIVATE_SIGMA_COL
                and col_all <= COLD_MAX):
            kind, priority = "私人专线", "高"
            read = (f"「{item.借}」一年动 {row_all:,} 笔，只有 {sigma_row:.2%} 走这条；"
                    f"而「{item.贷}」一年只出现 {col_all} 次，{sigma_col:.0%} 是它给的。"
                    "这个对手方像是专门为这条通道准备的。")
        elif (count <= RARE_MAX and sigma_row <= HUB_SIGMA_ROW
                and sigma_col <= HUB_SIGMA_COL and col_all >= HUB_MIN):
            kind, priority = "枢纽杂音", "低"
            read = (f"一次性稀有边，但对面是「{item.贷}」这样的枢纽"
                    f"（一年 {col_all:,} 笔），对它毫无感觉——像正常世界的杂音。")
            weak += 1
        elif row_all <= ISOLATED_MAX and col_all <= ISOLATED_MAX:
            kind, priority = "孤立双边", "中"
            read = (f"两端都是冷门科目（借方全年 {row_all} 笔、贷方 {col_all} 笔），"
                    "这条边就出现过这么一两次。样本量太小，谈不上专线，只是要解释。")

        if not kind:
            continue

        vids = (edges[(edges["借"] == item.借) & (edges["贷"] == item.贷)]
                .sort_values("金额", ascending=False)["凭证号"].tolist())
        out.append(lead_mod.lead(
            layer=LAYER, kind=f"科目组合孤立·{kind}",
            subject=f"{item.借} → {item.贷}",
            evidence=(f"全年 {count} 笔、合计 {float(item.金额):,.2f}"
                      f"（最大一笔 {float(item.最大单笔):,.2f}）；"
                      f"占借方 {sigma_row:.2%}、占贷方 {sigma_col:.2%}。{read}"),
            vouchers=vids, count=count, amount=float(item.金额), priority=priority))

    stats = {
        "edges": int(len(table)),
        "rows": int(len(edges)),
        "subjects": int(table["借"].nunique()),
        "big_threshold": round(big, 2),
        "private": sum(1 for x in out if "私人专线" in x["类型"]),
        "isolated": sum(1 for x in out if "孤立双边" in x["类型"]),
        "hub": weak,
        "structural_skipped": skipped_structural,
        # 常见边一起带走：报告要拿它当"常规长什么样"的对照，单独返回一个字段
        # 会在阶段层被丢掉（那里只搬 stats）。
        "matrix_preview": _top_matrix(table),
    }
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
