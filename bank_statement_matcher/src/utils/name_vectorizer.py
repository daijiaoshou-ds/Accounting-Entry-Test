"""公司名称向量化：TF-IDF + Bigram + L2 归一化"""
import math
import time
from typing import List, Dict, Set
import numpy as np
from bank_statement_matcher.src.utils.performance_logger import perf_logger


# 无效词列表（用于清洗公司名称）
# 按长度从长到短排序，避免部分匹配问题（如先匹配"公司"导致"有限公司"失效）
STOP_WORDS = [
    "股份有限公司", "股份公司", "有限公司", "有限责任", "股份有限",
    "公司", "有限", "股份", "集团", "责任",
    "科技", "技术", "银行",
]


class NameVectorizer:
    """
    公司名称向量化器
    流程：收集名称 → 无效词清洗 → Bigram 分词 → TF-IDF → L2 归一化
    """

    def __init__(self):
        self.vocab: List[str] = []           # bigram 词汇表（有序）
        self.idf: Dict[str, float] = {}      # bigram -> idf
        self.vectors: Dict[str, np.ndarray] = {}  # 原始名称 -> 归一化向量
        self._vocab_index: Dict[str, int] = {}    # bigram -> index（缓存）

    # ---------- 内部工具方法 ----------

    @staticmethod
    def _clean(name: str) -> str:
        """无效词清洗"""
        if not name:
            return ""
        cleaned = str(name).strip()
        for sw in STOP_WORDS:
            cleaned = cleaned.replace(sw, "")
        return cleaned.strip()

    @staticmethod
    def _bigrams(name: str) -> List[str]:
        """
        Bigram 分词：连续2个字符作为一个特征词
        字符长度 ≤ 2 的不分词，返回空列表
        """
        if len(name) <= 2:
            return []
        return [name[i] + name[i + 1] for i in range(len(name) - 1)]

    # ---------- 训练 ----------

    def fit(self, names: List[str]) -> None:
        """
        训练：计算所有名称的 TF-IDF 向量并 L2 归一化
        names: 所有公司名称列表（会自动去重）
        """
        unique_names = list(set(str(n).strip() for n in names if n))
        if not unique_names:
            return

        t0 = time.time()
        # 1. 清洗 + Bigram
        cleaned_names = [self._clean(n) for n in unique_names]
        name_bigrams: List[List[str]] = []
        all_bigrams: Set[str] = set()

        for cn in cleaned_names:
            bgs = self._bigrams(cn)
            name_bigrams.append(bgs)
            all_bigrams.update(bgs)

        if not all_bigrams:
            # 所有名称清洗后都太短，无法分词
            return

        # 2. 建立词汇表
        self.vocab = sorted(all_bigrams)
        self._vocab_index = {bg: i for i, bg in enumerate(self.vocab)}  # 缓存
        vocab_index = self._vocab_index
        N = len(cleaned_names)

        # 3. 计算 IDF
        for bg in self.vocab:
            df = sum(1 for bgs in name_bigrams if bg in bgs)
            self.idf[bg] = math.log((1 + N) / (1 + df)) + 1

        # 4. 计算 TF-IDF 向量并 L2 归一化
        for i, orig_name in enumerate(unique_names):
            vec = np.zeros(len(self.vocab))
            for bg in name_bigrams[i]:
                idx = vocab_index[bg]
                vec[idx] = self.idf[bg]  # TF=1（bigram 不重复）

            # L2 归一化
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            self.vectors[orig_name] = vec
        dt = time.time() - t0
        if dt > 1.0:
            perf_logger.info(f"    向量化耗时: {dt:.2f}s (名称数={len(unique_names)}, 词汇表={len(self.vocab)})")

    # ---------- 查询 ----------

    def get_vector(self, name: str) -> np.ndarray:
        """获取指定名称的向量（未训练过的名称会实时计算）"""
        if not name:
            return np.zeros(len(self.vocab))

        cleaned = self._clean(name)
        bgs = self._bigrams(cleaned)

        if not bgs or not self.vocab:
            return np.zeros(len(self.vocab))

        vec = np.zeros(len(self.vocab))
        for bg in bgs:
            idx = self._vocab_index.get(bg)
            if idx is not None:
                vec[idx] = self.idf[bg]

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def cosine_similarity(self, name1: str, name2: str) -> float:
        """
        计算两个公司名称的余弦相似度
        快速通道：
          1. 清洗后相等 → 1.0
          2. 清洗后长度≤2 且互相包含 → 1.0，否则 0.0
        """
        if not name1 or not name2:
            return 0.0

        c1 = self._clean(name1)
        c2 = self._clean(name2)

        # 快速通道 1：清洗后完全相等
        if c1 == c2:
            return 1.0

        # 快速通道 2：清洗后长度 ≤ 2
        if len(c1) <= 2 or len(c2) <= 2:
            return 1.0 if (c1 in c2 or c2 in c1) else 0.0

        # 正常计算余弦相似度
        v1 = self.get_vector(name1)
        v2 = self.get_vector(name2)

        if len(v1) == 0 or len(v2) == 0:
            return 0.0

        return float(np.dot(v1, v2))

    def batch_similarity(self, query_name: str, candidate_names: List[str]) -> List[float]:
        """计算 query_name 与多个 candidate_names 的相似度，返回列表"""
        return [self.cosine_similarity(query_name, cn) for cn in candidate_names]
