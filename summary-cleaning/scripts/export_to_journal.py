#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结果导出脚本

将训练分类结果写回用户的原始序时账——在摘要列旁边新增"摘要分类"列，
原封不动保留用户的所有原始数据。
"""

import argparse
import io
import sys
from pathlib import Path

import pandas as pd

# 确保控制台输出使用 UTF-8 编码
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

# 导入预处理和训练模块中的工具函数
sys.path.insert(0, str(Path(__file__).parent))
from preprocess_journal import (
    find_column,
    build_unique_voucher_id,
    collect_journal_files,
    get_source_prefix,
    DATE_CANDIDATES,
    VOUCHER_CANDIDATES,
    SUMMARY_CANDIDATES,
    YEAR_CANDIDATES,
    MONTH_CANDIDATES,
)


def load_journal(path: Path) -> pd.DataFrame:
    """加载原始序时账文件。"""
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(path, engine="openpyxl")
    elif suffix == ".csv":
        return pd.read_csv(path)
    else:
        raise ValueError(f"不支持的文件格式：{suffix}")


def load_training_report(path: Path) -> pd.DataFrame:
    """加载训练报告的命中明细。"""
    return pd.read_excel(path, sheet_name="命中明细", engine="openpyxl")


def build_match_map(hit_df: pd.DataFrame) -> dict:
    """构建 (唯一凭证号, 摘要) → 业务桶 的查找字典。

    同一个 (ID, 摘要) 只对应一条记录（训练前已去重），
    多桶命中的情况用"、"拼接。
    """
    match_map = {}
    for _, row in hit_df.iterrows():
        key = (str(row["ID"]), str(row["文本内容"]).strip())
        match_map[key] = str(row["命中业务桶"])
    return match_map


def export(
    journal_path: Path,
    report_path: Path,
    output_dir: Path,
    output_name: str | None = None,
) -> Path:
    """将分类结果写回原始序时账。

    返回输出文件路径。
    """
    # 1. 加载数据
    journal = load_journal(journal_path)
    hit = load_training_report(report_path)
    match_map = build_match_map(hit)

    print(f"原始序时账：{len(journal)} 行")
    print(f"训练命中记录：{len(hit)} 条")

    # 2. 识别列
    date_col = find_column(journal, DATE_CANDIDATES)
    voucher_col = find_column(journal, VOUCHER_CANDIDATES)
    summary_col = find_column(journal, SUMMARY_CANDIDATES)
    year_col = find_column(journal, YEAR_CANDIDATES)
    month_col = find_column(journal, MONTH_CANDIDATES)

    if voucher_col is None:
        raise ValueError(f"序时账中未找到凭证号列。候选名：{VOUCHER_CANDIDATES}")
    if summary_col is None:
        raise ValueError(f"序时账中未找到摘要列。候选名：{SUMMARY_CANDIDATES}")

    date_source = "无"
    if date_col:
        date_source = f"日期列「{date_col}」"
    elif year_col and month_col:
        date_source = f"年列「{year_col}」+ 月列「{month_col}」"

    print(f"识别：凭证号=「{voucher_col}」, 摘要=「{summary_col}」, 日期来源={date_source}")

    # 3. 生成唯一凭证号（与预处理用相同的来源前缀，确保能匹配）
    source_prefix = get_source_prefix(journal_path)
    journal["_unique_voucher_id"] = build_unique_voucher_id(
        journal, date_col, voucher_col, year_col, month_col, source_prefix
    )

    # 4. 匹配业务桶
    classification = []
    matched = 0
    unmatched = 0
    empty_summary = 0

    for _, row in journal.iterrows():
        uid = str(row["_unique_voucher_id"])
        summary = row[summary_col]

        # 空摘要
        if pd.isna(summary) or str(summary).strip() == "":
            classification.append("空摘要")
            empty_summary += 1
            continue

        summary_clean = str(summary).strip()
        key = (uid, summary_clean)

        if key in match_map:
            classification.append(match_map[key])
            matched += 1
        else:
            classification.append("未分类")
            unmatched += 1

    # 5. 插入"摘要分类"列
    # 找到摘要列的位置，在其后插入
    summary_idx = journal.columns.get_loc(summary_col)
    journal.insert(summary_idx + 1, "摘要分类", classification)

    # 6. 删除临时列
    journal = journal.drop(columns=["_unique_voucher_id"])

    # 7. 统计
    total_valid = matched + unmatched
    hit_rate = matched / total_valid * 100 if total_valid > 0 else 0
    print(f"\n匹配结果：")
    print(f"  已分类：{matched} 行")
    print(f"  未分类：{unmatched} 行")
    print(f"  空摘要：{empty_summary} 行")
    print(f"  命中率：{hit_rate:.1f}%（不含空摘要）")

    # 8. 保存（用来源前缀命名，避免多公司同名覆盖）
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_name is None:
        prefix = source_prefix  # 复用前面已计算的 source_prefix
        output_name = f"{prefix}_已分类"

    output_path = output_dir / f"{output_name}.xlsx"
    journal.to_excel(output_path, index=False, engine="openpyxl")
    print(f"\n结果已导出：{output_path}")

    return output_path


def find_report_for_journal(journal_path: Path, report_dir: Path) -> Path | None:
    """在报告目录中查找与序时账 source_prefix 匹配的训练报告。"""
    prefix = get_source_prefix(journal_path)
    reports = sorted(report_dir.glob("bucket_training_report_*.xlsx"),
                     key=lambda x: x.stat().st_mtime, reverse=True)
    for r in reports:
        if prefix in r.stem:
            return r
    return None


def main():
    parser = argparse.ArgumentParser(
        description='将训练分类结果写回原始序时账，新增"摘要分类"列。'
                    '单文件：--journal file --report file；批量：--journal dir --report-dir dir'
    )
    parser.add_argument(
        "--journal", type=Path, required=True,
        help="序时账文件或文件夹",
    )
    parser.add_argument(
        "--report", type=Path, default=None,
        help="训练报告路径（单文件模式）",
    )
    parser.add_argument(
        "--report-dir", type=Path, default=None,
        help="训练报告目录（批量模式，按 source_prefix 自动匹配）",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output"),
        help="输出目录（默认：output）",
    )
    args = parser.parse_args()

    if not args.journal.exists():
        print(f"错误：路径不存在 —— {args.journal}")
        sys.exit(1)
    if not args.report and not args.report_dir:
        print("错误：必须指定 --report（单文件）或 --report-dir（批量）")
        sys.exit(1)

    journal_files = collect_journal_files(args.journal)
    if not journal_files:
        print("没有找到可导出的序时账文件。")
        sys.exit(1)

    print(f"找到 {len(journal_files)} 个序时账文件\n")

    for i, f in enumerate(journal_files, 1):
        # 确定每个文件对应的报告
        if args.report:
            report_path = args.report
        else:
            report_path = find_report_for_journal(f, args.report_dir)
            if report_path is None:
                print(f"[{i}/{len(journal_files)}] 跳过 {f.name}：未找到匹配的训练报告")
                continue

        print(f"[{i}/{len(journal_files)}] 导出：{f.name} ← {report_path.name}")
        try:
            export(f, report_path, args.output_dir)
        except Exception as e:
            print(f"  错误：{e}，跳过该文件")
        print()

    print("导出完成！")


if __name__ == "__main__":
    main()
