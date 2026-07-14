# -*- coding: utf-8 -*-
"""
核心算法引擎：PMI相关性矩阵、凭证向量化、相关性传播、评分器

理论依据：docs/theory.md 第1-4节、第6节
"""

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import SUBJECT_CLARITY, DEFAULT_CLARITY


# ============================================================================
# PMI 相关性矩阵
# ============================================================================

class PMIMatrix:
    """点间互信息 (Pointwise Mutual Information) 相关性矩阵 R。

    矩阵形式：行和列都是一级科目，值 = PMI(科目A, 科目B)
    PMI(A,B) = log( N * count_AB / (count_A * count_B) )
    截断负值至0；共现=0时 PMI=0。
    """

    def __init__(self):
        self.subjects: List[str] = []
        self.matrix: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # 工厂方法
    # ------------------------------------------------------------------

    @classmethod
    def from_vouchers(cls, df: pd.DataFrame,
                      voucher_col: str,
                      subject_col: str) -> "PMIMatrix":
        """从凭证数据构建 PMI 矩阵。

        Args:
            df: 序时账 DataFrame
            voucher_col: 凭证号列名
            subject_col: 一级科目列名
        """
        instance = cls()

        # 统计：每张凭证包含哪些科目
        voucher_subjects: Dict[str, set] = {}
        for vid, group in df.groupby(voucher_col):
            subs = set(group[subject_col].dropna().astype(str))
            # 过滤空字符串
            subs = {s.strip() for s in subs if s.strip()}
            if subs:
                voucher_subjects[str(vid)] = subs

        if not voucher_subjects:
            instance.subjects = []
            instance.matrix = pd.DataFrame()
            return instance

        all_subjects = sorted(set().union(*voucher_subjects.values()))
        instance.subjects = all_subjects
        n_subjects = len(all_subjects)
        subject_idx = {s: i for i, s in enumerate(all_subjects)}
        N = len(voucher_subjects)

        # 计数
        count_A = np.zeros(n_subjects, dtype=np.float64)
        count_AB = np.zeros((n_subjects, n_subjects), dtype=np.float64)

        for subs in voucher_subjects.values():
            sub_list = list(subs)
            for s in sub_list:
                idx = subject_idx.get(s)
                if idx is not None:
                    count_A[idx] += 1
            # 共现计数（无向对，A ≤ B）
            for i in range(len(sub_list)):
                a_idx = subject_idx.get(sub_list[i])
                if a_idx is None:
                    continue
                for j in range(i, len(sub_list)):
                    b_idx = subject_idx.get(sub_list[j])
                    if b_idx is None:
                        continue
                    count_AB[a_idx, b_idx] += 1
                    if a_idx != b_idx:
                        count_AB[b_idx, a_idx] += 1

        # 计算 PMI
        matrix = np.zeros((n_subjects, n_subjects), dtype=np.float64)
        for i in range(n_subjects):
            for j in range(n_subjects):
                if i == j:
                    matrix[i, j] = 1.0  # 自身相关性固定为1
                    continue
                if count_AB[i, j] == 0:
                    matrix[i, j] = 0.0  # Laplace 平滑：共现=0 → PMI=0
                    continue
                # PMI = ln( N * count_AB / (count_A * count_B) )
                numerator = N * count_AB[i, j]
                denominator = count_A[i] * count_A[j]
                if denominator == 0:
                    matrix[i, j] = 0.0
                    continue
                pmi = math.log(numerator / denominator)
                # 截断负值 + 上限钳制（防止稀疏共现产生极端PMI）
                matrix[i, j] = min(max(0.0, pmi), 7.0)

        instance.matrix = pd.DataFrame(matrix, index=all_subjects, columns=all_subjects)
        return instance

    @classmethod
    def from_counters(cls, N: int,
                      count_A: Dict[str, int],
                      count_AB: Dict[Tuple[str, str], int],
                      subjects: List[str]) -> "PMIMatrix":
        """从全局计数器构建 PMI 矩阵。

        Args:
            N: 全局凭证总数
            count_A: 科目出现次数
            count_AB: 科目共现次数（键为 (A, B)，A ≤ B）
            subjects: 要纳入矩阵的科目列表
        """
        instance = cls()
        instance.subjects = sorted(subjects)
        n = len(instance.subjects)
        subject_idx = {s: i for i, s in enumerate(instance.subjects)}

        if N == 0:
            instance.matrix = pd.DataFrame(index=instance.subjects, columns=instance.subjects, data=0.0)
            return instance

        matrix = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i, j] = 1.0
                    continue
                si, sj = instance.subjects[i], instance.subjects[j]
                # 规范化复合键
                key = (si, sj) if si <= sj else (sj, si)
                c_ab = count_AB.get(key, 0)
                if c_ab == 0:
                    matrix[i, j] = 0.0
                    continue
                c_a = count_A.get(si, 0)
                c_b = count_A.get(sj, 0)
                if c_a == 0 or c_b == 0:
                    matrix[i, j] = 0.0
                    continue
                numerator = N * c_ab
                denominator = c_a * c_b
                pmi = math.log(numerator / denominator)
                matrix[i, j] = min(max(0.0, pmi), 7.0)

        instance.matrix = pd.DataFrame(matrix, index=instance.subjects, columns=instance.subjects)
        return instance

    # ------------------------------------------------------------------
    # 融合
    # ------------------------------------------------------------------

    def fuse(self, universal_R: "PMIMatrix", alpha: float = 0.2) -> "PMIMatrix":
        """融合通用 R 和公司专属 R。

        Final_R = (1 - alpha) * universal_R + alpha * company_R
        按两个矩阵的科目并集对齐，缺失科目填 0。
        """
        if universal_R.matrix is None or universal_R.matrix.empty:
            return self
        if self.matrix is None or self.matrix.empty:
            return universal_R

        # 科目并集
        all_subs = sorted(set(self.subjects) | set(universal_R.subjects))
        n = len(all_subs)

        # 对齐并填充
        def align(m: "PMIMatrix") -> np.ndarray:
            if m.matrix is None or m.matrix.empty:
                return np.zeros((n, n))
            aligned = np.zeros((n, n))
            for i, si in enumerate(all_subs):
                if si not in m.matrix.index:
                    continue
                for j, sj in enumerate(all_subs):
                    if sj not in m.matrix.columns:
                        continue
                    aligned[i, j] = m.matrix.at[si, sj]
            return aligned

        company_arr = align(self)
        universal_arr = align(universal_R)

        fused_arr = (1.0 - alpha) * universal_arr + alpha * company_arr

        result = PMIMatrix()
        result.subjects = all_subs
        result.matrix = pd.DataFrame(fused_arr, index=all_subs, columns=all_subs)
        return result

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def to_dataframe(self) -> pd.DataFrame:
        """返回 PMI 矩阵的 DataFrame 视图。"""
        if self.matrix is None:
            return pd.DataFrame()
        return self.matrix

    def is_empty(self) -> bool:
        """矩阵是否为空（无数据）。"""
        return self.matrix is None or self.matrix.empty

    @property
    def shape(self) -> Tuple[int, int]:
        """矩阵形状。"""
        if self.is_empty():
            return (0, 0)
        return self.matrix.shape


