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
from .config import (ALL_BUCKETS, BUCKET_SRC_FALLBACK, BUCKET_SRC_HARD,
                     BUCKET_SRC_MODEL, BUCKET_SRC_PATCH, COL_BUCKET_SRC,
                     CONF_HIGH, CONF_MID, FIELD_LABELS, HARD_RULE_BUCKETS,
                     LOWEST_CONF_N, MODEL_BUCKETS, REQUIRED_FIELDS)
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
# ③b 业务分类补丁（AI 复核改判）
# ============================================================================
#
# 为什么需要这条回边：
#   现金流是拿业务桶算的（④ 直接读 classifications）。如果 AI 在跑完之后才发现
#   某个桶判错了，此前**没有任何合规通道**把结论送回程序——现金流补丁只认
#   「待复核」的行、只改现金流量项目，改不了桶；手改导出 Excel 则违反
#   "产出文件由程序落盘"。
#   实测后果：样例 460 条现金流分录里 **431 条（93.7%）由「桶+方向」直接判定**，
#   桶一错，项目就跟着错，而且候选唯一，程序看起来"很确定"，不会报警。
#
# 所以补丁必须在 ④ **之前**套用：先改桶，再由程序重算现金流，
# 最后只落盘一份自洽的交付物（业务分类与现金流量项目互不矛盾）。

