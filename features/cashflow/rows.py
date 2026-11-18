# -*- coding: utf-8 -*-
"""行级操作：挑出货币资金行、把现金流结论写回去。

只做**与现金流有关**的行级活；分类结论由上游 `summary_cleaner` 写在凭证级表上，
这里按凭证号把它铺到每一行。

三个刻意的设计：

1. **行号是位置号**（0-based 的 `range(len(df))`），不是 DataFrame 索引。
   读表引擎（calamine / openpyxl / csv）给的索引各不相同，中间做一次筛选就错位；
   用位置号才能保证「补丁里的行号 → Excel 里的第几行」永远对得上。
2. **只有货币资金那一行才有现金流量项目**——会计惯例；非货币资金行留空，
   但它所在的整张凭证会出现在「货币资金凭证」表里（编现金流看的正是对方在干什么）。
3. **这里不落盘**：写盘统一走 `journal_io.write_sheets`（格式策略只有一处）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from accounting_domain import MONEY_FUNDS

from .judge import CF_NONE, MoneyRow


def to_number(value: Any) -> float:
    """把单元格变成数字。

    必须处理："100,000.00"（千分位）、NaN、None、""、"-"。
    NaN 是 truthy，`value or 0` 这类写法**无效**，必须显式判 isna。
    """
    if value is None:
        return 0.0
    if isinstance(value, float) and pd.isna(value):
        return 0.0
    text = str(value).replace(",", "").replace("，", "").strip()
    if text in ("", "-", "—", "nan", "None"):
        return 0.0
    num = pd.to_numeric(text, errors="coerce")
    return 0.0 if pd.isna(num) else float(num)


def _amounts(series: pd.Series) -> np.ndarray:
    """整列转数字（向量化）：87 万行逐格调 to_number 要 2 秒以上。"""
    text = (series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("，", "", regex=False)
            .str.strip())
    return pd.to_numeric(text, errors="coerce").fillna(0.0).to_numpy(dtype=float)


def build_money_rows(df: pd.DataFrame, mapping: Dict[str, str]
                     ) -> Tuple[Dict[str, List[MoneyRow]], Dict[str, bool]]:
    """按凭证分组，抽出每行 (科目, 借, 贷, 行号)，并标记哪些凭证含货币资金。

    Returns:
        (money_by_vid, has_money)
        money_by_vid: {凭证号: [MoneyRow, ...]} —— **只含现金流相关凭证**
        has_money:    {凭证号: bool} —— 全部凭证的标记，逐行写回用

    向量化标记 + 只对命中的行建对象：87 万行账约 0.6s。
    """
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]

    vids = df[v_col].astype(str).to_numpy()
    subjects = (df[s_col].where(df[s_col].notna(), "")
                .astype(str).str.strip().to_numpy())
    debit = _amounts(df[d_col]) if d_col in df.columns else np.zeros(len(df))
    credit = _amounts(df[c_col]) if c_col in df.columns else np.zeros(len(df))

    # 全部凭证「是否含货币资金」—— 用 groupby().any() 一次算完，
    # 再用 map 铺回每一行。不要用 np.isin(vids, wanted)：两个 object 数组做
    # 包含判断在 87 万行 × 14 万凭证上要跑几分钟（实测卡死过）。
    v_series = pd.Series(vids, dtype="object")
    has = pd.Series(pd.Series(subjects).isin(MONEY_FUNDS).to_numpy()
                    ).groupby(v_series, sort=False).any()
    has_money = {str(k): bool(v) for k, v in has.items()}
    money_mask = v_series.map(has_money).fillna(False).to_numpy(dtype=bool)

    wanted = {v for v, hit in has_money.items() if hit}
    money_by_vid: Dict[str, List[MoneyRow]] = {v: [] for v in wanted}
    for i in np.flatnonzero(money_mask):
        money_by_vid[vids[i]].append(MoneyRow(
            subject=subjects[i],
            debit=float(debit[i]),
            credit=float(credit[i]),
            row_index=int(i),
        ))
    return money_by_vid, has_money


def write_cashflow(df: pd.DataFrame, mapping: Dict[str, str],
                   money_flags: Dict[str, bool],
                   cf_by_row: Dict[int, dict]) -> pd.DataFrame:
    """把现金流量项目写回序时账（逐行）。"""
    v_col = mapping["voucher_no"]
    out = df.copy()
    n = len(out)
    out["是否现金流相关凭证"] = (
        out[v_col].astype(str).map(money_flags)
        .map({True: "是", False: ""}).fillna(""))
    out["现金流量项目"] = [cf_by_row.get(i, {}).get("cf_item", "") for i in range(n)]
    out["现金流方向"] = [cf_by_row[i].get("direction", CF_NONE)
                         if i in cf_by_row else "" for i in range(n)]
    out["现金流判定"] = [cf_by_row.get(i, {}).get("source", "") for i in range(n)]
    out["现金流复核"] = ["需复核" if cf_by_row.get(i, {}).get("needs_review") else ""
                         for i in range(n)]
    return out
