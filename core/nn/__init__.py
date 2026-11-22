# -*- coding: utf-8 -*-
"""模型推理：微调 BGE + 科目开关 + 分类头。

    text.py           摘要归一化（数字 → #）
    data.py           特征提取（整句摘要 + 科目开关向量）、训练记录提取
    model.py          FinanceClassifierModel
    model_loader.py   基础 BGE 的下载与加载（`models/` 缓存）
    embed_cache.py    摘要向量缓存（避免同一摘要重复过一遍编码器）
    inference.py      加载 4 件交付物并预测

交付物在 `_storage/`：`fine_tuned/` + `finance_classifier.pt` +
`subject_to_index.json` + `index_to_bucket.json`，四件必须一致，不一致直接报错。

**这一层不含训练**：微调循环、训练数据构建、分词可视化在 `tools/train/`。
科目名归一（`normalize_name`/`normalize_subject`）在 `accounting_domain/subject_norm.py`：
它是会计口径、四个功能共用，不只服务于模型——这里只是转发出来。
"""

from __future__ import annotations

from .data import (DEFAULT_SKIP_BUCKETS, HARD_RULE_BUCKETS,  # noqa: F401
                   build_group_summary, extract_subjects_from_group,
                   extract_training_records, extract_voucher_features)
from .model import FinanceClassifierModel  # noqa: F401
from accounting_domain.subject_norm import normalize_name, normalize_subject  # noqa: F401
from .text import normalize_summary  # noqa: F401

__all__ = [
    "DEFAULT_SKIP_BUCKETS",
    "HARD_RULE_BUCKETS",
    "build_group_summary",
    "extract_subjects_from_group",
    "extract_training_records",
    "extract_voucher_features",
    "normalize_name",
    "normalize_subject",
    "normalize_summary",
    "FinanceClassifierModel",
]

# 推理（需要 torch，缺依赖时这一层整体不可用，但 core.classify 仍能工作）
try:
    from .inference import FinanceClassifierInference  # noqa: F401
    from .model_loader import (MODEL_CHOICES, get_model_cache_dir,  # noqa: F401
                               is_model_complete, load_encoder_tokenizer,
                               resolve_model_dir)
    __all__.extend(["FinanceClassifierInference", "MODEL_CHOICES",
                    "get_model_cache_dir", "is_model_complete",
                    "load_encoder_tokenizer", "resolve_model_dir"])
except ImportError:      # 没装 torch/transformers
    pass
