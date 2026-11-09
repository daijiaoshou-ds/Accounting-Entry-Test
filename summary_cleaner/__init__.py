# -*- coding: utf-8 -*-
"""序时账清洗 —— 4.0（单一流程：程序准备输入，模型判定，程序写回）。

子模块:
- pipeline.py  : 编排（读表 → 科目归一化 → 模型 → 硬约束 → 写回）
- v2/          : 配置（桶表/科目表）、bucket_rules（别名/校准/父级回退）、
                 列名识别、训练数据持久化
- nn/          : 模型（BGE 微调 + 科目开关 + 分类头）与推理/训练

**已删除**（4.0）：PMI 矩阵、凭证向量化、程序打分、关键词/自动词、
金额特征、纠错回路、制单人偏置。原因见 pipeline.py 顶部注释。
"""

from .v2.config import (
    BUCKET_CLARITY,
    BUCKET_REGISTRY,
    COLUMN_NAME_PATTERNS,
    DEFAULT_CLARITY,
    SUBJECT_CLARITY,
    get_bucket_names,
    load_buckets_json,
    load_subject_list,
)
from .v2.classifier import HARD_RULE_BUCKETS, JournalClassifier
from .v2.errors import EngineNotReady
from .v2.fingerprint import compute_fingerprint

__version__ = "4.0.0"

__all__ = [
    "JournalClassifier",
    "EngineNotReady",
    "HARD_RULE_BUCKETS",
    "compute_fingerprint",
    "BUCKET_REGISTRY",
    "BUCKET_CLARITY",
    "COLUMN_NAME_PATTERNS",
    "DEFAULT_CLARITY",
    "SUBJECT_CLARITY",
    "get_bucket_names",
    "load_buckets_json",
    "load_subject_list",
]

# ── NN 模块（可选依赖：需要 torch）──
try:
    from .nn import (  # noqa: F401
        DEFAULT_SKIP_BUCKETS,
        MODEL_CHOICES,
        FinanceClassifierInference,
        FinanceClassifierModel,
        FinanceClassifierTrainer,
        build_bucket_index,
        build_hash_training_data,
        build_subject_switch_index,
        extract_training_records,
        list_training_files,
        load_merged_records,
        mark_reviewed,
        merge_training_data,
        resolve_model_dir,
        split_records,
    )
    _NN_AVAILABLE = True
except Exception:  # noqa: BLE001 - 无 torch 时只影响训练/推理
    _NN_AVAILABLE = False
