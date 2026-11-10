# -*- coding: utf-8 -*-
"""桶合并表：把细分桶折叠成父桶，其余代码不需要知道细节。

当前只用于「费用族」：`费用·办公与行政运营` 等 5 个子桶 → `费用`。

为什么用"折叠"而不是直接改写金标准：
1. **标签不可再生**。子桶名一旦被物理改写，以后想恢复子级就只能重新标注；
   折叠是读取时生效，原始 `training_data.json` 一个字都不动。
2. 历史任务、报告、导出里的旧桶名还能被正确解释（`fold()` 幂等）。
3. 想反悔：删掉 `bucket_merge.json` 里的条目即可，数据侧零成本。

依据（验证集 13,773 条实测）：
- 费用族占金标准 **24.1%**（5,595 / 23,230 条），其中 1,639 条只标到「费用」；
- 费用族内部互串占全部错误的 **30.9%**（270 / 873）；
- 同模型折叠后准确率 93.66% → **95.62%**（+1.96pt），不重训。

文件格式（`assets/bucket_merge.json`）::

    {"merge": {"费用·办公与行政运营": "费用", ...},
     "note": "...", "updated": "2026-11-10"}
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set

_ASSET = Path(__file__).resolve().parent / "assets" / "bucket_merge.json"


@lru_cache(maxsize=1)
def _load() -> Dict[str, str]:
    try:
        data = json.loads(_ASSET.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    merge = data.get("merge") or {}
    return {str(k): str(v) for k, v in merge.items() if k and v and k != v}


def merge_map() -> Dict[str, str]:
    """子桶 → 父桶（只读副本）。"""
    return dict(_load())


def reset_cache() -> None:
    _load.cache_clear()


def fold(bucket: str) -> str:
    """把桶名折叠到最终口径；未配置的子桶原样返回（幂等）。"""
    return _load().get(bucket, bucket)


def folded_children() -> Set[str]:
    """被折叠掉的桶名（它们不该再作为模型输出或下拉选项出现）。"""
    return set(_load())


def children_of(parent: str) -> List[str]:
    return sorted(k for k, v in _load().items() if v == parent)
