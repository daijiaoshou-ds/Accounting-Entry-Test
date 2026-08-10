# -*- coding: utf-8 -*-
"""
分类模型 — BGE 编码器 + 科目开关压缩 + 分类头

架构（NN_training2.0.md）:
  摘要整句 ─→ BGE encoder ─→ [CLS] 向量 (Large:1024 / Base:768 维)
  科目开关(一级科目[方向] one-hot) ─→ nn.Linear(开关数, subject_dim=64) ─→ 64 维
  concat → Linear(hidden+64, 256) → ReLU → Dropout → Linear(256, num_buckets)
  → logits → CrossEntropy

与旧版（legacy/VoucherEmbeddingModel + Triplet Loss 聚类）完全不同:
V3.0 是分类任务（监督学习，标签 = V2.1 审核后的桶）。
"""

import torch
import torch.nn as nn


class FinanceClassifierModel(nn.Module):
    """BGE 编码器 + 科目开关压缩 + 分类头。"""

    def __init__(
        self,
        encoder: nn.Module,
        num_subjects: int,
        num_buckets: int,
        subject_dim: int = 64,
        hidden_dim: int = 256,
        dropout: float = 0.1,
    ):
        """
        Args:
            encoder: BGE AutoModel（from transformers）
            num_subjects: 科目开关数（subject_to_index 长度）
            num_buckets: 桶数（index_to_bucket 长度）
            subject_dim: 科目开关压缩维度（默认 64）
            hidden_dim: 分类头隐层（默认 256）
            dropout: 分类头 Dropout
        """
        super().__init__()
        self.encoder = encoder
        self.encoder_hidden = encoder.config.hidden_size

        self.subject_dim = subject_dim
        self.num_subjects = num_subjects
        self.num_buckets = num_buckets

        # 科目开关: one-hot [num_subjects] → 64 维稠密向量
        self.subject_linear = nn.Linear(num_subjects, subject_dim)

        # 分类头: concat(CLS, subject_vec) → 桶 logits
        self.classifier = nn.Sequential(
            nn.Linear(self.encoder_hidden + subject_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_buckets),
        )

        self._init_weights()

    def _init_weights(self):
        """分类头用较小的初始化（BGE 已预训练，头应保守起步）。"""
        for module in (self.subject_linear,):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        subject_switches: torch.Tensor,
    ) -> torch.Tensor:
        """计算桶 logits。

        Args:
            input_ids: [B, seq_len] token ids
            attention_mask: [B, seq_len]
            subject_switches: [B, num_subjects] float 0/1 one-hot 开关

        Returns:
            [B, num_buckets] logits
        """
        # BGE → CLS 池化 [B, hidden]
        outputs = self.encoder(
            input_ids=input_ids,
            attention_mask=attention_mask,
            return_dict=True,
        )
        cls_vec = outputs.last_hidden_state[:, 0, :]  # [B, hidden]

        # 科目开关压缩 [B, subject_dim]
        subject_vec = self.subject_linear(subject_switches)

        # 拼接 → 分类头
        fused = torch.cat([cls_vec, subject_vec], dim=1)
        return self.classifier(fused)  # [B, num_buckets]
