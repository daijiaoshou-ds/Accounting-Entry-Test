# -*- coding: utf-8 -*-
"""读表：**唯一**知道"用什么引擎打开文件"的地方。

以前三套读法各写一遍：`pd.read_excel`、`pd.read_excel(dtype=str)`、polars/fastexcel。
引擎选择其实是纯工程细节，跟业务无关，放在这里之后功能层只会拿到 DataFrame。

| 后缀 | 引擎 | 为什么 |
|---|---|---|
| .xlsx/.xlsm/.xlsb/.ods | **calamine**（Rust） | 实测 64,255 行 openpyxl 10.3s → calamine 2.5s |
| .xls | calamine | openpyxl 根本不支持 .xls |
| .csv/.txt/.tsv | pandas + 编码回退 | 国内财务软件常导 GBK |
| .parquet | pyarrow | job 内部复用，不重新解析原件 |

calamine 缺装或解析失败 → 自动回退 openpyxl；两个都失败才抛错，报错里带两个引擎的原始信息。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, BinaryIO, Tuple, Union

import pandas as pd

Source = Union[str, Path, bytes, BinaryIO]

EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xlsb", ".ods", ".xls"}
CSV_SUFFIXES = {".csv", ".txt", ".tsv"}
CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


def suffix_of(source: Source, filename: str = "") -> str:
    name = filename or getattr(source, "name", "") or ""
    if not name and isinstance(source, (str, Path)):
        name = str(source)
    return Path(str(name)).suffix.lower()


def _read_csv(source: Source) -> pd.DataFrame:
    """按编码回退读 csv；分隔符让 pandas 自己嗅探。

    **一律 `dtype=str`**：csv 里没有类型信息，让 pandas 推断的代价是凭证号
    `0001` 变成 `1`、日期变成 `2025/1/20` 这种不可逆的变形。要算数字走
    `Journal.money()`（它自己去千分位转 float），要日期走 `Journal.dates()`。
    """
    if isinstance(source, bytes):
        raw = source
    elif isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        pos = getattr(source, "seek", None)     # 同一 stream 不能被第一个编码吃掉
        if callable(pos):
            try:
                source.seek(0)
            except Exception:
                pass
        raw = source.read()

    last: Exception | None = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc, sep=None,
                               engine="python", dtype=str)
        except UnicodeDecodeError as e:
            last = e
            continue
        except Exception:
            try:                     # sep=None 在大文件上偏慢，退回默认分隔符
                return pd.read_csv(io.BytesIO(raw), encoding=enc, dtype=str)
            except UnicodeDecodeError as e:
                last = e
                continue
    raise ValueError(f"无法识别 CSV 编码（已试 {', '.join(CSV_ENCODINGS)}）：{last}")


def read_frame(source: Source, filename: str = "",
               **kwargs: Any) -> Tuple[pd.DataFrame, str]:
    """读成 DataFrame，返回 (df, 实际用的引擎名)。

    Args:
        source: 路径 / bytes / 文件对象（Streamlit、FastAPI UploadFile 都行）
        filename: source 没有 .name 时用来判断后缀
        **kwargs: 透传给 pd.read_excel（如 sheet_name）
    """
    suffix = suffix_of(source, filename)

    if suffix == ".parquet":
        return pd.read_parquet(source), "parquet"

    if suffix in CSV_SUFFIXES:
        return _read_csv(source), "csv"

    if suffix and suffix not in EXCEL_SUFFIXES:
        # 后缀被改过：先按 csv 试，失败再按 excel
        try:
            return _read_csv(source), "csv"
        except Exception:
            pass

    if isinstance(source, bytes):
        source = io.BytesIO(source)

    if suffix == ".xls":
        try:
            return pd.read_excel(source, engine="calamine", **kwargs), "calamine"
        except ImportError as e:
            raise ValueError(
                "读取 .xls 需要 python-calamine："
                "pip install python-calamine -i https://mirrors.aliyun.com/pypi/simple/"
            ) from e

    try:
        return pd.read_excel(source, engine="calamine", **kwargs), "calamine"
    except Exception as fast_err:
        # 回退 openpyxl：calamine 对合并单元格/加密/超链接表更挑剔
        if isinstance(source, (io.BytesIO, io.BufferedReader)) or hasattr(source, "seek"):
            try:
                source.seek(0)
            except Exception:
                pass
        try:
            return pd.read_excel(source, engine="openpyxl", **kwargs), "openpyxl"
        except Exception as slow_err:
            raise ValueError(
                f"读取失败。calamine: {type(fast_err).__name__} {fast_err}；"
                f"openpyxl: {type(slow_err).__name__} {slow_err}"
            ) from slow_err
