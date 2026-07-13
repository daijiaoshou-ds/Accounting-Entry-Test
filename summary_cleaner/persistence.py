# -*- coding: utf-8 -*-
"""
全局计数器持久化 — 通用 R 矩阵的"水池"

维护三个全局账本：
1. N_global — 累计凭证总数
2. count_A  — 每个科目累计出现凭证数
3. count_AB — 科目对累计共现凭证数

持久化格式：JSON，存储在 _storage/global_counters.json
"""

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .engine import PMIMatrix


class GlobalCounters:
    """全局计数器：从海量凭证中累积科目共现统计，用于构建通用 PMI 矩阵。

    理论依据：docs/theory.md 第1.2.4节
    — 基于大数定律，计数器永远累加，随时可以"酿酒"生成通用 R。
    """

    # 放在包内 _storage/ 下，整个 summary_cleaner/ 自包含
    DEFAULT_PATH = Path(__file__).parent / "_storage" / "global_counters.json"
    AUTO_TIER1_PATH = Path(__file__).parent / "_storage" / "auto_words_tier1.json"
    AUTO_TIER2_PATH = Path(__file__).parent / "_storage" / "auto_words_tier2.json"
    AUTO_TIER3_PATH = Path(__file__).parent / "_storage" / "auto_words_tier3.json"
    WORD_DATA_PATH = Path(__file__).parent / "_storage" / "word_data.json"

    def __init__(self):
        self.N: int = 0
        self.count_A: Dict[str, int] = defaultdict(int)
        self.count_AB: Dict[Tuple[str, str], int] = defaultdict(int)
        self._fingerprints: List[str] = []      # 历史指纹列表
        self._train_count: int = 0               # 累计训练次数
        self._train_times: List[str] = []        # 每次训练的时间戳
        # 理论增强（theory_boost.md）
        self.amount_stats: dict = {}             # {bucket: {n, sum_ln, sum_ln2}}
        self.word_counts: dict = {}              # {bucket: {word: count}}
        self.word_bucket_counts: dict = {}       # {bucket: voucher_count}
        # 自动词三层存储
        self.auto_scores_tier1: dict = {}        # {bucket: {word: {pmi, auto_score, count}}}
        self.auto_scores_tier2: dict = {}        # {bucket: {word: {pmi, auto_score, count, sessions}}}
        self.auto_scores_tier3: list = []        # [{word, bucket, pmi, count, sessions, discarded_at}]
        self.auto_scores_deleted: list = []      # ["word:bucket", ...]
        self.word_sessions: dict = {}            # {bucket: {word: [session_ids]}}

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def load(self, path: Path = None) -> bool:
        """从 JSON 加载计数器。返回 True 表示文件存在且加载成功。"""
        path = path or self.DEFAULT_PATH
        if not path.exists():
            return False

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.N = int(data.get("N_global", 0))
            self.count_A = defaultdict(int, data.get("count_A", {}))
            self._fingerprints = data.get("_fingerprints", [])
            self._train_count = data.get("_train_count", 0)
            self._train_times = data.get("_train_times", [])
            self.amount_stats = data.get("amount_stats", {})
            # 词频数据：从 tier2 文件读取（含 word_counts + auto_scores_deleted）
            tier2_raw = self._load_tier_file_raw(self.AUTO_TIER2_PATH)
            if tier2_raw:
                self.word_counts = tier2_raw.get("word_counts", {})
                self.word_bucket_counts = tier2_raw.get("word_bucket_counts", {})
                self.auto_scores_deleted = tier2_raw.get("auto_scores_deleted", [])
                self.word_sessions = tier2_raw.get("word_sessions", {})
            else:
                # 回退：旧格式 inline 数据
                self.word_counts = data.get("word_counts", {})
                self.word_bucket_counts = data.get("word_bucket_counts", {})
                self.auto_scores_deleted = data.get("auto_scores_deleted", [])
                self.word_sessions = data.get("word_sessions", {})
            # 三层存储：优先独立文件，回退 inline 数据，兼容旧格式
            self.auto_scores_tier1 = self._load_tier_file(self.AUTO_TIER1_PATH) or data.get("auto_scores_tier1", {})
            self.auto_scores_tier2 = self._load_tier_file(self.AUTO_TIER2_PATH) or data.get("auto_scores_tier2", {})
            self.auto_scores_tier3 = data.get("auto_scores_tier3", [])
            # 向后兼容：旧格式 auto_scores（扁平 dict）自动迁移到 tier1
            old_auto_scores = data.get("auto_scores", {})
            if old_auto_scores and not self.auto_scores_tier1:
                self.auto_scores_tier1 = old_auto_scores
            # tier3 垃圾桶
            tier3_raw = self._load_tier_file_raw(self.AUTO_TIER3_PATH)
            if tier3_raw and "trash" in tier3_raw:
                self.auto_scores_tier3 = tier3_raw["trash"]
            # 还原复合键 "A||B" → (A, B)
            for key, val in data.get("count_AB", {}).items():
                parts = key.split("||")
                if len(parts) == 2:
                    self.count_AB[(parts[0], parts[1])] = int(val)
            # 自动迁移：global_counters.json 还有内嵌的词数据 → 拆分保存
            needs_migrate = bool(
                data.get("word_counts") or data.get("auto_scores_tier1") or
                data.get("auto_scores")
            )
            if needs_migrate:
                try:
                    self.save(path)
                except OSError:
                    pass  # 迁移失败不影响使用

            return True
        except (json.JSONDecodeError, KeyError, ValueError):
            # 文件损坏，重置
            self.N = 0
            self.count_A = defaultdict(int)
            self.count_AB = defaultdict(int)
            self._last_fingerprint = ""
            return False

    def save(self, path: Path = None):
        """将计数器原子化持久化为 JSON（先写临时文件，成功后再替换）。

        同时保留 .bak 备份，防止写入过程中断导致数据丢失。
        """
        path = path or self.DEFAULT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)

        # 复合键格式：A||B（A ≤ B）
        count_ab_serialized = {}
        for (a, b), val in self.count_AB.items():
            if a <= b:
                key = f"{a}||{b}"
            else:
                key = f"{b}||{a}"
            existing = count_ab_serialized.get(key, 0)
            count_ab_serialized[key] = max(existing, int(val))

        # ── 主计数器（PMI核心 + 金额特征，不含词相关数据）──
        data = {
            "_说明": "全局计数器 — 用于构建通用PMI矩阵。每次分类后自动更新。",
            "_凭证总数说明": "N_global = 累计处理过的凭证数量（每张凭证算1次，不是分录行数）",
            "N_global": self.N,
            "count_A": dict(self.count_A),
            "count_AB": count_ab_serialized,
            "_fingerprints": self._fingerprints,
            "_train_count": self._train_count,
            "_train_times": self._train_times,
            "amount_stats": self.amount_stats,
        }

        self._atomic_write(path, data)

        # ── 自动词 Tier 1（高频词，只放计算结果）──
        tier1_data = {
            "_说明": "自动词 Tier 1 — 高频强特征词（count ≥ 5），可直接review",
            "_train_count": self._train_count,
            "_train_times": self._train_times,
        }
        tier1_data.update(self.auto_scores_tier1)
        self._atomic_write(self.AUTO_TIER1_PATH, tier1_data)

        # ── 自动词 Tier 2（低频词，与Tier1结构一致）──
        tier2_data = {
            "_说明": "自动词 Tier 2 — 低频词（count < 5），跨session累积中",
            "_train_count": self._train_count,
            "_train_times": self._train_times,
            "_fingerprints": self._fingerprints,
            "word_sessions": self.word_sessions,
        }
        tier2_data.update(self.auto_scores_tier2)
        self._atomic_write(self.AUTO_TIER2_PATH, tier2_data)

        # ── 词频原始数据（死的，只追加不重算）──
        word_data = {
            "_说明": "词频原始数据 — 自动词计算的输入源。计算结果见 auto_words_tier1/2/3.json",
            "_train_count": self._train_count,
            "_train_times": self._train_times,
            "word_counts": self.word_counts,
            "word_bucket_counts": self.word_bucket_counts,
            "auto_scores_deleted": self.auto_scores_deleted,
        }
        self._atomic_write(self.WORD_DATA_PATH, word_data)

        # ── 自动词 Tier 3（垃圾桶）──
        tier3_data = {
            "_说明": "自动词 Tier 3 — 垃圾桶（多轮低频已丢弃，仅日志）",
            "_train_count": self._train_count,
            "_train_times": self._train_times,
            "_fingerprints": self._fingerprints,
            "trash": self.auto_scores_tier3,
        }
        self._atomic_write(self.AUTO_TIER3_PATH, tier3_data)

    @staticmethod
    def _load_tier_file(path: Path) -> Optional[dict]:
        """加载独立自动词文件，不存在时返回 None。

        自动剥离元数据键（以 _ 开头），只返回桶名 → 词数据。
        """
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            # 剥离元数据键 + 保留字段，只保留桶数据
            _META_KEYS = {"word_sessions", "trash", "word_counts", "word_bucket_counts", "auto_scores_deleted"}
            return {k: v for k, v in raw.items()
                    if not k.startswith("_") and k not in _META_KEYS}
        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def _load_tier_file_raw(path: Path) -> Optional[dict]:
        """加载独立自动词文件（含元数据），用于需要 _fingerprints 等字段时。"""
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            return None

    def _atomic_write(self, path: Path, data: dict):
        """原子写入 JSON 文件（先写 .tmp，成功后再 replace）。

        不使用 .bak —— .tmp 本身就是崩溃恢复的保险。
        如果 .tmp 存在说明上次写入中断，直接覆盖即可。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json_str, encoding="utf-8")
        # 直接原子替换（Windows 上目标不存在时不会报拒绝访问）
        tmp_path.replace(path)

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def update(self, df: pd.DataFrame,
               voucher_col: str,
               subject_col: str) -> bool:
        """从一批凭证数据中增量更新计数器。

        自动检测重复数据：如果凭证ID指纹与上次相同，跳过更新。

        Args:
            df: 序时账 DataFrame
            voucher_col: 凭证号列名
            subject_col: 一级科目列名

        Returns:
            True 表示实际更新了计数器，False 表示被指纹去重跳过
        """
        # 计算数据指纹（凭证ID排序后取哈希）
        fingerprint = self._compute_fingerprint(df, voucher_col)
        if fingerprint in self._fingerprints:
            return False  # 历史重复数据，跳过

        for vid, group in df.groupby(voucher_col):
            subjects = set()
            for _, row in group.iterrows():
                s = str(row[subject_col]).strip() if pd.notna(row[subject_col]) else ""
                if s:
                    subjects.add(s)

            if not subjects:
                continue

            self.N += 1
            sub_list = sorted(subjects)

            for s in sub_list:
                self.count_A[s] += 1

            for i in range(len(sub_list)):
                for j in range(i, len(sub_list)):
                    key = (sub_list[i], sub_list[j]) if sub_list[i] <= sub_list[j] else (sub_list[j], sub_list[i])
                    self.count_AB[key] += 1

        self._fingerprints.append(fingerprint)
        self._train_count += 1
        self._train_times.append(
            pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        return True

    def _compute_fingerprint(self, df: pd.DataFrame, voucher_col: str) -> str:
        """计算数据指纹：所有凭证ID排序后取 SHA256 前16位。"""
        ids = sorted(df[voucher_col].dropna().astype(str).unique())
        return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]

    # ------------------------------------------------------------------
    # 生成通用 R
    # ------------------------------------------------------------------

    def build_universal_R(self, subjects: List[str]) -> Optional[PMIMatrix]:
        """从当前计数器构建通用 PMI 矩阵。

        Args:
            subjects: 要纳入矩阵的科目列表

        Returns:
            PMIMatrix，如果没有任何数据则返回 None
        """
        if self.N == 0:
            return None

        # 过滤出参与过统计的科目
        relevant_subjects = [s for s in subjects if s in self.count_A]
        if not relevant_subjects:
            return None

        return PMIMatrix.from_counters(
            N=self.N,
            count_A=dict(self.count_A),
            count_AB=dict(self.count_AB),
            subjects=relevant_subjects,  # 只传出现过科目，避免全量零行
        )

    # ------------------------------------------------------------------
    # 管理
    # ------------------------------------------------------------------

    def reset(self):
        """重置所有计数器。"""
        self.N = 0
        self.count_A = defaultdict(int)
        self.count_AB = defaultdict(int)
        self._fingerprints = []
        self._train_count = 0
        self._train_times = []
        self.amount_stats = {}
        self.word_counts = {}
        self.word_bucket_counts = {}
        self.auto_scores_tier1 = {}
        self.auto_scores_tier2 = {}
        self.auto_scores_tier3 = []
        self.auto_scores_deleted = []
        self.word_sessions = {}

    def get_stats(self) -> Dict[str, object]:
        """返回计数器摘要统计。"""
        return {
            "N_global": self.N,
            "unique_subjects": len(self.count_A),
            "total_cooccurrence_pairs": len(self.count_AB),
            "top_subjects": sorted(
                self.count_A.items(),
                key=lambda x: -x[1],
            )[:20] if self.count_A else [],
        }

    def delete_persisted(self, path: Path = None):
        """删除所有持久化文件（含自动词独立文件）。"""
        path = path or self.DEFAULT_PATH
        all_paths = [
            path, path.with_suffix(".tmp"),
            self.AUTO_TIER1_PATH, self.AUTO_TIER1_PATH.with_suffix(".tmp"),
            self.AUTO_TIER2_PATH, self.AUTO_TIER2_PATH.with_suffix(".tmp"),
            self.AUTO_TIER3_PATH, self.AUTO_TIER3_PATH.with_suffix(".tmp"),
        ]
        for p in all_paths:
            if p.exists():
                p.unlink()
        self.reset()
