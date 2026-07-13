# -*- coding: utf-8 -*-
"""
记忆学习器 — 理论增强（theory_boost.md）

1. AmountProfiler:  金额特征 — 统计每桶 ln(金额) 的mu/sigma，偏离时给惩罚分
2. WordFeatureLearner: 词特征 — jieba分词 + TF-IDF + PMI 自动发现强特征词
"""

import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

# ============================================================================
# 静态配置
# ============================================================================

LAMBDA_A = 0.3     # 金额特征缩放系数
LAMBDA_AUTO = 1.0  # 自动词特征全局系数
PMI_THRESHOLD = 0.5      # PMI 低于此值的自动词不参与打分
MIN_WORD_COUNT = 3        # 至少出现3次才纳入自动词候选（降低门槛让tier2积累）
MIN_BUCKET_VOUCHERS = 10  # 桶至少10张凭证，PMI才可靠
MIN_AMOUNT_SAMPLES = 10   # 金额特征至少10个样本才参与打分

# 三层存储阈值
TIER1_COUNT_THRESHOLD = 5       # count >= 5 → 高频词 (Tier 1)
DISCARD_SESSION_THRESHOLD = 5   # 出现 5+ 个不同 session 仍低频 → 丢垃圾桶 (Tier 3)

# 自动词发现的停用词——这些词全桶高频出现，无区分能力
_AUTO_WORD_STOP_SET = {
    # 通用会计动词
    "支付", "收到", "转入", "转出", "冲销", "调整", "结转",
    "计提", "摊销", "核销", "报销", "付款", "收款", "入账",
    # 银行名称
    "招行", "工行", "农行", "建行", "中行", "交行", "浦发",
    "兴业", "民生", "光大", "中信", "华夏", "平安银行",
    "招商银行", "工商银行", "农业银行", "建设银行", "中国银行",
    # 通用词汇
    "本月", "本期", "合计", "金额", "凭证", "分录", "发票",
    "月份", "人民币", "美元", "港币", "欧元", "日元",
    "年月", "月份", "上年", "次年", "本年",
    # 高频无意义词
    "深圳", "北京", "上海", "广州", "广东", "有限", "公司",
    "有限公", "限公司", "技术", "科技",
}


# ============================================================================
# 金额特征学习器
# ============================================================================

class AmountProfiler:
    """统计每个业务桶的凭证金额分布（对数正态），用于金额惩罚分。

    存储: {bucket: {n, sum_ln, sum_ln2}} — O(1) 增量更新
    查询: mu = sum_ln/n, sigma = sqrt(sum_ln2/n - mu²)
    得分: s_amount = lambda_a × tanh(-z²/2), z = (ln(amt) - mu) / sigma
    """

    def __init__(self):
        self.stats: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def update(self, classified_df: pd.DataFrame,
               voucher_col: str,
               amount_col: str):
        """增量更新每桶对数金额的 n/sum_ln/sum_ln2。

        Args:
            classified_df: 已分类的序时账（含「业务分类」列）
            voucher_col: 凭证号列
            amount_col: 金额列（借方金额列，用于计算凭证合计金额）
        """
        # 按凭证聚合合计金额
        for vid, group in classified_df.groupby(voucher_col):
            bucket = group["业务分类"].iloc[0]
            if bucket in ("未分类", "无法分类"):
                continue

            total_amt = _safe_float_abs_sum(group[amount_col]) if amount_col in group.columns else 0.0
            if total_amt <= 0:
                continue

            ln_amt = math.log(total_amt)

            if bucket not in self.stats:
                self.stats[bucket] = {"n": 0, "sum_ln": 0.0, "sum_ln2": 0.0}

            s = self.stats[bucket]
            s["n"] += 1
            s["sum_ln"] += ln_amt
            s["sum_ln2"] += ln_amt * ln_amt

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def compute_profiles(self) -> Dict[str, dict]:
        """返回每桶的 {μ, σ}。数据不足时返回 μ=0, σ=1。"""
        profiles = {}
        for bucket, s in self.stats.items():
            n = s["n"]
            if n < MIN_AMOUNT_SAMPLES:
                continue  # 样本不足，不参与金额打分
            mu = s["sum_ln"] / n
            variance = s["sum_ln2"] / n - mu * mu
            sigma = math.sqrt(max(variance, 1e-6))
            profiles[bucket] = {"mu": mu, "sigma": sigma}
        return profiles

    def score(self, voucher_amount: float, bucket: str,
              profiles: Dict[str, dict] = None) -> float:
        """计算金额惩罚分 s_amount(bucket)。

        s_amount = λ_a × tanh(-z²/2)
        返回范围: (-λ_a, 0]，z=0时最高(0分)，z→∞时最低(-λ_a)
        """
        if profiles is None:
            profiles = self.compute_profiles()

        profile = profiles.get(bucket)
        if profile is None:
            return 0.0  # 样本不足，金额特征不参与
        if profile["sigma"] <= 1e-6:
            return 0.0

        if voucher_amount <= 0:
            return -LAMBDA_A  # 金额为0或负，最大惩罚

        z = (math.log(voucher_amount) - profile["mu"]) / profile["sigma"]
        s = - (z * z) / 2.0
        return LAMBDA_A * math.tanh(s)

    def score_all(self, voucher_amount: float,
                  profiles: Dict[str, dict] = None) -> Dict[str, float]:
        """对所有有数据的桶计算金额惩罚分。无 profile 的桶返回 0。"""
        if profiles is None:
            profiles = self.compute_profiles()
        return {b: self.score(voucher_amount, b, profiles) for b in profiles}

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return dict(self.stats)

    @classmethod
    def from_dict(cls, data: dict) -> "AmountProfiler":
        profiler = cls()
        profiler.stats = dict(data)
        return profiler

    def is_empty(self) -> bool:
        return len(self.stats) == 0


