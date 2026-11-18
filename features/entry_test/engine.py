# -*- coding: utf-8 -*-
"""会计分录测试 2.0 —— 数据视图。

2.0 的每一块（完整性 / 画像 / 后面的各项检验）都从同一张行表取数，口径只在
这里定一次：凭证号、期间、科目、借贷、摘要、制单人。

为什么不各层各取：

- 借方/贷方里有**负数**（红字冲销，`case/序时账1.xlsx` 里 1,850 行）。`abs()`
  和"直接求和"是两个不同的金额：两层各写一遍，就会出现"画像里这张凭证 30 万、
  线索里 0 元"这种事，而且没人会怀疑到口径上。
- 期间有的账只有「月份」列（1、2…），有的只有日期。**日期优先**推导 `YYYY-MM`：
  直接拿月份列，2024-12 和 2025-01 会并成同一个「12 月」和「01 月」。
- 一张凭证 60 行时，"这张凭证多大"按 `max(|借方合计|, |贷方合计|)` 算：两者
  正常相等，红字凭证两边同负，取绝对值最大的那一侧才是实质金额。
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

# 字段契约就在这里（`Feature.requires` 读它）：
# 完整性只要「凭证号 + 日期 + 一借一贷」就能跑；画像还想要制单人（缺了不影响）。
REQUIRES = ("voucher_id", "date", "subject_l1", "debit", "credit")


def view(journal) -> pd.DataFrame:
    """行表。列固定，下游不再碰 `journal` 的原始列名。"""
    dates = journal.dates("date")
    raw_period = journal.keys("period")
    period = dates.dt.strftime("%Y-%m").where(dates.notna(), raw_period)

    frame = pd.DataFrame({
        "vid": journal.keys("voucher_id"),
        "date": dates,
        "period": period,
        "subject": journal.keys("subject_l1"),
        "subject_l2": journal.keys("subject_l2"),
        "subject_code": journal.keys("subject_code"),
        "debit": journal.money("debit"),
        "credit": journal.money("credit"),
        "summary": journal.keys("summary"),
        "maker": journal.keys("maker"),
    })
    # 行发生额（带符号）：红字行是负数，两层都用这一个口径
    frame["amount"] = frame["debit"] + frame["credit"]
    return frame


def _first_text(series: pd.Series) -> str:
    """第一个非空值：凭证的摘要/制单人取第一条有内容的行。"""
    for value in series:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_date(series: pd.Series):
    valid = series.dropna()
    return valid.iloc[0] if len(valid) else pd.NaT


def vouchers(frame: pd.DataFrame) -> pd.DataFrame:
    """逐凭证：行数、借贷合计、金额、期间、摘要、制单人、涉及科目。

    列名用中文，因为这张表会原样进导出物；内部逻辑也别再造一份英文名。
    """
    if frame.empty:
        return pd.DataFrame(columns=["vid", "凭证号", "期间", "日期", "摘要", "制单人",
                                     "行数", "借方合计", "贷方合计", "金额", "科目"])

    grouped = frame.groupby("vid", sort=False)
    out = pd.DataFrame({
        "行数": grouped.size(),
        "借方合计": grouped["debit"].sum().round(2),
        "贷方合计": grouped["credit"].sum().round(2),
        "期间": grouped["period"].agg(_first_text),
        "日期": grouped["date"].agg(_first_date),
        "摘要": grouped["summary"].agg(_first_text),
        "制单人": grouped["maker"].agg(_first_text),
        "科目": grouped["subject"].agg(lambda s: [x for x in pd.unique(s) if x]),
    }).reset_index()
    out["凭证号"] = out["vid"]
    out["金额"] = np.maximum(out["借方合计"].abs(), out["贷方合计"].abs()).round(2)
    return out


def amount_of(debit: Any, credit: Any) -> float:
    """单行金额（带符号）：借方优先，都给就是净额。"""
    return float(debit or 0.0) + float(credit or 0.0)


def subjects_of(frame: pd.DataFrame) -> Dict[str, List[str]]:
    """{凭证号: [一级科目…]}（去重保序，空的丢掉）。"""
    out: Dict[str, List[str]] = {}
    for vid, group in frame.groupby("vid", sort=False):
        seen: List[str] = []
        for name in group["subject"]:
            text = str(name or "").strip()
            if text and text not in seen:
                seen.append(text)
        out[str(vid)] = seen
    return out
