# -*- coding: utf-8 -*-
"""行为层：现金收付。

**为什么单独一块**：它跟科目组合无关。一张凭证用现金付了 25 万，无论科目怎么配、
落在哪个业务群，这件事本身就值得看——现金没有银行流水可对，票据容易缺，个税容易漏，
也是账外资金最常见的出口。

识别只看一个动作：凭证里「库存现金」有没有动。

- **现金支付**：库存现金出现在贷方（钱从抽屉出去）；
- **现金收款**：库存现金出现在借方（钱进抽屉）；
- **提现**：借方但那笔钱来自银行存款——买菜的备用金，跟"收了一笔现金货款"不是一回事，
  单独标出来，不混进收现。

**阈值随账走，不拍固定数**：本账付现均值 = 付现总额 / 付现凭证数，阈值取均值的 50%。
一份一年只付三次现的账，和一份天天付现的账，门槛本来就不该一样。均值会被最大一笔
拉高，所以低优先级的一律保留、只按金额排序——现金这件事，小金额也值得知道。

不给建议：现金该不该用、怎么整改，是项目组结合内控去判断的事。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from . import leads as lead_mod

LAYER = "行为层"

CASH = "库存现金"
BANK = "银行存款"
# 阈值 = 该类均值 × 这个系数
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

    def emit(vids: pd.Series, kind: str, mean: float, withdraw: bool = False) -> None:
        limit = mean * coef
        for vid, amount in vids.sort_values(ascending=False).items():
            peers = other.get(str(vid), [])
            detail = f"对手方 {'、'.join(peers) or '—'}"
            if withdraw:
                detail = f"钱来自{'、'.join(peers) or '—'}"
            out.append(lead_mod.lead(
                layer=LAYER, kind=kind,
                subject=str(vid),
                evidence=(f"{amount:,.2f} 元，摘要「{memo.get(str(vid), '')}」，{detail}；"
                          f"本账{('付现' if kind == '现金支付' else '收现')}均值 {mean:,.2f} 元，"
                          f"门槛是它的 {coef:.0%}（{limit:,.2f} 元）。"),
                vouchers=[str(vid)], count=1, amount=float(amount),
                priority=("高" if amount >= limit > 0 else "低")))

    if len(pay):
        emit(pay, "现金支付", pay_mean)
    if len(recv):
        withdraw = recv[recv.index.map(
            lambda vid: bool(other.get(str(vid))) and
            all(s == BANK for s in other.get(str(vid), [])))]
        plain = recv.drop(index=withdraw.index, errors="ignore")
        if len(withdraw):
            emit(withdraw, "现金提现", recv_mean, withdraw=True)
        if len(plain):
            emit(plain, "现金收款", recv_mean)

    stats = {
        "pay": {"count": int(len(pay)), "amount": round(float(pay.sum()), 2),
                "mean": round(pay_mean, 2), "threshold": round(pay_mean * coef, 2)},
        "recv": {"count": int(len(recv)), "amount": round(float(recv.sum()), 2),
                 "mean": round(recv_mean, 2), "threshold": round(recv_mean * coef, 2)},
    }
    return {"leads": out, "stats": stats}
