# -*- coding: utf-8 -*-
"""行为层：现金收付。

**为什么单独一块**：它跟科目组合无关。一张凭证用现金付了 25 万，无论科目怎么配、
落在哪个业务群，这件事本身就值得看——现金没有银行流水可对，票据容易缺，个税容易漏，
也是账外资金最常见的出口。

识别只看一个动作：凭证里「库存现金」有没有动。

- **现金支付**：库存现金出现在贷方（钱从抽屉出去）；
- **现金收款**：库存现金出现在借方（钱进抽屉）——这里指**广义的取得库存现金**，
  包括从银行提现。提几十万现金出来本身就是问题：正常企业走对公转账，抽屉里不该
  过这么大的量。所以不把提现剔出去，也不单列。

**阈值取两者更低**：本账该类均值 × 50%，与 **5,000 元** 这个硬线比，哪个低用哪个。
硬线的依据是"现在企业很少有超过几千元的现金收支"；仍要跟均值比，是因为一份账里
可能出现很多笔两三千的现金收支——单看 5,000 会全部放过，按均值算才看得见它们。
低优先级的一律保留、只按金额排序：现金没有银行流水可对，小金额也值得知道。

不给建议：现金该不该用、怎么整改，是项目组结合内控去判断的事。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from . import leads as lead_mod

LAYER = "行为层"

CASH = "库存现金"
# 阈值上限：企业很少有超过几千元的现金收支，超过这个数就该看一眼
HARD_LIMIT = 5_000.0
# 阈值 = min(该类均值 × 这个系数, HARD_LIMIT)
COEF = 0.5


def _counterparts(frame: pd.DataFrame) -> Dict[str, List[str]]:
    """每张凭证里，除现金以外的科目（按金额降序，最多 6 个）。"""
    rest = frame[frame["subject"].astype(str).str.strip() != CASH].copy()
    rest["subject"] = rest["subject"].astype(str).str.strip()
    rest["amount"] = rest["debit"].abs() + rest["credit"].abs()
    rest = rest.groupby(["vid", "subject"])["amount"].sum().reset_index()
    rest = rest.sort_values(["vid", "amount"], ascending=[True, False])
    out: Dict[str, List[str]] = {}
    for vid, block in rest.groupby("vid"):
        out[str(vid)] = [str(s) for s in block["subject"].head(6)]
    return out


def _summary(frame: pd.DataFrame) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for vid, text in zip(frame["vid"].astype(str), frame.get("summary", "")):
        if vid not in out:
            out[vid] = str(text or "")[:40]
    return out


def detect(frame: pd.DataFrame,
           coef: float = COEF) -> Dict[str, Any]:
    """现金收付 → 线索（一张凭证一条）。"""
    if frame is None or frame.empty:
        return {"leads": [], "stats": {"pay": {}, "recv": {}, "withdraw": {}}}

    work = frame.copy()
    work["subject"] = work["subject"].astype(str).str.strip()
    cash = work[work["subject"] == CASH]
    if cash.empty:
        return {"leads": [], "stats": {"pay": {}, "recv": {}, "withdraw": {},
                                       "skipped": "没有库存现金"}}

    debit = cash.groupby("vid")["debit"].sum().abs()
    credit = cash.groupby("vid")["credit"].sum().abs()
    pay = credit[credit > 0]
    recv = debit[debit > 0]

    other = _counterparts(work)
    memo = _summary(work)

    def mean_of(series: pd.Series) -> float:
        return float(series.sum() / len(series)) if len(series) else 0.0

    pay_mean, recv_mean = mean_of(pay), mean_of(recv)
    out: List[Dict[str, Any]] = []

    def emit(vids: pd.Series, kind: str, mean: float) -> None:
        # 两个门槛取低：账内均值的一半，和 5,000 元硬线
        limit = min(mean * coef, HARD_LIMIT)
        for vid, amount in vids.sort_values(ascending=False).items():
            peers = other.get(str(vid), [])
            out.append(lead_mod.lead(
                layer=LAYER, kind=kind,
                subject=str(vid),
                evidence=(f"{amount:,.2f} 元，摘要「{memo.get(str(vid), '')}」，"
                          f"对手方 {'、'.join(peers) or '—'}；"
                          f"本账{('付现' if kind == '现金支付' else '收现')}均值 {mean:,.2f} 元、"
                          f"一半是 {mean * coef:,.2f} 元，与硬线 {HARD_LIMIT:,.0f} 取低后"
                          f"门槛 {limit:,.2f} 元。"),
                vouchers=[str(vid)], count=1, amount=float(amount),
                priority=("高" if amount >= limit else "低")))

    if len(pay):
        emit(pay, "现金支付", pay_mean)
    if len(recv):
        # 提现也在这里：广义取得库存现金，几十万现金从银行取出来本身就是问题
        emit(recv, "现金收款", recv_mean)

    stats = {
        "pay": {"count": int(len(pay)), "amount": round(float(pay.sum()), 2),
                "mean": round(pay_mean, 2),
                "threshold": round(min(pay_mean * coef, HARD_LIMIT), 2)},
        "recv": {"count": int(len(recv)), "amount": round(float(recv.sum()), 2),
                 "mean": round(recv_mean, 2),
                 "threshold": round(min(recv_mean * coef, HARD_LIMIT), 2)},
        "hard_limit": HARD_LIMIT,
    }
    return {"leads": out, "stats": stats}
