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

from .config import LAMBDA_RANK, EMA_ALPHA, get_storage_dir
from .storage_utils import safe_write_json

# T3 科目（纯资金管道，无业务含义，不参与纠错信号）
_T3_SUBJECTS = {"银行存款", "库存现金", "其它货币资金", "其他货币资金"}

# 纠错信号科目指纹匹配阈值（Jaccard 相似度）
# 0.6 允许 ±1 个科目的容差，同时拦截单科目信号污染多科目凭证
JACCARD_THRESHOLD = 0.6


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
        # 桶顺位表: {"ctx:..."|"acc1:...": {bucket: count}}
        self.rank_table: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # 历史日志
        self.corrections: List[dict] = []
        # 每条凭证的原生桶（首次纠正时的原始分类，永不改变）
        self._vid_native: Dict[str, str] = {}
        # 每条凭证当前产生的信号: {vid: [(entity, correct_bucket), ...]}
        self._vid_signals: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        # 每条凭证的再确认轮次（同桶再确认几次就 +1）
        self._vid_batches: Dict[str, int] = {}
        # 金额 EMA 精确撤销: {vid: (bucket, amount)} + 每桶按序历史（撤销时重放重建）
        self._vid_ema: Dict[str, Tuple[str, float]] = {}
        self._ema_history: Dict[str, List[Tuple[str, float]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # 加载/保存
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """加载纠错历史。返回 True 表示文件存在。"""
        corrections_path = get_storage_dir() / "corrections.json"
        if not corrections_path.exists():
            return False
        try:
            data = json.loads(corrections_path.read_text(encoding="utf-8"))
            self.amount_ema = data.get("amount_ema", {})
            self.rank_table = defaultdict(lambda: defaultdict(int))
            for key, counts in data.get("rank_table", {}).items():
                self.rank_table[key] = defaultdict(int, counts)
            self.corrections = data.get("corrections", [])
            # 恢复原生桶映射 + vid 信号清单
            self._vid_native = data.get("_vid_native", {})
            self._vid_signals = defaultdict(list)
            for vid, signals in data.get("_vid_signals", {}).items():
                self._vid_signals[vid] = [tuple(s) for s in signals]
            self._vid_batches = data.get("_vid_batches", {})
            # 金额 EMA 撤销记录（旧文件无此字段 → 空，兼容）
            self._vid_ema = {}
            for vid, entry in data.get("_vid_ema", {}).items():
                if isinstance(entry, list) and len(entry) == 2:
                    self._vid_ema[vid] = (entry[0], float(entry[1]))
            self._ema_history = defaultdict(list)
            for bucket, history in data.get("_ema_history", {}).items():
                self._ema_history[bucket] = [tuple(e) for e in history]
            return True
        except (json.JSONDecodeError, KeyError):
            return False

    def save(self):
        """持久化纠错数据（自动备份到 _storage/backups/）。"""
        get_storage_dir().mkdir(parents=True, exist_ok=True)

        rank_serialized = {}
        for key, counts in self.rank_table.items():
            rank_serialized[key] = dict(counts)

        data = {
            "_说明": "纠错回路 — 金额EMA + 桶顺位表",
            "amount_ema": self.amount_ema,
            "rank_table": rank_serialized,
            "corrections": self.corrections,
            "total_corrections": len(self.corrections),
            "_vid_native": self._vid_native,
            "_vid_signals": {vid: [list(s) for s in signals]
                           for vid, signals in self._vid_signals.items()},
            "_vid_batches": self._vid_batches,
            "_vid_ema": {vid: [bucket, amount]
                         for vid, (bucket, amount) in self._vid_ema.items()},
            "_ema_history": {bucket: [list(e) for e in history]
                             for bucket, history in self._ema_history.items()},
        }

        safe_write_json(get_storage_dir() / "corrections.json", data)

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
        # 0. 覆盖逻辑：同 vid 改判到不同桶 → 先撤销旧信号
        old_bucket = None
        if vid in self._vid_signals and self._vid_signals[vid]:
            old_bucket = self._vid_signals[vid][0][1]
        if old_bucket and old_bucket != correct_bucket:
            decrement = self._vid_batches.get(vid, 1)
            for entity, _ in self._vid_signals[vid]:
                self.rank_table[entity][old_bucket] = max(0, self.rank_table[entity][old_bucket] - decrement)
                if self.rank_table[entity][old_bucket] == 0:
                    del self.rank_table[entity][old_bucket]
                if sum(self.rank_table[entity].values()) == 0:
                    del self.rank_table[entity]
            del self._vid_signals[vid]
            self._vid_batches.pop(vid, None)
        is_reaffirm = (old_bucket is not None and old_bucket == correct_bucket)

        # 1. 金额 EMA 更新（可撤销：同 vid 重复提交先撤销旧贡献再重放）
        self._apply_amount_ema(vid, correct_bucket, amount)

        # 2. 桶顺位表更新 — 四维信号：ctx:{acc1}+{keyword}+{native}
        #    native = 首次纠正时的原始分类，永不改变
        if vid not in self._vid_native:
            self._vid_native[vid] = original_bucket
        native = self._vid_native[vid]

        keywords = []
        if summary:
            keywords.extend(_jieba_tokenize(summary))
        if subject_details:
            for sd in subject_details:
                if sd and sd.strip():
                    keywords.append(sd.strip())

        acc1_list = []
        if subjects:
            for s in subjects:
                s = s.strip()
                if s and s not in _T3_SUBJECTS:
                    acc1_list.append(s)
        subj_fp = ",".join(sorted(set(acc1_list)))  # 全科目指纹

        vid_signals = []
        if subj_fp:
            for kw in keywords:
                entity = f"ctx:{subj_fp}+{kw}+{native}"
                self.rank_table[entity][correct_bucket] += 1
                vid_signals.append((entity, correct_bucket))
            if not keywords:
                # 无关键词时也带 native（与 ctx 信号同口径，查询侧校验）
                entity = f"acc1:{subj_fp}|{native}"
                self.rank_table[entity][correct_bucket] += 1
                vid_signals.append((entity, correct_bucket))
        self._vid_signals[vid] = vid_signals
        if is_reaffirm:
            self._vid_batches[vid] = self._vid_batches.get(vid, 1) + 1
        elif old_bucket is None:
            self._vid_batches[vid] = 1
        else:
            # 覆盖改判：新信号批次计数重置为 1（撤销时已 pop，须显式重设）
            self._vid_batches[vid] = 1

        # 3. 日志
        self.corrections.append({
            "vid": vid,
            "summary": summary[:120] if summary else "",
            "original": original_bucket,
            "correct": correct_bucket,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        self.save()

    def record_corrections_batch(self, corrections: List[dict]):
        """批量处理一次上传的所有纠错（算作一个批次）。

        规则：
        - 同批次同 (vid, bucket) 去重
        - 同 vid 改判不同桶 → 覆盖（撤销旧信号，写入新信号）
        - 同 vid 同桶再确认 → 轮次 +1（新批次累加）
        - 每个 (entity, bucket) 每批次只 +1

        Args:
            corrections: [{"vid", "original_bucket", "correct_bucket",
                           "amount", "summary", "subjects", "subject_details"}, ...]
        """
        if not corrections:
            return

        batch_seen: Set[Tuple[str, str]] = set()           # 本批次去重
        batch_signals: Dict[str, set] = defaultdict(set)   # entity → {buckets}

        for c in corrections:
            vid = str(c.get("vid", ""))
            correct_bucket = c.get("correct_bucket", "")
            amount = float(c.get("amount", 0) or 0)
            summary = str(c.get("summary", "") or "")
            subjects = c.get("subjects", []) or []
            subject_details = c.get("subject_details", []) or []

            # 0. 本批次去重
            dedup_key = (vid, correct_bucket)
            if dedup_key in batch_seen:
                continue
            batch_seen.add(dedup_key)

            # 0b. 覆盖逻辑：同 vid 改判到不同桶 → 先撤销旧信号（含所有再确认轮次）
            old_bucket = None
            if vid in self._vid_signals and self._vid_signals[vid]:
                old_bucket = self._vid_signals[vid][0][1]  # 旧目标桶
            if old_bucket and old_bucket != correct_bucket:
                decrement = self._vid_batches.get(vid, 1)
                for entity, _ in self._vid_signals[vid]:
                    self.rank_table[entity][old_bucket] = max(0, self.rank_table[entity][old_bucket] - decrement)
                    if self.rank_table[entity][old_bucket] == 0:
                        del self.rank_table[entity][old_bucket]
                    if sum(self.rank_table[entity].values()) == 0:
                        del self.rank_table[entity]
                    # 同步撤销本批次待写入的增量——旧实现只对 rank_table 做减法，
                    # 批次末尾统一 +1 时会把刚撤销的旧桶信号"复活"
                    # （实测：同批次 vid→A 再 vid→B，A 残留 count=1）
                    if entity in batch_signals:
                        batch_signals[entity].discard(old_bucket)
                        if not batch_signals[entity]:
                            del batch_signals[entity]
                del self._vid_signals[vid]
                self._vid_batches.pop(vid, None)

            # 1. 金额 EMA（可撤销：改判覆盖时旧桶贡献会被精确回滚）
            self._apply_amount_ema(vid, correct_bucket, amount)

            # 2. 提取信号（四维：acc1 + keyword + native_bucket → correct_bucket）
            original_bucket = c.get("original_bucket", "")
            if vid not in self._vid_native:
                self._vid_native[vid] = original_bucket
            native = self._vid_native[vid]

            keywords = []
            if summary:
                keywords.extend(_jieba_tokenize(summary))
            if subject_details:
                for sd in subject_details:
                    if sd and sd.strip():
                        keywords.append(sd.strip())

            acc1_list = []
            for s in subjects:
                s = s.strip() if s else ""
                if s and s not in _T3_SUBJECTS:
                    acc1_list.append(s)
            subj_fp = ",".join(sorted(set(acc1_list)))  # 全科目指纹

            # 再确认 vs 覆盖
            is_reaffirm = (old_bucket is not None and old_bucket == correct_bucket)

            vid_signals = []
            if subj_fp:
                for kw in keywords:
                    entity = f"ctx:{subj_fp}+{kw}+{native}"
                    batch_signals[entity].add(correct_bucket)
                    vid_signals.append((entity, correct_bucket))
                if not keywords:
                    # 无关键词时也带 native（与 ctx 信号同口径，查询侧校验）
                    entity = f"acc1:{subj_fp}|{native}"
                    batch_signals[entity].add(correct_bucket)
                    vid_signals.append((entity, correct_bucket))
            self._vid_signals[vid] = vid_signals
            # 再确认：轮次 +1；首次：轮次 = 1；覆盖改判：轮次重置为 1
            if is_reaffirm:
                self._vid_batches[vid] = self._vid_batches.get(vid, 1) + 1
            elif old_bucket is None:
                self._vid_batches[vid] = 1
            else:
                self._vid_batches[vid] = 1

            # 3. 日志
            self.corrections.append({
                "vid": vid,
                "summary": summary[:120] if summary else "",
                "original": c.get("original_bucket", ""),
                "correct": correct_bucket,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

        # 批次计数：每个 (entity, bucket) 在本批次只 +1
        for entity, buckets in batch_signals.items():
            for bucket in buckets:
                self.rank_table[entity][bucket] += 1

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

    def _apply_amount_ema(self, vid: str, bucket: str, amount: float):
        """对纠错金额更新 EMA，并登记可撤销记录。

        同 vid 重复提交（再确认 / 整表重传）→ 先撤销旧贡献再重放，
        防止同一金额被反复 EMA（n 与 μ/σ 无限漂移——修复前每次页面
        rerun 都会把纠错再学一遍）。
        """
        if amount <= 0:
            return
        self._undo_amount_ema(vid)
        self._ema_history[bucket].append((vid, amount))
        self._vid_ema[vid] = (bucket, amount)
        self._rebuild_bucket_ema(bucket)

    def _undo_amount_ema(self, vid: str):
        """撤销某凭证对金额 EMA 的贡献（改判覆盖时调用）。

        EMA 是顺序依赖的不可逆运算，撤销 = 从该桶历史中移除该 vid 的
        记录，再按剩余顺序重放重建——精确，且不影响其他 vid 的贡献。
        """
        entry = self._vid_ema.pop(vid, None)
        if entry is None:
            return
        bucket, amount = entry
        history = self._ema_history.get(bucket, [])
        for i, (h_vid, h_amount) in enumerate(history):
            if h_vid == vid and h_amount == amount:
                del history[i]
                break
        else:
            return  # 历史中无此记录（数据异常），保守不动
        if not history:
            self._ema_history.pop(bucket, None)
            self.amount_ema.pop(bucket, None)
        else:
            self._rebuild_bucket_ema(bucket)

    def _rebuild_bucket_ema(self, bucket: str):
        """按历史顺序重放金额，重建该桶 EMA（撤销后保持一致）。"""
        self.amount_ema.pop(bucket, None)
        for _, amount in self._ema_history.get(bucket, []):
            self._update_amount_ema(bucket, amount)

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
                           subject_details: List[str] = None,
                           original_bucket: str = "") -> Dict[str, float]:
        """对一张凭证计算桶顺位增强分 d(bucket)。

        四维信号联合：ctx:{科目指纹}+{keyword}+{native_bucket}
        - 科目指纹 = 整张凭证的全部 T3 过滤后科目排序拼接
        - native_bucket = 首次纠正时的原始分类，永不改变
        - 匹配使用 Jaccard 相似度 ≥ 0.6（允许 ±1 科目容差）

        硬设定：1批=2.0, 2批=2.25, 3批+=2.5

        Returns:
            {bucket: d_score}，d ∈ [0, 2.5×λ_rank]
        """
        if not self.rank_table:
            return {}

        # 提取关键词 + 一级科目（过滤 T3）
        keywords = []
        if summary:
            keywords.extend(_jieba_tokenize(summary))
        if subject_details:
            for sd in subject_details:
                if sd and sd.strip():
                    keywords.append(sd.strip())

        acc1_list = []
        if subjects:
            for s in subjects:
                s = s.strip()
                if s and s not in _T3_SUBJECTS:
                    acc1_list.append(s)

        query_fp_set = frozenset(acc1_list)
        if not query_fp_set:
            return {}

        # 遍历 rank_table 每条记录，用 Jaccard 代替精确匹配
        bucket_max_p: Dict[str, float] = defaultdict(float)
        bucket_best_cnt: Dict[str, int] = defaultdict(int)

        for entity, counts in self.rank_table.items():
            if not counts:
                continue

            if entity.startswith("ctx:"):
                parts = entity[4:].split("+", 2)
                if len(parts) < 3:
                    continue
                stored_fp, stored_kw, stored_native = parts

                if original_bucket and stored_native != original_bucket:
                    continue

                if stored_kw not in keywords:
                    continue

                stored_fp_set = frozenset(stored_fp.split(","))
                jaccard = len(query_fp_set & stored_fp_set) / len(query_fp_set | stored_fp_set)
                if jaccard < JACCARD_THRESHOLD:
                    continue

            elif entity.startswith("acc1:"):
                rest = entity[5:]
                # 新格式 "acc1:{指纹}|{native}" 校验原生桶；旧格式（无 |）跳过
                if "|" in rest:
                    stored_fp, stored_native = rest.split("|", 1)
                    if original_bucket and stored_native != original_bucket:
                        continue
                else:
                    stored_fp = rest
                stored_fp_set = frozenset(stored_fp.split(","))
                jaccard = len(query_fp_set & stored_fp_set) / len(query_fp_set | stored_fp_set)
                if jaccard < JACCARD_THRESHOLD:
                    continue
            else:
                continue

            total = sum(counts.values())
            if total == 0:
                continue
            for bucket, cnt in counts.items():
                p = cnt / total
                if p > bucket_max_p[bucket]:
                    bucket_max_p[bucket] = p
                    bucket_best_cnt[bucket] = cnt

        # 硬设定：1批=2.0, 2批=2.25, 3批+=2.5
        _BOOST_TABLE = {1: 2.0, 2: 2.25}
        result = {}
        for bucket, p in bucket_max_p.items():
            cnt = bucket_best_cnt[bucket]
            multiplier = _BOOST_TABLE.get(cnt, 2.5)
            result[bucket] = p * LAMBDA_RANK * multiplier
        return result
