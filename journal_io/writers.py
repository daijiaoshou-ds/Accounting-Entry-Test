# -*- coding: utf-8 -*-
"""写表：**唯一**决定"落成什么格式"的地方。

以前四处各写一遍（`io_utils.serialize_table`、`cashflow_writer/rows.write_sheets`、
`contra_analyzer` 输出、`entry_test` 输出），阈值三套。

现在按**用途**分三档（`target`），一件事一个默认：

| target | 用途 | 默认 | 理由 |
|---|---|---|---|
| `download` | 用户点"导出" | ≤5 万行 xlsx，以上 csv | xlsx 约 22 万单元格/秒：6.4 万行×39 列要 34s；换 csv 2s |
| `archive` | job 内部落盘 | parquet | 小 15 倍、读写快 2 倍、保前导零 |
| `preview` | 前端表格 | 不落盘，分页 JSON | 前端不会要 80 万行 |

阈值只有一个常量，环境变量 `JOURNAL_XLSX_MAX_ROWS` 可覆盖。
超预算时**照样出文件**，但在 `WriteResult.hint` 里说清楚"预计 Ns，建议 CSV"，
由前端决定要不要提示用户 —— 不替用户做主。
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# xlsx 超过这个行数就改 csv（Excel 单表上限 1,048,576，留一半余量）
XLSX_MAX_ROWS = int(os.getenv("JOURNAL_XLSX_MAX_ROWS", "50000"))
# 实测 xlsxwriter 约 22 万单元格/秒（1,440 万单元格 64.8s）
CELLS_PER_SEC = 220_000
# 超过这个预计耗时就在 hint 里建议换格式
BUDGET_SECONDS = 10.0

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_CSV = "text/csv"

TARGETS = ("download", "archive", "preview")


@dataclass
class WriteResult:
    paths: Dict[str, str] = field(default_factory=dict)   # {sheet/名: 实际路径}
    fmt: str = ""                                          # xlsx / csv / parquet
    seconds: float = 0.0
    hint: str = ""
    sheet_rows: Dict[str, int] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.paths)


# ============================================================================
# 估算
# ============================================================================

def estimate_xlsx_seconds(rows: int, cols: int) -> float:
    return round(rows * cols / CELLS_PER_SEC, 1)


def will_be_slow(df: pd.DataFrame) -> bool:
    return estimate_xlsx_seconds(len(df), len(df.columns)) > BUDGET_SECONDS


def too_large_hint(rows: int) -> str:
    """超过阈值时给用户的提示（未超过返回空串）。"""
    if rows <= XLSX_MAX_ROWS:
        return ""
    return (f"本次结果 {rows:,} 行，超过 {XLSX_MAX_ROWS:,} 行阈值，已导出 CSV。"
            f"Excel 单表上限 1,048,576 行，用 Excel 打开 CSV 后请勿直接「另存为 xlsx」，"
            f"否则会丢行。")


def _slow_hint(df: pd.DataFrame) -> str:
    if not will_be_slow(df):
        return ""
    return (f"这份表 {len(df):,} 行 × {len(df.columns)} 列，写 xlsx 预计 "
            f"{estimate_xlsx_seconds(len(df), len(df.columns)):.0f}s；"
            f"换成 CSV 通常 2-5s。")


# ============================================================================
# 写
# ============================================================================

def _write_xlsx(sheets: Dict[str, pd.DataFrame], path: Path,
                freeze: bool = True) -> None:
    """多 sheet 写一个 xlsx。xlsxwriter 优先（快约 1.6×），缺装回退 openpyxl。"""
    try:
        with pd.ExcelWriter(path, engine="xlsxwriter") as w:
            for name, frame in sheets.items():
                frame.to_excel(w, index=False, sheet_name=name[:31])
                if freeze:
                    w.sheets[name[:31]].freeze_panes(1, 0)
    except (ImportError, ModuleNotFoundError):
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            for name, frame in sheets.items():
                frame.to_excel(w, index=False, sheet_name=name[:31])


def write_table(df: pd.DataFrame, path: str | Path, *,
                target: str = "download", sheet_name: str = "Sheet1") -> WriteResult:
    """单表落盘。"""
    return write_sheets({sheet_name: df}, path, target=target)


def write_sheets(sheets: Dict[str, pd.DataFrame], path: str | Path, *,
                 target: str = "download") -> WriteResult:
    """多表落盘。target 决定格式：

    - `archive`：每张表一个 parquet（`<stem>_<sheet>.parquet`）
    - `download`：全部塞一个 xlsx；任一张超过 `XLSX_MAX_ROWS` 就整组改 csv
    - `preview`：不落盘（前端要分页 JSON，用 `to_records`）
    """
    if target not in TARGETS:
        raise ValueError(f"target 只能是 {TARGETS}，收到 {target!r}")
    if target == "preview":
        raise ValueError("preview 不落盘：前端请用 journal_io.to_records(df) 取分页数据")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    biggest = max((len(v) for v in sheets.values()), default=0)
    rows_by_sheet = {k: int(len(v)) for k, v in sheets.items()}
    t0 = time.perf_counter()

    if target == "archive":
        out: Dict[str, str] = {}
        single = len(sheets) == 1
        for name, frame in sheets.items():
            # 单张表就落到你给的那个路径（`raw.parquet` 不该变成 `raw_raw.parquet`）
            p = (path.with_suffix(".parquet") if single
                 else path.with_name(f"{path.stem}_{name}.parquet"))
            frame.to_parquet(p, index=False)
            out[name] = str(p)
        return WriteResult(paths=out, fmt="parquet",
                           seconds=round(time.perf_counter() - t0, 2),
                           sheet_rows=rows_by_sheet)

    if biggest > XLSX_MAX_ROWS:
        out = {}
        for name, frame in sheets.items():
            p = path.with_name(f"{path.stem}_{name}.csv")
            frame.to_csv(p, index=False, encoding="utf-8-sig")
            out[name] = str(p)
        return WriteResult(paths=out, fmt="csv",
                           seconds=round(time.perf_counter() - t0, 2),
                           hint=too_large_hint(biggest),
                           sheet_rows=rows_by_sheet)

    _write_xlsx(sheets, path)
    hint = ""
    for frame in sheets.values():
        hint = _slow_hint(frame)
        if hint:
            break
    return WriteResult(paths={k: str(path) for k in sheets}, fmt="xlsx",
                       seconds=round(time.perf_counter() - t0, 2), hint=hint,
                       sheet_rows=rows_by_sheet)


def serialize_bytes(df: pd.DataFrame, filename: str,
                    sheet_name: str = "Sheet1") -> Tuple[bytes, str, str, str, str]:
    """序列化成可下载字节流（HTTP 导出用）。

    Returns: (bytes, 最终文件名, MIME, 格式 "xlsx"/"csv", 提示文案)
    """
    stem = Path(str(filename)).stem or "结果"
    rows = len(df)

    if rows > XLSX_MAX_ROWS:
        buf = io.BytesIO()
        df.to_csv(buf, index=False, encoding="utf-8-sig")
        return (buf.getvalue(), f"{stem}.csv", MIME_CSV, "csv", too_large_hint(rows))

    buf = io.BytesIO()
    _write_xlsx({sheet_name: df}, buf)          # xlsxwriter/openpyxl 都接受 BytesIO
    return (buf.getvalue(), f"{stem}.xlsx", MIME_XLSX, "xlsx", _slow_hint(df))


# ============================================================================
# 预览
# ============================================================================

def to_records(df: pd.DataFrame, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """分页/预览用：NaN → None，日期 → iso 字符串。"""
    view = df if limit is None else df.head(int(limit))
    view = view.where(pd.notna(view), None)
    import json
    return json.loads(view.to_json(orient="records", force_ascii=False,
                                   date_format="iso"))
