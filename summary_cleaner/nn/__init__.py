# -*- coding: utf-8 -*-
"""
神经网络模块（V3.0）— 微调 BGE 中文 Embedding 模型做凭证分类

V3.0 输入是「整句摘要 + 科目开关向量」，输出桶概率（CrossEntropy 分类）。
（旧版 Pattern×Keyword + Triplet Loss 聚类已删除，git 历史 bf15cc3 可追溯）

数据流:
  1. V2.1 classify() Step 9 → training/{hash}.json 自动生成（records_v1 格式）
  2. 人工审核（改 reviewed: true）→ merge_training_data() → training_data.json
  3. FinanceClassifierTrainer.train() → 微调 BGE → 4 件交付物
  4. FinanceClassifierInference.predict() → 桶预测

模块结构:
  - data.py         : 训练记录提取（整句摘要 + 科目开关，纯 pandas）
  - training_data.py: hash 分离/审核/合并/索引构建（纯 pandas）
  - model_loader.py : BGE 模型下载（ModelScope 优先 / HF 回退）+ 加载
  - model.py        : FinanceClassifierModel（BGE + 科目 Linear + 分类头）
  - trainer.py      : 微调循环（CE + AMP + gradient checkpointing + 三策略）
  - inference.py    : 推理预测（加载 4 件交付物）
"""

# 数据管线（纯 pandas，无 torch 依赖 —— V2.1 无 torch 也能导出训练数据）
from .data import (
    HARD_RULE_BUCKETS,
    DEFAULT_SKIP_BUCKETS,
    extract_subjects_from_group,
    extract_training_records,
)
from .training_data import (
    RECORDS_FORMAT,
    build_hash_training_data,
    merge_training_data,
    load_merged_records,
    list_training_files,
    mark_reviewed,
    build_subject_switch_index,
    build_bucket_index,
    split_records,
)

# 模型层（需要 torch/transformers，可选依赖）
try:
    from .model_loader import (
        MODEL_CHOICES,
        get_model_cache_dir,
        is_model_complete,
        resolve_model_dir,
        load_encoder_tokenizer,
    )
    from .model import FinanceClassifierModel
    from .trainer import FinanceClassifierTrainer
    from .inference import FinanceClassifierInference
    _MODEL_LAYER_AVAILABLE = True
except ImportError:
    _MODEL_LAYER_AVAILABLE = False  # torch/transformers 缺失时仅数据管线可用

__all__ = [
    # 数据管线
    "HARD_RULE_BUCKETS",
    "DEFAULT_SKIP_BUCKETS",
    "extract_subjects_from_group",
    "extract_training_records",
    "RECORDS_FORMAT",
    "build_hash_training_data",
    "merge_training_data",
    "load_merged_records",
    "list_training_files",
    "mark_reviewed",
    "build_subject_switch_index",
    "build_bucket_index",
    "split_records",
    # 模型层
    "MODEL_CHOICES",
    "get_model_cache_dir",
    "is_model_complete",
    "resolve_model_dir",
    "load_encoder_tokenizer",
    "FinanceClassifierModel",
    "FinanceClassifierTrainer",
    "FinanceClassifierInference",
]
