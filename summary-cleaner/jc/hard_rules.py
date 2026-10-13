# -*- coding: utf-8 -*-
"""硬规则 —— 程序唯一保留的"写死"判断，只有两条。

为什么这两条必须留在程序里（而不是交给模型）：

  技术报告明确规定「硬规则桶绝不进入训练数据」——因为这类凭证是
  **机械性的**，没有业务语义，学它只会污染模型分布。
  于是模型**没有能力输出**这两个桶。若把这两条也砍掉，
  纯资金划转和结汇凭证会被硬塞进某个业务桶（多半是"日常费用"），是错的。

  所以：模型负责 18 个业务桶，程序负责这 2 个机械桶。

判定条件（与原 2.x 完全一致，未做改动）：

  ① 资金内部往来：一张凭证的**全部**一级科目都属于货币资金
     —— 纯资金划转（银行转银行、取现、内部调拨），无业务含义。

  ② 汇兑损益：含财务费用 + 无其他损益类科目 + 无黑名单科目 + 摘要含汇兑词
     —— 四个条件同时满足才算，避免"代付货款"被误判。
"""

from __future__ import annotations

from typing import List

from .config import (
    CASH_SUBJECTS, HARD_FX_BLACKLIST, HARD_FX_KEYWORDS, HARD_FX_OTHER_PL,
    HARD_FX_SUBJECT,
)


def is_internal_transfer(subjects_raw: List[str]) -> bool:
    """① 资金内部往来：全部一级科目都是货币资金。

    subjects_raw 用**裸科目名**（不带 [借]/[贷]）。
    空科目列表不算（无法判断，交给模型）。
    """
    if not subjects_raw:
        return False
    return set(subjects_raw).issubset(CASH_SUBJECTS)


def is_fx_gain_loss(subjects_raw: List[str], summary: str) -> bool:
    """② 汇兑损益：四个条件同时满足。

    R1 必须含财务费用
    R2 不能有其他损益类科目
    R3 不能有黑名单科目（应付职工薪酬 / 应交税费 / 应付票据 等）
    R4 摘要必须含汇兑/结汇/收汇之一
    """
    subjects = set(subjects_raw)
    if HARD_FX_SUBJECT not in subjects:
        return False
    if subjects & HARD_FX_OTHER_PL:
        return False
    if subjects & HARD_FX_BLACKLIST:
        return False
    text = summary or ""
    return any(kw in text for kw in HARD_FX_KEYWORDS)


def preassign(items: List[dict]) -> dict:
    """对整批凭证跑硬规则，返回 {凭证号: 桶名}（只含命中的）。

    优先级：资金内部往来 → 汇兑损益（两条互斥，实际不会同时命中：
    资金内部往来要求全是货币资金，而汇兑损益要求含财务费用）。
    """
    out = {}
    for it in items:
        vid = it["vid"]
        raw = it.get("subjects_raw") or []
        summary = it.get("summary", "")
        if is_internal_transfer(raw):
            out[vid] = "资金内部往来"
        elif is_fx_gain_loss(raw, summary):
            out[vid] = "汇兑损益"
    return out
