# -*- coding: utf-8 -*-
"""
推理引擎 — 加载训练好的模型进行凭证分类

用法:
    inference = ModelInference(model_dir="path/to/nn_models/")
    result = inference.predict_single(patterns, keywords)
    results = inference.predict_batch(vouchers)

分类逻辑:
    1. 计算凭证向量: Σ pattern_emb[p] ⊙ keyword_emb[k]
    2. L2 归一化
    3. 计算与各桶中心的余弦相似度
    4. 取最高相似度的桶 → 分类结果
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch

from .model import VoucherEmbeddingModel, cosine_similarity
from .vocab import VocabManager


class ModelInference:
    """加载训练好的模型，进行凭证分类预测。

    独立于训练流程——只需要 model.pt + vocab.json 即可运行。
    不依赖 PyTorch 的 optimizer state，适合部署环境。
    """

    def __init__(self, model_dir: str = None, device: str = None):
        """
        Args:
            model_dir: 模型目录（包含 best_model.pt + vocab.json）
            device: 推理设备（默认自动选择 CPU/GPU）
        """
        self.model_dir = Path(model_dir) if model_dir else None
        self.model: Optional[VoucherEmbeddingModel] = None
        self.vocab: Optional[VocabManager] = None
        self._bucket_names: List[str] = []
        self._loaded = False

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)

        if model_dir:
            self.load(model_dir)

    # ------------------------------------------------------------------
    # 加载模型
    # ------------------------------------------------------------------

    def load(self, model_dir: str):
        """加载模型和词表。

        Args:
            model_dir: 包含 best_model.pt 和 vocab.json 的目录路径
        """
        model_dir = Path(model_dir)

        # 1. 加载词表
        vocab_path = model_dir / "vocab.json"
        if not vocab_path.exists():
            raise FileNotFoundError(f"词表文件不存在: {vocab_path}")
        self.vocab = VocabManager.load(vocab_path)
        print(f"[OK] 已加载词表: {self.vocab}")

        # 2. 加载模型
        model_path = model_dir / "best_model.pt"
        if not model_path.exists():
            # 尝试其他命名
            candidates = sorted(model_dir.glob("*.pt"))
            if candidates:
                model_path = candidates[0]
            else:
                raise FileNotFoundError(f"模型文件不存在: {model_dir}")

        checkpoint = torch.load(model_path, map_location=self.device)

        # 从 checkpoint 恢复模型参数
        dim = checkpoint.get("dim", 128)
        num_patterns = checkpoint.get("num_patterns", self.vocab.num_patterns)
        num_keywords = checkpoint.get("num_keywords", self.vocab.num_keywords)

        # 如果词表比 checkpoint 大（增量添加过），用词表的大小
        num_patterns = max(num_patterns, self.vocab.num_patterns)
        num_keywords = max(num_keywords, self.vocab.num_keywords)

        self.model = VoucherEmbeddingModel(num_patterns, num_keywords, dim)

        # 处理 checkpoint 中 embedding 与当前模型大小不一致的情况
        state_dict = checkpoint["model_state_dict"]
        if state_dict["pattern_emb.weight"].shape[0] != num_patterns:
            print(f"  [WARN] Pattern 数量不匹配，扩展 embedding 层...")
            # 用 checkpoint 的权重填充，新增部分随机初始化
            old_weight = state_dict["pattern_emb.weight"]
            new_weight = self.model.pattern_emb.weight.data
            new_weight[:old_weight.shape[0]] = old_weight
            state_dict["pattern_emb.weight"] = new_weight

        if state_dict["keyword_emb.weight"].shape[0] != num_keywords:
            old_weight = state_dict["keyword_emb.weight"]
            new_weight = self.model.keyword_emb.weight.data
            new_weight[:old_weight.shape[0]] = old_weight
            state_dict["keyword_emb.weight"] = new_weight

        self.model.load_state_dict(state_dict, strict=False)
        self.model.to(self.device)
        self.model.eval()

        # 3. 提取桶名列表
        if hasattr(self.model, "_bucket_names_list"):
            self._bucket_names = self.model._bucket_names_list
        else:
            # 从桶中心 buffer 推断
            centroids = self.model.bucket_centroids
            if centroids is not None:
                self._bucket_names = [f"bucket_{i}" for i in range(len(centroids))]

        self._loaded = True
        print(f"[OK] 已加载模型: {model_path}")
        print(f"  维度={dim}, Patterns={num_patterns}, Keywords={num_keywords}")
        print(f"  桶数={len(self._bucket_names)}, 设备={self.device}")

    # ------------------------------------------------------------------
    # 预测
    # ------------------------------------------------------------------

    def predict_single(
        self,
        patterns: List[str],
        keywords: List[str],
    ) -> Dict:
        """对单张凭证进行分类。

        Args:
            patterns: Pattern 字符串列表，如 ["制造费用|借", "银行存款|贷"]
            keywords: Keyword 字符串列表，如 ["办公费", "A4纸"]

        Returns:
            {
                "bucket": str,           # 预测桶名
                "confidence": float,     # 置信度 (0~1)
                "top3": [(bucket, sim), ...],  # Top-3 候选
                "voucher_vector": np.array,    # 凭证向量 (用于分析)
            }
        """
        if not self._loaded:
            raise RuntimeError("模型未加载，请先调用 load()")

        # 转换 Pattern → ID
        pattern_ids = []
        for p in patterns:
            pid = self.vocab.get_pattern_id(p)
            if pid is not None:
                pattern_ids.append(pid)

        if not pattern_ids:
            return {
                "bucket": "未分类",
                "confidence": 0.0,
                "top3": [],
                "voucher_vector": None,
            }

        # 转换 Keyword → ID（只取词表中存在的）
        keyword_ids = []
        for k in keywords:
            kid = self.vocab.get_keyword_id(k)
            if kid is not None:
                keyword_ids.append(kid)

        # 计算凭证向量
        p_tensor = torch.tensor(pattern_ids, dtype=torch.long, device=self.device)
        k_tensor = torch.tensor(keyword_ids, dtype=torch.long, device=self.device)

        with torch.no_grad():
            vec = self.model.compute_voucher_vector(p_tensor, k_tensor)
            vec = torch.nn.functional.normalize(vec, p=2, dim=0)

            # 与各桶中心计算相似度
            centroids = self.model.bucket_centroids
            if centroids is None:
                raise RuntimeError("模型缺少桶中心向量，请重新训练")

            centroids = centroids.to(self.device)
            centroids = torch.nn.functional.normalize(centroids, p=2, dim=1)

            sims = vec @ centroids.T  # [num_buckets]

            # Top-3
            top3_indices = sims.argsort(descending=True)[:3]
            top3 = [
                (self._bucket_names[i], round(sims[i].item(), 4))
                for i in top3_indices
            ]

            best_bucket = top3[0][0]
            confidence = top3[0][1]

        return {
            "bucket": best_bucket,
            "confidence": confidence,
            "top3": top3,
            "voucher_vector": vec.cpu().numpy(),
        }

    def predict_batch(
        self,
        vouchers: List[Dict],
    ) -> List[Dict]:
        """批量分类多张凭证。

        Args:
            vouchers: List[dict], 每个 dict:
                - "voucher_id": str
                - "patterns": List[str]
                - "keywords": List[str]

        Returns:
            List[dict], 每个 dict 在输入基础上新增:
                - "predicted_bucket": str
                - "confidence": float
        """
        results = []
        for v in vouchers:
            pred = self.predict_single(v["patterns"], v["keywords"])
            results.append({
                **v,
                "predicted_bucket": pred["bucket"],
                "confidence": pred["confidence"],
                "top3": pred["top3"],
            })
        return results

    # ------------------------------------------------------------------
    # 分析工具
    # ------------------------------------------------------------------

    def get_bucket_centroids(self) -> Dict[str, np.ndarray]:
        """返回各桶的中心向量（用于可视化分析）。"""
        if not self._loaded or self.model.bucket_centroids is None:
            return {}

        centroids = self.model.bucket_centroids.cpu().numpy()
        return {
            name: centroids[i]
            for i, name in enumerate(self._bucket_names)
        }

    def find_similar_keywords(self, keyword: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """查找与指定关键词最相似的其他关键词（用于分析词向量的语义）。

        Args:
            keyword: 查询关键词
            top_k: 返回前 K 个

        Returns:
            [(keyword, similarity), ...]
        """
        kid = self.vocab.get_keyword_id(keyword)
        if kid is None:
            return []

        vec = self.model.keyword_emb.weight[kid]
        vec = torch.nn.functional.normalize(vec, p=2, dim=0)

        all_vecs = torch.nn.functional.normalize(
            self.model.keyword_emb.weight, p=2, dim=1
        )
        sims = all_vecs @ vec  # [num_keywords]
        top_indices = sims.argsort(descending=True)[1:top_k+1]  # 跳过自己

        return [
            (self.vocab._id_to_keyword[i.item()], round(sims[i].item(), 4))
            for i in top_indices
        ]

    def find_similar_patterns(self, pattern: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """查找与指定 Pattern 最相似的其他 Pattern。

        Args:
            pattern: 查询 Pattern，如 "制造费用|借"
            top_k: 返回前 K 个

        Returns:
            [(pattern, similarity), ...]
        """
        pid = self.vocab.get_pattern_id(pattern)
        if pid is None:
            return []

        vec = self.model.pattern_emb.weight[pid]
        vec = torch.nn.functional.normalize(vec, p=2, dim=0)

        all_vecs = torch.nn.functional.normalize(
            self.model.pattern_emb.weight, p=2, dim=1
        )
        sims = all_vecs @ vec
        top_indices = sims.argsort(descending=True)[1:top_k+1]

        return [
            (self.vocab._id_to_pattern[i.item()], round(sims[i].item(), 4))
            for i in top_indices
        ]

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def bucket_names(self) -> List[str]:
        return self._bucket_names

    @property
    def model_info(self) -> dict:
        """返回模型信息摘要。"""
        if not self._loaded:
            return {"loaded": False}
        return {
            "loaded": True,
            "dim": self.model.dim,
            "num_patterns": self.model.num_patterns,
            "num_keywords": self.model.num_keywords,
            "num_buckets": len(self._bucket_names),
            "buckets": self._bucket_names,
            "device": str(self.device),
            "model_dir": str(self.model_dir),
        }
