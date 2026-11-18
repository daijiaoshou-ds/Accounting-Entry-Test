# -*- coding: utf-8 -*-
"""域（Domain）与输入契约 —— 功能分层的唯一注册表。

一个「域」= **一份可以共用的数据配置** + 在这份数据上跑的一组功能。
判据只有一条：两个功能能不能共用同一份已配好的数据？能就同域。

域只决定三件事，不决定第四件：

    1. 新建任务时上传什么          → InputSpec.label（"序时账" / "采购明细"）
    2. 这份文件算不算该域的数据    → InputSpec.fields（比单个功能宽松）
    3. 功能卡怎么分组              → DomainSpec.features（长度 1 也照常存在）

它**不**决定功能能跑什么。功能自己的字段契约（`Feature.requires`）才是准的：
对方科目 / 分录测试比清洗多要一个「日期」，域不替它们做主——否则一张不含日期
的账会被清洗也挡在门外，而它其实跑得了清洗。

为什么域不是"按业务话题分类"：按话题分类会逼着每个独特功能单独占一层，层越来
越多却换不来任何配置上的收益。按"共用一份数据"分，结果天然是几个大簇 + 一堆
单例；单例照样是一个域（`features` 长度 1），前端只是不为它画分组框。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# 上传允许的后缀：账和成本资料都是 Excel/CSV，先统一
ACCEPT = (".xlsx", ".xls", ".xlsm", ".csv")


@dataclass(frozen=True)
class InputSpec:
    """域的一个输入角色（槽位）。

    Attributes:
        role: 角色 id（`ledger` / `purchase`）。功能只认角色，不认文件名。
        label: 中文标签，**新建任务的上传文案由它生成**，不在前端写死。
        fields: 判定"这份文件算不算这个角色"的必需字段。取功能契约里**最松**的
            那一份（能跑清洗就够），理由见模块注释。
        optional: 认到更好、缺了不拦的字段（给出映射表单里可折叠的那一段）。
        many: 是否允许多份（分月账、多家银行流水）。合并规则见 docs。
        hint: 表单副标题，写清要哪几列。
    """
    role: str
    label: str
    fields: Tuple[str, ...] = ()
    optional: Tuple[str, ...] = ()
    many: bool = False
    hint: str = ""


@dataclass(frozen=True)
class DomainSpec:
    """一个数据域。

    Attributes:
        id: 短标识（URL / 任务归属 / `--domain`）
        name: 中文名（页面分组标题）
        inputs: 输入角色，第一个是主角色
        features: 域内功能 id（顺序 = 前端卡片顺序）
        description: 一句话说明（分组副标题）
    """
    id: str
    name: str
    inputs: Tuple[InputSpec, ...]
    features: Tuple[str, ...]
    description: str = ""

    @property
    def primary(self) -> InputSpec:
        return self.inputs[0]

    @property
    def solo(self) -> bool:
        """只有一个功能：前端不画分组框，但数据上它仍然是一个域。"""
        return len(self.features) == 1


# 顺序 = 前端分组顺序。加功能先问"它跟谁共用一份数据"，再决定放哪个域。
DOMAINS: Tuple[DomainSpec, ...] = (
    DomainSpec(
        id="ledger",
        name="序时账处理",
        description="一份序时账，四个功能共用：对方科目 → 清洗 → 现金流 / 分录测试",
        inputs=(
            InputSpec(
                role="ledger",
                label="序时账",
                # 与清洗的字段契约（`core.ledger.REQUIRES`）一致：能跑清洗就算一份账
                fields=("voucher_id", "subject_l1", "debit", "credit"),
                optional=("date", "subject_l2", "summary", "currency", "amount_orig", "maker"),
                many=True,
                hint="凭证号 / 一级科目 / 借方 / 贷方，多个月分表也行",
            ),
        ),
        # 顺序 = 管线的顺序，也是卡片从左到右的顺序：
        #   对方科目分析（不需要模型，纯算术，谁都能先跑）
        #   → 序时账清洗（要模型；跑完带上对方科目）
        #   → 现金流 / 分录测试（都吃清洗的结果，彼此无依赖）
        # 对方科目放最前面是为了"只想看对方科目的人不必先把模型下下来"；
        # 放在清洗后面的话，档这道门就变成了模型。
        features=("contra", "cleaning", "cashflow", "entry_test"),
    ),
    DomainSpec(
        id="cost",
        name="成本计算",
        description="采购明细等成本资料，与序时账完全独立",
        inputs=(
            InputSpec(
                role="purchase",
                label="采购明细",
                fields=("item", "qty", "price"),
                optional=("period", "supplier", "summary"),
                many=True,
                hint="物料 / 数量 / 单价，多个供应商或月份可以一起传",
            ),
        ),
        features=("cost",),
    ),
)

DOMAIN_BY_ID: Dict[str, DomainSpec] = {d.id: d for d in DOMAINS}

# 功能 id → 域 id。一个功能只属于一个域。
FEATURE_DOMAIN: Dict[str, str] = {
    fid: d.id for d in DOMAINS for fid in d.features
}


def all() -> Tuple[DomainSpec, ...]:
    return DOMAINS


def ids() -> List[str]:
    return [d.id for d in DOMAINS]


def get(domain_id: str) -> DomainSpec:
    key = str(domain_id or "").strip()
    if key not in DOMAIN_BY_ID:
        raise KeyError(f"未知域 {key!r}（可用：{ids()}）")
    return DOMAIN_BY_ID[key]


def of_feature(feature_id: str) -> str:
    """功能属于哪个域；没登记的功能退回 ledger（老调用方不用改）。"""
    return FEATURE_DOMAIN.get(str(feature_id or "").strip(), "ledger")


def feature_ids() -> List[str]:
    """按域顺序摊平的功能清单 —— `features/__init__.py` 的加载顺序从这里来。"""
    return [fid for d in DOMAINS for fid in d.features]


def requires_of(domain_id: str) -> Tuple[str, ...]:
    return get(domain_id).primary.fields


def optional_of(domain_id: str) -> Tuple[str, ...]:
    return get(domain_id).primary.optional


def ui_fields_of(domain_id: str) -> Tuple[str, ...]:
    """映射表单要显示的字段（必需在前、可选在后、去重）。"""
    spec = get(domain_id).primary
    return tuple(spec.fields) + tuple(k for k in spec.optional if k not in spec.fields)


def manifests() -> List[dict]:
    """域清单（纯数据，**不 import 任何功能模块**）。

    功能和域是两件事：列域不需要把 torch 拉起来，所以这里只给 id 和契约，
    卡片的名字、状态由 `features.manifest()` 另外提供。
    """
    return [{
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "solo": d.solo,
        "features": list(d.features),
        "inputs": [{
            "role": i.role,
            "label": i.label,
            "fields": list(i.fields),
            "optional": list(i.optional),
            "many": i.many,
            "hint": i.hint,
        } for i in d.inputs],
    } for d in DOMAINS]
