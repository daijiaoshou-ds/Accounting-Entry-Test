# -*- coding: utf-8 -*-
"""Pipeline —— 3.0 的完整清洗流程编排。

    序时账 Excel
        │
        ├─ ① ETL        读文件、认列名、规范化、抽出模型输入（一凭证一条）
        ├─ ② 硬规则     两条机械规则预分配（资金内部往来 / 汇兑损益）
        ├─ ③ 模型打分   按输入折叠 → 批量推理 → top1 + 置信度
        ├─ ④ 写回       结论逐行写回原序时账
        └─ ⑤ 导出       分类结果 / 待复核清单 / 统计摘要

程序不参与业务桶打分：18 个业务桶全部来自模型，中/低置信度交给 AI 复核。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from . import etl
from . import hard_rules
from .config import (ALL_BUCKETS, CONF_HIGH, CONF_MID, FIELD_LABELS,
                     HARD_RULE_BUCKETS, MODEL_BUCKETS, REQUIRED_FIELDS)
from .model import JournalClassifier, pick_device


# ============================================================================
# ① 字段检查（不加载模型、不写任何数据）
# ============================================================================

def check_file(path: str | Path,
               override: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """读文件 + 自动认列名 + 校验必需字段。"""
    df = etl.read_journal(path)
    mapping = etl.detect_columns(df)
    mapping, bad = etl.apply_mapping_override(df, mapping, override)

    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    preview = {}
    for key, col in mapping.items():
        samples = [str(v) for v in df[col].dropna().astype(str).head(3)]
        preview[key] = {"列名": col, "样例": samples,
                        "字段": FIELD_LABELS.get(key, key)}
    return {
        "file": str(Path(path).resolve()),
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "mapping": mapping,
        "preview": preview,
        "missing_required": missing,
        "missing_required_cn": [FIELD_LABELS.get(m, m) for m in missing],
        "unknown_overrides": bad,
        "ok": not missing,
    }


# ============================================================================
# ②-⑤ 完整清洗
# ============================================================================

def run_file(path: str | Path,
             out_dir: Optional[str | Path] = None,
             override: Optional[Dict[str, str]] = None,
             classifier: Optional[JournalClassifier] = None,
             chunk_size: int = 64,
             progress=None) -> Dict[str, Any]:
    """跑完一份序时账的完整清洗，返回结果统计（含导出文件路径）。

    Args:
        classifier: 复用已加载的模型实例（批量跑多份文件时传，省去重复加载）
        progress: 可选回调 progress(str)，用于向用户报进度
    """
    def log(msg):
        if progress:
            progress(msg)

    t_all = time.perf_counter()
    src = Path(path)
    out_dir = Path(out_dir) if out_dir else src.parent / "序时账清洗结果"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    # ── ① ETL ──
    log("① 读取并规范化序时账...")
    df = etl.read_journal(src)
    mapping = etl.detect_columns(df)
    mapping, _bad = etl.apply_mapping_override(df, mapping, override)
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        raise ValueError(
            "必需字段缺失：" + "、".join(FIELD_LABELS.get(m, m) for m in missing)
            + "。请先用 check 确认列名，或用 --set 指定映射")

    items, per_voucher = etl.extract_items(df, mapping)
    n_vouchers = len(items)
    log(f"   {len(df)} 行 / {n_vouchers} 张凭证")

    # ── ② 硬规则 ──
    log("② 硬规则预分配（资金内部往来 / 汇兑损益）...")
    assign = hard_rules.preassign(items)
    hard_counts = Counter(assign.values())
    log(f"   命中 {len(assign)} 张 {dict(hard_counts) if assign else ''}")

    # ── ③ 模型打分（只对未被硬规则拿走的凭证）──
    model_items = [it for it in items if it["vid"] not in assign]
    probs_by_vid: Dict[str, Dict[str, float]] = {}
    unknown_by_vid: Dict[str, List[str]] = {}
    unique_inputs = len(model_items)
    if model_items:
        if classifier is None:
            log("③ 加载模型...")
            classifier = JournalClassifier()
        log(f"③ 模型打分（{len(model_items)} 张，按输入折叠）...")
        results, unique_inputs, _keys = classifier.predict_folded(
            model_items, chunk_size=chunk_size)
        for it, r in zip(model_items, results):
            probs_by_vid[it["vid"]] = r["probs"]
            unknown_by_vid[it["vid"]] = r["unknown_subjects"]
        if unique_inputs < len(model_items):
            log(f"   输入折叠：{len(model_items)} → {unique_inputs} 次推理"
                f"（省 {1 - unique_inputs / len(model_items):.0%}）")

    # ── 组装结论 ──
    classifications: Dict[str, str] = {}
    confidences: Dict[str, str] = {}
    top3_by_vid: Dict[str, List] = {}
    for it in items:
        vid = it["vid"]
        if vid in assign:
            classifications[vid] = assign[vid]
            confidences[vid] = "高"          # 程序写死，无歧义
            continue
        probs = probs_by_vid.get(vid) or {}
        if not probs:
            # 无摘要 / 模型返回空 → 交给 AI 复核
            classifications[vid] = "其他业务"
            confidences[vid] = "低"
            continue
        top = max(probs.items(), key=lambda kv: kv[1])
        classifications[vid] = top[0]
        confidences[vid] = ("高" if top[1] >= CONF_HIGH
                            else "中" if top[1] >= CONF_MID else "低")
        if classifier is not None:
            top3_by_vid[vid] = classifier.top_k(probs, 3)

    # ── ④ 写回 ──
    log("④ 写回序时账...")
    classified = etl.write_back(df, mapping, classifications, confidences)

    # ── ⑤ 导出 ──
    log("⑤ 导出结果文件...")
    files = _export(classified, per_voucher, probs_by_vid, top3_by_vid,
                    classifications, confidences, hard_counts, mapping,
                    out_dir, stem, unique_inputs, len(model_items))

    stats = _stats(classified, mapping, classifications, confidences,
                   hard_counts, per_voucher)
    stats["elapsed_seconds"] = round(time.perf_counter() - t_all, 1)
    stats["output_files"] = files
    stats["column_mapping"] = mapping
    stats["model"] = (classifier.meta if classifier is not None else {})
    stats["unique_inputs"] = unique_inputs
    stats["model_calls_saved"] = max(len(model_items) - unique_inputs, 0)
    return stats


# ============================================================================
# 导出
# ============================================================================

def _export(classified: pd.DataFrame, per_voucher: pd.DataFrame,
            probs_by_vid, top3_by_vid, classifications, confidences,
            hard_counts, mapping, out_dir: Path, stem: str,
            unique_inputs: int, model_items: int) -> Dict[str, str]:
    files: Dict[str, str] = {}

    # 1) 分类结果（给用户）
    p = out_dir / f"{stem}_分类结果.xlsx"
    etl.write_excel(classified, p, "分类结果")
    files["分类结果"] = str(p)

    # 2) 待复核清单（给 AI）——只含中/低置信度，按凭证一条
    review_rows = []
    for _, row in per_voucher.iterrows():
        vid = str(row["vid"])
        conf = confidences.get(vid, "低")
        if conf == "高":
            continue
        probs = probs_by_vid.get(vid) or {}
        top3 = top3_by_vid.get(vid) or []
        review_rows.append({
            "凭证号": vid,
            "业务分类": classifications.get(vid, "未分类"),
            "置信度": conf,
            "科目组合": " + ".join(row["subjects"] or []),
            "摘要": str(row["summary"])[:200],
            "金额": row["amount"],
            "制单人": row["bookkeeper"],
            "模型Top1": f"{top3[0][0]}({top3[0][1]:.3f})" if top3 else "",
            "模型Top2": f"{top3[1][0]}({top3[1][1]:.3f})" if len(top3) > 1 else "",
            "模型Top3": f"{top3[2][0]}({top3[2][1]:.3f})" if len(top3) > 2 else "",
        })
    if review_rows:
        p2 = out_dir / f"{stem}_待复核.xlsx"
        etl.write_excel(pd.DataFrame(review_rows), p2, "待复核")
        files["待复核清单"] = str(p2)

    # 3) 凭证级明细（含模型概率，便于追溯）
    detail_rows = []
    for _, row in per_voucher.iterrows():
        vid = str(row["vid"])
        probs = probs_by_vid.get(vid) or {}
        top3 = top3_by_vid.get(vid) or []
        detail_rows.append({
            "凭证号": vid,
            "业务分类": classifications.get(vid, "未分类"),
            "置信度": confidences.get(vid, "低"),
            "判定来源": "硬规则" if vid in _hard_vids(hard_counts, classifications)
                        else "模型",
            "科目组合": " + ".join(row["subjects"] or []),
            "摘要": str(row["summary"])[:200],
            "金额": row["amount"],
            "制单人": row["bookkeeper"],
            "模型Top1": f"{top3[0][0]}" if top3 else "",
            "模型Top1概率": round(top3[0][1], 4) if top3 else None,
            "候选桶数": len(probs),
        })
    if detail_rows:
        p3 = out_dir / f"{stem}_凭证明细.xlsx"
        etl.write_excel(pd.DataFrame(detail_rows), p3, "凭证明细")
        files["凭证明细"] = str(p3)

    # 4) 去重后的模型输入清单（AI 按"条目"复核用，比逐凭证省得多）
    uniq: Dict[Any, dict] = {}
    for _, row in per_voucher.iterrows():
        vid = str(row["vid"])
        if classifications.get(vid) in ("资金内部往来", "汇兑损益"):
            continue
        key = (tuple(row["subjects"] or []), str(row["summary"]))
        if key in uniq:
            uniq[key]["凭证数"] += 1
            continue
        probs = probs_by_vid.get(vid) or {}
        top3 = top3_by_vid.get(vid) or []
        uniq[key] = {
            "科目组合": " + ".join(row["subjects"] or []),
            "摘要": str(row["summary"])[:200],
            "业务分类": classifications.get(vid, "未分类"),
            "置信度": confidences.get(vid, "低"),
            "凭证数": 1,
            "模型Top1": f"{top3[0][0]}({top3[0][1]:.3f})" if top3 else "",
            "模型Top2": f"{top3[1][0]}({top3[1][1]:.3f})" if len(top3) > 1 else "",
        }
    if uniq:
        p4 = out_dir / f"{stem}_待复核条目.xlsx"
        etl.write_excel(pd.DataFrame(list(uniq.values())), p4, "待复核条目")
        files["待复核条目"] = str(p4)

    return files


def _hard_vids(hard_counts, classifications) -> set:
    return {v for v, b in classifications.items() if b in HARD_RULE_BUCKETS}


# ============================================================================
# 统计
# ============================================================================

def _stats(classified: pd.DataFrame, mapping: Dict[str, str],
           classifications: Dict[str, str], confidences: Dict[str, str],
           hard_counts: Counter, per_voucher: pd.DataFrame) -> Dict[str, Any]:
    v_col = mapping["voucher_no"]
    bucket_counts = dict(Counter(classifications.values()))
    conf_dist = dict(Counter(confidences.values()))

    amount_col = mapping.get("debit", "")
    amount_by_bucket = {}
    if amount_col and amount_col in classified.columns:
        nums = pd.to_numeric(
            classified[amount_col].astype(str).str.replace(",", "").str.strip(),
            errors="coerce").abs().fillna(0)
        tmp = classified.assign(_amt=nums)
        amount_by_bucket = {
            str(b): round(float(v), 2)
            for b, v in tmp.groupby("业务分类")["_amt"].sum().items()
        }

    n = len(classifications)
    unclassified = bucket_counts.get("未分类", 0)
    hard_total = sum(hard_counts.values()) if hard_counts else 0

    return {
        "total_vouchers": n,
        "total_rows": int(len(classified)),
        "classified": n - unclassified,
        "unclassified": unclassified,
        "coverage": round((n - unclassified) / n, 4) if n else 0.0,
        "bucket_counts": bucket_counts,
        "amount_by_bucket": amount_by_bucket,
        "confidence_dist": conf_dist,
        "hard_rule_hits": dict(hard_counts) if hard_counts else {},
        "hard_rule_total": hard_total,
        "model_vouchers": n - hard_total,
        "bucket_count": len(ALL_BUCKETS),
    }
