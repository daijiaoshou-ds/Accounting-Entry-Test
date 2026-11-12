# -*- coding: utf-8 -*-
"""功能：现金流量表编写（cashflow）。

阶段链见 `stages.py`；这里是"接线"——把阶段、字段需求、导出项组装成 Feature。

`accepts_upstream` 声明"可以从哪个功能的任务接着跑"：前端据此列出可选的
已完成任务，用户就不必把同一份账的分类与复核再做一遍。
"""

from __future__ import annotations

from jobflow.feature import Feature
from jobflow.stage import Stage

from ..cleaning import stages as clean_s
from . import stages as s


def _no_upstream(ctx) -> bool:
    return not clean_s.has_upstream(ctx)


def build() -> Feature:
    return Feature(
        id="cashflow",
        name="现金流量表编写",
        description="先由 summary_cleaner 判业务桶，再把「桶 + 货币资金方向」映射成现金流量项目",
        requires=s.REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        accepts_upstream=("cleaning",),
        stages=(
            Stage(id="link", title="接入上游清洗结果", actor="program",
                  run=clean_s.run_link, enabled=clean_s.has_upstream,
                  hash_extra=clean_s.upstream_signature,
                  skip_note="没有指定上游任务，本任务自己读表与分类"),
            Stage(id="read", title="读表与字段识别", actor="program",
                  run=clean_s.run_read, enabled=_no_upstream,
                  skip_note="已从上游任务接过来"),
            Stage(id="clean", title="业务分类", actor="program",
                  depends_on=("read",), run=clean_s.run_clean,
                  enabled=_no_upstream, skip_note="已从上游任务接过来"),
            # 上游模式下这一段**也要跑**：中/低置信上游看过，但"桶与钱的方向不符"
            # 是上游发现不了的，只在接上游时列出这一类（见 stages.review_bucket）
            Stage(id="review_bucket", title="复核业务分类（中/低置信 + 方向冲突）",
                  actor="ai", depends_on=("clean",), review=s.review_bucket,
                  help=s.REVIEW_HELP_BUCKET),
            Stage(id="judge", title="编现金流量表", actor="program",
                  depends_on=("clean", "review_bucket", "link"), run=s.run_judge),
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
