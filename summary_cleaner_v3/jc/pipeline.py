# -*- coding: utf-8 -*-
"""Pipeline —— 3.0 的完整清洗流程编排。

    序时账 Excel
        │
        ├─ ① ETL        读文件、认列名、规范化、抽出模型输入（一凭证一条）
        ├─ ② 硬规则     两条机械规则预分配（资金内部往来 / 汇兑损益）
        ├─ ③ 模型打分   按输入折叠 → 批量推理 → top1 + 置信度
        ├─ ④ 写回       结论逐行写回原序时账
        ├─ ⑤ 现金流     业务桶 → 现金流量项目（纯程序，按货币资金借贷定方向）
        └─ ⑥ 导出       分类结果 / 现金流相关凭证 / 现金流量表 / 复核清单

程序不参与业务桶打分：18 个业务桶全部来自模型，中/低置信度交给 AI 复核。
现金流是清洗之后的第二步，只依赖业务桶，不依赖模型、也不依赖 AI。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from . import cashflow
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

    df = etl.add_row_index(df)
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

    # ── ④ 现金流（业务桶 → 现金流量项目，纯程序）──
    log("④ 编制现金流量表...")
    money_by_vid, money_flags = etl.build_money_rows(df, mapping)
    cf_by_row: Dict[int, dict] = {}
    for vid, rows in money_by_vid.items():
        bucket = classifications.get(vid, "")
        for row_no, verdict in cashflow.judge_voucher(rows, bucket):
            cf_by_row[row_no] = {
                "cf_item": verdict.cf_item,
                "direction": verdict.direction,
                "amount": verdict.amount,
                "source": verdict.source,
                "needs_review": verdict.needs_review,
                "no_cash": verdict.no_cash,
                "candidates": verdict.candidates,
                "bucket": verdict.bucket,
                "note": verdict.note,
            }
    cf_stats = _cashflow_stats(money_by_vid, cf_by_row)
    log(f"   现金流相关凭证 {cf_stats['money_vouchers']} 张 / "
        f"货币资金分录 {cf_stats['money_rows']} 条 / "
        f"需复核 {cf_stats['needs_review_rows']} 条")

    # ── ⑤ 写回 ──
    log("⑤ 写回序时账...")
    classified = etl.write_back(df, mapping, classifications, confidences)
    classified = etl.write_cashflow(classified, mapping, money_flags, cf_by_row)

    # ── ⑥ 导出 ──
    log("⑥ 导出结果文件...")
    files = _export(classified, per_voucher, probs_by_vid, top3_by_vid,
                    classifications, confidences, hard_counts, mapping,
                    out_dir, stem, unique_inputs, len(model_items),
                    money_by_vid, money_flags, cf_by_row)

    stats = _stats(classified, mapping, classifications, confidences,
                   hard_counts, per_voucher)
    stats["elapsed_seconds"] = round(time.perf_counter() - t_all, 1)
    stats["output_files"] = files
    stats["column_mapping"] = mapping
    stats["model"] = (classifier.meta if classifier is not None else {})
    stats["unique_inputs"] = unique_inputs
    stats["model_calls_saved"] = max(len(model_items) - unique_inputs, 0)
    stats["cashflow"] = cf_stats
    return stats


# ============================================================================
# 现金流统计
# ============================================================================

def _cashflow_stats(money_by_vid: Dict[str, List], cf_by_row: Dict[int, dict]
                    ) -> Dict[str, Any]:
    """现金流结果概况：多少张相关凭证、多少条分录、多少条要复核。"""
    by_item: Dict[str, float] = {}
    by_section: Dict[str, float] = {}
    review_rows = 0
    empty_rows = 0
    wash_rows = 0
    for row_no, v in cf_by_row.items():
        if v.get("no_cash"):
            wash_rows += 1
            continue
        item = v.get("cf_item") or ""
        if not item:
            empty_rows += 1
        else:
            by_item[item] = by_item.get(item, 0.0) + float(v.get("amount") or 0)
            sec = cashflow.section_of(item) or "其他"
            by_section[sec] = by_section.get(sec, 0.0) + float(v.get("amount") or 0)
        if v.get("needs_review"):
            review_rows += 1

    return {
        "money_vouchers": len(money_by_vid),
        "money_rows": len(cf_by_row),
        "wash_rows": wash_rows,
        "needs_review_rows": review_rows,
        "rows_without_item": empty_rows,
        "amount_by_item": {k: round(v, 2) for k, v in
                           sorted(by_item.items(), key=lambda kv: -abs(kv[1]))},
        "amount_by_section": {k: round(v, 2) for k, v in by_section.items()},
        "net_cash": round(sum(by_item.values()), 2),
    }


# ============================================================================
# 导出
# ============================================================================

def _export(classified: pd.DataFrame, per_voucher: pd.DataFrame,
            probs_by_vid, top3_by_vid, classifications, confidences,
            hard_counts, mapping, out_dir: Path, stem: str,
            unique_inputs: int, model_items: int,
            money_by_vid: Optional[Dict[str, List]] = None,
            money_flags: Optional[Dict[str, bool]] = None,
            cf_by_row: Optional[Dict[int, dict]] = None) -> Dict[str, str]:
    files: Dict[str, str] = {}

    # 1) 分类结果（给用户）—— 完整序时账 + 业务分类 + 现金流量项目
    p = out_dir / f"{stem}_分类结果.xlsx"
    etl.write_excel(_drop_internal(classified), p, "分类结果")
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

    # 5) 现金流相关凭证（整张凭证，带现金流量项目）
    if money_by_vid:
        files.update(_export_cashflow(classified, mapping, money_by_vid,
                                      money_flags or {}, cf_by_row or {},
                                      out_dir, stem))

    return files


# ============================================================================
# 现金流导出（理论文档"给用户交付什么"的三件）
# ============================================================================

def _drop_internal(df: pd.DataFrame) -> pd.DataFrame:
    """丢掉内部用的 _行号 列（不写给用户）。"""
    cols = [c for c in df.columns if not str(c).startswith("_")]
    return df[cols]


def _export_cashflow(classified: pd.DataFrame, mapping: Dict[str, str],
                     money_by_vid: Dict[str, List],
                     money_flags: Dict[str, bool],
                     cf_by_row: Dict[int, dict],
                     out_dir: Path, stem: str) -> Dict[str, str]:
    """导出：① 现金流相关凭证 ② 现金流量表 ③ 现金流待复核。"""
    files: Dict[str, str] = {}
    v_col = mapping["voucher_no"]

    # ── ① 现金流相关凭证：含货币资金科目的**整张**凭证 ──
    is_money_voucher = classified[v_col].astype(str).map(money_flags).fillna(False)
    cf_detail = classified[is_money_voucher].copy()
    p5 = out_dir / f"{stem}_现金流相关凭证.xlsx"
    etl.write_excel(_drop_internal(cf_detail), p5, "现金流相关凭证")
    files["现金流相关凭证"] = str(p5)

    # ── ② 现金流量表（按项目聚合，收付分列）──
    rows = []
    for row_no, v in cf_by_row.items():
        item = v.get("cf_item") or ""
        if not item or v.get("no_cash"):
            continue
        amt = float(v.get("amount") or 0)
        rows.append({
            "现金流量项目": item,
            "活动类型": cashflow.section_of(item) or "其他",
            "_in": amt if amt > 0 else 0.0,
            "_out": -amt if amt < 0 else 0.0,
        })
    if rows:
        raw = pd.DataFrame(rows)
        agg = (raw.groupby(["活动类型", "现金流量项目"], as_index=False)
               .agg(收现=("_in", "sum"), 付现=("_out", "sum"), 笔数=("_in", "size")))
        agg["净额"] = agg["收现"] - agg["付现"]

        # 按现金流量表的标准顺序排列（经营 / 投资 / 筹资 / 其他）
        order = {s: i for i, s in enumerate(cashflow.load_cf_config()["sections"])}
        item_order = {n: i for i, n in enumerate(cashflow.cf_items())}
        agg["_s"] = agg["活动类型"].map(order).fillna(9)
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
        p6 = out_dir / f"{stem}_现金流量表.xlsx"
        etl.write_excel(pd.concat([agg, total], ignore_index=True), p6, "现金流量表")
        files["现金流量表"] = str(p6)

    # ── ③ 现金流待复核（判不下来的条目）──
    review = []
    for row_no, v in sorted(cf_by_row.items()):
        if not v.get("needs_review"):
            continue
        r = classified.iloc[row_no]
        review.append({
            "行号": row_no + 1,           # 给用户看用 1-based
            "凭证号": str(r.get(v_col, "")),
            "业务分类": v.get("bucket", ""),
            "摘要": str(r.get(mapping.get("summary", ""), ""))[:120],
            "一级科目": str(r.get(mapping.get("subject", ""), "")),
            "借方金额": etl.to_number(r.get(mapping.get("debit", ""))),
            "贷方金额": etl.to_number(r.get(mapping.get("credit", ""))),
            "现金流方向": v.get("direction", ""),
            "候选现金流量项目": "、".join(v.get("candidates") or []),
            "问题": v.get("note", ""),
        })
    if review:
        p7 = out_dir / f"{stem}_现金流待复核.xlsx"
        etl.write_excel(pd.DataFrame(review), p7, "现金流待复核")
        files["现金流待复核"] = str(p7)

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
