# -*- coding: utf-8 -*-
"""
摘要清洗2.0 — 基于PMI相关性矩阵的序时账业务分类系统

理论依据：summary-cleaning2.0/theory.md
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
]
