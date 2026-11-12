# -*- coding: utf-8 -*-
"""功能：现金流量表编写（cashflow）。

阶段链见 `stages.py`；这里是"接线"——把阶段、字段需求、导出项组装成 Feature。
"""

from __future__ import annotations

from jobflow.feature import Feature
from jobflow.stage import Stage

from . import stages as s


def build() -> Feature:
    return Feature(
        id="cashflow",
        name="现金流量表编写",
        description="先由 summary_cleaner 判业务桶，再把「桶 + 货币资金方向」映射成现金流量项目",
        requires=s.REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        stages=(
            Stage(id="read", title="读表与字段识别", actor="program",
                  run=s.run_read),
            Stage(id="clean", title="业务分类", actor="program",
                  depends_on=("read",), run=s.run_clean),
            Stage(id="review_bucket", title="复核业务分类（中/低置信）", actor="ai",
                  depends_on=("clean",), review=s.review_bucket,
                  help=s.REVIEW_HELP_BUCKET),
            Stage(id="judge", title="编现金流量表", actor="program",
                  depends_on=("clean", "review_bucket"), run=s.run_judge),
            Stage(id="review_cf", title="复核待确认的现金流条目", actor="ai",
                  depends_on=("judge",), review=s.review_cf,
                  help=s.REVIEW_HELP_CF),
            Stage(id="export", title="写回并导出", actor="program",
                  depends_on=("judge", "review_cf"), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "现金流量表（Excel/CSV）", "stage": "export"},
        ),
    )
