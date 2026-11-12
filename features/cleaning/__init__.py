# -*- coding: utf-8 -*-
"""功能：序时账清洗（cleaning）。

四阶段：读表 → 业务分类 → 复核分类（AI）→ 写回导出。

它的 `read` / `clean` / `review_bucket` **也被下游功能直接复用**
（`from ..cleaning.stages import run_read`），所以这里既是功能，也是共用段。
"""

from __future__ import annotations

from jobflow.feature import Feature
from jobflow.stage import Stage

from . import stages as s


def build() -> Feature:
    return Feature(
        id="cleaning",
        name="序时账清洗",
        description="模型判业务桶（24 类）+ 四类硬约束；复核中/低置信后写回原表",
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
            Stage(id="export", title="写回并导出", actor="program",
                  depends_on=("clean", "review_bucket"), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "业务分类结果（Excel/CSV）", "stage": "export"},
        ),
    )
