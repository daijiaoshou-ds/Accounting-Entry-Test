# -*- coding: utf-8 -*-
"""v10.1 桶注册表的单一数据源适配器。

背景
----
旧体系把桶定义分散在三处：``config.py::BUCKET_REGISTRY``、
``assets/业务桶与keyword.json``、``bucket_to_cf.json``。改桶时要手工同步，
极易漏。本模块让 ``config.py`` 直接从 ``assets/bucket_taxonomy_v10.json``
生成注册表，同时把旧版人工调过的科目权重继承过来。

设计
----
- ``LEGACY_ALIAS``：旧桶名 → 新桶名（一对一），用于迁移运行时配置
  和散落在代码里的字面量。
- ``SPREAD``：旧桶名 → 多个新桶名（一对多），用于继承科目偏好。
  例如旧「长期资产」同时覆盖购建和处置。
- 新桶的 clarity 是 1–5，旧程序用 1–10，转换时乘 2 保持相对关系。

开关
----
``SUMMARY_CLEANER_BUCKETS=v9`` 可回退到旧 20 桶（用于对比 / 应急）。
默认 v10。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

_ASSETS = Path(__file__).resolve().parent / "assets" / "bucket_taxonomy_v10.json"

# ── 旧 20 桶 → 新 31 桶（一对一）──
LEGACY_ALIAS: Dict[str, str] = {
    "销售收入": "销售",
    "存货采购": "采购",
    "长期资产": "长期资产购建",
    "职工薪酬": "薪酬",
    # v10.2：「生产制造」重新成为正式桶（经济业务口径的生产转化环节），
    # 不再重定向到成本结转；成本结转只保留销售出库与商品成本结转。
    "生产制造": "生产制造",
    "日常费用": "费用",
    "分红股利": "利润分配",
    "税费": "税费",
    "投资本金": "投资",
    "借款筹资": "借款",
    "利息收支": "利息支出",
    "员工借款": "员工借支",
    "押金保证金": "押金保证金",
    "单位往来": "单位往来",
    "其他业务": "其他经营收支",
    "折旧摊销": "折旧摊销",
    "资金内部往来": "资金内部往来",
    "汇兑损益": "汇兑损益",
    "研发": "研发",
    "政府补助": "政府补助",
    # v10.2：桶名扩义，平台费/境外月费/信用证费都进来了
    "银行手续费": "结算手续费",
}

# ── 旧桶拆成多个新桶：科目偏好要复制给每一个 ──
SPREAD: Dict[str, List[str]] = {
    "长期资产": ["长期资产购建", "长期资产处置"],
    "投资本金": ["投资", "权益筹资"],
    "分红股利": ["利润分配", "投资"],
    "利息收支": ["利息支出", "存款利息"],
    "日常费用": ["费用"],
}

# 旧版人工调过的权重注入新桶时，上限压到 1.0；anchors 用 0.8 起步
_ANCHOR_WEIGHT = 0.8
_FIRST_ANCHOR_WEIGHT = 1.0


def load_taxonomy() -> dict:
    """读取 v10.1 配置。"""
    with open(_ASSETS, encoding="utf-8") as f:
        return json.load(f)


def build_registry(legacy: Optional[Dict[str, dict]] = None) -> Dict[str, dict]:
    """生成新版 BUCKET_REGISTRY。

    Args:
        legacy: 旧 ``config.py::BUCKET_REGISTRY``，用于继承人工调过的
            科目权重（1.0/0.8/0.5 分级）。传 None 时只用 anchors。

    Returns:
        ``{桶名: {"clarity": float, "subjects": {科目: 权重}, "keywords": []}}``
        格式与旧版完全一致，因此下游派生逻辑无需改动。
    """
    cfg = load_taxonomy()
    registry: Dict[str, dict] = {}

    # ① 从 v10.1 配置建骨架（31 桶 + 父级「费用」）
    for name, info in cfg["buckets"].items():
        anchors = list(info.get("anchors") or [])
        subjects = {}
        for i, subj in enumerate(anchors):
            subjects[subj] = _FIRST_ANCHOR_WEIGHT if i == 0 else _ANCHOR_WEIGHT
        registry[name] = {
            "clarity": float(info.get("clarity") or 3) * 2.0,  # 1–5 → 1–10
            "subjects": subjects,
            "keywords": list(info.get("keywords") or []),
        }

    for name, info in (cfg.get("parent_nodes") or {}).items():
        registry.setdefault(name, {
            "clarity": float(info.get("clarity") or 2) * 2.0,
            "subjects": {},
            "keywords": [],
        })

    # ② 继承旧版人工权重：按 LEGACY_ALIAS / SPREAD 注入
    for old_name, old_info in (legacy or {}).items():
        targets = SPREAD.get(old_name) or [LEGACY_ALIAS.get(old_name, old_name)]
        old_subjects = old_info.get("subjects") or {}
        old_kws = old_info.get("keywords") or []
        for target in targets:
            if target not in registry:
                continue
            entry = registry[target]
            for subj, weight in old_subjects.items():
                w = float(weight)
                if w > entry["subjects"].get(subj, 0.0):
                    entry["subjects"][subj] = min(w, 1.0)
            for kw in old_kws:
                if kw not in entry["keywords"]:
                    entry["keywords"].append(kw)

    return registry


def apply_alias(name: str) -> str:
    """把旧桶名转成新桶名；本来就是新桶名则原样返回。"""
    return LEGACY_ALIAS.get(name, name)


def alias_keys(mapping: dict) -> dict:
    """把 ``{关键词: {桶名: 分数}}`` / ``{角色: 桶名}`` 里的旧桶名替换掉。

    一对多的旧桶按 SPREAD 的第一个目标处理（分数表只需要一个落点）。
    """
    out = {}
    for key, value in mapping.items():
        if isinstance(value, dict):
            merged: Dict[str, float] = {}
            for bucket, score in value.items():
                targets = SPREAD.get(bucket) or [apply_alias(bucket)]
                target = targets[0]
                merged[target] = max(merged.get(target, -1e9), float(score))
            out[key] = merged
        elif isinstance(value, str):
            out[key] = apply_alias(value)
        elif isinstance(value, (set, list, tuple)):
            out[key] = type(value)(apply_alias(x) for x in value)
        else:
            out[key] = value
    return out
