# -*- coding: utf-8 -*-
"""
神经网络凭证聚类模块 (V3.0)

基于 Pattern × Keyword 哈达玛积 (Hadamard Product) 交互的凭证向量化模型，
使用 Triplet Loss 对比学习训练，实现上下文感知的凭证聚类。

核心思想：
- 同一个关键词（如"办公费"）与不同的 Pattern（如"制造费用"vs"管理费用"）
  做哈达玛积，会产生不同方向的推力向量 → 解决关键词歧义问题。
- 训练阶段由开发者把控数据质量，模型训练好后直接交付用户使用。

数据流：
  1. V2.1 classify() → classified_df + _storage/auto_words_tier1.json
  2. load_keywords_from_storage() → 加载自动词 + 手工关键词
  3. 开发者审核关键词（增/删）→ save_reviewed_keywords()
  4. extract_voucher_data() → 逐凭证提取 patterns + keywords
  5. build_training_dataset() → TrainingDataset
  6. ModelTrainer.train() → 训练模型
  7. ModelInference.predict_single() → 分类预测

模块结构：
- model.py    : VoucherEmbeddingModel (Embedding + Hadamard 积)
- vocab.py    : Pattern/Keyword 词表管理 (string ↔ id)
- data.py     : 数据提取/构建 + 关键词审核 + Excel备选路径
- trainer.py  : 训练循环 + checkpoint + 日志
- inference.py: 推理预测 (加载模型 → 分类)
"""

from .model import VoucherEmbeddingModel
from .vocab import VocabManager
from .data import (
    TrainingDataset,
    extract_voucher_data,
    build_training_dataset,
    load_keywords_from_storage,
    save_reviewed_keywords,
    load_reviewed_keywords,
    cleanup_auto_words,
    export_training_data,
    import_training_data,
)
from .trainer import ModelTrainer
from .inference import ModelInference
from .training_data import (
    build_hash_training_data,
    merge_training_data,
    load_merged_training_data,
    list_training_files,
    mark_reviewed,
    export_vector_tables,
)

__all__ = [
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
    "build_hash_training_data",
    "merge_training_data",
    "load_merged_training_data",
    "list_training_files",
    "mark_reviewed",
    "export_vector_tables",
    "ModelTrainer",
    "ModelInference",
]
