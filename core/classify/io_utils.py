# -*- coding: utf-8 -*-
"""读表 / 写表 —— **兼容层**。

真正的实现在 `journal_io`（仓库根目录）。这里只保留旧函数名，
让 `summary_cleaner` 内部的调用方（service / ui / subjects）不用一次性改完。

新代码请直接用：

    from journal_io import read, write_table, serialize_bytes
    j = read("序时账.xlsx", requires=("voucher_id", "subject_l1", "debit", "credit"))
    j.df        # 带类型的原表
    j.raw       # 全字符串保真视图（凭证号 0001 不丢零）

历史理由（保留在这里，免得后人再踩）：读 Excel 用 calamine（Rust），
64,255 行实测比 openpyxl 快 4 倍；csv 国内财务软件常导 GBK，所以有编码回退。
这两条现在都写在 `journal_io/readers.py`。
"""

from __future__ import annotations

from typing import Any, Tuple

import pandas as pd

from journal_io import readers as _readers
from journal_io import writers as _writers

# ── 历史常量（值来自 journal_io，别在这里改）──
EXCEL_ROW_LIMIT = _writers.XLSX_MAX_ROWS
EXCEL_SUFFIXES = _readers.EXCEL_SUFFIXES
CSV_SUFFIXES = _readers.CSV_SUFFIXES
CSV_ENCODINGS = _readers.CSV_ENCODINGS
MIME_XLSX = _writers.MIME_XLSX
MIME_CSV = _writers.MIME_CSV

Source = _readers.Source


def read_table(source: Source, filename: str = "", **kwargs: Any) -> pd.DataFrame:
    """读成 DataFrame（旧接口，只要表不要 Journal）。"""
    df, _engine = _readers.read_frame(source, filename=filename, **kwargs)
    return df


def serialize_table(df: pd.DataFrame, filename: str,
                    sheet_name: str = "Sheet1"
                    ) -> Tuple[bytes, str, str, str]:
    """序列化成可下载字节流（旧接口，4 元组）。"""
    data, name, mime, fmt, _hint = _writers.serialize_bytes(df, filename, sheet_name)
    return data, name, mime, fmt


def too_large_hint(rows: int) -> str:
    return _writers.too_large_hint(rows)
