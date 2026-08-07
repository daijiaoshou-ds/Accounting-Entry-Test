# -*- coding: utf-8 -*-
"""
神经网络模型 — 基于 Pattern × Keyword 哈达玛积的凭证向量化

数学原理：
  给定一张凭证，它包含:
  - Pattern 集合: {p1, p2, ...} （"制造费用借", "银行存款贷"...）
  - Keyword 集合: {k1, k2, ...} （"办公费", "A4纸"...）

  凭证向量 = Σ_i Σ_j V(p_i) ⊙ V(k_j)

  其中 ⊙ 是哈达玛积（对应位相乘）。
  这个设计的核心洞察：同一个 keyword"办公费"与不同的 pattern 相乘，
  产生不同的推力向量 → 上下文感知。

模型规模 (128维):
  - Pattern Embedding: num_patterns × 128
  - Keyword Embedding: num_keywords × 128
  - 总参数量 ≈ 几十万，在 RTX 4060 上训练仅需几秒/轮
"""

import torch
import torch.nn as nn


class VoucherEmbeddingModel(nn.Module):
    """凭证嵌入模型。

    Embedding 表:
    - pattern_emb: [num_patterns, dim]  — 每个 (科目, 方向) 的向量
    - keyword_emb: [num_keywords, dim]  — 每个关键词的向量

    前向传播:
    1. 对凭证的每个 (pattern, keyword) 配对做哈达玛积
    2. 所有配对求和 → 凭证向量
    """

    def __init__(self, num_patterns: int, num_keywords: int, dim: int = 128):
        """
        Args:
            num_patterns: Pattern 总数（如 500 个）
            num_keywords: Keyword 总数（如 3000 个）
            dim: 向量维度，默认 128
        """
        super().__init__()
        self.dim = dim
        self.num_patterns = num_patterns
        self.num_keywords = num_keywords

        # Embedding 层
        self.pattern_emb = nn.Embedding(num_patterns, dim)
        self.keyword_emb = nn.Embedding(num_keywords, dim)

        # 桶中心向量（训练后计算，推理时用于分类）
        self.register_buffer(
            "bucket_centroids", None  # [num_buckets, dim]，训练后赋值
        )
        self.register_buffer(
            "bucket_names", None  # List[str]，桶名列表
        )

        self._init_weights()

    def _init_weights(self):
        """统一初始化: [-0.5/√dim, 0.5/√dim]。

        为什么不用默认的 N(0,1)？
        哈达玛积会让两个均匀分布的乘积分布在接近 0 的窄区间。
        用较小的初始化范围让初始凭证向量集中在原点附近，
        训练时有更大的调整空间。
        """
        bound = 0.5 / (self.dim ** 0.5)
        nn.init.uniform_(self.pattern_emb.weight, -bound, bound)
        nn.init.uniform_(self.keyword_emb.weight, -bound, bound)

    def compute_voucher_vector(
        self,
        pattern_ids: torch.Tensor,
        keyword_ids: torch.Tensor,
    ) -> torch.Tensor:
        """计算单张凭证的向量。

        一张凭证 = 一个完整的科目组合 (pattern) + 一组关键词 (keywords)。

        Args:
            pattern_ids: [num_patterns] 通常只有 1 个（完整科目组合的 ID）
            keyword_ids: [num_keywords] 关键词 ID 列表

        Returns:
            [dim] 凭证向量

        数学:
            pattern 的嵌入向量分别与每个 keyword 做哈达玛积后求和：

            voucher_vec = Σ_k (pattern_emb ⊙ keyword_emb[k])

            如果有多个 pattern（极少情况），先求和：
            voucher_vec = Σ_p Σ_k (pattern_emb[p] ⊙ keyword_emb[k])
                        = (Σ_p pattern_emb[p]) ⊙ (Σ_k keyword_emb[k])
        """
        if len(pattern_ids) == 0:
            return torch.zeros(self.dim)

        p_sum = self.pattern_emb(pattern_ids).sum(dim=0)  # [dim]

        if len(keyword_ids) == 0:
            return p_sum  # 无关键词时只用 pattern 信号

        k_sum = self.keyword_emb(keyword_ids).sum(dim=0)  # [dim]
        return p_sum * k_sum  # [dim]

    def forward(self, batch: list) -> torch.Tensor:
        """批量计算凭证向量。

        Args:
            batch: list of dicts, 每个 dict 包含:
                - 'pattern_ids': List[int]
                - 'keyword_ids': List[int]

        Returns:
            [batch_size, dim] 凭证向量矩阵
        """
        vectors = []
        for item in batch:
            p_ids = torch.tensor(item["pattern_ids"], dtype=torch.long)
            k_ids = torch.tensor(item["keyword_ids"], dtype=torch.long)
            vec = self.compute_voucher_vector(p_ids, k_ids)
            vectors.append(vec)
        return torch.stack(vectors)

    def compute_bucket_centroids(
        self,
        dataset: "TrainingDataset",
        bucket_to_idx: dict,
    ):
        """计算所有桶的中心向量（推理时用于最近邻分类）。

        桶中心 = 桶内所有凭证向量的均值。

        Args:
            dataset: 训练数据集
            bucket_to_idx: {bucket_name: index}
        """
        num_buckets = len(bucket_to_idx)
        centroids = torch.zeros(num_buckets, self.dim)
        counts = torch.zeros(num_buckets)

        with torch.no_grad():
            for i in range(len(dataset)):
                item = dataset[i]
                bucket = item["bucket"]
                if bucket not in bucket_to_idx:
                    continue
                idx = bucket_to_idx[bucket]
                vec = self.compute_voucher_vector(
                    torch.tensor(item["pattern_ids"], dtype=torch.long),
                    torch.tensor(item["keyword_ids"], dtype=torch.long),
                )
                centroids[idx] += vec
                counts[idx] += 1

        # 避免除零
        counts = counts.clamp(min=1)
        centroids = centroids / counts.unsqueeze(1)

        # 注册为 buffer（随模型一起保存）
        self.register_buffer("bucket_centroids", centroids)
        self.register_buffer(
            "bucket_names",
            torch.tensor(
                [""] * num_buckets,
                dtype=torch.float32,  # 仅用于占位
            ),
        )
        # 用列表存储桶名（非 tensor）
        self._bucket_names_list = sorted(bucket_to_idx.keys())

    def get_pattern_vector(self, pattern_id: int) -> torch.Tensor:
        """获取单个 Pattern 的嵌入向量（用于调试/分析）。"""
        return self.pattern_emb.weight[pattern_id].detach().clone()

    def get_keyword_vector(self, keyword_id: int) -> torch.Tensor:
        """获取单个 Keyword 的嵌入向量（用于调试/分析）。"""
        return self.keyword_emb.weight[keyword_id].detach().clone()


