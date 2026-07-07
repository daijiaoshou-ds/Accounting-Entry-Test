#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
序时账预处理脚本

从原始序时账中提取凭证号和摘要，生成唯一凭证号并去重，
输出标准格式文件，直接作为训练脚本的输入。

处理流程：
  序时账 → 自动识别列 → 生成唯一凭证号 → 去重 → 输出标准格式
"""

import argparse
import io
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

import pandas as pd

# 确保控制台输出使用 UTF-8 编码
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 列名自动识别
# ---------------------------------------------------------------------------

# 可能的列名映射（按优先级排列）
DATE_CANDIDATES = [
    "记账日期", "会计日期", "日期", "凭证日期", "业务日期",
    "date", "日期 ", "记账日", "账务日期", "交易日期",
]

VOUCHER_CANDIDATES = [
    "凭证号", "凭证字号", "凭证编号", "凭证字", "传票号",
    "voucher_no", "凭证号 ", "凭证", "凭证号数",
]

SUMMARY_CANDIDATES = [
    "摘要", "交易摘要", "业务摘要", "说明", "备注",
    "summary", "description", "摘要内容", "交易说明",
]

YEAR_CANDIDATES = ["年", "年度", "year"]
MONTH_CANDIDATES = ["月", "月份", "month"]


def find_column(df: pd.DataFrame, candidates: list) -> str | None:
    """在 DataFrame 的列中查找匹配的列名。"""
    # 精确匹配优先
    for col in df.columns:
        col_str = str(col).strip()
        for candidate in candidates:
            if col_str == candidate:
                return col
    # 模糊匹配（包含关系）
    for col in df.columns:
        col_str = str(col).strip()
        for candidate in candidates:
            if candidate in col_str:
                return col
    return None


# ---------------------------------------------------------------------------
# 唯一凭证号生成
# ---------------------------------------------------------------------------

def parse_date(val) -> datetime | None:
    """尝试解析各种日期格式。"""
    if pd.isna(val):
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        # Excel 序列号日期
        try:
            from datetime import timedelta
            base = datetime(1899, 12, 30)
            return base + timedelta(days=int(val))
        except Exception:
            return None

    val_str = str(val).strip()
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
        "%Y年%m月%d日", "%y-%m-%d", "%y/%m/%d",
        "%d-%m-%Y", "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(val_str, fmt)
        except ValueError:
            continue

    # 最后尝试：负年或无分隔符的短格式
    match = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", val_str)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    return None


def build_unique_voucher_id(df: pd.DataFrame, date_col: str | None,
                             voucher_col: str, year_col: str | None,
                             month_col: str | None,
                             source_prefix: str = "") -> pd.Series:
    """为每一行生成全局唯一凭证号：来源前缀_年+月+凭证号。

    优先级：
    1. 有记账日期列 → 从日期提取年月
    2. 有独立的年/月列 → 直接使用
    3. 都没有 → 只用凭证号本身

    source_prefix 用于跨文件区分，如 "公司A" → "公司A_202401_001"
    """
    ids = []

    for idx, row in df.iterrows():
        year_part = None
        month_part = None

        # 尝试从日期列提取
        if date_col and date_col in df.columns:
            dt = parse_date(row[date_col])
            if dt:
                year_part = str(dt.year)
                month_part = f"{dt.month:02d}"

        # 回退到独立的年/月列
        if year_part is None and year_col and year_col in df.columns:
            y = row[year_col]
            if not pd.isna(y):
                year_part = str(int(y))

        if month_part is None and month_col and month_col in df.columns:
            m = row[month_col]
            if not pd.isna(m):
                month_part = f"{int(m):02d}"

        # 获取凭证号
        voucher = row[voucher_col]
        if pd.isna(voucher):
            voucher_str = f"未知_{idx}"
        else:
            voucher_str = str(int(voucher)) if isinstance(voucher, (int, float)) and float(voucher).is_integer() else str(voucher)

        # 组合：来源前缀 + 年月 + 凭证号
        if year_part and month_part:
            unique_id = f"{year_part}{month_part}_{voucher_str}"
        elif year_part:
            unique_id = f"{year_part}_{voucher_str}"
        else:
            unique_id = voucher_str

        if source_prefix:
            unique_id = f"{source_prefix}_{unique_id}"

        ids.append(unique_id)

    return pd.Series(ids, index=df.index)


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

def deduplicate(df: pd.DataFrame, id_col: str, summary_col: str) -> pd.DataFrame:
    """按 (唯一凭证号, 摘要) 去重。

    同一个凭证号下，如果多行摘要相同，只保留一条。
    同一个凭证号下，摘要不同的行各自保留。
    """
    before = len(df)

    # 去除摘要为空的记录
    df = df.dropna(subset=[summary_col])
    df = df[df[summary_col].astype(str).str.strip() != ""]

    # 按 (唯一凭证号, 摘要) 去重
    df = df.drop_duplicates(subset=[id_col, summary_col], keep="first")

    after = len(df)
    print(f"  去重：{before} 行 → {after} 行（去除 {before - after} 行重复/空值）")

    # 统计同一凭证号下有多条不同摘要的情况
    id_counts = df.groupby(id_col).size()
    multi_summary = (id_counts > 1).sum()
    if multi_summary > 0:
        print(f"  其中 {multi_summary} 个凭证号包含多条不同摘要")

    return df


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def get_source_prefix(input_path: Path, base_dir: Path | None = None) -> str:
    """从文件路径提取来源前缀。

    - 如果 base_dir 是文件夹且文件在子文件夹中（如 journals/公司A/序时账.xlsx）→ 用子文件夹名
    - 否则用文件名（不含扩展名）
    """
    if base_dir and base_dir.is_dir():
        try:
            rel = input_path.relative_to(base_dir)
            if len(rel.parts) > 1:
                return rel.parts[0]
        except ValueError:
            pass

    return input_path.stem


def preprocess_journal_to_df(input_path: Path,
                              source_prefix: str = "") -> pd.DataFrame | None:
    """处理一份序时账文件，返回清洗后的 DataFrame（两列：序号, 摘要）。

    不写文件，用于批量处理时在内存中合并。
    返回 None 表示处理失败。

    source_prefix 用于跨文件唯一性，如不提供则自动从文件路径提取。
    """
    # 1. 读取文件
    suffix = input_path.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(input_path, engine="openpyxl")
    elif suffix == ".csv":
        df = pd.read_csv(input_path)
    else:
        print(f"  错误：不支持的文件格式 —— {suffix}")
        return None

    print(f"已读取：{input_path.name}（{len(df)} 行 × {len(df.columns)} 列）")
    print(f"  列名：{list(df.columns)}")

    # 2. 自动识别列
    date_col = find_column(df, DATE_CANDIDATES)
    voucher_col = find_column(df, VOUCHER_CANDIDATES)
    summary_col = find_column(df, SUMMARY_CANDIDATES)
    year_col = find_column(df, YEAR_CANDIDATES)
    month_col = find_column(df, MONTH_CANDIDATES)

    if voucher_col is None:
        print(f"  错误：未找到凭证号列，跳过该文件。候选名：{VOUCHER_CANDIDATES}")
        return None
    if summary_col is None:
        print(f"  错误：未找到摘要列，跳过该文件。候选名：{SUMMARY_CANDIDATES}")
        return None

    date_source = "无"
    if date_col:
        date_source = f"日期列「{date_col}」"
    elif year_col and month_col:
        date_source = f"年列「{year_col}」+ 月列「{month_col}」"

    # 自动检测来源前缀（如果调用方没传）
    if not source_prefix:
        source_prefix = get_source_prefix(input_path)

    print(f"  识别结果：凭证号=「{voucher_col}」, 摘要=「{summary_col}」, 日期来源={date_source}")

    # 3. 生成唯一凭证号（含来源前缀，确保跨文件全局唯一）
    df["_unique_voucher_id"] = build_unique_voucher_id(
        df, date_col, voucher_col, year_col, month_col, source_prefix
    )

    sample_ids = df["_unique_voucher_id"].drop_duplicates().head(5).tolist()
    print(f"  唯一凭证号样例：{sample_ids}")

    # 4. 去重
    df_clean = deduplicate(df, "_unique_voucher_id", summary_col)

    # 5. 构建输出
    df_out = pd.DataFrame({
        "序号": df_clean["_unique_voucher_id"],
        "摘要": df_clean[summary_col].astype(str).str.strip(),
    })

    # 二次去重
    df_out = df_out.drop_duplicates(subset=["序号", "摘要"])
    print(f"  最终输出：{len(df_out)} 条记录")

    lengths = df_out["摘要"].str.len()
    print(f"  摘要长度：最短={lengths.min()}, 最长={lengths.max()}, 平均={lengths.mean():.0f}")

    return df_out


def collect_journal_files(input_path: Path) -> list[Path]:
    """收集待处理的序时账文件列表。

    - 如果是文件 → 返回 [文件]
    - 如果是文件夹 → 递归查找所有 .xlsx/.xls/.csv（支持子文件夹如 公司A/序时账.xlsx）
    """
    if input_path.is_file():
        suffix = input_path.suffix.lower()
        if suffix not in [".xlsx", ".xls", ".csv"]:
            print(f"警告：不支持的文件格式，跳过 —— {input_path.name}")
            return []
        return [input_path]

    if input_path.is_dir():
        files = []
        for f in sorted(input_path.rglob("*")):
            if f.is_file() and f.suffix.lower() in [".xlsx", ".xls", ".csv"]:
                files.append(f)
        if not files:
            print(f"警告：文件夹及其子文件夹中没有找到支持的文件 —— {input_path}")
        return files

    return []


def write_training_data(df_out: pd.DataFrame, output_dir: Path, name: str) -> Path:
    """将清洗后的 DataFrame 写入标准训练数据文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"摘要文本_{name}.xlsx"
    df_out.to_excel(output_path, index=False, engine="openpyxl")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="序时账预处理：提取凭证号+摘要，生成唯一ID并去重。支持单文件或文件夹批量处理。"
    )
    parser.add_argument(
        "--input", type=Path, required=True,
        help="序时账文件路径或文件夹路径（.xlsx/.xls/.csv）"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("training_data"),
        help="输出目录（默认：training_data）"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"错误：路径不存在 —— {args.input}")
        sys.exit(1)

    # 收集待处理文件
    journal_files = collect_journal_files(args.input)
    if not journal_files:
        print("没有可处理的序时账文件。")
        sys.exit(1)

    print(f"找到 {len(journal_files)} 个序时账文件\n")

    # 清空输出目录（避免上次训练残留）
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 逐个处理，每个文件独立输出（不合并）
    success_count = 0
    for i, f in enumerate(journal_files, 1):
        print(f"[{i}/{len(journal_files)}] 处理：{f.name}")
        try:
            df_out = preprocess_journal_to_df(f)
            if df_out is not None:
                # 用来源前缀命名，避免同名覆盖
                prefix = get_source_prefix(f, args.input if args.input.is_dir() else None)
                write_training_data(df_out, args.output_dir, prefix)
                success_count += 1
        except Exception as e:
            print(f"  错误：{e}，跳过该文件")
        print()

    if success_count == 0:
        print("所有文件处理失败。")
        sys.exit(1)

    print(f"预处理完成！共 {success_count} 个训练数据文件 → {args.output_dir}")


if __name__ == "__main__":
    main()
