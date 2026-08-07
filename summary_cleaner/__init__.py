# -*- coding: utf-8 -*-
"""
摘要清洗 — 基于PMI相关性矩阵的序时账业务分类系统

子模块:
- v2/ : V2.1 程序 (PMI矩阵 + 关键词匹配 + 纠错回路)
- nn/ : V3.0 神经网络 (Pattern×Keyword 哈达玛积 + Triplet Loss)
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
        VoucherEmbeddingModel,
        VocabManager,
        TrainingDataset,
        extract_voucher_data,
        build_training_dataset,
        load_keywords_from_storage,
        save_reviewed_keywords,
        load_reviewed_keywords,
        cleanup_auto_words,
        export_training_data,
        import_training_data,
        build_clean_training_data,
        load_clean_training_data,
        export_vector_tables,
        get_training_data_info,
        validate_training_data,
        ModelTrainer,
        ModelInference,
    )
    _NN_AVAILABLE = True
except ImportError:
    _NN_AVAILABLE = False

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
    "VoucherEmbeddingModel",
    "VocabManager",
    "TrainingDataset",
    "extract_voucher_data",
    "build_training_dataset",
    "load_keywords_from_storage",
    "save_reviewed_keywords",
    "load_reviewed_keywords",
    "cleanup_auto_words",
    "export_training_data",
    "import_training_data",
    "build_clean_training_data",
    "load_clean_training_data",
    "export_vector_tables",
    "get_training_data_info",
    "validate_training_data",
    "ModelTrainer",
    "ModelInference",
]