def load_bucket_patch(path: str | Path) -> Dict[str, Any]:
    """读业务分类补丁并做格式校验。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"补丁文件不存在: {p}")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("补丁文件顶层必须是 JSON 对象")
    vouchers = doc.get("vouchers")
    if vouchers is None:
        raise ValueError(
            "补丁文件缺 \"vouchers\" 字段——业务分类补丁形如 "
            "{\"vouchers\": {\"记-001\": {\"bucket\": \"单位往来\", "
            "\"note\": \"依据（可选）\"}}}")
    if not isinstance(vouchers, dict):
        raise ValueError("\"vouchers\" 必须是对象：凭证号 -> {\"bucket\": \"...\"}")
    return doc


def apply_bucket_patch(classifications: Dict[str, str],
                       sources: Dict[str, str],
                       patch: Optional[dict]) -> Dict[str, Any]:
    """把 AI 的「凭证号 → 业务桶」复核结论套到分类上（原地改）。

    只做两件事：校验桶名合法、确认凭证号存在。**不碰现金流**——现金流会在
    ④ 里按改好的桶重算，这正是这条回边存在的意义。
    """
    stat: Dict[str, Any] = {"provided": 0, "applied": 0,
                            "rejected": [], "unknown_vouchers": []}
    if not patch:
        return stat

    vouchers = patch.get("vouchers") or {}
    stat["provided"] = len(vouchers)
    valid = set(ALL_BUCKETS)

    for key, spec in vouchers.items():
        vid = str(key)
        if isinstance(spec, str):
            spec = {"bucket": spec}
        if not isinstance(spec, dict):
            stat["rejected"].append({"凭证号": vid, "原因": "值必须是对象或字符串"})
            continue
        bucket = str(spec.get("bucket", "")).strip()
        if bucket not in valid:
            stat["rejected"].append({
                "凭证号": vid,
                "原因": f"不是 20 个业务桶之一：{bucket or '(空)'}"})
            continue
        if vid not in classifications:
            stat["unknown_vouchers"].append(vid)
            continue
        classifications[vid] = bucket
        sources[vid] = BUCKET_SRC_PATCH
        stat["applied"] += 1

    return stat


# ============================================================================
# ②-⑤ 完整清洗
# ============================================================================

def run_file(path: str | Path,
             out_dir: Optional[str | Path] = None,
             override: Optional[Dict[str, str]] = None,
             classifier: Optional[JournalClassifier] = None,
             chunk_size: int = 64,
             cf_patch: Optional[dict] = None,
             bucket_patch: Optional[dict] = None,
             lowest_n: int = LOWEST_CONF_N,
             progress=None) -> Dict[str, Any]:
    """跑完一份序时账的完整清洗，返回结果统计（含导出文件路径）。

    执行顺序里有一处**不可调换**：业务分类补丁（③b）必须早于现金流（④）。
    现金流是拿业务桶算的，先改桶再算现金流，交付物才自洽。

    Args:
        classifier:   复用已加载的模型实例（批量跑多份文件时传，省去重复加载）
        cf_patch:     现金流补丁（人工/AI 对"待复核"条目的结论），
                      形如 {"rows": {"行号": {"item": "现金流量项目"}}}
        bucket_patch: 业务分类补丁（AI 复核改判的桶），
                      形如 {"vouchers": {"记-001": {"bucket": "单位往来"}}}；
                      在算现金流**之前**套用，所以现金流会按改好的桶重算
        lowest_n:     出参里附几张「最可疑」凭证（按 top1 概率升序，含完整分录）；
                      0 表示不出（见 config.LOWEST_CONF_N 的说明）
        progress:     可选回调 progress(str)，用于向用户报进度
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
    raw_df = etl.read_journal(src)
    mapping = etl.detect_columns(raw_df)
    mapping, _bad = etl.apply_mapping_override(raw_df, mapping, override)
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        raise ValueError(
            "必需字段缺失：" + "、".join(FIELD_LABELS.get(m, m) for m in missing)
            + "。请先用 check 确认列名，或用 --set 指定映射")

    df = etl.add_row_index(raw_df)
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
    sources: Dict[str, str] = {}
    top3_by_vid: Dict[str, List] = {}
    for it in items:
        vid = it["vid"]
        if vid in assign:
            classifications[vid] = assign[vid]
            confidences[vid] = "高"          # 程序写死，无歧义
            sources[vid] = BUCKET_SRC_HARD
            continue
        probs = probs_by_vid.get(vid) or {}
        if not probs:
            # 无摘要 / 模型返回空 → 交给 AI 复核
            classifications[vid] = "其他业务"
            confidences[vid] = "低"
            sources[vid] = BUCKET_SRC_FALLBACK
            continue
        top = max(probs.items(), key=lambda kv: kv[1])
        classifications[vid] = top[0]
        confidences[vid] = ("高" if top[1] >= CONF_HIGH
                            else "中" if top[1] >= CONF_MID else "低")
        sources[vid] = BUCKET_SRC_MODEL
        if classifier is not None:
            top3_by_vid[vid] = classifier.top_k(probs, 3)

    # ── ③b 业务分类补丁（AI 复核改判）—— 必须在 ④ 之前 ──
    bucket_stat = apply_bucket_patch(classifications, sources, bucket_patch)
    if bucket_stat["provided"]:
        log(f"③b 应用业务分类补丁：提交 {bucket_stat['provided']} 条 / "
            f"采纳 {bucket_stat['applied']} 条 / "
            f"驳回 {len(bucket_stat['rejected'])} 条"
            + (f" / 凭证号不存在 {len(bucket_stat['unknown_vouchers'])} 条"
               if bucket_stat["unknown_vouchers"] else ""))
        if bucket_stat["applied"]:
            log("   已改判的桶会带着重算现金流（见 ④）")

    # ── ④ 现金流（业务桶 → 现金流量项目，纯程序）──
    log("④ 编制现金流量表...")
    money_by_vid, money_flags = etl.build_money_rows(df, mapping)
    cf_by_row: Dict[int, dict] = {}
    counterparts: Dict[int, List[str]] = {}
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
            counterparts[row_no] = list(verdict.counterparty)

    # 待复核清单（给 AI 兜底用，走 CLI 出参，不进给用户的 Excel）
    row_info = _row_info(df, mapping)
    review_items = cashflow.make_review_payload(cf_by_row, row_info, counterparts)
    log(f"   程序判定 {len(cf_by_row) - len(review_items)} 条 / "
        f"待复核 {len(review_items)} 条")

    # ── ④b 应用补丁（人工/AI 对"待复核"的结论）──
    if cf_patch:
        cf_by_row, patch_stat = cashflow.apply_patch(cf_by_row, cf_patch)
        log(f"   应用补丁：提交 {patch_stat['provided']} 条 / "
            f"采纳 {patch_stat['applied']} 条 / "
            f"驳回 {len(patch_stat['rejected'])} 条")
    else:
        patch_stat = None
    review_items = cashflow.make_review_payload(cf_by_row, row_info, counterparts)

    cf_stats = _cashflow_stats(money_by_vid, cf_by_row)
    cf_stats["patched_rows"] = (patch_stat or {}).get("applied", 0)
    cf_stats["patch_rejected"] = (patch_stat or {}).get("rejected", [])
    cf_stats["patch_unknown_rows"] = (patch_stat or {}).get("unknown_rows", [])
    cf_stats["patch_not_review"] = (patch_stat or {}).get("not_review", [])
    log(f"   现金流相关凭证 {cf_stats['money_vouchers']} 张 / "
        f"货币资金分录 {cf_stats['money_rows']} 条 / "
        f"仍需复核 {cf_stats['needs_review_rows']} 条")

    # ── ⑤ 写回 ──
    log("⑤ 写回序时账...")
    classified = etl.write_back(df, mapping, classifications, confidences,
                               sources)
    classified = etl.write_cashflow(classified, mapping, money_flags, cf_by_row)

    # ── ⑥ 导出（一个 Excel，多 sheet）──
    log("⑥ 导出结果文件...")
    exported = _export(classified, per_voucher, probs_by_vid, top3_by_vid,
                       classifications, confidences, hard_counts, mapping,
                       out_dir, stem, unique_inputs, len(model_items),
                       money_by_vid, money_flags, cf_by_row, raw_df=raw_df)

    stats = _stats(classified, mapping, classifications, confidences,
                   hard_counts, per_voucher)
    stats["elapsed_seconds"] = round(time.perf_counter() - t_all, 1)
    stats["output_files"] = exported["files"]
    stats["sheet_rows"] = exported["sheet_rows"]
    stats["cashflow_statement"] = exported["cashflow_statement"]
    stats["cashflow_review"] = review_items
    stats["column_mapping"] = mapping
    stats["model"] = (classifier.meta if classifier is not None else {})
    stats["unique_inputs"] = unique_inputs
    stats["model_calls_saved"] = max(len(model_items) - unique_inputs, 0)
    stats["cashflow"] = cf_stats
    stats["bucket_patch"] = bucket_stat
    stats["lowest_confidence"] = (
        _lowest_confidence(probs_by_vid, top3_by_vid, df, mapping, lowest_n)
        if lowest_n and lowest_n > 0 else {"count": 0, "how_to_use": "", "items": []})
    return stats


