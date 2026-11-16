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
from journal_io import contracts

# id → 模块路径（模块里要有 build() -> Feature）。
# **顺序和归属都从域契约来**（`journal_io/contracts.py` 的 DOMAINS）：层决定卡片
# 顺序，也决定新建任务时上传什么。功能模块名一律是 `features.<id>`，所以这里只
# 摊平一次，不再手写第二份清单——手写的那份迟早和契约对不上。
_LOADERS: Dict[str, str] = {fid: f"features.{fid}" for fid in contracts.feature_ids()}

# id → 中文名。**只给"读状态"用**（`jobflow.scan` 列任务清单时不能 import 功能
# 模块——那会把 torch 拉起来）。真正的名字在 Feature.name，这里只是老任务的兜底。
_NAMES: Dict[str, str] = {
    "cleaning": "序时账清洗",
    "cashflow": "现金流量表编写",
    "contra": "对方科目分析",
    "entry_test": "会计分录测试",
    "cost": "成本计算",
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


def domains() -> List[dict]:
    """域清单（纯数据，不 import 任何功能模块）。

    前端拿它建分组标题和"新建任务"的入口；功能卡本身还是从 `manifests()` 来，
    因为名字、状态、能不能跑都在功能自己身上。
    """
    return contracts.manifests()


def domain_of(feature_id: str) -> str:
    return contracts.of_feature(feature_id)


def all() -> List[Feature]:
    return [get(f) for f in _LOADERS]


def manifests() -> List[dict]:
    return [f.manifest() for f in all()]
