# -*- coding: utf-8 -*-
"""`Journal` —— 一份序时账，一套访问器。

**为什么要有这个对象**：以前两套读法互相矛盾 —— `contra_analyzer` 用
`dtype=str`（凭证号 `0001` 转数字会丢前导零、日期会变形），`summary_cleaner`
用带类型的读法（金额要算）。谁都没错，错在没有一个能同时给两种视图的东西。

现在：

    j.df      原样（读进来什么样就什么样，**写回用它**：原列原序一个不动）
    j.raw     全字符串视图（保真：0001 还是 0001，日期是 YYYY-MM-DD）
    j.keys()  键视图（凭证号/日期/科目 → 可用于拼接、匹配、去重）
    j.money() 金额视图（去千分位、转 float、NaN→0）

四个视图共享同一行序，`j.raw.iloc[i]` 与 `j.df.iloc[i]` 永远是同一行。
`raw` 是惰性求值的 —— 不需要保真字符串的功能一分钱都不花。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from . import schema as schema_mod
from .fields import FIELD_BY_KEY, labels
from .readers import Source, read_frame

# 折算列的名字：当源表只有「金额 + 借贷方向」时由这里生成
DERIVED_DEBIT = "借方金额(折算)"
DERIVED_CREDIT = "贷方金额(折算)"


class Journal:
    """一份序时账 + 字段映射 + 四种视图。"""

    def __init__(self, df: pd.DataFrame, schema: schema_mod.JournalSchema,
                 source: str = ""):
        self.df = df
        self.schema = schema
        self.source = source
        self._raw: Optional[pd.DataFrame] = None

    # ── 基本 ──
    def __len__(self) -> int:
        return len(self.df)

    def __repr__(self) -> str:
        return (f"<Journal {Path(self.source).name or '(memory)'} "
                f"{len(self.df)} 行 · {len(self.schema.mapping)} 字段 · "
                f"engine={self.schema.engine}>")

    @property
    def columns(self) -> List[str]:
        return [str(c) for c in self.df.columns]

    # ── 视图 ──
    @property
    def typed(self) -> pd.DataFrame:
        """带类型的原表（= 读进来那份，写回用它）。"""
        return self.df

    @property
    def raw(self) -> pd.DataFrame:
        """全字符串视图（惰性）。日期格式化成 YYYY-MM-DD，缺失为空串。"""
        if self._raw is None:
            out = self.df.copy()
            for col in out.columns:
                s = out[col]
                if pd.api.types.is_datetime64_any_dtype(s):
                    out[col] = s.dt.strftime("%Y-%m-%d").fillna("")
                else:
                    out[col] = s.where(s.notna(), "").astype(str)
            self._raw = out
        return self._raw

    def keys(self, field: str) -> pd.Series:
        """键视图：`j.keys("voucher_id")` → 保真字符串序列（缺失为空串）。"""
        col = self.schema.get(field)
        if not col or col not in self.df.columns:
            return pd.Series([""] * len(self.df), index=self.df.index, dtype="object")
        s = self.df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            return s.dt.strftime("%Y-%m-%d").fillna("").astype(object)
        return s.where(s.notna(), "").astype(str).str.strip().astype(object)

    def money(self, field: str) -> pd.Series:
        """金额视图：去千分位 → float → NaN 归零。"""
        col = self.schema.get(field)
        if not col or col not in self.df.columns:
            return pd.Series([0.0] * len(self.df), index=self.df.index, dtype=float)
        text = (self.df[col].astype(str)
                .str.replace(",", "", regex=False)
                .str.replace("，", "", regex=False)
                .str.strip())
        return pd.to_numeric(text, errors="coerce").fillna(0.0).astype(float)

    def dates(self, field: str = "date") -> pd.Series:
        col = self.schema.get(field)
        if not col or col not in self.df.columns:
            return pd.Series(pd.NaT, index=self.df.index)
        return pd.to_datetime(self.df[col], errors="coerce")

    # ── 凭证级 ──
    def vouchers(self) -> pd.DataFrame:
        """按凭证号聚合：行数、借贷合计、科目集合、首行摘要。

        列名用 canonical（凭证号/借方合计/…），不带内部下划线。
        """
        v_key = self.keys("voucher_id")
        if not v_key.any():
            return pd.DataFrame(columns=["凭证号", "行数", "借方合计", "贷方合计",
                                         "科目", "摘要"])
        frame = pd.DataFrame({
            "凭证号": v_key,
            "借方": self.money("debit").abs(),
            "贷方": self.money("credit").abs(),
            "科目": self.keys("subject_l1"),
            "摘要": self.keys("summary"),
        })
        grp = frame.groupby("凭证号", sort=False)
        out = pd.DataFrame({
            "行数": grp.size(),
            "借方合计": grp["借方"].sum().round(2),
            "贷方合计": grp["贷方"].sum().round(2),
            "科目": grp["科目"].agg(lambda s: "、".join(pd.unique(s[s != ""]))),
            "摘要": grp["摘要"].first(),
        }).reset_index()
        return out

    # ── 校验 ──
    def require(self, requires: Iterable[str]) -> "Journal":
        """缺必需字段就抛 ValueError（文案直接给用户看）。"""
        msg = schema_mod.describe_missing(requires, self.schema.mapping, self.columns)
        if msg:
            raise ValueError(msg)
        return self

    # ── 写回 ──
    def write_back(self, extra: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """原表 + 追加列（原列、原序一个不动；折算列会被丢掉）。"""
        out = self.df.copy()
        drop = [c for c in (DERIVED_DEBIT, DERIVED_CREDIT) if c in out.columns]
        if drop:
            out = out.drop(columns=drop)
        for name, value in (extra or {}).items():
            if isinstance(value, pd.Series):
                out[name] = value.reset_index(drop=True).values
            elif isinstance(value, (list, tuple)) and len(value) == len(out):
                out[name] = list(value)
            else:
                out[name] = value
        return out


# ============================================================================
# 读
# ============================================================================

def read(source: Source, *, filename: str = "", requires: Iterable[str] = (),
         derive: bool = True, **kwargs: Any) -> Journal:
    """读序时账 → Journal。

    Args:
        source: 路径 / bytes / 文件对象
        filename: source 没有 .name 时用来判断后缀
        requires: 必需字段（canonical），缺了直接抛错
        derive: 源表只有「金额 + 借贷方向」时，是否自动折算成借/贷两列
        **kwargs: 透传 pd.read_excel（sheet_name 等）

    Returns:
        Journal；`j.schema.mapping` 是 canonical → 列名
    """
    df, engine = read_frame(source, filename=filename, **kwargs)
    src = str(source) if isinstance(source, (str, Path)) else (filename or "")
    schema = schema_mod.build_schema(df, engine=engine, source=src)

    if derive:
        df, schema = _derive_debit_credit(df, schema)

    journal = Journal(df, schema, source=src)
    if requires:
        journal.require(requires)
    return journal


def _derive_debit_credit(df: pd.DataFrame, schema: schema_mod.JournalSchema
                         ) -> tuple[pd.DataFrame, schema_mod.JournalSchema]:
    """只有「金额 + 借贷方向」时折算成借/贷两列。

    触发条件**很严**：借、贷两列都认不出来，且金额列和方向列都认得出来。
    只要表里已有借方或贷方列就不动 —— 宁可让上游按老路走，也不猜。
    """
    if schema.has("debit") or schema.has("credit"):
        return df, schema
    if not (schema.has("amount") and schema.has("dc_flag")):
        return df, schema

    amount = pd.to_numeric(
        df[schema.get("amount")].astype(str)
        .str.replace(",", "", regex=False).str.strip(),
        errors="coerce").fillna(0.0).abs()

    flag = df[schema.get("dc_flag")].astype(str).str.strip()
    is_debit = flag.str.startswith(("借", "D", "d"))
    is_credit = flag.str.startswith(("贷", "C", "c"))
    if not (is_debit.any() or is_credit.any()):
        return df, schema

    out = df.copy()
    out[DERIVED_DEBIT] = amount.where(is_debit, 0.0)
    out[DERIVED_CREDIT] = amount.where(is_credit, 0.0)
    schema.mapping["debit"] = DERIVED_DEBIT
    schema.mapping["credit"] = DERIVED_CREDIT
    schema.derived = (DERIVED_DEBIT, DERIVED_CREDIT)
    return out, schema
