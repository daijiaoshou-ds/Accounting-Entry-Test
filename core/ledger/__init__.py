# -*- coding: utf-8 -*-
"""账的共用阶段：读表 → 业务分类 → 复核桶，以及接着上游任务跑。

这四个功能的第一段是**同一件事**，且结果必须一致：

    cleaning     直接用
    cashflow     直接用（再追加一类"方向冲突"复核理由）
    contra       只要 read
    entry_test   只要 read（业务分类来自上游）

`features/cleaning/stages.py` 曾经承担这一段，但它同时又是"清洗"这个功能的实现，
于是现金流要 `from ..cleaning import stages` 才能跑——功能之间横向依赖。现在这一
段搬到这里，`features/*` 互相不 import。
"""

from __future__ import annotations

from .classify import run_clean
from .read import (REQUIRES, prepared_dir, prepared_signature, read_journal,
                   run_read)
from .review import (ASSIGNABLE_BUCKETS, REVIEW_HELP_BUCKET, ExtraHook,
                     expand_group_decisions, fmt_top3, review_items,
                     voucher_lines)
from .upstream import (has_upstream, run_link, same_ledger, upstream_id,
                       upstream_signature)

__all__ = [
    "REQUIRES", "prepared_dir", "prepared_signature", "read_journal", "run_read",
    "run_clean",
    "ASSIGNABLE_BUCKETS", "REVIEW_HELP_BUCKET", "ExtraHook",
    "expand_group_decisions", "fmt_top3", "review_items", "voucher_lines",
    "has_upstream", "run_link", "same_ledger", "upstream_id", "upstream_signature",
]