def _row_info(df: pd.DataFrame, mapping: Dict[str, str]) -> Dict[int, dict]:
    """{行号: {凭证号, 摘要, 科目, 借方, 贷方}} —— 给待复核清单补上下文。"""
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]
    sum_col = mapping.get("summary", "")
    has_sum = bool(sum_col) and sum_col in df.columns

    out: Dict[int, dict] = {}
    for (row_no, vid, subj, debit, credit, text) in zip(
            df["_行号"], df[v_col], df[s_col], df[d_col], df[c_col],
            df[sum_col] if has_sum else [""] * len(df)):
        out[int(row_no)] = {
            "凭证号": str(vid),
            "科目": "" if subj is None else str(subj).strip(),
            "摘要": "" if text is None else str(text),
            "借方": etl.to_number(debit),
            "贷方": etl.to_number(credit),
        }
    return out


def _voucher_entries(df: pd.DataFrame, mapping: Dict[str, str],
                     vids: List[str]) -> Dict[str, List[dict]]:
    """{凭证号: [全部分录]} —— 给「最可疑」清单补上判断依据。

    只认需要的凭证，避免给整份序时账做无谓的分组。
    """
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]
    det_col = mapping.get("subject_name") or ""
    has_det = bool(det_col) and det_col in df.columns

    want = {str(v) for v in vids}
    out: Dict[str, List[dict]] = {v: [] for v in want}
    for vno, subj, det, debit, credit in zip(
            df[v_col], df[s_col],
            df[det_col] if has_det else [None] * len(df),
            df[d_col], df[c_col]):
        key = str(vno)
        if key not in want:
            continue
        item: Dict[str, Any] = {
            "一级科目": "" if subj is None or pd.isna(subj) else str(subj).strip(),
            "借方金额": etl.to_number(debit),
            "贷方金额": etl.to_number(credit),
        }
        if has_det:
            item["明细"] = None if det is None or pd.isna(det) else str(det)
        out[key].append(item)
    return out


def _summary_by_vid(df: pd.DataFrame, mapping: Dict[str, str],
                    vids: List[str]) -> Dict[str, str]:
    """{凭证号: 摘要}（同张凭证摘要通常一致，取第一条非空）。"""
    sum_col = mapping.get("summary") or ""
    v_col = mapping["voucher_no"]
    want = {str(v) for v in vids}
    out: Dict[str, str] = {}
    if not sum_col or sum_col not in df.columns:
        return out
    for vno, text in zip(df[v_col], df[sum_col]):
        key = str(vno)
        if key not in want or key in out:
            continue
        if text is not None and not pd.isna(text) and str(text).strip():
            out[key] = str(text)
    return out


