#!/usr/bin/env python3
"""
序时账与银行流水核对工具

用法:
    python main.py --journal 序时账.xlsx --bank 银行流水.xlsx --output 核对结果.xlsx
"""
import argparse
import sys
import pandas as pd

from bank_statement_matcher.src.core.matcher import ReconciliationEngine, export_results


def main():
    parser = argparse.ArgumentParser(description="序时账与银行流水核对工具")
    parser.add_argument("--journal", "-j", required=True, help="序时账 Excel 文件路径")
    parser.add_argument("--bank", "-b", required=True, help="银行流水 Excel 文件路径")
    parser.add_argument("--output", "-o", default="核对结果.xlsx", help="输出文件路径")
    parser.add_argument("--sheet-journal", default=0, help="序时账 sheet 名称或索引")
    parser.add_argument("--sheet-bank", default=0, help="银行流水 sheet 名称或索引")
    parser.add_argument("--account", default=None, help="指定核对某个明细科目（如：银行存款-建行0911）")
    parser.add_argument("--tol", type=float, default=0.01, help="金额匹配容差（默认 0.01）")

    args = parser.parse_args()

    print(f"📖 加载序时账: {args.journal}")
    df_journal = pd.read_excel(args.journal, sheet_name=args.sheet_journal)
    print(f"   共 {len(df_journal)} 行, 列: {list(df_journal.columns)}")

    print(f"🏦 加载银行流水: {args.bank}")
    df_bank = pd.read_excel(args.bank, sheet_name=args.sheet_bank)
    print(f"   共 {len(df_bank)} 行, 列: {list(df_bank.columns)}")

    engine = ReconciliationEngine(tol=args.tol)

    try:
        engine.load_journal(df_journal)
        print(f"   序时账字段映射: {engine.field_mapper.journal_map}")
    except ValueError as e:
        print(f"❌ 序时账字段识别失败: {e}")
        print("请手动指定字段映射，或检查表头")
        sys.exit(1)

    try:
        engine.load_bank(df_bank)
        print(f"   银行流水字段映射: {engine.field_mapper.bank_map}")
    except ValueError as e:
        print(f"❌ 银行流水字段识别失败: {e}")
        sys.exit(1)

    print("\n🔍 开始核对...")
    results = engine.run(detail_account=args.account)

    for acc, res in results.items():
        print(f"\n📋 明细科目: {acc}")
        print(f"   匹配成功: {res.summary['total_matches']} 笔")
        print(f"   未匹配序时账: {res.summary['total_unmatched_gl']} 笔")
        print(f"   未匹配银行流水: {res.summary['total_unmatched_bank']} 笔")

    export_results(results, args.output)
    print(f"\n✅ 完成！结果已保存至: {args.output}")


if __name__ == "__main__":
    main()
