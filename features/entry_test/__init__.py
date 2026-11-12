# -*- coding: utf-8 -*-
"""功能：会计分录测试（entry_test）。

四阶段：读表 → 异常打分（程序）→ 复核异常线索（AI）→ 导出两张表。

理论见 `docs/publish/JET_会计分录测试理论.md`：先把凭证按「业务群」归类，再看
一张凭证横跨了几个群、金额相对本群的重要性水平有多大，算出一个综合得分。它判的
是**统计意义上的异常**，不是账务错误——所以必须有一道人工/AI 复核，否则大额正常
业务全被标红。

功能 id 用 `entry_test` 而不是 `jet`：`journal_io/fields.py` 的字段契约表
（FEATURE_REQUIRES）早就按这个名字声明了必需/可选字段，`journal inspect --feature
entry_test` 靠它工作。
"""

from __future__ import annotations

from jobflow.feature import Feature
from jobflow.stage import Stage

from . import stages as s


def build() -> Feature:
    return Feature(
        id="entry_test",
        name="会计分录测试",
        description="按业务群聚类给凭证打异常分；高/中风险的凭证交给 AI 逐条判"
                    "「真异常 / 疑似 / 误报」",
        requires=s.REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        stages=(
            Stage(id="read", title="读表与字段识别", actor="program",
                  run=s.run_read),
            Stage(id="detect", title="业务群聚类与异常打分", actor="program",
                  depends_on=("read",), run=s.run_detect),
            Stage(id="review_anomaly", title="复核高/中风险的凭证", actor="ai",
                  depends_on=("detect",), review=s.review_anomaly,
                  help=s.REVIEW_HELP),
            Stage(id="export", title="写回并导出", actor="program",
                  depends_on=("detect", "review_anomaly"), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "会计分录测试（Excel/CSV）", "stage": "export"},
        ),
    )
