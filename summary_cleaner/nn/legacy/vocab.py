# -*- coding: utf-8 -*-
"""
词表管理器 — Pattern 和 Keyword 的 string ↔ id 映射

Pattern 格式: "科目名|方向"  (如 "制造费用|借", "银行存款|贷")
  - 只存一级科目 + 借贷方向，不含科目明细
  - 方向: "借" 或 "贷"

Keyword 格式: jieba 分词后的词条 (如 "办公费", "顺丰")
  - 经过 7 条过滤规则（同 memory_learner.py 的分词逻辑）
  - 可以是手动关键词命中词，也可以是自动词

序列化格式:
{
  "patterns":       ["制造费用|借", "银行存款|贷", ...],
  "keywords":       ["办公费", "顺丰", ...],
  "num_patterns":   500,
  "num_keywords":   3000,
  "created_at":     "2024-07-24 15:30:00",
  "updated_at":     "2024-07-24 16:00:00"
}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union


class VocabManager:
    """管理 Pattern 和 Keyword 的词汇表。

    支持:
    - 增量添加（训练时遇到新 Pattern/Keyword 自动分配 ID）
    - 序列化/反序列化（JSON）
    - 查找（string → id 和 id → string）
    """

    def __init__(self):
        # string → id
        self._pattern_to_id: Dict[str, int] = {}
        self._keyword_to_id: Dict[str, int] = {}
        # id → string
        self._id_to_pattern: Dict[int, str] = {}
        self._id_to_keyword: Dict[int, str] = {}

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def num_patterns(self) -> int:
        return len(self._pattern_to_id)

    @property
    def num_keywords(self) -> int:
        return len(self._keyword_to_id)

    @property
    def patterns(self) -> List[str]:
        """按 ID 顺序返回所有 Pattern 字符串。"""
        return [
            self._id_to_pattern[i]
            for i in range(self.num_patterns)
        ]

    @property
    def keywords(self) -> List[str]:
        """按 ID 顺序返回所有 Keyword 字符串。"""
        return [
            self._id_to_keyword[i]
            for i in range(self.num_keywords)
        ]

    # ------------------------------------------------------------------
    # 添加
    # ------------------------------------------------------------------

    def add_pattern(self, pattern: str) -> int:
        """添加一个 Pattern，返回其 ID（已存在则返回已有 ID）。"""
        pattern = pattern.strip()
        if pattern not in self._pattern_to_id:
            pid = len(self._pattern_to_id)
            self._pattern_to_id[pattern] = pid
            self._id_to_pattern[pid] = pattern
        return self._pattern_to_id[pattern]

    def add_keyword(self, keyword: str) -> int:
        """添加一个 Keyword，返回其 ID（已存在则返回已有 ID）。"""
        keyword = keyword.strip()
        if keyword not in self._keyword_to_id:
            kid = len(self._keyword_to_id)
            self._keyword_to_id[keyword] = kid
            self._id_to_keyword[kid] = keyword
        return self._keyword_to_id[keyword]

    def add_patterns(self, patterns: List[str]) -> List[int]:
        """批量添加 Patterns。"""
        return [self.add_pattern(p) for p in patterns]

    def add_keywords(self, keywords: List[str]) -> List[int]:
        """批量添加 Keywords。"""
        return [self.add_keyword(k) for k in keywords]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_pattern_id(self, pattern: str) -> Optional[int]:
        """获取 Pattern ID，不存在返回 None。"""
        return self._pattern_to_id.get(pattern.strip())

    def get_keyword_id(self, keyword: str) -> Optional[int]:
        """获取 Keyword ID，不存在返回 None。"""
        return self._keyword_to_id.get(keyword.strip())

    def get_or_add_pattern(self, pattern: str) -> int:
        """获取 Pattern ID，不存在则自动添加。"""
        return self.add_pattern(pattern)

    def get_or_add_keyword(self, keyword: str) -> int:
        """获取 Keyword ID，不存在则自动添加。"""
        return self.add_keyword(keyword)

    def lookup_patterns(self, patterns: List[str]) -> List[int]:
        """查找多个 Pattern ID，不存在的返回已有 ID 或自动新增。"""
        return [self.get_or_add_pattern(p) for p in patterns]

    def lookup_keywords(self, keywords: List[str]) -> List[int]:
        """查找多个 Keyword ID，不存在的跳过（返回空列表）。"""
        result = []
        for k in keywords:
            kid = self.get_keyword_id(k)
            if kid is not None:
                result.append(kid)
        return result

    def lookup_keywords_strict(self, keywords: List[str]) -> List[int]:
        """只返回词表中存在的 Keyword ID（不自动新增）。"""
        result = []
        for k in keywords:
            kid = self.get_keyword_id(k)
            if kid is not None:
                result.append(kid)
        return result

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "patterns": self.patterns,
            "keywords": self.keywords,
            "num_patterns": self.num_patterns,
            "num_keywords": self.num_keywords,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def save(self, path: Union[str, Path]):
        """保存词表到 JSON 文件。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        data["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "VocabManager":
        """从 JSON 文件加载词表。"""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"词表文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        vocab = cls()
        for pattern in data.get("patterns", []):
            vocab.add_pattern(pattern)
        for keyword in data.get("keywords", []):
            vocab.add_keyword(keyword)
        return vocab

    @classmethod
    def from_data(
        cls,
        patterns: List[str],
        keywords: List[str],
    ) -> "VocabManager":
        """从 Pattern 和 Keyword 列表构建词表。"""
        vocab = cls()
        for p in patterns:
            vocab.add_pattern(p)
        for k in keywords:
            vocab.add_keyword(k)
        return vocab

    # ------------------------------------------------------------------
    # 信息
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """返回词表统计信息。"""
        return {
            "num_patterns": self.num_patterns,
            "num_keywords": self.num_keywords,
            "top_patterns": self.patterns[:10],
            "top_keywords": self.keywords[:10],
        }

    def __repr__(self) -> str:
        return (
            f"VocabManager(patterns={self.num_patterns}, "
            f"keywords={self.num_keywords})"
        )
