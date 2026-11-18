# -*- coding: utf-8 -*-
"""现金流结果的统计口径。

**只有这一份**：阶段的 `judge` 跑完、插件的结果页、导出的概况表都读它。
以前它住在 `pipeline.py` 里，而那个文件是"独立跑一份账"的第二套实现
（自己读表、自己认列名、自己判、自己导出）——字段识别已经统一在任务配置
那一步（`journal inspect` + `run --mapping`），所以那套并行的实现删掉，
统计这一段留下来。
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import judge


def cashflow_stats(money_by_vid: Dict[str, List], cf_by_row: Dict[int, dict]
                   ) -> Dict[str, Any]:
    """现金流结果概况。

    `rows_without_item` 恒 ≥ needs_review_rows：它必然包含"待复核"（本来就没项目）
    再加"桶本身不产生现金流量"（资金内部往来、折旧摊销）。**只有「未分类(异常)」
    不为 0 才需要处理**，它不是告警指标。
    """
    by_item: Dict[str, float] = {}
    by_section: Dict[str, float] = {}
    by_source: Dict[str, int] = {}
    review_rows = empty_rows = wash_rows = 0
    empty_no_cash = empty_review = empty_unclassified = 0

    for row_no, v in cf_by_row.items():
        src = v.get("source") or ""
        by_source[src] = by_source.get(src, 0) + 1
        if v.get("no_cash"):
            wash_rows += 1
            continue
        item = v.get("cf_item") or ""
        if not item:
            empty_rows += 1
            if v.get("source") == judge.SRC_NONE:
                empty_unclassified += 1
            elif v.get("needs_review"):
                empty_review += 1
            else:
                empty_no_cash += 1
        else:
            by_item[item] = by_item.get(item, 0.0) + float(v.get("amount") or 0)
            sec = judge.section_of(item) or "其他"
            by_section[sec] = by_section.get(sec, 0.0) + float(v.get("amount") or 0)
        if v.get("needs_review"):
            review_rows += 1

    return {
        "money_vouchers": len(money_by_vid),
        "money_rows": len(cf_by_row),
        "wash_rows": wash_rows,
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        "needs_review_rows": review_rows,
        "rows_without_item": empty_rows,
        "rows_without_item_detail": {
            "桶不产生现金流量(正常)": empty_no_cash,
            "待复核": empty_review,
            "未分类(异常)": empty_unclassified,
        },
        "rows_without_item_note": (
            "正常值 = 「桶不产生现金流量」（资金内部往来=自家账户互转、折旧摊销）"
            "+「待复核」（本来就没项目）。**只要「未分类(异常)」是 0 就不用管**。"),
        "amount_by_item": {k: round(v, 2) for k, v in
                           sorted(by_item.items(), key=lambda kv: -abs(kv[1]))},
        "amount_by_section": {k: round(v, 2) for k, v in by_section.items()},
        "net_cash": round(sum(by_item.values()), 2),
    }
