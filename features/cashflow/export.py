# -*- coding: utf-8 -*-
"""导出：一份交付物，两张 sheet。

设计立场：**落盘是"确定的活"，由程序负责**，AI 不参与、也不手改产出文件。

版面（顺序即 sheet 顺序）：

    ① 现金流量表    —— 按项目聚合，收付分列，末尾一行净增加额
    ② 货币资金凭证  —— 与现金流有关的**整张**凭证 + 现金流量项目

**不抄整本账**：原始序时账和全量业务分类在「序时账清洗」那份产物里，这里再放
一遍只是把同一本账重复一次（11,604 行 × 2）。现金流这份只回答"钱怎么流的"。

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
    """现金流量表：按项目聚合，收付分列，末尾一行净增加额。

    中性项目（「汇率变动对现金的影响」）**只列净额**：它的金额本来就有正有负
    （外币折算），按收/付拆开会把合计的收现、付现各撑大几百万。标准报表里这
    一行也是一条净额线，不进收付两列，但净增加额要把它算进去。
    """
    rows = []
    for v in cf_by_row.values():
        item = v.get("cf_item") or ""
        if not item or v.get("no_cash"):
            continue
        amt = float(v.get("amount") or 0)
        neutral = judge.item_direction(item) == judge.CF_NEUTRAL
        rows.append({
            "现金流量项目": item,
            "活动类型": judge.section_of(item) or "其他",
            "_in": 0.0 if neutral else (amt if amt > 0 else 0.0),
            "_out": 0.0 if neutral else (-amt if amt < 0 else 0.0),
            "_net": amt,
        })
    empty = pd.DataFrame(columns=["活动类型", "现金流量项目", "收现", "付现", "笔数", "净额"])
    if not rows:
        return empty

    agg = (pd.DataFrame(rows)
           .groupby(["活动类型", "现金流量项目"], as_index=False)
           .agg(收现=("_in", "sum"), 付现=("_out", "sum"),
                笔数=("_net", "size"), 净额=("_net", "sum")))
    for col in ("收现", "付现", "净额"):
        agg[col] = agg[col].round(2)

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


def export(classified: pd.DataFrame,
           mapping: Dict[str, str],
           money_flags: Dict[str, bool],
           cf_by_row: Dict[int, dict],
           out_dir: Any,
           stem: str,
           statement_frame: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """写出交付物，返回 {files, sheet_rows, statement}。"""
    sheets: Dict[str, pd.DataFrame] = {}
    stmt = statement_frame if statement_frame is not None else statement(cf_by_row)
    if stmt is not None and not stmt.empty:
        sheets["现金流量表"] = stmt
    # 货币资金凭证（整张凭证）：编现金流看的正是货币资金的对方在干什么
    if money_flags:
        mv = money_voucher_view(classified, mapping, money_flags)
        if not mv.empty:
            sheets["货币资金凭证"] = mv
    if not sheets:
        raise ValueError("现金流没有可导出的内容（既没有报表行，也没有货币资金凭证）")

    # 写盘格式由 journal_io 统一决定（≤5 万行 xlsx，以上 csv；archive 走 parquet）
    written = write_sheets(sheets, out_dir / f"{stem}_现金流量表.xlsx",
                          target="download")
    return {
        "files": written.paths,
        "sheet_rows": written.sheet_rows,
        "fmt": written.fmt,
        "hint": written.hint,
        "seconds": written.seconds,
        "statement": stmt,
    }