# ============================================================================
# 凭证向量化
# ============================================================================

class VoucherVectorizer:
    """将一张凭证的若干行分录转换为科目向量 v。

    计算步骤：
    1. amt_i = abs(借方金额_i) + abs(贷方金额_i)  — 每个科目的参与金额
    2. total_amt = Σ amt_i  — 凭证总参与金额
    3. x_i = amt_i / total_amt  — 归一化
    4. v_i = x_i * C_i  — 乘以清晰度系数
    """

    def __init__(self):
        self.clarity = SUBJECT_CLARITY

    def _get_clarity(self, subject: str) -> float:
        """获取科目清晰度系数（支持"其他/其它"等字符变体）。"""
        s = subject.strip()
        if s in self.clarity:
            return self.clarity[s]
        # 尝试常见字符变体
        if "其他" in s:
            variant = s.replace("其他", "其它")
        elif "其它" in s:
            variant = s.replace("其它", "其他")
        else:
            variant = s
        if variant != s and variant in self.clarity:
            return self.clarity[variant]
        return DEFAULT_CLARITY

    def vectorize(self, voucher_rows: pd.DataFrame,
                  subject_col: str,
                  debit_col: str,
                  credit_col: str,
                  all_subjects: List[str]) -> pd.Series:
        """将一张凭证向量化。

        Args:
            voucher_rows: 同一张凭证的所有分录行
            subject_col: 一级科目列名
            debit_col: 借方金额列名
            credit_col: 贷方金额列名
            all_subjects: 完整的科目列表（与 PMI 矩阵对齐）

        Returns:
            pd.Series, index=all_subjects, 值为向量元素
        """
        # 步骤1: 按科目聚合参与金额
        amt_by_subject: Dict[str, float] = defaultdict(float)

        for _, row in voucher_rows.iterrows():
            subj = str(row[subject_col]).strip() if pd.notna(row[subject_col]) else ""
            if not subj:
                continue

            debit = _safe_float(row.get(debit_col, 0))
            credit = _safe_float(row.get(credit_col, 0))
            amt = abs(debit) + abs(credit)
            amt_by_subject[subj] += amt

        # 步骤2: 归一化
        total_amt = sum(amt_by_subject.values())
        if total_amt == 0:
            return pd.Series(0.0, index=all_subjects, dtype=np.float64)

        # 步骤3+4: 归一化 + 清晰度加成
        result = pd.Series(0.0, index=all_subjects, dtype=np.float64)
        for subj, amt in amt_by_subject.items():
            if subj in result.index:
                x = amt / total_amt
                c = self._get_clarity(subj)
                result[subj] = x * c

        return result


def _safe_float(val) -> float:
    """安全转换为 float，处理 NaN 和 None。"""
    try:
        if val is None:
            return 0.0
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return v
    except (ValueError, TypeError):
        return 0.0


# ============================================================================
# 相关性传播
# ============================================================================