def _lowest_confidence(probs_by_vid: Dict[str, Dict[str, float]],
                       top3_by_vid: Dict[str, List],
                       df: pd.DataFrame, mapping: Dict[str, str],
                       n: int) -> Dict[str, Any]:
    """按模型 top1 概率**升序**列出最可疑的 N 张凭证（给 AI 复核用）。

    为什么不能靠 confidence_dist 做诊断：这个模型对熟悉口径会饱和到概率 1.0
    （实测中位数 1.0000、99.5% 落进「高」档），三档标签几乎恒为全高，**起不到
    判别作用**；但**概率的排序是有信息的**——实测最低的那几条正是判错的几条。
    所以这里给排序，不给 AI 看假的三档分布。

    硬规则预分配的凭证不在 probs_by_vid 里，自然不参与（它们本来无歧义）。
    """
    ranked: List[tuple] = []
    for vid, probs in probs_by_vid.items():
        if not probs:
            continue
        top = top3_by_vid.get(vid) or []
        if top:
            bucket, p = str(top[0][0]), float(top[0][1])
            alts = [[str(b), round(float(v), 4)] for b, v in top[1:]]
        else:
            bucket, p = max(probs.items(), key=lambda kv: kv[1])
            p = float(p)
            alts = []
        ranked.append((p, str(vid), bucket, alts))
    ranked.sort(key=lambda t: t[0])
    ranked = ranked[:max(1, int(n))]

    vids = [r[1] for r in ranked]
    ledgers = _voucher_entries(df, mapping, vids)
    sums = _summary_by_vid(df, mapping, vids)

    items = [{
        "凭证号": vid,
        "业务分类": bucket,
        "概率": round(p, 4),
        "次选": alts,
        "摘要": sums.get(vid, "")[:200],
        "分录": ledgers.get(vid, []),
    } for p, vid, bucket, alts in ranked]

    return {
        "count": len(items),
        "how_to_use": (
            "按模型对已判桶的概率**升序**，最可疑的排在最前；"
            "「次选」是模型拿不准时的第二/第三选择，往往就是正确答案。"
            "逐条核对整张凭证（别只看摘要）：**判错了就写成业务分类补丁**"
            "（{\"vouchers\": {\"凭证号\": {\"bucket\": \"…\"}}}）交给 "
            "run --bucket-patch —— 程序会在算现金流**之前**套用它，"
            "现金流会按改好的桶重算，交付物才自洽。"
            "别手改 Excel 的分类列：那不会重算现金流。"),
        "items": items,
    }


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
# 导出（一个 Excel，多 sheet）
# ============================================================================
#
# 设计立场（skill 设计心得 · 核心二）：
#   落盘是"确定的活"，由程序负责，AI 不参与、也不手改产出文件。
#   用户拿到的是**一份**交付物，不是一个结果一个 xlsx。
#
# 版面刻意简单，只呈现结果：
#   ① 原始序时账      —— 用户给什么就是什么，一个字不改
#   ② 业务分类结果    —— ① + 业务分类/置信度 + 现金流量项目
#   ③ 货币资金凭证    —— 与现金流有关的整张凭证 + 现金流量项目
#
# 没有"待复核"sheet：待复核条目走 CLI 出参（JSON）给 AI，
# 不塞进给用户的交付物里。AI 的结论通过补丁回到程序，再重出本文件。

ADDED_COLUMNS = ("业务分类", COL_BUCKET_SRC, "置信度", "现金流量项目",
                 "现金流方向", "现金流判定", "现金流复核",
                 "是否现金流相关凭证")


def _raw_view(classified: pd.DataFrame, mapping: Dict[str, str],
              rename_subject: bool = True) -> pd.DataFrame:
    """还原成"用户原始序时账"：只保留用户的列，顺序不变。

    rename_subject：是否把科目列**统一成程序口径「一级科目」**。
      - 第①张 sheet（原始序时账）必须 **False**：用户给什么就是什么，
        连列名都不改，否则"原始"两个字就名不副实。
      - 第②③张 sheet **True**：那两张是程序产出，把科目列统一成「一级科目」，
        这样不管用户原来叫「科目」还是「存货科目」，下游都有个稳定列名可找。

    注意：这里只改科目这一列的**名字**，值一个不动；其余列保留用户原列名。
    （早先的注释把方向写反了，害得 `sample` 去按「科目」找列、静默丢失。）
    """
    cols = [c for c in classified.columns
            if not str(c).startswith("_") and c not in ADDED_COLUMNS]
    out = classified[cols].copy()
    if (rename_subject and mapping.get("subject")
            and mapping["subject"] in out.columns):
        out = out.rename(columns={mapping["subject"]: "一级科目"})
    return out


