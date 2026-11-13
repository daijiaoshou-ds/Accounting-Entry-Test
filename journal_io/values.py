# -*- coding: utf-8 -*-
"""单元格取值的安全函数。

为什么需要：`ctx.save()` 落 parquet 时，字符串列统一成 pandas 的 `string`
（可空类型，见 `writers.parquet_safe`）。读回来空单元格就是 `pd.NA`，而
`pd.NA` 在布尔上下文里直接抛

    TypeError: boolean value of NA is ambiguous

所以 `str(row["科目"] or "")` 这种写法在真账上（有空白科目、半截表头）会当场
把整条复核打断——而且断在 AI 阶段，任务永远停在「正在跑」。取单元格一律走这里：
缺失给默认值，不做隐式真假判断。
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _missing(value: Any) -> bool:
    """None / NaN / pd.NA / NaT 都算缺失；字符串和数组不算。"""
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    if isinstance(value, (str, bytes, list, tuple, dict, set)):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def text(value: Any, default: str = "") -> str:
    """单元格 → 字符串（去掉首尾空白）。空/缺失 → `default`。"""
    if _missing(value):
        return default
    out = str(value).strip()
    return out or default


def amount(value: Any, default: float = 0.0) -> float:
    """单元格 → 金额。千分位、全角逗号、空串、NaN 都吃（真账里全有）。"""
    if _missing(value):
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        out = float(str(value).replace(",", "").replace("，", "").strip() or 0)
    except (TypeError, ValueError):
        return default
    return default if out != out else out          # NaN → 默认（bool(nan) 是真）


__all__ = ["text", "amount"]
