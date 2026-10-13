# -*- coding: utf-8 -*-
"""端到端：跑一遍并核对现金流结果。

重点看两件事：
  ① 投资本金 / 借款筹资这些歧义桶还能不能判出来（科目裁判）
  ② 桶判错的行**不再被静默洗掉** —— 单向桶（销售收入/存货采购/日常费用…）
     遇到反方向凭证，程序标"待复核 · 方向与桶不符"，而不是顺着方向挑一个
     看起来合理的项目。第2轮我们手工补过的那张「福费廷贷款」就是这类。
"""
import contextlib
import io
import json
import sys
from pathlib import Path

import pandas as pd

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

# ============================================================================
# 断言
# ============================================================================
rev = list(stats.get("cashflow_review") or [])
mismatch = [x for x in rev if "方向与桶不符" in x["原因"]]
print(f"\n待复核 {len(rev)} 条，其中「方向与桶不符」{len(mismatch)} 条：")
for x in mismatch:
    print(f"  {x['凭证号']}  {x['业务分类']}  [{x['现金流方向']}现]  "
          f"{x['摘要'][:24]}")

assert cf["needs_review_rows"] == len(rev), \
    f"待复核计数与清单不一致：{cf['needs_review_rows']} vs {len(rev)}"
# 没有项目的行不能比"待复核"多 —— 多出来的就是**静默漏判**
assert cf["rows_without_item"] >= cf["needs_review_rows"], \
    "有既没项目、又没标待复核的行（静默漏判）"
assert len(mismatch) >= 1, \
    "样例里应有「方向与桶不符」的凭证；一条都没有说明单向桶规则没生效"
assert all(x["候选项目"] == [] for x in mismatch), \
    "方向不符的行不该给出反方向候选（会诱导 AI 挑一个必被驳回的项目）"

# 旗舰回归：记-00543（福费廷贷款 2000 万）曾被模型判成「存货采购」，
# 而它的钱是**收进来**的 —— 上一轮靠人工补丁才纠正。
# 现在只要它还挂着「存货采购」，程序就必须自己报「待复核 · 方向与桶不符」。
book = OUT / "样例序时账_结果.xlsx"
res = pd.read_excel(book, sheet_name="业务分类结果", dtype=object)
ff = res[res["凭证号"].astype(str) == "记-00543"]
if len(ff) and set(ff["业务分类"].astype(str)) == {"存货采购"}:
    srcs = set(ff["现金流判定"].astype(str))
    print(f"\n记-00543（福费廷）判定={srcs}")
    assert "待复核" in srcs, \
        f"存货采购 + 收现 没被判成待复核（实得 {srcs}）——错桶又被洗成现金流项目了"
else:
    print(f"\n记-00543 不在样例里或桶已不是「存货采购」（跳过该回归）")

assert "吸收投资收到的现金" not in cf["amount_by_item"], \
    "样例里没有股东出资，不该出现吸收投资"
assert cf["amount_by_item"].get("收回投资收到的现金", 0) > 0, "应有收回投资"
print("\n✅ 断言全部通过")