# ============================================================================
# 词特征学习器
# ============================================================================

class WordFeatureLearner:
    """自动发现强特征词（野生词汇）。

    流程:
    1. jieba 分词 → 过滤单字/数字/纯英文
    2. 在每桶内部累积词频
    3. TF-IDF + PMI → 自动词得分
    4. 与手工 keyword 的 max(b, c) 合并
    """

    def __init__(self):
        # {bucket: {word: count}}
        self.word_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.total_vouchers: int = 0  # 所有桶的凭证总数
        self._bucket_voucher_counts: Dict[str, int] = {}  # 每桶凭证数
        # 三层缓存
        self._auto_scores_tier1: Dict[str, Dict[str, dict]] = {}  # 高频词
        self._auto_scores_tier2: Dict[str, Dict[str, dict]] = {}  # 低频词（累积中）
        self._manual_keyword_set: Set[str] = set()  # 手动关键词集合（用于取 max 时过滤）
        # Session 跟踪
        self._session_counter: int = 0  # 当前 session ID（每次 update 递增）
        self._word_sessions: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
        # 黑名单 + 垃圾桶
        self._deleted_words: Set[str] = set()  # {"完工:职工薪酬", ...}
        self._trash_bin: List[dict] = []  # 垃圾桶日志

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def update(self, classified_df: pd.DataFrame,
               voucher_col: str,
               summary_col: str,
               subject_detail_col: str = None):
        """对已分类凭证分词并累积词频。

        Args:
            classified_df: 含「业务分类」列的已分类数据
            voucher_col: 凭证号列
            summary_col: 摘要列
            subject_detail_col: 二级科目名称列（可选）
        """
        self._session_counter += 1
        sid = self._session_counter

        for vid, group in classified_df.groupby(voucher_col):
            bucket = group["业务分类"].iloc[0]
            if bucket in ("未分类", "无法分类"):
                continue

            # 每桶凭证数
            self._bucket_voucher_counts[bucket] = self._bucket_voucher_counts.get(bucket, 0) + 1
            self.total_vouchers += 1

            # 拼接摘要 + 科目名称
            text = ""
            if summary_col and summary_col in group.columns:
                text += " ".join(group[summary_col].dropna().astype(str))
            if subject_detail_col and subject_detail_col in group.columns:
                text += " " + " ".join(group[subject_detail_col].dropna().astype(str))

            if not text.strip():
                continue

            # 分词 + 过滤
            words = self._tokenize_and_filter(text)

            # 去重: 同一凭证同一词只计一次
            unique_words = set(words)
            for w in unique_words:
                self.word_counts[bucket][w] += 1
                self._word_sessions[bucket][w].add(sid)

    # ------------------------------------------------------------------
    # 分词
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize_and_filter(text: str) -> List[str]:
        """jieba 分词 + 过滤。

        过滤规则:
        - 长度 < 2 的单字
        - 纯数字（含金额/日期）
        - 纯英文字母
        - 纯标点/空白
        """
        try:
            import jieba
        except ImportError:
            return []

        words = jieba.lcut(text)

        filtered = []
        for w in words:
            w = w.strip()
            if len(w) < 2:
                continue
            if re.match(r'^[0-9\.\-\/]+$', w):   # 纯数字/日期
                continue
            if re.match(r'^[a-zA-Z]+$', w):       # 纯英文
                continue
            # 过滤发票号/单号类噪音（字母数字混合，数字占比>50%）
            if re.match(r'^[A-Za-z0-9\.\-\/]+$', w):
                digits = sum(1 for c in w if c.isdigit())
                if digits / len(w) > 0.5:
                    continue
            # 过滤超长纯数字串
            if len(w) >= 6 and all(c in '0123456789.-/' for c in w):
                continue
            # 过滤停用词（全桶高频、无区分力）
            if w in _AUTO_WORD_STOP_SET:
                continue
            filtered.append(w)

        return filtered

    # ------------------------------------------------------------------
    # 自动词评分（PMI → auto_score）
    # ------------------------------------------------------------------

    def compute_auto_scores(self) -> Dict[str, Dict[str, Dict[str, dict]]]:
        """对所有桶计算自动词得分，按三层分类输出。

        Tier 1: count >= TIER1_COUNT_THRESHOLD → 高频词，已确认的强特征
        Tier 2: count < TIER1_COUNT_THRESHOLD → 低频词，累积中
        Tier 3: count < TIER1_COUNT_THRESHOLD 且出现在 >= DISCARD_SESSION_THRESHOLD
                个不同 session → 垃圾桶，不参与打分

        PMI(w, bucket) = ln( P(w in bucket) / (P(w in global) × P(bucket)) )
        """
        if self.total_vouchers == 0:
            return {"tier1": {}, "tier2": {}}

        total_all = self.total_vouchers
        bucket_voucher_count: Dict[str, int] = self._bucket_voucher_counts

        # Step 1: 收集全局统计
        global_word_count: Dict[str, int] = defaultdict(int)
        for bucket, wc in self.word_counts.items():
            for w, cnt in wc.items():
                global_word_count[w] += cnt

        # Step 2: 收集垃圾桶 + PMI 候选
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        candidate_words: Dict[str, Dict[str, dict]] = {}
        new_trash: List[dict] = []

        for bucket, wc in self.word_counts.items():
            n_bucket = bucket_voucher_count.get(bucket, 0)
            if n_bucket < MIN_BUCKET_VOUCHERS:
                continue

            P_bucket = n_bucket / total_all
            bucket_candidates = {}

            for word, cnt_in_bucket in wc.items():
                # 过滤已删除词
                if f"{word}:{bucket}" in self._deleted_words:
                    continue

                sessions = self._word_sessions.get(bucket, {}).get(word, set())
                session_cnt = len(sessions)

                # 低于 MIN_WORD_COUNT 的词：检查是否该丢垃圾桶
                if cnt_in_bucket < MIN_WORD_COUNT:
                    if session_cnt >= DISCARD_SESSION_THRESHOLD:
                        new_trash.append({
                            "word": word,
                            "bucket": bucket,
                            "pmi": 0.0,
                            "auto_score": 0.0,
                            "count": cnt_in_bucket,
                            "sessions": session_cnt,
                            "discarded_at": now,
                        })
                    continue  # 不参与 PMI

                P_w_in_bucket = cnt_in_bucket / n_bucket
                P_w_global = global_word_count.get(word, 0) / total_all

                if P_w_global == 0 or P_bucket == 0:
                    continue

                pmi = round(math.log(P_w_in_bucket / (P_w_global * P_bucket)), 4)

                auto_score = pmi_to_auto_score(pmi)
                if auto_score > 0:
                    bucket_candidates[word] = {
                        "pmi": pmi,
                        "auto_score": round(auto_score, 4),
                        "count": cnt_in_bucket,
                        "sessions": sorted(list(sessions)),
                    }

            if bucket_candidates:
                candidate_words[bucket] = bucket_candidates

        # 将垃圾桶加入全局 trash_bin
        self._trash_bin.extend(new_trash)

        # Step 3: 跨桶排他过滤（强特征词必须唯一归属一个桶）
        word_bucket_count: Dict[str, int] = defaultdict(int)
        for bucket, word_scores in candidate_words.items():
            for word in word_scores:
                word_bucket_count[word] += 1

        cross_bucket_words = {w for w, cnt in word_bucket_count.items() if cnt > 1}
        if cross_bucket_words:
            for bucket in list(candidate_words.keys()):
                candidate_words[bucket] = {
                    w: info for w, info in candidate_words[bucket].items()
                    if w not in cross_bucket_words
                }
                if not candidate_words[bucket]:
                    del candidate_words[bucket]

        # Step 4: 分三层（tier1/tier2，tier3 已在 Step 2 收集）
        tier1: Dict[str, Dict[str, dict]] = {}
        tier2: Dict[str, Dict[str, dict]] = {}

        for bucket, word_scores in candidate_words.items():
            for word, info in word_scores.items():
                cnt = info["count"]
                session_cnt = len(info["sessions"])

                if cnt >= TIER1_COUNT_THRESHOLD:
                    # Tier 1: 高频词
                    if bucket not in tier1:
                        tier1[bucket] = {}
                    tier1[bucket][word] = info
                else:
                    # Tier 2: 低频词，继续累积
                    if bucket not in tier2:
                        tier2[bucket] = {}
                    tier2[bucket][word] = info

        self._auto_scores_tier1 = tier1
        self._auto_scores_tier2 = tier2

        return {"tier1": tier1, "tier2": tier2}

    def record_bucket_voucher_counts(self, counts: Dict[str, int]):
        """记录每桶的凭证数（从 AmountProfiler 或其他来源获取）。"""
        self._bucket_voucher_counts = counts

    # ------------------------------------------------------------------
    # 手动删除
    # ------------------------------------------------------------------

    def delete_word(self, word: str, bucket: str):
        """手动删除一个自动词（写入黑名单，从 tier1/tier2 中移除）。

        Args:
            word: 词
            bucket: 所属桶名
        """
        self._deleted_words.add(f"{word}:{bucket}")
        # 从缓存中移除
        for cache in (self._auto_scores_tier1, self._auto_scores_tier2):
            if bucket in cache and word in cache[bucket]:
                del cache[bucket][word]
                if not cache[bucket]:
                    del cache[bucket]

    def get_tier1_words(self) -> Dict[str, Dict[str, dict]]:
        """获取 Tier 1 高频词。"""
        return dict(self._auto_scores_tier1)

    def get_tier2_words(self) -> Dict[str, Dict[str, dict]]:
        """获取 Tier 2 低频词。"""
        return dict(self._auto_scores_tier2)

    def get_trash_bin(self) -> List[dict]:
        """获取垃圾桶日志。"""
        return list(self._trash_bin)

    def get_deleted_words(self) -> List[str]:
        """获取已删除词列表。"""
        return sorted(self._deleted_words)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        result = {}
        for bucket, wc in self.word_counts.items():
            result[bucket] = dict(wc)
        # 序列化 word_sessions：set → sorted list
        sessions_serialized = {}
        for bucket, wd in self._word_sessions.items():
            sessions_serialized[bucket] = {}
            for word, sid_set in wd.items():
                sessions_serialized[bucket][word] = sorted(list(sid_set))
        return {
            "word_counts": result,
            "total_vouchers": self.total_vouchers,
            "bucket_voucher_counts": self._bucket_voucher_counts,
            "_session_counter": self._session_counter,
            "word_sessions": sessions_serialized,
            "deleted_words": sorted(self._deleted_words),
            "trash_bin": self._trash_bin,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WordFeatureLearner":
        learner = cls()
        wc_data = data.get("word_counts", {})
        for bucket, wc in wc_data.items():
            learner.word_counts[bucket] = defaultdict(int, wc)
        learner.total_vouchers = data.get("total_vouchers", 0)
        learner._bucket_voucher_counts = data.get("bucket_voucher_counts", {})
        learner._session_counter = data.get("_session_counter", 0)
        # 恢复 word_sessions: list → set
        sessions_data = data.get("word_sessions", {})
        for bucket, wd in sessions_data.items():
            for word, sid_list in wd.items():
                learner._word_sessions[bucket][word] = set(sid_list)
        learner._deleted_words = set(data.get("deleted_words", []))
        learner._trash_bin = data.get("trash_bin", [])
        return learner

    def is_empty(self) -> bool:
        return self.total_vouchers == 0

    # ------------------------------------------------------------------
    # 匹配（分类时调用）
    # ------------------------------------------------------------------

    def set_manual_keywords(self, keyword_matcher):
        """注入手动关键词集合，用于避免与手工词重复加分。"""
        if keyword_matcher:
            self._manual_keyword_set = set(keyword_matcher.keyword_scores.keys())

    def match_voucher(self, summary: str, subjects: List[str],
                       subject_details: List[str] = None) -> Dict[str, float]:
        """对一张凭证计算自动词得分 per bucket。

        theory_boost §2.5: 同一词手工/自动取 max(b, c)。
        实现策略：自动词匹配时跳过已在手动关键词集合中的词，
        这样手动词已经贡献了 b，自动词不会重复加分。
        不同词仍可累加（如手动"差旅"+0.6，自动"顺丰"+0.8 → 1.4）。
        """
        # 合并 tier1 + tier2 用于匹配（tier3 不参与）
        if not self._auto_scores_tier1 and not self._auto_scores_tier2:
            return {}

        # 拼接文本
        parts = []
        if summary:
            parts.append(str(summary))
        if subjects:
            parts.append(" ".join(str(s) for s in subjects if s))
        if subject_details:
            parts.append(" ".join(str(s) for s in subject_details if s))
        text = " ".join(parts)

        # 分词
        words = set(self._tokenize_and_filter(text))
        if not words:
            return {}

        # 过滤掉手动关键词（避免重复加分）
        words = words - self._manual_keyword_set
        if not words:
            return {}

        # 查 tier1 + tier2
        bucket_scores: Dict[str, float] = defaultdict(float)
        for cache in (self._auto_scores_tier1, self._auto_scores_tier2):
            for bucket, word_scores in cache.items():
                for word in words:
                    info = word_scores.get(word)
                    if info is None:
                        continue
                    score = info["auto_score"] if isinstance(info, dict) else info
                    bucket_scores[bucket] += score * LAMBDA_AUTO

        return dict(bucket_scores)


# ============================================================================
# PMI → auto_score 映射
# ============================================================================

def pmi_to_auto_score(pmi: float) -> float:
    """分段线性映射 PMI → [0, 1] 自动词得分。

    | PMI 范围      | auto_score  |
    |----------------|-------------|
    | ≤ 0.5          | 0           |
    | (0.5, 1.5]    | 0 → 0.3    |
    | (1.5, 3.0]    | 0.3 → 0.7  |
    | (3.0, 5.0]    | 0.7 → 0.9  |
    | > 5.0          | 1.0         |
    """
    if pmi <= 0.5:
        return 0.0
    if pmi <= 1.5:
        return (pmi - 0.5) * 0.3
    if pmi <= 3.0:
        return 0.3 + (pmi - 1.5) * (0.4 / 1.5)
    if pmi <= 5.0:
        return 0.7 + (pmi - 3.0) * 0.1
    return 1.0


# ============================================================================
# 辅助
# ============================================================================

def _safe_float_sum(series: pd.Series) -> float:
    """安全求和，忽略 NaN。"""
    return float(series.dropna().apply(
        lambda x: float(x) if not (math.isnan(float(x)) or math.isinf(float(x))) else 0.0
    ).sum())


def _safe_float_abs_sum(series: pd.Series) -> float:
    """安全求和（取绝对值），忽略 NaN。与分类侧 abs(借方)+abs(贷方) 口径一致。"""
    return float(series.dropna().apply(
        lambda x: abs(float(x)) if not (math.isnan(float(x)) or math.isinf(float(x))) else 0.0
    ).sum())
