# -*- coding: utf-8 -*-
"""统一的数据流入口：读表 / 写表。

**读**
- Excel（.xlsx/.xls/.xlsm/.xlsb/.ods）→ pandas + **calamine** 引擎（Rust 实现）
  实测 64,255 行：openpyxl 10.3s → calamine 2.5s（**4.1×**），且 dtype 与 openpyxl 完全一致。
  calamine 缺失或解析失败时自动回退 openpyxl（.xls 除外——openpyxl 本来就不支持）。
  顺带修掉一个既有 bug：旧代码对所有非 csv 文件都用 openpyxl，**.xls 直接读不了**。
- CSV/TXT → 编码回退 `utf-8-sig → utf-8 → gb18030`（国内财务软件导出的 csv 常是 GBK，
  旧代码直接用默认编码，遇到中文就抛 UnicodeDecodeError）。

**写**
- ≤ 50 万行 → xlsx（xlsxwriter，比 openpyxl 快约 1.6×）
- > 50 万行 → CSV（utf-8-sig，Excel 双击不乱码）
- Excel 单表硬上限 1,048,576 行，50 万阈值留 4 倍余量，
  避免用户二次加工（加列、透视）后再溢出。

**为什么不做「上传即转 Parquet」**
转换一次 = 读 33s + 写 7s ≈ 40s，而 ui.py 已用「内容哈希 + session_state」保证
一份文件一次会话只读一遍，转换是纯支出。等有了后台任务 / 断点续跑再启用。
（内部落盘用 Parquet 优于 CSV：小 15 倍、读写快 2×、保留科目编码前导零。）
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, BinaryIO, Tuple, Union

import pandas as pd

# Excel 单表行数上限 1,048,576；阈值取 50 万，留 4 倍余量
EXCEL_ROW_LIMIT = 500_000

EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods"}
CSV_SUFFIXES = {".csv", ".txt", ".tsv"}
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_CSV = "text/csv"

Source = Union[str, Path, bytes, BinaryIO]


def _suffix_of(source: Source, filename: str = "") -> str:
    """推断文件后缀（优先 source 自带的 name）。"""
    name = filename or getattr(source, "name", "") or ""
    if not name and isinstance(source, (str, Path)):
        name = str(source)
    return Path(str(name)).suffix.lower()


def _read_csv(source: Source) -> pd.DataFrame:
    """按编码回退读 csv；分隔符也让 pandas 自己嗅探。"""
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:  # 文件对象：读出字节后再试，避免 stream 被第一个编码吃掉
        pos = getattr(source, "tell", None)
        if callable(pos):
            try:
                source.seek(0)
            except Exception:
                pass
        raw = source.read()

    last_err: Exception | None = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc, sep=None, engine="python")
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except Exception:
            # sep=None 的 python 引擎对超大文件偏慢，退回默认分隔符再试一次
            try:
                return pd.read_csv(io.BytesIO(raw), encoding=enc)
            except UnicodeDecodeError as e:
                last_err = e
                continue
    raise ValueError(f"无法识别 CSV 编码（已尝试 {', '.join(CSV_ENCODINGS)}）：{last_err}")


def read_table(source: Source, filename: str = "", **kwargs: Any) -> pd.DataFrame:
    """读取用户上传的表格，返回 DataFrame。

    Args:
        source: 路径 / bytes / 文件对象（Streamlit 的 UploadedFile 都可以）
        filename: source 没有 .name 时用来判断后缀
        **kwargs: 透传给 pd.read_excel（如 sheet_name）

    Returns:
        DataFrame；读不出来时抛 ValueError，消息可直接展示给用户。
    """
    suffix = _suffix_of(source, filename)

    if suffix in CSV_SUFFIXES:
        return _read_csv(source)

    # 未知后缀（用户可能改了名）：先按 csv 试，失败再按 excel
    if suffix and suffix not in EXCEL_SUFFIXES:
        try:
            return _read_csv(source)
        except Exception:
            pass

    if isinstance(source, bytes):
        source = io.BytesIO(source)

    if suffix == ".xls":
        # openpyxl 不支持 .xls，只能靠 calamine；缺失时明确报错而不是抛一个看不懂的异常
        try:
            return pd.read_excel(source, engine="calamine", **kwargs)
        except ImportError as e:
            raise ValueError(
                "读取 .xls 需要 python-calamine："
                "pip install python-calamine -i https://mirrors.aliyun.com/pypi/simple/"
            ) from e

    # 首选 calamine（Rust）：快 4 倍且 dtype 与 openpyxl 一致
    try:
        return pd.read_excel(source, engine="calamine", **kwargs)
    except Exception as fast_err:
        # 回退 openpyxl：calamine 对某些畸形文件（合并单元格、加密、超链接表）更挑剔
        if isinstance(source, (io.BytesIO, io.BufferedReader)) or hasattr(source, "seek"):
            try:
                source.seek(0)
            except Exception:
                pass
        try:
            return pd.read_excel(source, engine="openpyxl", **kwargs)
        except Exception as slow_err:
            raise ValueError(
                f"读取失败。calamine: {type(fast_err).__name__} {fast_err}；"
                f"openpyxl: {type(slow_err).__name__} {slow_err}"
            ) from slow_err


def serialize_table(
    df: pd.DataFrame,
    filename: str,
    sheet_name: str = "Sheet1",
) -> Tuple[bytes, str, str, str]:
    """把 DataFrame 序列化成可下载的字节流，自动按行数选格式。

    Args:
        df: 待导出的表
        filename: 期望的文件名（后缀会被按实际格式替换）
        sheet_name: xlsx 的工作表名

    Returns:
        (bytes, 最终文件名, MIME, 格式标识 "xlsx"/"csv")
    """
    stem = Path(str(filename)).stem or "结果"
    rows = len(df)

    if rows > EXCEL_ROW_LIMIT:
        buf = io.BytesIO()
        df.to_csv(buf, index=False, encoding="utf-8-sig")
        return buf.getvalue(), f"{stem}.csv", MIME_CSV, "csv"

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]
        ws.freeze_panes(1, 0)
        if len(df.columns):
            ws.autofilter(0, 0, rows, len(df.columns) - 1)
    return buf.getvalue(), f"{stem}.xlsx", MIME_XLSX, "xlsx"


def too_large_hint(rows: int) -> str:
    """超过阈值时给用户的提示文案（未超过返回空串）。"""
    if rows <= EXCEL_ROW_LIMIT:
        return ""
    return (
        f"本次结果 {rows:,} 行，超过 {EXCEL_ROW_LIMIT:,} 行阈值，已导出 CSV。"
        f"Excel 单表上限 1,048,576 行，用 Excel 打开 CSV 后请勿直接「另存为 xlsx」，"
        f"否则会丢行。"
    )
