# -*- coding: utf-8 -*-
"""功能注册表。

一个功能 = 一个 `Feature`（阶段链 + 字段需求 + 导出项）。注册在这里而不是散在
API 路由里，是为了让 **CLI、API、前端读同一份清单**（`journal features` /
`GET /api/features`）。

按需导入：`get("cashflow")` 才会 import 现金流那套（连带 torch），
列清单时不要把所有功能都拉起来。
"""

from __future__ import annotations

import importlib
from typing import Dict, List

from jobflow.feature import Feature

# id → 模块路径（模块里要有 build() -> Feature）
_LOADERS: Dict[str, str] = {
    # 顺序 = 前端菜单顺序；cleaning 在最前，因为它是所有下游的第一段
    "cleaning": "features.cleaning",
    "cashflow": "features.cashflow",
    "contra": "features.contra",
    "entry_test": "features.entry_test",
}

# id → 中文名。**只给"读状态"用**（`jobflow.scan` 列任务清单时不能 import 功能
# 模块——那会把 torch 拉起来）。真正的名字在 Feature.name，这里只是老任务的兜底。
_NAMES: Dict[str, str] = {
    "cleaning": "序时账清洗",
    "cashflow": "现金流量表编写",
    "contra": "对方科目分析",
    "entry_test": "会计分录测试",
}

_cache: Dict[str, Feature] = {}


def get(feature_id: str) -> Feature:
    fid = str(feature_id).strip()
    if fid not in _LOADERS:
        raise KeyError(f"未知功能 {fid!r}（可用：{list(_LOADERS)}）")
    if fid not in _cache:
        mod = importlib.import_module(_LOADERS[fid])
        _cache[fid] = mod.build()
    return _cache[fid]


def ids() -> List[str]:
    return list(_LOADERS)


def names() -> Dict[str, str]:
    """id → 中文名（不 import 任何功能模块）。"""
    return dict(_NAMES)


def all() -> List[Feature]:
    return [get(f) for f in _LOADERS]


def manifests() -> List[dict]:
    return [f.manifest() for f in all()]