class TripletLoss(nn.Module):
    """对比学习三元组损失 — Batch Hard Mining。

    对 batch 中每个凭证（anchor）：
    - 正例（positive）= batch 内同类凭证中最不相似的那个（最难正例）
    - 负例（negative）= batch 内异类凭证中最相似的那个（最难负例）

    损失：L = max(0, d(anchor, positive) - d(anchor, negative) + margin)
    其中 d = 1 - cosine_similarity（余弦距离）。

    目标：同类凭证比异类凭证至少近 margin。
    """

    def __init__(self, margin: float = 0.5):
        """
        Args:
            margin: 余弦距离边界值。anchor 与 positive 的距离必须比
                    anchor 与 negative 的距离至少小 margin。
                    推荐范围: 0.3 ~ 0.5
        """
        super().__init__()
        self.margin = margin

    def forward(
        self,
        embeddings: torch.Tensor,
        labels: torch.Tensor,
    ) -> torch.Tensor:
        """
        Args:
            embeddings: [batch_size, dim] L2 归一化后的凭证向量
            labels: [batch_size] 桶标签（整数）

        Returns:
            标量 loss
        """
        batch_size = embeddings.size(0)

        # 归一化（确保余弦距离计算稳定）
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

        # 余弦相似度矩阵 [batch_size, batch_size]
        sim_matrix = embeddings @ embeddings.T

        # 余弦距离 = 1 - 余弦相似度
        dist_matrix = 1.0 - sim_matrix

        total_loss = 0.0
        valid_anchors = 0

        for i in range(batch_size):
            # 同类 mask（排除自己）
            pos_mask = (labels == labels[i]) & (
                torch.arange(batch_size, device=labels.device) != i
            )
            # 异类 mask
            neg_mask = labels != labels[i]

            if not pos_mask.any() or not neg_mask.any():
                continue

            # 最难正例 = 同类中距离最大的（最不像的同类）
            hardest_pos_dist = dist_matrix[i][pos_mask].max()
            # 最难负例 = 异类中距离最小的（最像的异类）
            hardest_neg_dist = dist_matrix[i][neg_mask].min()

            # Triplet Loss
            loss = torch.clamp(
                hardest_pos_dist - hardest_neg_dist + self.margin, min=0.0
            )
            total_loss += loss
            valid_anchors += 1

        if valid_anchors == 0:
            return torch.tensor(0.0, requires_grad=True)

        return total_loss / valid_anchors


# ============================================================================
# 工具函数
# ============================================================================

def cosine_similarity(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """计算两个向量的余弦相似度。

    Args:
        a: [dim] 或 [batch, dim]
        b: [dim] 或 [batch, dim]

    Returns:
        标量或 [batch]
    """
    a = torch.nn.functional.normalize(a, p=2, dim=-1)
    b = torch.nn.functional.normalize(b, p=2, dim=-1)
    return (a * b).sum(dim=-1)
