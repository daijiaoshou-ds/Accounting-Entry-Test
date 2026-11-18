# -*- coding: utf-8 -*-
"""导出：一份交付物，多张 sheet。

设计立场：**落盘是"确定的活"，由程序负责**，AI 不参与、也不手改产出文件。
用户拿到的是一份 Excel，不是一个结论一个文件。

版面（顺序即 sheet 顺序）：

    ① 原始序时账    —— 用户给什么就是什么，一个字不改
    ② 业务分类结果  —— ① + 业务分类/置信度 + 现金流量项目
    ③ 货币资金凭证  —— 与现金流有关的**整张**凭证 + 现金流量项目

没有"待复核"sheet：待复核条目走 CLI 出参（JSON）给 AI，不塞进给用户的交付物。
AI 的结论通过补丁回到程序，再重出本文件。

**现金流判定结论与业务分类同源**：桶改了，现金流跟着重算，所以两张表永远自洽。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from journal_io import write_sheets

from . import judge

# 程序追加的列（顺序即导出顺序）
ADDED_COLUMNS = ("业务分类", "判定来源", "置信度",
                 "模型概率", "校准概率", "模型Top3", "后处理",
                 "是否现金流相关凭证",
                 "现金流量项目", "现金流方向", "现金流判定", "现金流复核")


def _base_columns(classified: pd.DataFrame, mapping: Dict[str, str]
                  ) -> list:
    """原始列（保持用户顺序，去掉内部下划线列），科目列统一成「一级科目」。"""
    cols = [c for c in classified.columns
            if not str(c).startswith("_") and c not in ADDED_COLUMNS]
    out = list(cols)
    s_col = mapping.get("subject")
    if s_col and s_col in out and s_col != "一级科目":
        out[out.index(s_col)] = "一级科目"
    return out


def business_view(classified: pd.DataFrame, mapping: Dict[str, str],
                  with_flag: bool = True) -> pd.DataFrame:
    """业务分类结果表：原始列 + 程序追加列。"""
    cols = _base_columns(classified, mapping)
    frame = classified.copy()
    s_col = mapping.get("subject")
    if s_col and s_col != "一级科目" and s_col in frame.columns:
        frame = frame.rename(columns={s_col: "一级科目"})
    add = [c for c in ADDED_COLUMNS if c in frame.columns
           and (with_flag or c != "是否现金流相关凭证")]
    return frame[cols + add]


def money_voucher_view(classified: pd.DataFrame, mapping: Dict[str, str],
                       money_flags: Dict[str, bool]) -> pd.DataFrame:
    """含货币资金科目的**整张**凭证（不是只留货币资金行）。

    编现金流要看的正是货币资金的对方在干什么，所以整张凭证都留着；
    但只给货币资金那一行填「现金流量项目」——这是会计惯例。
    """
    v_col = mapping["voucher_no"]
    hit = classified[v_col].astype(str).map(money_flags).fillna(False).astype(bool)
    return business_view(classified[hit], mapping, with_flag=False)


def statement(cf_by_row: Dict[int, dict]) -> pd.DataFrame:
    """现金流量表：按项目聚合，收付分列，末尾一行净增加额。"""
    rows = []
    for v in cf_by_row.values():
        item = v.get("cf_item") or ""
        if not item or v.get("no_cash"):
            continue
        amt = float(v.get("amount") or 0)
        rows.append({
            "现金流量项目": item,
            "活动类型": judge.section_of(item) or "其他",
            "_in": amt if amt > 0 else 0.0,
            "_out": -amt if amt < 0 else 0.0,
        })
    empty = pd.DataFrame(columns=["活动类型", "现金流量项目", "收现", "付现", "笔数", "净额"])
    if not rows:
        return empty

    agg = (pd.DataFrame(rows)
           .groupby(["活动类型", "现金流量项目"], as_index=False)
           .agg(收现=("_in", "sum"), 付现=("_out", "sum"), 笔数=("_in", "size")))
    agg["净额"] = agg["收现"] - agg["付现"]

    sec_order = {s: i for i, s in enumerate(judge.load_cf_config()["sections"])}
    item_order = {n: i for i, n in enumerate(judge.cf_items())}
    agg["_s"] = agg["活动类型"].map(sec_order).fillna(9)
    agg["_i"] = agg["现金流量项目"].map(item_order).fillna(999)
    agg = agg.sort_values(["_s", "_i"]).drop(columns=["_s", "_i"])

    total = pd.DataFrame([{
        "活动类型": "合计",
        "现金流量项目": "现金及现金等价物净增加额",
        "收现": round(float(agg["收现"].sum()), 2),
        "付现": round(float(agg["付现"].sum()), 2),
        "笔数": int(agg["笔数"].sum()),
        "净额": round(float(agg["净额"].sum()), 2),
    }])
    return pd.concat([agg, total], ignore_index=True)


def export(raw: pd.DataFrame,
           classified: pd.DataFrame,
           mapping: Dict[str, str],
           money_flags: Dict[str, bool],
           cf_by_row: Dict[int, dict],
           out_dir: Any,
           stem: str,
           statement_frame: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """写出交付物，返回 {files, sheet_rows, statement}。"""
    # ① 原始序时账：用户给什么就是什么（列名也不动）
    sheets: Dict[str, pd.DataFrame] = {"原始序时账": raw}
    # ② 业务分类结果 + 现金流量项目
    sheets["业务分类结果"] = business_view(classified, mapping)
    # ③ 货币资金凭证（整张凭证）
    if money_flags:
        mv = money_voucher_view(classified, mapping, money_flags)
        if not mv.empty:
            sheets["货币资金凭证"] = mv

    # 写盘格式由 journal_io 统一决定（≤5 万行 xlsx，以上 csv；archive 走 parquet）
    written = write_sheets(sheets, out_dir / f"{stem}_现金流量表.xlsx",
                          target="download")
    return {
        "files": written.paths,
        "sheet_rows": written.sheet_rows,
        "fmt": written.fmt,
        "hint": written.hint,
        "seconds": written.seconds,
        "statement": (statement_frame if statement_frame is not None
                      else statement(cf_by_row)),
    }
