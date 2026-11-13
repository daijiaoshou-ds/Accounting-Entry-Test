# -*- coding: utf-8 -*-
"""复核粒度：一条待办 = 一组业务等价的凭证；交一条，整组生效。

为什么重要：引擎本来就把"同科目组合 + 摘要去数字后相同"的凭证压成一组、判一次抄全组
（`summary_cleaner/dedup.py`）。复核如果还是逐张问，1 万张同模板凭证就是 1 万条待办，
而人只需要看 1 条。这个测试钉三件事：

1. 待办按组给：组内多张只出一条，带「张数」和「样张」；
2. 结论按组代表交，写回时铺到组内每一张；
3. 老数据（没有组列 / 直接按凭证号交）照样能用。

用法：
    venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_group_review.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from features.cleaning import stages as clean_s  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


row = {"业务分类": "采购", "置信度": "中", "校准概率": 0.4,
       "摘要": "付货款", "科目": "应付账款", "金额": 100.0, "模型Top3": "[]"}
detail = pd.DataFrame([
    {"凭证号": "v1", "组ID": "g00001", "组代表": "v1", **row},
    {"凭证号": "v2", "组ID": "g00001", "组代表": "v1", **row},
    {"凭证号": "v3", "组ID": "g00002", "组代表": "v3", **row},
])

view = detail.copy()
view["_vid"] = view["凭证号"].astype(str)
view["_conf"] = [""] * len(view)

rows = clean_s._group_rows(view)
check(len(rows) == 2, f"应压成 2 组，实际 {len(rows)}")
if len(rows) == 2:
    head, size, why, members = max(rows, key=lambda item: item[1])
    check(size == 2, f"多张组的张数不对：{size}")
    check(head["组代表"] == "v1", "组的代表取错了")
    check(members == ["v1", "v2"], f"样张不对：{members}")

# 结论按组代表交 → 铺到组内每张
out = clean_s.expand_group_decisions(detail, {"v1": "销售"})
check(out == {"v1": "销售", "v2": "销售"}, f"组结论没铺开：{out}")

# 直接按组内非代表凭证改：只改它自己（兼容手工改单张）
single = clean_s.expand_group_decisions(detail, {"v2": "研发"})
check(single == {"v2": "研发"}, f"按单张改的口径不对：{single}")

# 老数据：没有组列时原样透传
legacy = clean_s.expand_group_decisions(detail.drop(columns=["组ID", "组代表"]), {"v9": "费用"})
check(legacy == {"v9": "费用"}, f"老数据（无分组列）处理不对：{legacy}")

if fails:
    print("\n".join(f"FAIL {x}" for x in fails))
    sys.exit(1)
print("PASS 复核按组给、按组交、整组生效；老数据兼容")
