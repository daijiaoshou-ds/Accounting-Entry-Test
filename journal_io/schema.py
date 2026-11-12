# -*- coding: utf-8 -*-
"""列名识别与校验。

识别算法只有两步，刻意不做模糊/相似度匹配 —— 财务表列名就那么几种写法，
猜错的代价（整本账科目错位）远大于猜不中（报错让用户 `--set` 一次）。

    ① **精确匹配**：列名整字等于某个字段的 `exact` 别名 → 直接认领
    ② **子串匹配**：按「别名越长越优先 → 别名列表里越靠前越优先」排序，
       逐个认领没被占用的列；`excludes` 里的词命中则跳过

一列只能被一个字段认领（`taken`），所以不会出现「借方金额」同时喂给
`debit` 和 `amount`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from .fields import FIELD_BY_KEY, FIELDS, FieldSpec, labels

# canonical → 上游历史键名（summary_cleaner / service / web 仍在用旧名）
LEGACY_KEYS: Dict[str, str] = {
    "voucher_id": "voucher_no",
    "date": "date",
    "period": "period",
    "subject_l1": "subject",
    "subject_l2": "subject_name",
    "subject_code": "subject_code",
    "summary": "summary",
    "debit": "debit",
    "credit": "credit",
    "amount": "amount",
    "dc_flag": "dc_flag",
    "currency": "currency",
    "amount_orig": "original_amt",
    "maker": "bookkeeper",
    "counterparty": "counterparty",
    "dept": "dept",
    "staff": "staff",
    "customer": "customer",
    "supplier": "supplier",
    "project": "project",
}


@dataclass
class JournalSchema:
    """一次读表的字段映射结果。"""
    mapping: Dict[str, str]                 # canonical → 实际列名
    columns: Tuple[str, ...] = ()           # 原表全部列名（保持原序）
    engine: str = ""                        # calamine / openpyxl / csv / parquet
    source: str = ""
    unmapped: Tuple[str, ...] = ()          # 没被任何字段认领的列
    derived: Tuple[str, ...] = ()           # 由程序折算出来的列（写回时丢掉）

    # ── 查询 ──
    def get(self, key: str) -> str:
        return self.mapping.get(key, "")

    def has(self, key: str) -> bool:
        return bool(self.mapping.get(key))

    def missing(self, requires: Iterable[str]) -> List[str]:
        return [k for k in requires if not self.mapping.get(k)]

    def legacy(self) -> Dict[str, str]:
        """转成历史键名（voucher_no / subject / subject_name …）。"""
        return to_legacy_mapping(self.mapping)

    def label_of(self, key: str) -> str:
        return FIELD_BY_KEY[key].label if key in FIELD_BY_KEY else key

    def describe(self) -> Dict[str, object]:
        return {
            "mapping": dict(self.mapping),
            "engine": self.engine,
            "columns": list(self.columns),
            "unmapped": list(self.unmapped),
        }


# ============================================================================
# 识别
# ============================================================================

def detect(columns: Sequence[str]) -> Dict[str, str]:
    """列名 → canonical 字段。返回只含**认出来**的字段。"""
    cols = [str(c).strip() for c in columns]
    mapping: Dict[str, str] = {}
    taken: Dict[str, str] = {}          # 列名 → 字段

    def free_field(spec: FieldSpec) -> bool:
        return spec.key not in mapping

    # ① 精确匹配（优先级 = FIELDS 的定义顺序）
    for spec in FIELDS:
        if not spec.exact:
            continue
        for col in cols:
            if col in taken or not free_field(spec):
                continue
            if col in spec.exact:
                mapping[spec.key] = col
                taken[col] = spec.key

    # ② 子串匹配：别名越长越可信；同长度按别名列表顺序；再按列顺序
    cands: List[Tuple[int, int, int, FieldSpec, str]] = []
    for spec in FIELDS:
        for alias_idx, alias in enumerate(spec.aliases):
            for col_idx, col in enumerate(cols):
                if col in taken:
                    continue
                if any(x in col for x in spec.excludes):
                    continue
                if alias in col:
                    cands.append((-len(alias), alias_idx, col_idx, spec, col))

    cands.sort(key=lambda x: (x[0], x[1], x[2]))
    for _neg_len, _alias_idx, _col_idx, spec, col in cands:
        if col in taken or not free_field(spec):
            continue
        mapping[spec.key] = col
        taken[col] = spec.key

    return mapping


def to_legacy_mapping(mapping: Dict[str, str]) -> Dict[str, str]:
    """canonical 映射 → 历史键名映射（保持旧调用方不用改）。"""
    out: Dict[str, str] = {}
    for key, col in mapping.items():
        legacy = LEGACY_KEYS.get(key)
        if legacy:
            out[legacy] = col
    return out


def legacy_key(key: str) -> str:
    """canonical 名 → 历史键名（不在表里的原样返回）。"""
    return LEGACY_KEYS.get(key, key)


def from_legacy_mapping(mapping: Dict[str, str]) -> Dict[str, str]:
    """历史键名 → canonical（新代码接旧配置时用）。"""
    reverse = {v: k for k, v in LEGACY_KEYS.items()}
    return {reverse.get(k, k): v for k, v in mapping.items() if v}


def validate(mapping: Dict[str, str], requires: Iterable[str]) -> Tuple[bool, List[str]]:
    """校验必需字段是否齐。返回 (ok, 缺失字段的中文标签)。"""
    miss = [k for k in requires if not mapping.get(k)]
    return (not miss), labels(miss)


def build_schema(df: pd.DataFrame, engine: str = "", source: str = "") -> JournalSchema:
    mapping = detect(list(df.columns))
    used = set(mapping.values())
    return JournalSchema(
        mapping=mapping,
        columns=tuple(str(c) for c in df.columns),
        engine=engine,
        source=source,
        unmapped=tuple(str(c) for c in df.columns if c not in used),
    )


def preview(df: pd.DataFrame, mapping: Dict[str, str],
            n: int = 3) -> Dict[str, Dict[str, object]]:
    """每个字段给几行样例（前端映射表单、报错排查用）。"""
    out: Dict[str, Dict[str, object]] = {}
    for key, col in mapping.items():
        if col not in df.columns:
            continue
        samples = [str(v) for v in df[col].dropna().astype(str).head(n)]
        out[key] = {"column": col, "label": FIELD_BY_KEY[key].label
                    if key in FIELD_BY_KEY else key, "samples": samples}
    return out


def describe_missing(requires: Iterable[str], mapping: Dict[str, str],
                     columns: Sequence[str]) -> Optional[str]:
    """缺字段时的报错文案（直接给用户看，含"怎么办"）。"""
    miss = [k for k in requires if not mapping.get(k)]
    if not miss:
        return None
    head = "、".join(labels(miss))
    cols = "、".join(str(c) for c in columns[:12])
    return (f"必需字段缺失：{head}。表的列是：{cols}…。"
            f"用 --set {FIELD_BY_KEY[miss[0]].label if miss[0] in FIELD_BY_KEY else miss[0]}="
            f"\"你的列名\" 这样指定映射")
