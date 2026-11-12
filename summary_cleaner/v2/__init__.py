# -*- coding: utf-8 -*-
"""V2 包 —— 4.0 起只保留「非打分」的部分。

留下：配置（桶表 / 科目表）、bucket_rules（别名重定向 / 温度校准 / 父级回退）、
      列名识别（已移到 journal_io）、训练数据持久化（指纹去重）。
删除：engine（PMI / 向量化 / 打分）、matcher（关键词）、memory_learner
      （自动词 / 金额特征）、correction（纠错回路）。
"""

from .bucket_rules import (  # noqa: F401
    calibrated_prob,
    merge_alias_probs,
    postprocess,
    remap_bucket,
    reset_stats,
    rules_summary,
)
from .classifier import HARD_RULE_BUCKETS, SENTINEL_BUCKETS, JournalClassifier
from .config import (
    BUCKET_CLARITY,
    BUCKET_REGISTRY,
    DEFAULT_CLARITY,
    SUBJECT_CLARITY,
    get_bucket_names,
    load_buckets_json,
    load_subject_list,
)
from .errors import EngineNotReady
from .fingerprint import compute_fingerprint

__all__ = [
    "JournalClassifier",
    "HARD_RULE_BUCKETS",
    "SENTINEL_BUCKETS",
    "EngineNotReady",
    "compute_fingerprint",
    "BUCKET_REGISTRY",
    "BUCKET_CLARITY",
    "DEFAULT_CLARITY",
    "SUBJECT_CLARITY",
    "get_bucket_names",
    "load_buckets_json",
    "load_subject_list",
    "calibrated_prob",
    "merge_alias_probs",
    "postprocess",
    "remap_bucket",
    "reset_stats",
    "rules_summary",
]
