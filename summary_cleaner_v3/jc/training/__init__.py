# -*- coding: utf-8 -*-
"""训练模块（**只有开发者版需要，发布时整个剔除**）

## 为什么训练在 3.0 里变了

旧版（2.x）的训练数据标签来自**程序的打分结果**（"程序原生桶"）。
3.0 删掉了程序打分，所以标签来源改成：

    **模型判断 → AI 复核（含改判）→ 复核后的结果作为训练标签**

这比旧版更可靠：旧版是"程序自动导出 → 无人看过就落盘"，
3.0 是"每一条都经过 AI 复核才进训练集"。

## 目录约定（见 jc/config.py）

    assets/model/          推理读这里，训练也写这里（best 落盘，训完即生效）
    _dev/training/         训练数据：金标准 + 未审 + 备份   ← 发布剔除
    _dev/models/           BGE 基座缓存（约 1.6GB）        ← 发布剔除
    _dev/backups/          上一版权重备份                  ← 发布剔除

## 三条容易被忽略的纪律（来自技术报告的踩坑记录）

1. **记录级 80/20 切分**：先切记录再按 count 展开。旧版先展开再切，
   同一记录的展开样本同时进训练/验证集，val_acc 虚高（数据泄漏）。
2. **早停必须开**：预训练模型 1–3 轮即收敛，之后编码器继续偏离预训练最优区，
   train_loss 降而 val_acc 崩（在背训练数据）。
3. **编码器 LR 1e-5**：2e-5 会在第 1–2 轮后崩盘（v2 实测 -8.3 个点）。
"""

from .data import (  # noqa: F401
    extract_subjects_from_group,
    build_group_summary,
    extract_training_records,
    HARD_RULE_BUCKETS,
    DEFAULT_SKIP_BUCKETS,
)
from .training_data import (  # noqa: F401
    build_hash_training_data,
    merge_training_data,
    load_merged_records,
    list_training_files,
    mark_reviewed,
    build_subject_switch_index,
    build_bucket_index,
    split_records,
    compute_review_confidence,
    generate_ai_review_guide,
    RECORDS_FORMAT,
    MAX_COUNT_EXPAND,
)
from .model_loader import (  # noqa: F401
    MODEL_CHOICES,
    resolve_model_dir,
    load_encoder_tokenizer,
    is_model_complete,
    get_model_cache_dir,
)
from .train_state import (  # noqa: F401
    get_train_state,
)

__all__ = [
    # 数据抽取
    "extract_subjects_from_group", "build_group_summary",
    "extract_training_records", "HARD_RULE_BUCKETS", "DEFAULT_SKIP_BUCKETS",
    # 数据管线
    "build_hash_training_data", "merge_training_data", "load_merged_records",
    "list_training_files", "mark_reviewed", "build_subject_switch_index",
    "build_bucket_index", "split_records", "compute_review_confidence",
    "generate_ai_review_guide", "RECORDS_FORMAT", "MAX_COUNT_EXPAND",
    # 基座模型
    "MODEL_CHOICES", "resolve_model_dir", "load_encoder_tokenizer",
    "is_model_complete", "get_model_cache_dir",
    # 训练状态
    "get_train_state",
    # 训练器（惰性导入，避免 `import jc.training` 就拉 torch 训练栈）
    "FinanceClassifierTrainer", "FinanceDataset",
]


def __getattr__(name):
    """惰性导入训练器（它依赖 torch 训练栈，比较重）。"""
    if name in ("FinanceClassifierTrainer", "FinanceDataset"):
        from .trainer import FinanceClassifierTrainer, FinanceDataset
        return {"FinanceClassifierTrainer": FinanceClassifierTrainer,
                "FinanceDataset": FinanceDataset}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
