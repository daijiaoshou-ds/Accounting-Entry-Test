# -*- coding: utf-8 -*-
"""
JournalClassifier — 分类编排器

完整 Pipeline：
1. 构建公司专属 PMI 矩阵 R
2. 加载/融合通用 R
3. 初始化偏好向量 w → 相关性传播 w'
4. 逐凭证：向量化 → 关键词匹配 → 评分 → 分配桶
5. 更新全局计数器
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .config import (
    BUCKET_SUBJECT_PREFERENCES,
    BUCKET_CLARITY,
    COLUMN_NAME_PATTERNS,
    build_bucket_preferences,
    load_buckets_json,
)
from .engine import (
    PMIMatrix,
    VoucherVectorizer,
    CorrelationPropagator,
    Scorer,
)
from .matcher import KeywordMatcher
from .memory_learner import (
    AmountProfiler,
    WordFeatureLearner,
)
from .persistence import GlobalCounters
from .correction import CorrectionManager


class JournalClassifier:
    """序时账业务分类器 — 主编排器。

    将原始序时账的每张凭证自动分类到 14 个标准业务桶中。

    Usage:
        classifier = JournalClassifier()
        result, details = classifier.classify(df, column_mapping, alpha=0.2)
    """

    def __init__(self,
                 buckets_path: Path = None,
                 subject_list: List[str] = None):
        """
        Args:
            buckets_path: 业务桶与keyword.json 路径
            subject_list: 一级科目列表（可从 config 加载）
        """
        if subject_list is None:
            from .config import load_subject_list
            subject_list = load_subject_list()

        self.subject_list = subject_list
        self.keyword_matcher = KeywordMatcher(buckets_path, subject_list)
        self.vectorizer = VoucherVectorizer()
        self.global_counters = GlobalCounters()

        # 桶名列表
        self._all_bucket_names = list(load_buckets_json(buckets_path).keys())
        # 硬规则桶：不参与正常打分，只通过 Step 0 预分配
        self._hard_rule_buckets = {"资金内部往来", "汇兑损益"}
        # 打分桶（不含硬规则桶）
        self.bucket_names = [b for b in self._all_bucket_names if b not in self._hard_rule_buckets]

        # classify() 执行后缓存的结果
        self.final_R: Optional[PMIMatrix] = None
        self.word_learner: Optional[WordFeatureLearner] = None

    # ------------------------------------------------------------------
    # 主分类方法
    # ------------------------------------------------------------------

    def classify(self,
                 df: pd.DataFrame,
                 column_mapping: Dict[str, str],
                 alpha: float = 0.2) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
        """执行完整分类流程。

        Args:
            df: 序时账原始数据
            column_mapping: 列名映射 {
                voucher_no, subject, subject_name, summary, debit, credit
            }
            alpha: 融合权重 (0~1)，公司专属 R 的占比

        Returns:
            (classified_df, score_detail_df, stats_dict)
            - classified_df: 原始数据 + 「业务分类」列
            - score_detail_df: 每张凭证的分数明细
            - stats_dict: 分类统计摘要
        """
        v_col = column_mapping["voucher_no"]
        s_col = column_mapping["subject"]
        sn_col = column_mapping.get("subject_name", "")
        sum_col = column_mapping.get("summary", "")
        d_col = column_mapping.get("debit", "")
        c_col = column_mapping.get("credit", "")

        # ---------------------------------------------------------------
        # Step 0: 过滤结转期间损益等纯会计结账凭证
        # ---------------------------------------------------------------
        # 检测规则：(摘要含结转/损益关键词) + (科目含本年利润/以前年度损益调整)
        # 或：科目含本年利润 + 科目数量 >= 5（结转凭证涉及大量科目）
        # 这些凭证是机械性的期末结转，无业务含义，直接归入「其他业务」
        # 同时排除本年利润科目，不参与 PMI 计算
        PERIOD_CLOSING_SUBJECTS = {"本年利润", "以前年度损益调整"}
        summary_col_available = sum_col and sum_col in df.columns

        closing_voucher_ids = set()
        if summary_col_available:
            for vid, group in df.groupby(v_col):
                summary_text = " ".join(group[sum_col].dropna().astype(str))
                subjects_in_voucher = set(group[s_col].dropna().astype(str))
                has_closing_subject = bool(subjects_in_voucher & PERIOD_CLOSING_SUBJECTS)
                if not has_closing_subject:
                    continue

                # 规则1: 摘要含结转/损益关键词
                PERIOD_CLOSE_KWS = (
                    "期间损益", "本期损益", "期末损益", "本月损益",
                    "结转损益", "损益结转", "结转期间", "结转本期",
                    "结转本月", "结转期末", "期末结转",
                )
                matched = any(kw in summary_text for kw in PERIOD_CLOSE_KWS)

                # 规则2: 科目数 >= 5（结转凭证通常涉及大量损益类科目）
                if matched or len(subjects_in_voucher) >= 5:
                    closing_voucher_ids.add(vid)

        # Step 0b: 检测资金内部往来——一级科目全为货币资金时强制归入
        # ---------------------------------------------------------------
        CASH_SUBJECTS = {"库存现金", "银行存款", "其他货币资金", "其它货币资金"}
        cash_voucher_ids = set()
        for vid, group in df.groupby(v_col):
            subjects_in_voucher = set(group[s_col].dropna().astype(str))
            if subjects_in_voucher and subjects_in_voucher.issubset(CASH_SUBJECTS):
                cash_voucher_ids.add(vid)

        # Step 0c: 检测汇兑损益——财务费用为唯一损益类科目 + 往来/资金科目
        # ---------------------------------------------------------------
        FX_SUBJECT = "财务费用"
        # 其他损益类科目（出现任何一个就不是纯汇兑损益）
        OTHER_PL_SUBJECTS = {
            "管理费用", "销售费用", "研发费用",
            "投资收益", "公允价值变动损益",
            "主营业务收入", "其他业务收入", "营业外收入",
            "主营业务成本", "其他业务成本",
            "税金及附加", "营业税金及附加", "所得税费用",
            "营业外支出", "资产减值损失", "信用减值损失",
            "以前年度损益调整",
        }
        # 不可能出现在汇兑损益场景的科目
        FX_BLACKLIST = {"应付职工薪酬", "应交税费", "应付利息", "应付股利", "应付债券","应付票据" }
        # 确认关键词（统一要求：避免代付货款等被误判为汇兑损益）
        FX_KEYWORDS = {"汇兑", "结汇", "收汇"}

        fx_voucher_ids = set()
        for vid, group in df.groupby(v_col):
            subjects = set(group[s_col].dropna().astype(str))

            # R1: 必须有财务费用
            if FX_SUBJECT not in subjects:
                continue
            # R2: 不能有其他损益类科目
            if subjects & OTHER_PL_SUBJECTS:
                continue
            # R3: 不能有黑名单科目
            if subjects & FX_BLACKLIST:
                continue
            # R4: 摘要必须包含汇兑关键词（往来科目也可能是代付货款，需摘要确认）
            summary_text = " ".join(group[sum_col].dropna().astype(str)) if summary_col_available else ""
            if not any(kw in summary_text for kw in FX_KEYWORDS):
                continue

            fx_voucher_ids.add(vid)

        # 为结账 + 资金往来 + 汇兑损益凭证预分配
        voucher_preassign: Dict[str, str] = {}
        for vid in closing_voucher_ids:
            voucher_preassign[str(vid)] = "其他业务"
        for vid in cash_voucher_ids:
            voucher_preassign[str(vid)] = "资金内部往来"
        for vid in fx_voucher_ids:
            voucher_preassign[str(vid)] = "汇兑损益"

        # 用于 PMI 计算的 DataFrame（排除预分配凭证 + 结账类科目）
        excluded_ids = closing_voucher_ids | cash_voucher_ids | fx_voucher_ids
        pmi_df = df[~df[v_col].isin(excluded_ids)].copy()
        pmi_df = pmi_df[~pmi_df[s_col].isin(PERIOD_CLOSING_SUBJECTS)]

        # ---------------------------------------------------------------
        # Step 1: 构建公司专属 PMI 矩阵
        # ---------------------------------------------------------------
        company_R = PMIMatrix.from_vouchers(pmi_df, v_col, s_col)

        # ---------------------------------------------------------------
        # Step 2: 加载全局计数器，构建通用 R，融合
        # ---------------------------------------------------------------
        self.global_counters.load()
        universal_R = self.global_counters.build_universal_R(company_R.subjects)

        if universal_R is not None and not universal_R.is_empty():
            final_R = company_R.fuse(universal_R, alpha)
        else:
            final_R = company_R

        self.final_R = final_R  # 缓存供 get_final_R() 使用

        if final_R.is_empty():
            # 完全没有有效数据（可能是全部凭证都是结转类）
            df = df.copy()
            df["业务分类"] = df[v_col].map(
                lambda vid: voucher_preassign.get(str(vid), "无法分类")
            )
            return df, pd.DataFrame(), {
                "total_vouchers": len(voucher_preassign),
                "coverage": 1.0,
            }

        # ---------------------------------------------------------------
        # Step 3-4: 初始化偏好向量 → 传播 w' = w × R
        # ---------------------------------------------------------------
        preferences = build_bucket_preferences(self.subject_list)
        # 只保留当前数据中存在的桶
        preferences = {k: v for k, v in preferences.items() if k in self.bucket_names}
        w_prime = CorrelationPropagator.propagate_all(preferences, final_R.to_dataframe())

        # ---------------------------------------------------------------
        # Step 4b: 加载历史学习数据（金额特征 + 词特征）
        # ---------------------------------------------------------------
        amount_profiler = AmountProfiler.from_dict(self.global_counters.amount_stats)
        amount_profiles = amount_profiler.compute_profiles()

        # 构建 from_dict 数据（历史词频从 tier 文件恢复）
        wl_init_data = {
            "word_counts": {},  # 不再持久化——tier 文件已有 count
            "total_vouchers": self.global_counters.N,
            "bucket_voucher_counts": self.global_counters.word_bucket_counts,
            # 从 tier 缓存提取历史词频（替代 word_raw.json）
            "tier1_counts": {b: {w: i["count"] for w, i in words.items()}
                           for b, words in self.global_counters.auto_scores_tier1.items()},
            "tier2_counts": {b: {w: i["count"] for w, i in words.items()}
                           for b, words in self.global_counters.auto_scores_tier2.items()},
            "_session_counter": self.global_counters.word_sessions.get("_session_counter", 0),
            "word_sessions": {k: v for k, v in self.global_counters.word_sessions.items() if k != "_session_counter"},
            "deleted_words": self.global_counters.auto_scores_deleted or [],
            "trash_bin": self.global_counters.auto_scores_tier3 or [],
        }
        word_learner = WordFeatureLearner.from_dict(wl_init_data)
        # 预计算自动词得分（有历史数据时才计算）
        word_learner.set_manual_keywords(self.keyword_matcher)
        word_learner.compute_auto_scores()
        # 恢复之前持久化的自动词缓存（如果 compute 没产生结果但有持久化数据）
        if not word_learner._auto_scores_tier1 and self.global_counters.auto_scores_tier1:
            word_learner._auto_scores_tier1 = self.global_counters.auto_scores_tier1
        if not word_learner._auto_scores_tier2 and self.global_counters.auto_scores_tier2:
            word_learner._auto_scores_tier2 = self.global_counters.auto_scores_tier2

        # 缓存 word_learner 供 UI 访问
        self.word_learner = word_learner

        # ---------------------------------------------------------------
        # Step 4c: 加载纠错回路
        # ---------------------------------------------------------------
        correction_mgr = CorrectionManager()
        correction_mgr.load()

        # ---------------------------------------------------------------
        # Step 5: 逐凭证分类
        # ---------------------------------------------------------------
        voucher_results = []    # 凭证级分数明细
        voucher_classification = {}  # 凭证号 → 桶名

        vouchers = df.groupby(v_col)

        for vid, group in vouchers:
            vid_str = str(vid)

            # 预分配凭证（结转期间损益等）跳过评分，直接使用预设分类
            if vid_str in voucher_preassign:
                top_bucket = voucher_preassign[vid_str]
                voucher_classification[vid] = top_bucket

                # 分数明细（预分配凭证的全部桶得分和偏置为0，仅预设桶得分=1）
                row_detail = {
                    "凭证号": vid_str,
                    "业务分类": top_bucket,
                    "摘要": str(group[sum_col].iloc[0])[:80] if sum_col and sum_col in group.columns else "",
                }
                for bucket_name in self._all_bucket_names:
                    row_detail[f"得分_{bucket_name}"] = 1.0 if bucket_name == top_bucket else 0.0
                    row_detail[f"结构_{bucket_name}"] = 0.0
                    row_detail[f"偏置_{bucket_name}"] = 0.0
                    row_detail[f"自动词_{bucket_name}"] = 0.0
                    row_detail[f"金额_{bucket_name}"] = 0.0
                voucher_results.append(row_detail)
                continue

            # 5a: 凭证向量 v
            v = self.vectorizer.vectorize(
                group, s_col, d_col, c_col, final_R.subjects
            )

            # 5b: 手工关键词偏置 b
            summary = str(group[sum_col].iloc[0]) if sum_col and sum_col in group.columns else ""
            subjects_in_voucher = group[s_col].dropna().astype(str).tolist()
            sub_details = group[sn_col].dropna().astype(str).tolist() if sn_col and sn_col in group.columns else None

            keyword_bias = self.keyword_matcher.match_voucher(
                summary, subjects_in_voucher, sub_details
            )

            # 5c: 自动词特征偏置 c（theory_boost §2）
            auto_word_bias = word_learner.match_voucher(
                summary, subjects_in_voucher, sub_details
            )

            # 5d: 金额特征惩罚分 s_amount（theory_boost §1）
            # 计算该凭证的合计金额（借方总额 = 贷方总额）
            voucher_amount = 0.0
            if d_col and d_col in group.columns:
                voucher_amount = float(group[d_col].dropna().apply(
                    lambda x: abs(float(x)) if pd.notna(x) else 0.0
                ).sum())
            amount_scores = amount_profiler.score_all(voucher_amount, amount_profiles)

            # 5e: 桶顺位增强 d（correct_errors_theory §2）
            rank_bonus = correction_mgr.compute_rank_bonus(
                summary, subjects_in_voucher, sub_details
            )

            # 5f: 评分 + 分类
            top_bucket, all_scores = Scorer.classify_voucher(
                v, w_prime, keyword_bias, BUCKET_CLARITY,
                auto_word_bias, amount_scores, rank_bonus,
            )

            voucher_classification[vid] = top_bucket

            # 记录分数明细（反映税费桶衰减后的真实值）
            from .config import TAX_DECAY
            row_detail = {
                "凭证号": vid_str,
                "业务分类": top_bucket,
                "摘要": summary[:80] if summary else "",
            }
            for bucket_name in self.bucket_names:
                total = all_scores.get(bucket_name, 0.0)
                b_val = keyword_bias.get(bucket_name, 0.0)
                c_val = auto_word_bias.get(bucket_name, 0.0)
                s_val = amount_scores.get(bucket_name, 0.0)
                if bucket_name == "税费":
                    b_val *= TAX_DECAY
                    c_val *= TAX_DECAY
                row_detail[f"得分_{bucket_name}"] = round(total, 6)
                row_detail[f"结构_{bucket_name}"] = round(total - b_val - c_val - s_val, 6)
                row_detail[f"偏置_{bucket_name}"] = round(b_val, 6)
                row_detail[f"自动词_{bucket_name}"] = round(c_val, 6)
                row_detail[f"金额_{bucket_name}"] = round(s_val, 6)
            voucher_results.append(row_detail)

        # ---------------------------------------------------------------
        # Step 6: 映射回原始 DataFrame
        # ---------------------------------------------------------------
        df = df.copy()
        df["业务分类"] = df[v_col].map(voucher_classification).fillna("未分类")

        # ---------------------------------------------------------------
        # Step 7: 先更新全局计数器（指纹去重在此处）
        # ---------------------------------------------------------------
        is_new_data = self.global_counters.update(pmi_df, v_col, s_col)

        # ---------------------------------------------------------------
        # Step 8: 学习（theory_boost — 金额特征 + 词特征）
        # ---------------------------------------------------------------
        # 金额特征：仅新数据更新（PMI 无关，指纹去重保护）
        # 自动词：按哈希分离存储——同哈希重跑时清除旧数据重新学习
        # ---------------------------------------------------------------
        if not voucher_preassign:
            voucher_preassign = {}
        learning_df = df[~df[v_col].astype(str).isin(voucher_preassign.keys())].copy()

        # 8a: 金额特征（保持不变——仅新数据更新）
        if is_new_data:
            if d_col and d_col in learning_df.columns:
                amount_profiler.update(learning_df, v_col, d_col)
                self.global_counters.amount_stats = amount_profiler.to_dict()

        # 8b: 自动词——按哈希分离
        fingerprint = self.global_counters._fingerprints[-1] if self.global_counters._fingerprints else \
                      self.global_counters._compute_fingerprint(pmi_df, v_col)

        # 同哈希重跑 → 清除旧自动词，重新学习
        if not is_new_data and self.global_counters.hash_word_exists(fingerprint):
            self.global_counters.delete_hash_words(fingerprint)

        # 从当前 session 的 word_learner 提取本哈希的词频
        # （word_learner 在 from_dict 时已载入历史 tier 数据，需先清零）
        session_wl = WordFeatureLearner()
        session_wl.set_manual_keywords(self.keyword_matcher)
        session_wl._deleted_words = word_learner._deleted_words
        session_wl._session_counter = word_learner._session_counter + 1

        # 只学习当前数据
        session_wl.update(learning_df, v_col,
                          sum_col if sum_col and sum_col in learning_df.columns else None,
                          sn_col if sn_col and sn_col in learning_df.columns else None)

        # 保存本哈希的词频
        sw_data = session_wl.to_dict()
        self.global_counters.save_hash_words(
            fingerprint,
            sw_data["word_counts"],
            sw_data["bucket_voucher_counts"],
        )

        # 8c: 聚合所有哈希 → 计算全局自动词
        all_wc, all_bvc = self.global_counters.load_all_hash_words()
        word_learner.word_counts = defaultdict(lambda: defaultdict(int))
        for bucket, words in all_wc.items():
            for word, cnt in words.items():
                word_learner.word_counts[bucket][word] = cnt
        word_learner._bucket_voucher_counts = all_bvc
        word_learner.total_vouchers = sum(all_bvc.values())

        # 重新计算词 sessions（从哈希文件数推断）
        hash_count = len(list(self.global_counters.HASH_WORD_DIR.glob("*.json"))) if self.global_counters.HASH_WORD_DIR.exists() else 0
        word_learner._session_counter = hash_count

        word_learner.compute_auto_scores()
        wl_data = word_learner.to_dict()
        self.global_counters.word_counts = all_wc
        self.global_counters.word_bucket_counts = all_bvc
        self.global_counters.auto_scores_tier1 = word_learner._auto_scores_tier1
        self.global_counters.auto_scores_tier2 = word_learner._auto_scores_tier2
        self.global_counters.auto_scores_tier3 = word_learner._trash_bin
        self.global_counters.auto_scores_deleted = sorted(word_learner._deleted_words)
        self.global_counters.word_sessions = wl_data.get("word_sessions", {})

        self.global_counters.save()

        # ---------------------------------------------------------------
        # 统计摘要
        # ---------------------------------------------------------------
        stats = self._compute_stats(df, v_col, voucher_classification, company_R, final_R)

        score_detail_df = pd.DataFrame(voucher_results)
        return df, score_detail_df, stats

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def _compute_stats(self, df: pd.DataFrame,
                       v_col: str,
                       classifications: Dict,
                       company_R: PMIMatrix,
                       final_R: PMIMatrix) -> dict:
        """计算分类统计摘要（统一按凭证数口径）。"""
        total_vouchers = len(classifications)

        # 按凭证号分组，取每张凭证第一个「业务分类」值
        voucher_bucket = df.groupby(v_col)["业务分类"].first()
        bucket_counts = voucher_bucket.value_counts().to_dict()

        # 金额分布（按凭证汇总）
        amount_col = None
        for col in df.columns:
            for pattern in ["借方金额", "借方", "debit"]:
                if pattern in str(col):
                    amount_col = col
                    break
            if amount_col:
                break

        amount_by_bucket = {}
        if amount_col:
            for bucket in self._all_bucket_names:
                # 属于该桶的凭证号
                bucket_vouchers = voucher_bucket[voucher_bucket == bucket].index
                total = df[df[v_col].isin(bucket_vouchers)][amount_col].apply(
                    lambda x: abs(float(x)) if pd.notna(x) else 0.0
                ).sum()
                amount_by_bucket[bucket] = round(float(total), 2)

        # 覆盖率（按凭证数）
        unclassified_vouchers = bucket_counts.get("未分类", 0)
        classified_vouchers = total_vouchers - unclassified_vouchers
        coverage = classified_vouchers / total_vouchers if total_vouchers > 0 else 0.0

        return {
            "total_vouchers": total_vouchers,
            "total_rows": len(df),
            "classified_count": classified_vouchers,
            "unclassified_count": unclassified_vouchers,
            "coverage": round(coverage, 4),
            "bucket_counts": bucket_counts,
            "amount_by_bucket": amount_by_bucket,
            "company_R_shape": company_R.shape,
            "final_R_shape": final_R.shape,
            "global_N": self.global_counters.N,
            "keyword_count": self.keyword_matcher.keyword_count,
        }

    # ------------------------------------------------------------------
    # PMI 矩阵导出
    # ------------------------------------------------------------------

    def get_final_R(self) -> Optional[pd.DataFrame]:
        """获取 classify() 执行后缓存的融合 PMI 矩阵。

        如果尚未执行 classify() 或分类失败，返回 None。
        """
        if self.final_R is not None and not self.final_R.is_empty():
            return self.final_R.to_dataframe()
        return None

    # ------------------------------------------------------------------
    # 全局计数器管理
    # ------------------------------------------------------------------

    def reset_global_counters(self):
        """重置全局计数器（删除所有累积数据）。"""
        self.global_counters.delete_persisted()
        self.global_counters.reset()

    def get_global_stats(self) -> dict:
        """获取全局计数器摘要。"""
        self.global_counters.load()
        return self.global_counters.get_stats()

    # ------------------------------------------------------------------
    # 列名自动检测
    # ------------------------------------------------------------------

    @staticmethod
    def auto_detect_columns(df: pd.DataFrame) -> Dict[str, str]:
        """自动检测 DataFrame 中的关键列名。

        参照现有两个功能的模式，返回 {key: actual_column_name}。
        """
        mapping = {}
        columns = list(df.columns)

        for key, patterns in COLUMN_NAME_PATTERNS.items():
            for col in columns:
                col_str = str(col).strip()
                for pat in patterns:
                    if pat in col_str:
                        mapping[key] = col
                        break
                if key in mapping:
                    break

        return mapping

    @staticmethod
    def validate_column_mapping(mapping: Dict[str, str]) -> Tuple[bool, List[str]]:
        """验证列名映射是否包含必需字段。"""
        required = ["voucher_no", "subject", "debit", "credit"]
        missing = [k for k in required if k not in mapping or not mapping[k]]
        return len(missing) == 0, missing