class CorrelationPropagator:
    """相关性传播：w' = w × R

    将每个桶的初始偏好向量 w 乘以 PMI 矩阵 R，
    让偏好沿科目相关性传播到相关科目。
    """

    @staticmethod
    def propagate_one(w: pd.Series, R_matrix: pd.DataFrame) -> pd.Series:
        """对单个偏好向量执行传播。

        w' = w × R → L2 归一化。
        保留偏好向量的原始权重差异，L2 仅用于防止高 PMI 导致爆炸。

        Args:
            w: 偏好向量，index=科目名
            R_matrix: PMI 矩阵，行和列都是科目名

        Returns:
            传播后的向量 w'
        """
        # 对齐索引
        w_aligned = w.reindex(R_matrix.index, fill_value=0.0)

        # w' = w × R（矩阵乘法）
        raw = w_aligned.values @ R_matrix.values

        # w' L2 归一化（限制幅度，防止高 PMI 泄漏）
        norm = np.linalg.norm(raw)
        if norm > 1e-10:
            raw = raw / norm

        return pd.Series(raw, index=R_matrix.index, dtype=np.float64)

    @staticmethod
    def propagate_all(bucket_preferences: Dict[str, Dict[str, float]],
                      R_matrix: pd.DataFrame) -> Dict[str, pd.Series]:
        """对所有桶的偏好向量执行传播。

        Args:
            bucket_preferences: {桶名: {科目名: 偏好值}}
            R_matrix: PMI 矩阵

        Returns:
            {桶名: 传播后的 Series (w')}
        """
        result = {}
        for bucket_name, prefs in bucket_preferences.items():
            w = pd.Series(prefs, dtype=np.float64)
            w_prime = CorrelationPropagator.propagate_one(w, R_matrix)
            result[bucket_name] = w_prime
        return result


# ============================================================================
# 评分器
# ============================================================================

class Scorer:
    """最终评分：Score = v·w' + b + c + s_amount

    v       = 凭证向量
    w'      = 传播后的桶偏好
    b       = 手工关键词偏置
    c       = 自动词特征偏置（理论增强 §2）
    s_amount = 金额惩罚分（理论增强 §1）
    """

    @staticmethod
    def score(voucher_vector: pd.Series,
              propagated_preferences: Dict[str, pd.Series],
              keyword_bias: Dict[str, float],
              bucket_clarity: Dict[str, float] = None,
              auto_word_bias: Dict[str, float] = None,
              amount_scores: Dict[str, float] = None,
              rank_bonus: Dict[str, float] = None,
              bookkeeper_bias: Dict[str, float] = None) -> Dict[str, float]:
        """计算凭证对每个桶的得分。

        Score = v·w' + max(b,c) + s_amount + d + e
        （b 和 c 取 max 而非相加，避免同一词被手工关键词和自动词重复计分）
        """
        if bucket_clarity is None:
            bucket_clarity = {}
        if auto_word_bias is None:
            auto_word_bias = {}
        if amount_scores is None:
            amount_scores = {}
        if rank_bonus is None:
            rank_bonus = {}
        if bookkeeper_bias is None:
            bookkeeper_bias = {}

        from .config import TAX_DECAY

        scores = {}
        for bucket_name, w_prime in propagated_preferences.items():
            v_aligned = voucher_vector.reindex(w_prime.index, fill_value=0.0)
            v_dot_w = float((v_aligned.values * w_prime.values).sum())
            b = keyword_bias.get(bucket_name, 0.0)
            c = auto_word_bias.get(bucket_name, 0.0)
            s_a = amount_scores.get(bucket_name, 0.0)
            d = rank_bonus.get(bucket_name, 0.0)
            e = bookkeeper_bias.get(bucket_name, 0.0)
            # 税费桶衰减：词偏置分打折扣，结构分/金额分/顺位分/制单人分不受影响
            if bucket_name == "税费":
                b *= TAX_DECAY
                c *= TAX_DECAY
            scores[bucket_name] = v_dot_w + max(b, c) + s_a + d + e

        # 降序排列：得分 → 桶清晰度 → 桶名
        sorted_scores = dict(sorted(
            scores.items(),
            key=lambda x: (-x[1], -bucket_clarity.get(x[0], 0.0), x[0]),
        ))
        return sorted_scores

    @staticmethod
    def classify_voucher(voucher_vector: pd.Series,
                          propagated_preferences: Dict[str, pd.Series],
                          keyword_bias: Dict[str, float],
                          bucket_clarity: Dict[str, float] = None,
                          auto_word_bias: Dict[str, float] = None,
                          amount_scores: Dict[str, float] = None,
                          rank_bonus: Dict[str, float] = None,
                          bookkeeper_bias: Dict[str, float] = None) -> Tuple[str, Dict[str, float]]:
        """分类单张凭证：返回最高分桶 + 完整分数明细。"""
        all_scores = Scorer.score(
            voucher_vector, propagated_preferences, keyword_bias,
            bucket_clarity, auto_word_bias, amount_scores, rank_bonus,
            bookkeeper_bias,
        )
        top_bucket = next(iter(all_scores.keys())) if all_scores else "未分类"
        return top_bucket, all_scores
