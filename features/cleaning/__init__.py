# -*- coding: utf-8 -*-
"""功能：序时账清洗（cleaning）。

五阶段：接对方科目（可选）→ 读表 → 业务分类 → 复核分类（AI）→ 写回导出。

**它在管线里排第二位**（对方科目分析 → 清洗 → 现金流 / 分录测试）。接上对方
科目是为了让交付物的每一行同时能看到「判成了什么业务」和「钱对给了谁」——这两
件事以前各在一份产物里，要看同一行只能自己拿 Excel 去 vlookup。

前四段里的 `read` / `clean` / `review_bucket` **不是清洗独有的**：现金流和分录
测试要用同一份实现、同一份结论，所以它们在 `core/ledger/`。本功能只加接对方科目
与写回导出两件事。
"""

from __future__ import annotations

from core.ledger import (REQUIRES, REVIEW_HELP_BUCKET, has_upstream,
                         prepared_signature, review_items, run_clean, run_read)
from jobflow.feature import Feature
from jobflow.stage import Stage

from . import stages as s


def build() -> Feature:
    return Feature(
        id="cleaning",
        name="序时账清洗",
        description="模型判业务桶（24 类）+ 四类硬约束；复核中/低置信后写回原表",
        requires=REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        accepts_upstream=("contra",),
        stages=(
            Stage(id="read", title="载入序时账", actor="program",
                  run=run_read, hash_extra=prepared_signature),
            Stage(id="link", title="接入对方科目分析", actor="program",
                  depends_on=("read",), run=s.run_link_contra,
                  enabled=has_upstream, hash_extra=s.contra_signature,
                  skip_note="没有指定上游任务，结果里不带对方科目"),
            Stage(id="clean", title="业务分类", actor="program",
                  depends_on=("read", "link"), run=run_clean),
            Stage(id="review_bucket", title="复核业务分类（中/低置信）", actor="ai",
                  depends_on=("clean",), review=review_items,
                  help=REVIEW_HELP_BUCKET),
            Stage(id="export", title="写回并导出", actor="program",
                  depends_on=("clean", "review_bucket"), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "业务分类结果（含对方科目）", "stage": "export"},
        ),
    )
