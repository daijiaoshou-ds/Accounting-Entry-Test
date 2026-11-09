# -*- coding: utf-8 -*-
"""纠错表：导出模板 → 用户在 Excel 改「纠错分类」→ 回传 → 落库学习。

抽到服务层的目的：Streamlit 页面和未来的 Web 前端要走**同一条**解析逻辑，
不然「网页改的」和「Excel 改的」会得到两种不同口径的纠错信号。
函数本身不依赖任何界面，只吃 DataFrame / bytes。
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..v2.config import BUCKET_REGISTRY, T3_SUBJECTS
from ..v2.io_utils import EXCEL_ROW_LIMIT, read_table, serialize_table, too_large_hint

_CANDIDATES = {
    "voucher_no": (["凭证号", "凭证编号", "voucher_no", "vid"], "凭证", False),
    "subject": (["一级科目", "subject"], "科目", False),
    "summary": (["摘要", "summary"], "摘要", False),
    "subject_name": (["科目名称", "科目明细", "subject_name"], "", False),
    "debit": (["借方金额", "debit", "借方(本币)"], "", True),
}


def find_col(df: pd.DataFrame, key: str) -> str:
    """按候选名 + 关键字兜底找列（与界面层旧实现同口径）。"""
    candidates, hint, numeric_only = _CANDIDATES[key]
    for c in candidates:
        if c in df.columns:
            if numeric_only and df[c].dtype.kind not in "iuf":
                continue
            return c
    for c in df.columns:
        if hint and hint in str(c):
            if numeric_only and df[c].dtype.kind not in "iuf":
                continue
            return c
    return ""


def resolve_columns(df: pd.DataFrame, mapping: Optional[Dict[str, str]]) -> Dict[str, str]:
    """优先用字段映射，缺失的再按列名猜。"""
    mapping = mapping or {}
    out = {}
    for key in _CANDIDATES:
        col = mapping.get(key) or find_col(df, key)
        out[key] = col if col and col in df.columns else ""
    return out


def all_bucket_names() -> List[str]:
    return list(BUCKET_REGISTRY.keys())


def build_sheet(df: pd.DataFrame,
                mapping: Optional[Dict[str, str]] = None,
                ) -> Tuple[bytes, str, str, str]:
    """生成纠错表（≤50 万行：带下拉的 xlsx；否则 CSV）。

    Returns:
        (data, filename, mime, hint)
    """
    cols = resolve_columns(df, mapping)
    v_col, s_col, sum_col = cols["voucher_no"], cols["subject"], cols["summary"]
    d_col = cols["debit"]
    if not v_col:
        raise ValueError("未找到凭证号列，请先在字段映射中设置「凭证号」")

    amount = None
    if d_col and d_col in df.columns:
        amount = pd.to_numeric(
            df[d_col].astype(str).str.replace(",", "").str.strip(),
            errors="coerce").abs().fillna(0.0)
    amt_by_vid = amount.groupby(df[v_col]).sum() if amount is not None else None

    rows = []
    for vid, group in df.groupby(v_col):
        bucket = group["业务分类"].iloc[0] if "业务分类" in group.columns else ""
        native = (group["程序原生桶"].iloc[0]
                  if "程序原生桶" in group.columns else bucket)
        summary = (str(group[sum_col].iloc[0])[:100]
                   if sum_col and sum_col in group.columns else "")
        subjects = ("、".join(group[s_col].dropna().astype(str).unique())
                    if s_col and s_col in group.columns else "")
        amt = float(amt_by_vid.get(vid, 0.0)) if amt_by_vid is not None else 0.0
        rows.append({
            "凭证号": str(vid), "摘要": summary, "科目": subjects,
            "金额": round(amt, 2), "当前分类": bucket,
            "程序原生桶": native, "纠错分类": bucket,
        })

    export_df = pd.DataFrame(rows)
    if len(export_df) > EXCEL_ROW_LIMIT:
        data, name, mime, _ = serialize_table(export_df, "纠错表.xlsx",
                                              sheet_name="纠错表")
        return data, name, mime, too_large_hint(len(export_df))

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="纠错表")
        ws = writer.sheets["纠错表"]
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation

        letter = get_column_letter(len(export_df.columns))
        dv = DataValidation(type="list",
                            formula1='"' + ",".join(all_bucket_names()) + '"',
                            allow_blank=True)
        dv.error = "请选择有效的业务桶"
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{len(export_df) + 1}")
    return (buffer.getvalue(), "纠错表.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "")


def parse_sheet(source: Any, df: pd.DataFrame,
                mapping: Optional[Dict[str, str]] = None,
                buckets: Optional[List[str]] = None,
                filename: str = "") -> List[Dict[str, Any]]:
    """解析回传的纠错表，返回改过桶的凭证列表（未改的跳过）。"""
    corr_df = read_table(source, filename=filename)
    for col in ("凭证号", "当前分类", "纠错分类"):
        if col not in corr_df.columns:
            raise ValueError(f"缺少必要列：「{col}」")

    valid = set(buckets or all_bucket_names())
    cols = resolve_columns(df, mapping)
    v_col = cols["voucher_no"]
    if not v_col:
        raise ValueError("未找到凭证号列，请先在字段映射中设置「凭证号」")

    by_vid = {str(k): g for k, g in df.groupby(v_col, sort=False)}
    corrections: List[Dict[str, Any]] = []
    for _, row in corr_df.iterrows():
        vid = str(row["凭证号"])
        current = str(row["当前分类"]).strip()
        corrected = str(row["纠错分类"]).strip()
        if corrected == current or corrected not in valid:
            continue

        native = current
        if "程序原生桶" in corr_df.columns:
            val = row["程序原生桶"]
            if pd.notna(val) and str(val).strip():
                native = str(val).strip()

        group = by_vid.get(vid)
        if group is None:
            continue

        sum_col, s_col = cols["summary"], cols["subject"]
        sn_col, d_col = cols["subject_name"], cols["debit"]
        summary = (str(group[sum_col].iloc[0])[:120]
                   if sum_col and len(group) else "")
        subjects = (group[s_col].dropna().astype(str).tolist()
                    if s_col else [])
        sub_details: List[str] = []
        if sn_col and sn_col in group.columns:
            mask = group[s_col].fillna("").astype(str).str.strip().isin(T3_SUBJECTS)
            sub_details = group.loc[~mask, sn_col].dropna().astype(str).tolist()
        amount = 0.0
        if d_col and d_col in group.columns:
            amt = pd.to_numeric(
                group[d_col].astype(str).str.replace(",", "").str.strip(),
                errors="coerce")
            amount = float(amt.abs().fillna(0).sum())

        corrections.append({
            "vid": vid, "original_bucket": native, "correct_bucket": corrected,
            "amount": amount, "summary": summary, "subjects": subjects,
            "subject_details": sub_details,
        })
    return corrections


def record(corrections: List[Dict[str, Any]]) -> Dict[str, int]:
    """把纠错写进纠错库（排名表/EMA/再确认轮次）。"""
    if not corrections:
        return {"corrections": 0, "vouchers": 0}
    from ..v2.correction import CorrectionManager

    mgr = CorrectionManager()
    mgr.load()
    mgr.record_corrections_batch(corrections)
    return {"corrections": len(corrections),
            "vouchers": len({c["vid"] for c in corrections})}
