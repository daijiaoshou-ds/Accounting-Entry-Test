# -*- coding: utf-8 -*-
"""业务桶判定内核：读表之后的那一步。

    桶表与科目表      config（BUCKET_REGISTRY、科目别名、模型存储位置）
    桶规则            bucket_rules（别名重定向 / 温度校准 / 父级回退）
    子桶折叠          bucket_merge
    分类器            classifier（模型 + 四类机械凭证硬约束）
    特征与摘要        pipeline / subjects / dedup
    列名读取          io_utils

4.0 起没有程序打分：业务桶 100% 由模型判定，程序只做读表、科目归一化、四类机械
凭证硬约束、写回。**这一层不含训练**——训练在 `tools/train/`。
"""

from __future__ import annotations

from . import pipeline
from .bucket_rules import (  # noqa: F401
    calibrated_prob,
    merge_alias_probs,
    postprocess,
    remap_bucket,
    reset_stats,
    rules_summary,
)
from .classifier import (HARD_RULE_BUCKETS, SENTINEL_BUCKETS,  # noqa: F401
                         JournalClassifier, get_classifier)
from .config import (BUCKET_CLARITY, BUCKET_REGISTRY, DEFAULT_CLARITY,  # noqa: F401
                     MERGED_BUCKETS, SUBJECT_CLARITY, get_bucket_names,
                     get_nn_storage_dir, load_buckets_json, load_subject_list)
from .errors import EngineNotReady  # noqa: F401
from .fingerprint import compute_fingerprint  # noqa: F401

__all__ = [
    "pipeline",
    "JournalClassifier",
    "get_classifier",
    "HARD_RULE_BUCKETS",
    "SENTINEL_BUCKETS",
    "EngineNotReady",
    "compute_fingerprint",
    "BUCKET_REGISTRY",
    "BUCKET_CLARITY",
    "DEFAULT_CLARITY",
    "SUBJECT_CLARITY",
    "MERGED_BUCKETS",
    "get_bucket_names",
    "get_nn_storage_dir",
    "load_buckets_json",
    "load_subject_list",
    "calibrated_prob",
    "merge_alias_probs",
    "postprocess",
    "remap_bucket",
    "reset_stats",
    "rules_summary",
]