def _money_voucher_view(classified: pd.DataFrame, mapping: Dict[str, str],
                        money_flags: Dict[str, bool]) -> pd.DataFrame:
    """第③张表：含货币资金科目的**整张**凭证（不是只留货币资金行）。

    编现金流要看的正是货币资金的对方在干什么，所以整张凭证都留着；
    但只给货币资金那一行填「现金流量项目」——这是会计惯例。
    """
    v_col = mapping["voucher_no"]
    hit = classified[v_col].astype(str).map(money_flags).fillna(False)
    out = classified[hit].copy()
    out = out.drop(columns=[c for c in ("是否现金流相关凭证",) if c in out.columns])
    extra = {c: out[c].values for c in ("业务分类", COL_BUCKET_SRC,
                                        "现金流量项目", "现金流方向")
             if c in out.columns}
    return _raw_view(out, mapping).assign(**extra)


def _cashflow_statement(cf_by_row: Dict[int, dict]) -> pd.DataFrame:
    """现金流量表：按项目聚合，收付分列，末尾一行净增加额。"""
    rows = []
    for v in cf_by_row.values():
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
    if not rows:
        return pd.DataFrame(columns=["活动类型", "现金流量项目", "收现",
                                     "付现", "笔数", "净额"])

    agg = (pd.DataFrame(rows)
           .groupby(["活动类型", "现金流量项目"], as_index=False)
           .agg(收现=("_in", "sum"), 付现=("_out", "sum"), 笔数=("_in", "size")))
    agg["净额"] = agg["收现"] - agg["付现"]

    # 按现金流量表标准顺序排（经营 / 投资 / 筹资 / 其他）
    sec_order = {s: i for i, s in enumerate(cashflow.load_cf_config()["sections"])}
    item_order = {n: i for i, n in enumerate(cashflow.cf_items())}
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


def _export(classified: pd.DataFrame, per_voucher: pd.DataFrame,
            probs_by_vid, top3_by_vid, classifications, confidences,
            hard_counts, mapping, out_dir: Path, stem: str,
            unique_inputs: int, model_items: int,
            money_by_vid: Optional[Dict[str, List]] = None,
            money_flags: Optional[Dict[str, bool]] = None,
            cf_by_row: Optional[Dict[int, dict]] = None,
            raw_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """写出**一个** Excel（多 sheet），返回 {files, sheet_rows}。"""
    cf_by_row = cf_by_row or {}
    money_flags = money_flags or {}
    base = raw_df if raw_df is not None else classified

    sheets: Dict[str, pd.DataFrame] = {}
    # ① 原始序时账：用户给什么就是什么，列名也不动
    sheets["原始序时账"] = _raw_view(base, mapping, rename_subject=False)
    # ② 业务分类结果 + ③ 货币资金凭证：程序产出，内部名还回用户口径
    #    注意：_raw_view 会把 ADDED_COLUMNS 全部剥掉（那是给①用的），
    #    所以这里必须**逐个**把新增列装回去——漏一个就会静默丢列。
    sheets["业务分类结果"] = _raw_view(classified, mapping).assign(
        业务分类=classified["业务分类"].values,
        分类判定=classified[COL_BUCKET_SRC].values,
        置信度=classified["置信度"].values,
        现金流量项目=classified["现金流量项目"].values,
        现金流方向=classified["现金流方向"].values,
        现金流判定=classified["现金流判定"].values,
    )
    if money_flags:
        mv = _money_voucher_view(classified, mapping, money_flags)
        if not mv.empty:
            sheets["货币资金凭证"] = mv

    path = out_dir / f"{stem}_结果.xlsx"
    etl.write_excel_sheets(sheets, path)

    return {
        "files": {"结果": str(path)},
        "sheet_rows": {k: int(len(v)) for k, v in sheets.items()},
        "cashflow_statement": _cashflow_statement(cf_by_row),
    }


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
