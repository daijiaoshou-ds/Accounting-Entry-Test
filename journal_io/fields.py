# -*- coding: utf-8 -*-
"""字段字典 —— **全项目唯一的一份列名别名表**。

以前列名识别散在四处（`summary_cleaner/v2/config.py`、`entry_test/data_processor.py`、
`api/app.py`、`contra_analyzer` 的调用方），同一个「一级科目」有四个名字，
新功能每加一次就要抄一遍。

这里做两件事：

1. **canonical 名统一**：功能层只说 `voucher_id / subject_l1 / debit`，
   不再出现 `voucher_no` / `first_level_subject` / `voucher_number` 三种写法。
2. **并集只是注册表，不是契约**：功能声明 `requires=(...)`，校验的是**它的子集**。
   并集变大不会逼每个功能都填。

字段的取舍标准：**账里真实出现过的列**（四份真账 + 三个功能的读表代码），
不猜"理论上应该有"的列。加字段前先问："哪个功能会用它？"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class FieldSpec:
    """一个标准字段。

    Attributes:
        key: canonical 名（功能层只用这个）
        label: 中文标签（报错、前端表单）
        aliases: 子串匹配用的别名，**顺序即优先级**（越靠前越优先）
        exact: 只做精确相等的别名（防止「科目」被「科目编码」抢走）
        excludes: 列名里含这些词就**不认**（防止「会计科目名称」被认成一级科目）
        kind: text / money / date / period —— 决定 Journal 用哪个访问器
    """
    key: str
    label: str
    aliases: Tuple[str, ...] = ()
    exact: Tuple[str, ...] = ()
    excludes: Tuple[str, ...] = ()
    kind: str = "text"


# 顺序 = 精确匹配的优先级（先到先得，一列只能被一个字段认领）
FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec(
        key="voucher_id", label="凭证号", kind="text",
        aliases=("凭证编号", "凭证号", "凭证字号", "单号", "字号", "编号",
                 "voucher_no", "voucher_number", "voucher"),
    ),
    FieldSpec(
        key="date", label="日期", kind="date",
        aliases=("制单日期", "记账日期", "凭证日期", "会计日期", "日期",
                 "voucher_date", "date"),
    ),
    FieldSpec(
        key="period", label="会计期间", kind="period",
        aliases=("会计期间", "月份", "期间", "month"),
    ),
    FieldSpec(
        key="subject_l1", label="一级科目", kind="text",
        aliases=("一级科目", "总账科目", "会计科目", "first_level_subject"),
        exact=("科目", "总账科目", "一级科目名称"),
        # 「会计科目名称」「科目明细」属于 subject_l2 —— 不排除的话，
        # 只有明细名的表会把整本账的科目都变成二级名。
        excludes=("名称", "明细"),
    ),
    FieldSpec(
        key="subject_l2", label="明细科目", kind="text",
        aliases=("科目名称", "科目明细", "明细科目", "二级科目",
                 "会计科目名称", "subject_name", "account_name"),
    ),
    FieldSpec(
        key="subject_code", label="科目编码", kind="text",
        aliases=("科目编码", "科目代码", "科目号", "account_code", "subject_code"),
    ),
    FieldSpec(
        key="summary", label="摘要", kind="text",
        aliases=("摘要", "业务描述", "说明", "备注", "summary",
                 "description", "business_summary"),
    ),
    FieldSpec(
        key="debit", label="借方金额", kind="money",
        aliases=("借方金额", "借方发生额", "借方", "debit_amount", "debit"),
        exact=("借",),
    ),
    FieldSpec(
        key="credit", label="贷方金额", kind="money",
        aliases=("贷方金额", "贷方发生额", "贷方", "credit_amount", "credit"),
        exact=("贷",),
    ),
    # 有些系统只导一列「金额」+ 一列「借贷方向」，Journal 会折算成 debit/credit
    FieldSpec(
        key="amount", label="发生额", kind="money",
        aliases=("发生额", "金额", "amount"),
    ),
    FieldSpec(
        key="dc_flag", label="借贷方向", kind="text",
        aliases=("借贷方向", "借贷标志", "方向", "dc_flag", "direction"),
    ),
    FieldSpec(
        key="currency", label="币种", kind="text",
        aliases=("币种", "币别", "货币", "currency"),
    ),
    FieldSpec(
        key="amount_orig", label="原币金额", kind="money",
        aliases=("原币金额", "外币金额", "原币", "original_amount", "original_amt"),
    ),
    FieldSpec(
        key="maker", label="制单人", kind="text",
        aliases=("制单人", "制表人", "记账人", "录入人", "bookkeeper"),
    ),
    FieldSpec(
        key="counterparty", label="对方科目", kind="text",
        aliases=("对方科目名称", "对方科目", "对应科目", "counter_subject"),
    ),
    # 维度列：分录测试的异常检测、对方科目分析的辅助判断会用到
    FieldSpec(key="dept", label="部门", aliases=("部门名称", "部门", "dept")),
    FieldSpec(key="staff", label="职员", aliases=("职员名称", "业务员", "职员", "staff")),
    FieldSpec(key="customer", label="客户", aliases=("客户名称", "客户", "customer")),
    FieldSpec(key="supplier", label="供应商", aliases=("供应商名称", "供应商", "supplier")),
    FieldSpec(key="project", label="项目", aliases=("项目名称", "项目", "project")),
)

FIELD_BY_KEY: Dict[str, FieldSpec] = {f.key: f for f in FIELDS}
ALL_KEYS: Tuple[str, ...] = tuple(f.key for f in FIELDS)


# ── 各功能的必需字段（契约）──────────────────────────────────────────────
# 只有列在这里的功能才保证"缺了要报错"；其余字段都是可选、缺了降精度不报错。
FEATURE_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "cleaning": ("voucher_id", "subject_l1", "debit", "credit"),
    "cashflow": ("voucher_id", "subject_l1", "debit", "credit"),
    "contra": ("voucher_id", "date", "subject_l1", "debit", "credit"),
    "entry_test": ("voucher_id", "date", "subject_l1", "debit", "credit"),
}


# 前端映射表单要**可选字段**也列出来（用户想指定到明细也没问题）
FEATURE_OPTIONAL: Dict[str, Tuple[str, ...]] = {
    "cleaning": ("date", "subject_l2", "summary", "currency", "amount_orig", "maker"),
    "cashflow": ("date", "summary"),
    "contra": ("subject_l2", "summary", "subject_code"),
    "entry_test": ("period", "subject_l2", "subject_code", "summary",
                   "counterparty", "currency", "amount_orig"),
}


def ui_fields_of(feature: str) -> Tuple[str, ...]:
    """前端表单该显示哪些字段（必需在前，可选在后）。"""
    req = FEATURE_REQUIRES.get(feature, ())
    opt = FEATURE_OPTIONAL.get(feature, ())
    return tuple(req) + tuple(k for k in opt if k not in req)


def requires_of(feature: str) -> Tuple[str, ...]:
    return FEATURE_REQUIRES.get(feature, ())


def labels(keys: Iterable[str]) -> List[str]:
    """[key, ...] → ['凭证号', ...]，报错文案用。"""
    return [FIELD_BY_KEY[k].label if k in FIELD_BY_KEY else k for k in keys]
