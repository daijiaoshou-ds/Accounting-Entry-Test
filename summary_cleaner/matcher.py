# -*- coding: utf-8 -*-
"""
AC自动机 + 关键词匹配器 — 计算偏置项 b

PurePythonAC: 从 v1 summary-cleaning/scripts/train_bucket_classifier.py 适配
KeywordMatcher: 封装 AC 自动机，为每张凭证计算各桶的关键词偏置分数
"""

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .config import load_buckets_json, KEYWORD_EXPLICIT_SCORES


# ============================================================================
# AC 自动机（纯 Python 实现）
# ============================================================================

class PurePythonAC:
    """Aho-Corasick 自动机，用于多模式字符串匹配。"""

    def __init__(self):
        self.root: dict = {}
        self._built = False

    def add_word(self, word: str, value):
        """向自动机中添加一个关键词及其关联值。"""
        if not word:
            return
        node = self.root
        for ch in word:
            node = node.setdefault(ch, {})
        node.setdefault("output", []).append(value)

    def make_automaton(self):
        """构建 fail 指针，完成自动机构造。"""
        from collections import deque

        queue = deque()
        self.root["fail"] = self.root

        for ch, node in self.root.items():
            if ch in ("output", "fail"):
                continue
            node["fail"] = self.root
            queue.append(node)

        while queue:
            current = queue.popleft()
            for ch, child in current.items():
                if ch in ("output", "fail"):
                    continue

                fail_node = current["fail"]
                while fail_node is not self.root and ch not in fail_node:
                    fail_node = fail_node["fail"]

                if ch in fail_node:
                    child["fail"] = fail_node[ch]
                else:
                    child["fail"] = self.root

                child.setdefault("output", []).extend(
                    child["fail"].get("output", [])
                )
                queue.append(child)

        self._built = True

    def iter(self, text: str):
        """遍历文本，逐个字符产出匹配到的 (结束位置, 关联值)。"""
        if not self._built:
            raise RuntimeError("必须先调用 make_automaton() 构建自动机")

        node = self.root
        for i, ch in enumerate(text):
            while node is not self.root and ch not in node:
                node = node["fail"]

            if ch in node:
                node = node[ch]
            else:
                node = self.root

            for value in node.get("output", []):
                yield (i, value)


# ============================================================================
# 关键词匹配器
# ============================================================================

