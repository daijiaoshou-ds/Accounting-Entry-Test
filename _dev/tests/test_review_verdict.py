# -*- coding: utf-8 -*-
"""复核分账：提交了但等于原判 → 确认，不等于原判 → 改判；看过没交 → 弃权。

这条口径直接决定报表上的「复核改判」数：早先只看到"有没有提交过"，
AI 为清空待办把认同也提交上来，改判数就被灌成待办总量。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from features.cleaning.stages import review_verdict

detail = pd.DataFrame({
    "凭证号": ["A1", "A2", "B1", "B2", "C1", "D1"],
    "组代表": ["A1", "A1", "B1", "B1", "C1", "D1"],
    "业务分类": ["费用", "费用", "采购", "采购", "销售", "薪酬"],
})
decisions = {"A1": "研发", "B1": "采购"}      # A 组改了，B 组是"认同原判"
requested = {"A1": {}, "B1": {}, "C1": {}}     # C 组看过没交 → 弃权；D 组没进待办

verdict, counts, changed_groups = review_verdict(detail, decisions, requested)

print("结论", verdict)
print("分账", counts, "改判组数", changed_groups)

assert verdict["A1"] == verdict["A2"] == "改判", verdict
assert verdict["B1"] == verdict["B2"] == "确认", verdict
assert verdict["C1"] == "弃权", verdict
assert verdict["D1"] == "未复核", verdict
assert counts == {"改判": 2, "确认": 2, "弃权": 1, "未复核": 1}, counts
assert changed_groups == 1, changed_groups
print("✅ 复核分账：改判/确认/弃权/未复核 各归各位")
