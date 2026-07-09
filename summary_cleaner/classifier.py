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
from .persistence import GlobalCounters


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
        self.bucket_names = list(load_buckets_json(buckets_path).keys())

        # classify() 执行后缓存的结果
        self.final_R: Optional[PMIMatrix] = None

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
        # 检测规则：摘要含"期间损益" + 科目含"本年利润"
        # 这些凭证是机械性的期末结转，无业务含义，直接归入「其他业务」
        # 同时排除本年利润科目，不参与 PMI 计算
        PERIOD_CLOSING_SUBJECTS = {"本年利润", "以前年度损益调整"}
        summary_col_available = sum_col and sum_col in df.columns

        closing_voucher_ids = set()
        if summary_col_available:
            for vid, group in df.groupby(v_col):
                summary_text = " ".join(group[sum_col].dropna().astype(str))
                subjects_in_voucher = set(group[s_col].dropna().astype(str))
                # 摘要含"期间损益" 且 科目含本年利润类
                if "期间损益" in summary_text and (subjects_in_voucher & PERIOD_CLOSING_SUBJECTS):
                    closing_voucher_ids.add(vid)

        # 为结账凭证预分配"其他业务"
        voucher_preassign: Dict[str, str] = {}
        for vid in closing_voucher_ids:
            voucher_preassign[str(vid)] = "其他业务"

        # 用于 PMI 计算的 DataFrame（排除结账凭证 + 排除结账类科目）
        pmi_df = df[~df[v_col].isin(closing_voucher_ids)].copy()
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
            # 完全没有有效数据
            df["业务分类"] = "无法分类"
            return df, pd.DataFrame(), {"error": "无有效凭证数据"}

        # ---------------------------------------------------------------
        # Step 3-4: 初始化偏好向量 → 传播 w' = w × R
        # ---------------------------------------------------------------
        preferences = build_bucket_preferences(self.subject_list)
        # 只保留当前数据中存在的桶
        preferences = {k: v for k, v in preferences.items() if k in self.bucket_names}
        w_prime = CorrelationPropagator.propagate_all(preferences, final_R.to_dataframe())

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
                for bucket_name in self.bucket_names:
                    row_detail[f"得分_{bucket_name}"] = 1.0 if bucket_name == top_bucket else 0.0
                    row_detail[f"向量_{bucket_name}"] = 0.0
                    row_detail[f"偏置_{bucket_name}"] = 0.0
                voucher_results.append(row_detail)
                continue

            # 5a: 凭证向量 v
            v = self.vectorizer.vectorize(
                group, s_col, d_col, c_col, final_R.subjects
            )

            # 5b: 关键词偏置 b
            summary = str(group[sum_col].iloc[0]) if sum_col and sum_col in group.columns else ""
            subjects_in_voucher = group[s_col].dropna().astype(str).tolist()
            sub_details = group[sn_col].dropna().astype(str).tolist() if sn_col and sn_col in group.columns else None

            keyword_bias = self.keyword_matcher.match_voucher(
                summary, subjects_in_voucher, sub_details
            )

            # 5c: 评分 + 分类
            top_bucket, all_scores = Scorer.classify_voucher(v, w_prime, keyword_bias)

            voucher_classification[vid] = top_bucket

            # 记录分数明细
            row_detail = {
                "凭证号": vid_str,
                "业务分类": top_bucket,
                "摘要": summary[:80] if summary else "",
            }
            for bucket_name in self.bucket_names:
                row_detail[f"得分_{bucket_name}"] = round(all_scores.get(bucket_name, 0.0), 6)
                row_detail[f"向量_{bucket_name}"] = round(
                    all_scores.get(bucket_name, 0.0) - keyword_bias.get(bucket_name, 0.0), 6
                )
                row_detail[f"偏置_{bucket_name}"] = round(keyword_bias.get(bucket_name, 0.0), 6)
            voucher_results.append(row_detail)

        # ---------------------------------------------------------------
        # Step 6: 映射回原始 DataFrame
        # ---------------------------------------------------------------
        df = df.copy()
        df["业务分类"] = df[v_col].map(voucher_classification).fillna("未分类")

        # ---------------------------------------------------------------
        # Step 7: 更新全局计数器
        # ---------------------------------------------------------------
        self.global_counters.update(pmi_df, v_col, s_col)
        self.global_counters.save()

        # ---------------------------------------------------------------
        # 统计摘要
        # ---------------------------------------------------------------
        stats = self._compute_stats(df, voucher_classification, company_R, final_R)

        score_detail_df = pd.DataFrame(voucher_results)
        return df, score_detail_df, stats

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def _compute_stats(self, df: pd.DataFrame,
                       classifications: Dict,
                       company_R: PMIMatrix,
                       final_R: PMIMatrix) -> dict:
        """计算分类统计摘要（统一按凭证数口径）。"""
        total_vouchers = len(classifications)

        # 按凭证数统计（每张凭证只取第一条分录的业务分类）
        voucher_col = [c for c in df.columns if c not in ("业务分类",)][0]
        # 按凭证号分组，取每张凭证第一个「业务分类」值
        voucher_bucket = df.groupby(voucher_col)["业务分类"].first()
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
            for bucket in self.bucket_names:
                # 属于该桶的凭证号
                bucket_vouchers = voucher_bucket[voucher_bucket == bucket].index
                total = df[df[voucher_col].isin(bucket_vouchers)][amount_col].apply(
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
