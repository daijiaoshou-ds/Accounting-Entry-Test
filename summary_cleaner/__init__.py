# -*- coding: utf-8 -*-
"""
摘要清洗 — 基于PMI相关性矩阵的序时账业务分类系统

子模块:
- v2/ : V2.1 程序 (PMI矩阵 + 关键词匹配 + 纠错回路)
- nn/ : V3.0 神经网络 (BGE 中文模型微调 + 科目开关 + 分类头)
"""

__version__ = "2.1.0"

# ── V2.1 程序 ──
from .v2.config import (
    get_bucket_names,
    SUBJECT_CLARITY,
    DEFAULT_CLARITY,
    COLUMN_NAME_PATTERNS,
    BUCKET_REGISTRY,
    BUCKET_SUBJECT_PREFERENCES,
    EXTRA_KEYWORDS,
    KEYWORD_EXPLICIT_SCORES,
    BUCKET_CLARITY,
    load_buckets_json,
    load_subject_list,
    build_bucket_preferences,
    LAMBDA_RANK,
    EMA_ALPHA,
)
from .v2.matcher import PurePythonAC, KeywordMatcher
from .v2.engine import PMIMatrix, VoucherVectorizer, CorrelationPropagator, Scorer
from .v2.persistence import GlobalCounters
from .v2.classifier import JournalClassifier
from .v2.memory_learner import (
    AmountProfiler, WordFeatureLearner, pmi_to_auto_score,
    LAMBDA_A, LAMBDA_AUTO,
    TIER1_COUNT_THRESHOLD, DISCARD_SESSION_THRESHOLD,
)
from .v2.correction import CorrectionManager

# ── NN 模块（V3.0）── 可选依赖（需要 PyTorch）
try:
    from .nn import (
        HARD_RULE_BUCKETS,
        DEFAULT_SKIP_BUCKETS,
        extract_training_records,
        build_hash_training_data,
        merge_training_data,
        load_merged_records,
        list_training_files,
        mark_reviewed,
        build_subject_switch_index,
        build_bucket_index,
        split_records,
        FinanceClassifierModel,
        FinanceClassifierTrainer,
        FinanceClassifierInference,
        MODEL_CHOICES,
        resolve_model_dir,
    )
    _NN_AVAILABLE = True
except ImportError:
    _NN_AVAILABLE = False  # torch/transformers 缺失时 V2.1 照常可用

__all__ = [
    # V2.1
    "get_bucket_names",
    "SUBJECT_CLARITY",
    "DEFAULT_CLARITY",
    "COLUMN_NAME_PATTERNS",
    "BUCKET_SUBJECT_PREFERENCES",
    "EXTRA_KEYWORDS",
    "KEYWORD_EXPLICIT_SCORES",
    "BUCKET_CLARITY",
    "load_buckets_json",
    "load_subject_list",
    "build_bucket_preferences",
    "PurePythonAC",
    "KeywordMatcher",
    "PMIMatrix",
    "VoucherVectorizer",
    "CorrelationPropagator",
    "Scorer",
    "GlobalCounters",
    "JournalClassifier",
    "AmountProfiler",
    "WordFeatureLearner",
    "pmi_to_auto_score",
    "LAMBDA_A",
    "LAMBDA_AUTO",
    "TIER1_COUNT_THRESHOLD",
    "DISCARD_SESSION_THRESHOLD",
    "CorrectionManager",
    "LAMBDA_RANK",
    "EMA_ALPHA",
    # NN (V3.0)
    "HARD_RULE_BUCKETS",
    "DEFAULT_SKIP_BUCKETS",
    "extract_training_records",
    "build_hash_training_data",
    "merge_training_data",
    "load_merged_records",
    "list_training_files",
    "mark_reviewed",
    "build_subject_switch_index",
    "build_bucket_index",
    "split_records",
    "FinanceClassifierModel",
    "FinanceClassifierTrainer",
    "FinanceClassifierInference",
    "MODEL_CHOICES",
    "resolve_model_dir",
]
