# -*- coding: utf-8 -*-
"""
纠错回路 — 金额 EMA + 桶顺位增强 (correct_errors_theory.md)

两个机制：
1. 金额 EMA: 用户纠错后，用 EMA 动态调整正确桶的 μ/σ
2. 桶顺位增强: 记录纠错历史的 (kw, acc1, acc2) → 正确桶，
   下次分类时按 P(纠错|实体) 给对应桶加分 +d
"""

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .config import LAMBDA_RANK, EMA_ALPHA
from .storage_utils import safe_write_json

# 存储路径
_STORAGE_DIR = Path(__file__).parent / "_storage"
_CORRECTIONS_PATH = _STORAGE_DIR / "corrections.json"


# ============================================================================
# 分词（与 WordFeatureLearner 复用同一套过滤，但不在此导入）
# ============================================================================

def _jieba_tokenize(text: str) -> List[str]:
    """jieba 分词 + 基础过滤。"""
    try:
        import jieba
    except ImportError:
        return []
    from .memory_learner import WordFeatureLearner
    return WordFeatureLearner._tokenize_and_filter(text)


# ============================================================================
# 纠错管理器
# ============================================================================

class CorrectionManager:
    """管理用户纠错历史，提供 EMA 金额更新和桶顺位增强。

    Usage:
        mgr = CorrectionManager()
        mgr.load()

        # 用户纠错
        mgr.record_correction(
            vid="记-0027", original="存货采购", correct="销售收入",
            amount=13929.0, summary="2024年12月份货款",
            subjects=["应收账款", "银行存款"],
            subject_details=["华为"],
        )

        # 分类时查询
        d = mgr.compute_rank_bonus(
            summary="2024年12月份货款",
            subjects=["应收账款", "银行存款"],
            subject_details=["华为"],
        )
    """

    def __init__(self):
        # EMA 金额特征: {bucket: {mu, sigma, n}}
        self.amount_ema: Dict[str, dict] = {}
        # 桶顺位表: {"kw:xxx"|"acc1:xxx"|"acc2:xxx": {bucket: count}}
        self.rank_table: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # 历史日志
        self.corrections: List[dict] = []
        # 去重: (vid, correct_bucket) 集合，防止同一纠错重复计数
        self._seen_corrections: Set[Tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # 加载/保存
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """加载纠错历史。返回 True 表示文件存在。"""
        if not _CORRECTIONS_PATH.exists():
            return False
        try:
            data = json.loads(_CORRECTIONS_PATH.read_text(encoding="utf-8"))
            self.amount_ema = data.get("amount_ema", {})
            self.rank_table = defaultdict(lambda: defaultdict(int))
            for key, counts in data.get("rank_table", {}).items():
                self.rank_table[key] = defaultdict(int, counts)
            self.corrections = data.get("corrections", [])
            # 恢复去重集合
            self._seen_corrections = set()
            for c in self.corrections:
                self._seen_corrections.add((c.get("vid", ""), c.get("correct", "")))
            return True
        except (json.JSONDecodeError, KeyError):
            return False

    def save(self):
        """持久化纠错数据（自动备份到 _storage/backups/）。"""
        _STORAGE_DIR.mkdir(parents=True, exist_ok=True)

        rank_serialized = {}
        for key, counts in self.rank_table.items():
            rank_serialized[key] = dict(counts)

        data = {
            "_说明": "纠错回路 — 金额EMA + 桶顺位表",
            "amount_ema": self.amount_ema,
            "rank_table": rank_serialized,
            "corrections": self.corrections,
            "total_corrections": len(self.corrections),
        }

        safe_write_json(_CORRECTIONS_PATH, data)

    # ------------------------------------------------------------------
    # 纠错记录
    # ------------------------------------------------------------------

    def record_correction(self,
                          vid: str,
                          original_bucket: str,
                          correct_bucket: str,
                          amount: float = 0.0,
                          summary: str = "",
                          subjects: List[str] = None,
                          subject_details: List[str] = None):
        """用户纠正一张凭证的分类。

        Args:
            vid: 凭证号
            original_bucket: 机器原始分类
            correct_bucket: 用户纠正后分类
            amount: 凭证合计金额
            summary: 摘要文本
            subjects: 一级科目列表
            subject_details: 二级科目明细列表
        """
        # 0. 去重检查：同一凭证 + 同一正确桶的纠错只记录一次
        dedup_key = (str(vid), correct_bucket)
        if dedup_key in self._seen_corrections:
            return  # 已记录过，跳过
        self._seen_corrections.add(dedup_key)

        # 1. 金额 EMA 更新
        if amount > 0:
            self._update_amount_ema(correct_bucket, amount)

        # 2. 桶顺位表更新
        if summary:
            kws = _jieba_tokenize(summary)
            for kw in kws:
                self.rank_table[f"kw:{kw}"][correct_bucket] += 1
        if subjects:
            for s in subjects:
                if s and s.strip():
                    self.rank_table[f"acc1:{s.strip()}"][correct_bucket] += 1
        if subject_details:
            for sd in subject_details:
                if sd and sd.strip():
                    self.rank_table[f"acc2:{sd.strip()}"][correct_bucket] += 1

        # 3. 日志
        self.corrections.append({
            "vid": vid,
            "summary": summary[:120] if summary else "",
            "original": original_bucket,
            "correct": correct_bucket,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        self.save()

    # ------------------------------------------------------------------
    # 金额 EMA
    # ------------------------------------------------------------------

    def _update_amount_ema(self, bucket: str, amount: float):
        """EMA 更新桶的 μ 和 σ。

        μ_new = (1-α) × μ_old + α × ln(amt)
        σ²_new = (1-α) × σ²_old + α × (ln(amt)-μ_old) × (ln(amt)-μ_new)
        σ_new = max(0.5, sqrt(σ²_new))
        """
        ln_amt = math.log(amount)
        alpha = EMA_ALPHA

        if bucket not in self.amount_ema or self.amount_ema[bucket].get("n", 0) == 0:
            # 冷启动：直接用此值初始化
            self.amount_ema[bucket] = {"mu": ln_amt, "sigma": 1.0, "n": 1}
            return

        s = self.amount_ema[bucket]
        mu_old = s["mu"]
        sigma_old = s.get("sigma", 1.0)
        s["n"] += 1

        # EMA 更新 μ
        mu_new = (1 - alpha) * mu_old + alpha * ln_amt

        # EMA 更新 σ²
        var_old = sigma_old * sigma_old
        var_new = (1 - alpha) * var_old + alpha * (ln_amt - mu_old) * (ln_amt - mu_new)
        sigma_new = max(0.5, math.sqrt(abs(var_new)))

        s["mu"] = mu_new
        s["sigma"] = sigma_new

    def get_amount_ema_score(self, bucket: str, amount: float) -> float:
        """用 EMA 金额特征计算惩罚分。

        与 AmountProfiler.score 相同公式，但用 EMA 的 μ/σ。
        n < 3 时返回 0（不参与）。
        """
        s = self.amount_ema.get(bucket)
        if s is None or s.get("n", 0) < 3:
            return 0.0
        if amount <= 0:
            return 0.0

        z = (math.log(amount) - s["mu"]) / s["sigma"]
        from .memory_learner import LAMBDA_A
        return LAMBDA_A * math.tanh(-z * z / 2.0)

    # ------------------------------------------------------------------
    # 桶顺位增强
    # ------------------------------------------------------------------

    def compute_rank_bonus(self,
                           summary: str = "",
                           subjects: List[str] = None,
                           subject_details: List[str] = None) -> Dict[str, float]:
        """对一张凭证计算桶顺位增强分 d(bucket)。

        算法：
        1. 提取 kw (jieba分词), acc1, acc2
        2. 对每个实体查 rank_table，计算 P(纠错到桶|实体)
        3. 对每桶，取所有实体的 max P，乘以 λ_rank

        Returns:
            {bucket: d_score}，d ∈ [0, λ_rank]
        """
        if not self.rank_table:
            return {}

        # 收集实体
        entities: List[str] = []
        if summary:
            for kw in _jieba_tokenize(summary):
                entities.append(f"kw:{kw}")
        if subjects:
            for s in subjects:
                if s and s.strip():
                    entities.append(f"acc1:{s.strip()}")
        if subject_details:
            for sd in subject_details:
                if sd and sd.strip():
                    entities.append(f"acc2:{sd.strip()}")

        if not entities:
            return {}

        # 对每桶，取所有实体的最大 P
        bucket_max_p: Dict[str, float] = defaultdict(float)
        for entity in entities:
            counts = self.rank_table.get(entity)
            if not counts:
                continue
            total = sum(counts.values())
            for bucket, cnt in counts.items():
                p = cnt / total
                if p > bucket_max_p[bucket]:
                    bucket_max_p[bucket] = p

        # 乘以 λ_rank
        return {b: p * LAMBDA_RANK for b, p in bucket_max_p.items()}
