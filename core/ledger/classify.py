# -*- coding: utf-8 -*-
"""clean 阶段：业务桶判定（纯模型 + 四类机械凭证硬约束，没有程序打分）。

放在共用内核里而不是"清洗功能"里，是因为现金流、分录测试都要用同一份判定——
同一张凭证在两个功能里判出不同的桶，是静默的错。
"""

from __future__ import annotations

from jobflow.stage import Ctx, StageResult

from ..classify import pipeline as clean_pipeline
from ..classify.classifier import get_classifier


def run_clean(ctx: Ctx) -> StageResult:
    j = ctx.journal
    clf = get_classifier()
    clf._load_nn_if_available()
    if not getattr(clf, "_nn_available", False):
        raise RuntimeError(
            "上游模型不可用：业务分类的前提是模型，"
            "请先确认模型交付物（core/nn/_storage/）齐备")

    res = clean_pipeline.run(j.df, j.schema.legacy(), clf,
                             export_training=False,
                             progress=lambda pct, msg: ctx.log(f"   [{pct:>3}%] {msg}"))
    ctx.save("result.parquet", res.df)
    ctx.save("detail.parquet", res.detail)

    stats = res.stats or {}
    engine = stats.get("engine") or {}
    return StageResult(
        artifacts={"result": str(ctx.path("clean", "result.parquet")),
                   "detail": str(ctx.path("clean", "detail.parquet"))},
        facts={
            "rows": int(len(res.df)),
            "vouchers": int(len(res.detail)),
            "bucket_counts": stats.get("bucket_counts") or {},
            "confidence_dist": stats.get("confidence_dist") or {},
            "hard_rule_counts": stats.get("hard_rule_counts") or {},
            "model_calls": engine.get("model_calls"),
            "modeled_vouchers": engine.get("modeled_vouchers"),
            "unclassified": engine.get("unclassified"),
            # 归一化之后模型仍不认识的科目**种数**（不是 dict 的键数）；
            # 明细在 detail.parquet 的「未知科目」列与 clean 的 stats 里
            "unknown_subjects": (stats.get("unknown_subjects") or {}).get("names", 0),
            "model": engine.get("model") or {},
        })
