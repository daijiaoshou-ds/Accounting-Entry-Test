# -*- coding: utf-8 -*-
"""科目组合预处理（`journal prepare`）。

一张凭证"由哪些科目组成"，是多个功能都要用的输入：

    序时账清洗        一级科目 + 方向
    对方科目分析      一级 + 明细科目 + 方向（它叫"模式"）

这里做的是**原生序时账**上的组合：只看这一行是借还是贷，不做任何归一化。
对方科目分析里真正的"模式"是在 **SS 归一化之后**才算的（负金额翻边、同科目
借贷两向拆开），那是它特有的逻辑，留在 `features/contra` 里，不在这里做——
把归一化后的东西当公共资产，别的功能拿到的就不是"账上写的"样子了。

输出两份：

    combo.parquet   每张凭证一行：凭证号 / 一级组合 / 明细组合 / 行数 / 金额
    combo.json      汇总：几种组合、最常见的前几个、耗时

组合的写法是 `科目[借]`，多个按科目名排序后用「、」连；一张凭证里同一个科目
借、贷都出现就算两个（方向不同就是不同的组合）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


def _side(debit: float, credit: float) -> str:
    if abs(debit) > 0.001:
        return "借"
    if abs(credit) > 0.001:
        return "贷"
    return ""


def combo_of(journal, detail: bool) -> pd.DataFrame:
    """按凭证聚合出组合。`detail=True` 时科目带明细（一级-二级）。"""
    mapping = journal.schema.legacy()
    vcol = mapping.get("voucher_no", "")
    s1 = mapping.get("subject", "")
    s2 = mapping.get("subject_name", "")
    dcol, ccol = mapping.get("debit", ""), mapping.get("credit", "")

    frame = pd.DataFrame({
        "voucher": journal.keys("voucher_id") if vcol else pd.Series(range(len(journal))),
        "subject": journal.keys("subject_l1") if s1 else "",
        "detail": journal.keys("subject_l2") if (detail and s2) else "",
        "debit": journal.money("debit") if dcol else 0.0,
        "credit": journal.money("credit") if ccol else 0.0,
    })

    def name(row) -> str:
        base = str(row["subject"] or "").strip() or "（空科目）"
        sub = str(row["detail"] or "").strip()
        if detail and sub and sub != "nan" and sub != base:
            base = f"{base}-{sub}"
        side = _side(float(row["debit"] or 0), float(row["credit"] or 0))
        return f"{base}[{side}]" if side else base

    frame["token"] = [name(row) for _, row in frame.iterrows()]

    grouped = frame.groupby("voucher", sort=False)
    out = pd.DataFrame({
        "凭证号": grouped.size().index.astype(str),
        "行数": grouped.size().values,
        "金额": grouped["debit"].sum().round(2).values,
    })
    # 组合：同一张凭证里去重、排序、顿号连接
    joined: List[str] = []
    for _, group in grouped:
        joined.append("、".join(sorted(set(group["token"]))))
    out["组合" if not detail else "明细组合"] = joined
    return out.reset_index(drop=True)


def write_journal(journal, out_dir: str | Path) -> Dict[str, str]:
    """把解析好的账落成 `raw.parquet` + `schema.json`。

    两个地方共用这一份写法：任务配置（`.tasks/<id>/prepared/`，一份账解析一次）
    和 job 的 read 阶段（`out/read/`，搬进来让任务能独立重跑）。
    分开写两份，迟早出现"配置那边认的列和阶段认的列不是一回事"。
    """
    import journal_io

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "raw.parquet"
    journal_io.write_table(journal.df, raw, target="archive")
    schema = {
        "mapping": journal.schema.mapping,
        "columns": list(journal.schema.columns),
        "engine": journal.schema.engine,
        "source": journal.schema.source,
        "unmapped": list(journal.schema.unmapped),
        "derived": list(journal.schema.derived),
    }
    meta = out_dir / "schema.json"
    meta.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"raw": str(raw), "schema": str(meta)}


def prepare(journal, out_dir: str | Path) -> Dict[str, Any]:
    """算两份组合，落盘并返回汇总（顺带落已解析的账）。"""
    started = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = write_journal(journal, out_dir)

    l1 = combo_of(journal, detail=False)
    l1l2 = combo_of(journal, detail=True)

    table = l1.rename(columns={"组合": "一级组合"}).merge(
        l1l2[["凭证号", "明细组合"]], on="凭证号", how="left")

    path = out_dir / "combo.parquet"
    table.to_parquet(path, index=False)

    def top(column: str, n: int = 8) -> List[Dict[str, Any]]:
        counts = table[column].value_counts().head(n)
        return [{"组合": str(key), "张数": int(value)} for key, value in counts.items()]

    summary = {
        "rows": int(len(journal)),
        "vouchers": int(len(table)),
        "l1_patterns": int(table["一级组合"].nunique()),
        "l1l2_patterns": int(table["明细组合"].nunique()),
        "top_l1": top("一级组合"),
        "top_l1l2": top("明细组合"),
        "seconds": round(time.time() - started, 2),
        "table": str(path),
        "prepared": str(out_dir),
        "raw": written["raw"],
        "mapping": dict(journal.schema.mapping),
        "columns": list(journal.schema.columns),
    }
    (out_dir / "combo.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
