# -*- coding: utf-8 -*-
"""
摘要清洗2.0 — 基于PMI相关性矩阵的序时账业务分类系统

理论依据：docs/theory.md
"""

__version__ = "2.0.0"

from .config import (
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
)
from .matcher import PurePythonAC, KeywordMatcher
from .engine import PMIMatrix, VoucherVectorizer, CorrelationPropagator, Scorer
from .persistence import GlobalCounters
from .classifier import JournalClassifier
from .memory_learner import (
    AmountProfiler, WordFeatureLearner, pmi_to_auto_score,
    LAMBDA_A, LAMBDA_AUTO,
    TIER1_COUNT_THRESHOLD, DISCARD_SESSION_THRESHOLD,
)
from .correction import CorrectionManager
from .config import LAMBDA_RANK, EMA_ALPHA

__all__ = [
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
]