class KeywordMatcher:
    """关键词匹配器：将摘要 + 科目名称送入 AC 自动机扫描，输出各桶的偏置分数 b。

    评分规则（两级）：
    A. 显式配置优先：KEYWORD_EXPLICIT_SCORES 中已配置的关键词，
       直接使用显式分数（支持负分抑制，如"报销"→生产制造-0.3）。
    B. 自动生成兜底：未显式配置的关键词，按以下规则自动生成：
       1. 基础分 base = 0.6 / N（N = 该关键词出现的桶数，稀释共享词）
       2. 长词加成：≥4 个中文字符 → +0.2
       3. 科目名加成：关键词本身是一级科目名 → +0.5

    去重：同一关键词在同一凭证中只计一次。
    累加：多个关键词命中同一个桶 → 分数累加。
    """

    def __init__(self, buckets_path: Path = None, subject_list: List[str] = None):
        """
        Args:
            buckets_path: 业务桶与keyword.json 路径
            subject_list: 一级科目列表（用于判断"科目名加成"）
        """
        self.buckets: Dict[str, List[str]] = load_buckets_json(buckets_path)
        self.subject_set: Set[str] = set(subject_list) if subject_list else set()

        # 计算每个关键词对每个桶的分数
        self.keyword_scores: Dict[str, Dict[str, float]] = self._compute_keyword_scores()

        # 构建 AC 自动机
        self.automaton = PurePythonAC()
        self._keyword_bucket_map: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        self._build_automaton()

    # ------------------------------------------------------------------
    # 评分计算
    # ------------------------------------------------------------------

    def _compute_keyword_scores(self) -> Dict[str, Dict[str, float]]:
        """关键词→桶→分数映射。显式配置优先，自动生成兜底。

        返回 {keyword: {bucket_name: score}}，score 可为负。
        """
        # 统计每个关键词出现在哪些桶中（用于自动生成的稀释计算）
        kw_buckets: Dict[str, List[str]] = defaultdict(list)
        for bucket_name, keywords in self.buckets.items():
            for kw in keywords:
                if kw and isinstance(kw, str):
                    kw_buckets[kw].append(bucket_name)

        scores: Dict[str, Dict[str, float]] = {}
        for kw, bucket_list in kw_buckets.items():
            # Step A: 检查是否有显式配置
            explicit = KEYWORD_EXPLICIT_SCORES.get(kw)
            if explicit is not None:
                # 使用显式分数（可为负）
                # 显式配置中未涉及的桶，如有必要保留自动生成值
                result = dict(explicit)
                # 对显式中未配置但该词所在的其他桶，给 0（不自动追加）
                scores[kw] = result
                continue

            # Step B: 自动生成（全正分）
            n = len(bucket_list)
            base = 0.6 / n  # 稀释

            chinese_chars = len(re.findall(r'[一-鿿]', kw))
            length_bonus = 0.2 if chinese_chars >= 4 else 0.0

            subject_bonus = 0.5 if kw in self.subject_set else 0.0

            per_bucket_score = round(base + length_bonus + subject_bonus, 4)
            scores[kw] = {b: per_bucket_score for b in bucket_list}

        return scores

    # ------------------------------------------------------------------
    # AC 自动机构建
    # ------------------------------------------------------------------

    def _build_automaton(self):
        """将所有关键词注册到 AC 自动机中。"""
        for kw, bucket_scores in self.keyword_scores.items():
            for bucket_name, score in bucket_scores.items():
                self.automaton.add_word(kw, (bucket_name, kw, score))
                self._keyword_bucket_map[kw].append((bucket_name, score))
        self.automaton.make_automaton()

    # ------------------------------------------------------------------
    # 匹配接口
    # ------------------------------------------------------------------

    def match_text(self, text: str) -> Dict[str, float]:
        """对单段文本执行 AC 自动机扫描，返回 {bucket_name: accumulated_score}。

        去重：同一关键词在同一文本中只计一次。
        """
        if not isinstance(text, str) or not text:
            return {}

        seen_keywords: Set[str] = set()
        bucket_scores: Dict[str, float] = defaultdict(float)

        for _end_pos, (bucket_name, kw, score) in self.automaton.iter(text):
            if kw not in seen_keywords:
                seen_keywords.add(kw)
                bucket_scores[bucket_name] += score

        return dict(bucket_scores)

    def match_voucher(self, summary: str, subjects: List[str],
                       subject_details: List[str] = None) -> Dict[str, float]:
        """扫描一张凭证的摘要 + 所有科目名称，返回各桶累加偏置。

        匹配对象：
        - 摘要文本
        - 一级科目名称列表
        - 科目名称（二级明细）列表（如提供）

        去重范围：整张凭证范围内，同一关键词只计一次。
        """
        parts = []
        if summary:
            parts.append(str(summary))
        if subjects:
            parts.append(" ".join(str(s) for s in subjects if s))
        if subject_details:
            parts.append(" ".join(str(s) for s in subject_details if s))

        combined = " ".join(parts)
        return self.match_text(combined)

    def get_hit_detail(self, text: str) -> Dict[str, List[str]]:
        """返回命中的详细信息：{bucket_name: [matched_keyword, ...]}。

        用于 UI 展示关键词命中情况。
        """
        if not isinstance(text, str) or not text:
            return {}

        seen: Dict[str, Set[str]] = defaultdict(set)
        for _end_pos, (bucket_name, kw, _score) in self.automaton.iter(text):
            seen[bucket_name].add(kw)

        return {k: sorted(v) for k, v in seen.items()}

    def match_voucher_detail(self, summary: str, subjects: List[str],
                              subject_details: List[str] = None) -> Dict[str, List[str]]:
        """返回凭证的详细命中信息。"""
        parts = []
        if summary:
            parts.append(str(summary))
        if subjects:
            parts.append(" ".join(str(s) for s in subjects if s))
        if subject_details:
            parts.append(" ".join(str(s) for s in subject_details if s))
        return self.get_hit_detail(" ".join(parts))

    @property
    def keyword_count(self) -> int:
        """关键词总数。"""
        return len(self.keyword_scores)

    @property
    def bucket_names(self) -> List[str]:
        """桶名列表。"""
        return list(self.buckets.keys())
