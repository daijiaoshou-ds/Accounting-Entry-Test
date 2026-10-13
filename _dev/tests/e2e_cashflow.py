# -*- coding: utf-8 -*-
"""端到端：跑一遍并核对现金流结果，重点看投资本金/借款筹资是否不再待复核。"""
import io
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "summary_cleaner_v3"))

from jc.pipeline import run_file  # noqa: E402

OUT = ROOT / "_dev" / "smoke" / "输出"

buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    stats = run_file(ROOT / "_dev" / "smoke" / "样例序时账.xlsx", out_dir=OUT)

cf = stats["cashflow"]
print(f"凭证={stats['total_vouchers']}  覆盖={stats['coverage']}  "
      f"耗时={stats['elapsed_seconds']}s")
print(f"现金流相关凭证={cf['money_vouchers']}  分录={cf['money_rows']}  "
      f"冲销={cf['wash_rows']}  待复核={cf['needs_review_rows']}")
print(f"净现金流={cf['net_cash']}")
print(f"分活动={json.dumps(cf['amount_by_section'], ensure_ascii=False)}")
print("\n按项目:")
for k, v in cf["amount_by_item"].items():
    print(f"  {v:>18,.2f}  {k}")

print("\n输出文件:")
for k, v in stats["output_files"].items():
    print(f"  {k}")

# 关键断言
assert cf["needs_review_rows"] == 0, f"出现待复核 {cf['needs_review_rows']} 条"
assert cf["rows_without_item"] == 0, f"有 {cf['rows_without_item']} 条没有项目"
assert "吸收投资收到的现金" not in cf["amount_by_item"], \
    "样例里没有股东出资，不该出现吸收投资"
assert cf["amount_by_item"].get("收回投资收到的现金", 0) > 0, "应有收回投资"
print("\n✅ 断言全部通过")
