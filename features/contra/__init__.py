# -*- coding: utf-8 -*-
"""功能：对方科目分析（contra）。

四阶段：读表 → 模式穷举 → 复核配对方案（AI）→ 写回导出。

理论见 `docs/publish/CSA_对方科目分析理论.md`：多借多贷的凭证要回答"哪一笔贷方
配的是哪一笔借方"，穷举能给出一批都成立的配对，靠奥卡姆剃刀排个序，剩下的分歧
（并列、高频）交给懂业务的人定。
"""

from __future__ import annotations

from jobflow.feature import Feature
from jobflow.stage import Stage

from . import stages as s


def build() -> Feature:
    return Feature(
        id="contra",
        name="对方科目分析",
        description="把多借多贷凭证拆成一对一的对方科目（穷举 + 奥卡姆剃刀）；"
                    "并列或高频的模式交给人定案",
        requires=s.REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        stages=(
            Stage(id="read", title="读表与字段识别", actor="program",
                  run=s.run_read),
            Stage(id="analyze", title="复杂模式穷举与排序", actor="program",
                  depends_on=("read",), run=s.run_analyze),
            Stage(id="review_pattern", title="复核复杂模式的配对方案", actor="ai",
                  depends_on=("analyze",), review=s.review_pattern,
                  help=s.REVIEW_HELP),
            Stage(id="export", title="写回并导出", actor="program",
                  depends_on=("analyze", "review_pattern"), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "对方科目分析（Excel/CSV）", "stage": "export"},
        ),
    )
