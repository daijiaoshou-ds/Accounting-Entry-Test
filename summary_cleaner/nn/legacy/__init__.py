# -*- coding: utf-8 -*-
"""
旧版神经网络模块（V2.0 架构）— 已归档

被 V3.0（BGE 中文模型微调）取代。本模块仅保留供排障/参考，不再被
summary_cleaner/__init__.py 引用。相对导入在同一目录内不受影响。

旧架构：Pattern × Keyword 哈达玛积 + Triplet Loss（聚类）
新架构：BGE 编码器 + 科目开关 + 分类头（CrossEntropy 分类）
"""

from .model import VoucherEmbeddingModel, TripletLoss, cosine_similarity
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
    "TripletLoss",
    "cosine_similarity",
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
