# -*- coding: utf-8 -*-
"""完整流程编排（本包只剩"现金流"这一段）。

    序时账 Excel
        │
        ├─ ① 读表 + 认列名            ← summary_cleaner（calamine）
        ├─ ② 业务桶判定               ← summary_cleaner（模型 + 四类硬约束）
        ├─ ③ 现金流：桶 + 方向 → 项目  ← 本包（纯程序，judge.judge_voucher）
        ├─ ④ 写回                     ← 本包（逐行：项目/方向/判定/复核）
        └─ ⑤ 导出                     ← 本包（原始账 / 业务分类结果 / 货币资金凭证）

**清洗逻辑与模型一行都不在这里。** 这是这次改造的核心：上游改了模型、改了桶、
改了硬约束，本包不用跟着改一行代码；反过来本包也不许往回改桶的判定。
唯一的例外是 ③ 前面的 `--bucket` 应急把手（按凭证号覆盖业务桶），
它是操作员给出的结论，不是程序判断。

为什么现金流必须在上游判完之后：实测 460 条现金流分录里 **431 条（93.7%）
由「桶 + 方向」直接定项**，桶一错项目就跟着错，而且候选唯一时程序看起来
"很确定"、不会报警。所以桶的质量决定现金流表的质量——这一层不做补救性猜测。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from . import export, judge, rows
from .config import ensure_upstream

ProgressCb = Optional[Any]          # progress(msg: str)

# 本包对上游的硬要求：没有这四列就编不了现金流
REQUIRED_FIELDS = ("voucher_no", "subject", "debit", "credit")
FIELD_LABELS = {
    "voucher_no": "凭证号",
    "subject": "一级科目",
    "debit": "借方金额",
    "credit": "贷方金额",
    "summary": "摘要",
    "subject_name": "科目明细/二级科目",
    "date": "日期",
}

_CLASSIFIER = None


# ============================================================================
# 上游：分类器与列名识别
# ============================================================================

def get_classifier():
    """上游分类器（进程内单例）。

    模型加载一次约 1.3GB，批量跑多份账时务必复用同一个实例。
    """
    global _CLASSIFIER
    if _CLASSIFIER is None:
        ensure_upstream()
        from summary_cleaner.v2.classifier import JournalClassifier
        from summary_cleaner.v2.config import load_subject_list
        _CLASSIFIER = JournalClassifier(subject_list=load_subject_list())
    return _CLASSIFIER


def _read(path: str | Path) -> pd.DataFrame:
    ensure_upstream()
    from summary_cleaner.v2.io_utils import read_table
    return read_table(path)


def resolve_mapping(df: pd.DataFrame,
                    override: Optional[Dict[str, str]] = None
                    ) -> Dict[str, str]:
    """列名映射：上游自动识别 → 手工覆盖 → 校验本包要求的四列。"""
    ensure_upstream()
    from summary_cleaner.v2.classifier import JournalClassifier

    mapping = dict(JournalClassifier.auto_detect_columns(df))
    for key, col in (override or {}).items():
        if not col:
            continue
        if col not in df.columns:
            raise ValueError(f"--set {key}={col}：表里没有这一列「{col}」")
        mapping[key] = col

    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        cols = "、".join(str(c) for c in df.columns[:12])
        raise ValueError(
            "必需字段缺失：" + "、".join(FIELD_LABELS[f] for f in missing)
            + f"。表的列是：{cols}…。用 --set 一级科目=\"科目\" 这样指定映射")
    return mapping


def _buckets() -> List[str]:
    """上游注册表里的业务桶（去掉哨兵）。"""
    ensure_upstream()
    from summary_cleaner.v2.config import BUCKET_REGISTRY
    return [b for b in BUCKET_REGISTRY if b != "待复核"]


# ============================================================================
# ① check：只读，不加载模型、不写任何文件
# ============================================================================

def check_file(path: str | Path,
               override: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """读文件 + 认列名 + 校验字段 + 数一下有多少货币资金凭证。"""
    df = _read(path)
    mapping = resolve_mapping(df, override)
    money_by_vid, _flags = rows.build_money_rows(df, mapping)

    preview = {}
    for key, col in mapping.items():
        samples = [str(v) for v in df[col].dropna().astype(str).head(3)]
        preview[key] = {"列名": col, "样例": samples,
                        "字段": FIELD_LABELS.get(key, key)}
    return {
        "ok": True,
        "file": str(Path(path).resolve()),
        "rows": int(len(df)),
        "vouchers": int(df[mapping["voucher_no"]].nunique()),
        "columns": [str(c) for c in df.columns],
        "mapping": {k: str(v) for k, v in mapping.items()},
        "preview": preview,
        "cashflow": {
            "money_vouchers": len(money_by_vid),
            "money_rows": sum(len(v) for v in money_by_vid.values()),
            "note": "只有这些凭证会进入现金流判定；其余凭证不影响现金流量表",
        },
    }


# ============================================================================
# ② run：清洗（上游）→ 现金流（本包）→ 导出
# ============================================================================

def _apply_bucket_patch(clean, mapping: Dict[str, str],
                        patch: Optional[dict]) -> Dict[str, Any]:
    """按凭证号覆盖业务桶（操作员的结论，不是程序判断）。

    格式：``{"vouchers": {"<凭证号>": "<业务桶>"}}``

    为什么保留这个把手：现金流"待复核"的根因多半是桶判错，没有它就只能
    回去改上游再整本重跑。它是应急通道，不是常规流程——常规流程是在
    `summary_cleaner` 侧把模型/别名表修好。
    """
    stat: Dict[str, Any] = {"provided": 0, "applied": 0, "changed_vouchers": 0,
                            "rejected": [], "unknown": []}
    vouchers = (patch or {}).get("vouchers") or {}
    if not vouchers:
        return stat
    stat["provided"] = len(vouchers)

    valid = set(_buckets())
    detail = clean.detail
    for vid, bucket in vouchers.items():
        vid, bucket = str(vid).strip(), str(bucket).strip()
        if bucket not in valid:
            stat["rejected"].append({"凭证号": vid,
                                     "原因": f"不是合法业务桶：{bucket or '(空)'}"})
            continue
        mask = detail["凭证号"].astype(str) == vid
        if not bool(mask.any()):
            stat["unknown"].append(vid)
            continue
        detail.loc[mask, "业务分类"] = bucket
        detail.loc[mask, "判定来源"] = "复核改判"
        stat["applied"] += 1
        stat["changed_vouchers"] += int(mask.sum())

    if stat["applied"]:
        v_col = mapping["voucher_no"]
        by_vid = dict(zip(detail["凭证号"].astype(str), detail["业务分类"]))
        src = dict(zip(detail["凭证号"].astype(str), detail["判定来源"]))
        keys = clean.df[v_col].astype(str)
        clean.df["业务分类"] = keys.map(by_vid).fillna("未分类")
        clean.df["判定来源"] = keys.map(src).fillna("")
    return stat


def _row_info(df: pd.DataFrame, mapping: Dict[str, str],
              row_nos: List[int]) -> Dict[int, dict]:
    """{行号: {凭证号, 摘要, 科目, 借方, 贷方}} —— 给待复核清单补上下文。

    只取需要的行（待复核通常几十条），87 万行的账不该为它整表转一遍。
    """
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]
    sum_col = mapping.get("summary", "")
    has_sum = bool(sum_col) and sum_col in df.columns

    out: Dict[int, dict] = {}
    for i in row_nos:
        if i < 0 or i >= len(df):
            continue
        r = df.iloc[i]
        out[int(i)] = {
            "凭证号": str(r[v_col]),
            "科目": "" if pd.isna(r[s_col]) else str(r[s_col]).strip(),
            "摘要": "" if not has_sum or pd.isna(r[sum_col]) else str(r[sum_col]),
            "借方": rows.to_number(r[d_col]),
            "贷方": rows.to_number(r[c_col]),
        }
    return out


def _cashflow_stats(money_by_vid: Dict[str, List], cf_by_row: Dict[int, dict]
                    ) -> Dict[str, Any]:
    """现金流结果概况。

    `rows_without_item` 恒 ≥ needs_review_rows：它必然包含"待复核"（本来就没项目）
    再加"桶本身不产生现金流量"（资金内部往来、折旧摊销）。**只有「未分类(异常)」
    不为 0 才需要处理**，它不是告警指标。
    """
    by_item: Dict[str, float] = {}
    by_section: Dict[str, float] = {}
    by_source: Dict[str, int] = {}
    review_rows = empty_rows = wash_rows = 0
    empty_no_cash = empty_review = empty_unclassified = 0

    for row_no, v in cf_by_row.items():
        src = v.get("source") or ""
        by_source[src] = by_source.get(src, 0) + 1
        if v.get("no_cash"):
            wash_rows += 1
            continue
        item = v.get("cf_item") or ""
        if not item:
            empty_rows += 1
            if v.get("source") == judge.SRC_NONE:
                empty_unclassified += 1
            elif v.get("needs_review"):
                empty_review += 1
            else:
                empty_no_cash += 1
        else:
            by_item[item] = by_item.get(item, 0.0) + float(v.get("amount") or 0)
            sec = judge.section_of(item) or "其他"
            by_section[sec] = by_section.get(sec, 0.0) + float(v.get("amount") or 0)
        if v.get("needs_review"):
            review_rows += 1

    return {
        "money_vouchers": len(money_by_vid),
        "money_rows": len(cf_by_row),
        "wash_rows": wash_rows,
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        "needs_review_rows": review_rows,
        "rows_without_item": empty_rows,
        "rows_without_item_detail": {
            "桶不产生现金流量(正常)": empty_no_cash,
            "待复核": empty_review,
            "未分类(异常)": empty_unclassified,
        },
        "rows_without_item_note": (
            "正常值 = 「桶不产生现金流量」（资金内部往来=自家账户互转、折旧摊销）"
            "+「待复核」（本来就没项目）。**只要「未分类(异常)」是 0 就不用管**。"),
        "amount_by_item": {k: round(v, 2) for k, v in
                           sorted(by_item.items(), key=lambda kv: -abs(kv[1]))},
        "amount_by_section": {k: round(v, 2) for k, v in by_section.items()},
        "net_cash": round(sum(by_item.values()), 2),
    }


def run_file(path: str | Path,
             out_dir: Optional[str | Path] = None,
             override: Optional[Dict[str, str]] = None,
             classifier=None,
             cf_patch: Optional[dict] = None,
             bucket_patch: Optional[dict] = None,
             progress: ProgressCb = None,
             export_training: Optional[bool] = None) -> Dict[str, Any]:
    """跑完一份序时账：上游判桶 → 编现金流 → 写回 → 导出一个 Excel。

    Args:
        classifier: 复用已加载的上游分类器（批量跑多份文件时传，省一次 1.3GB 加载）
        cf_patch:   现金流补丁 {"rows": {"<1-based 行号>": {"item": "现金流量项目"}}}
        bucket_patch: 业务桶覆盖 {"vouchers": {"<凭证号>": "<业务桶>"}}（应急把手）
        progress:   进度回调 progress(msg: str)
        export_training: 透传给上游（默认跟随环境变量，关闭）
    """
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    t_all = time.perf_counter()
    src = Path(path)
    out_dir = Path(out_dir) if out_dir else src.parent / "现金流量表编写结果"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    # ── ① 读表 ──
    log("① 读取序时账...")
    t_read = time.perf_counter()
    raw = _read(src)
    t_read = time.perf_counter() - t_read
    if raw.empty:
        raise ValueError("表格没有数据行")
    mapping = resolve_mapping(raw, override)
    log(f"   {len(raw)} 行 / {raw[mapping['voucher_no']].nunique()} 张凭证")

    # ── ② 业务桶（上游）──
    clf = classifier or get_classifier()
    clf._load_nn_if_available()
    if not getattr(clf, "_nn_available", False):
        raise RuntimeError(
            "上游模型不可用，无法判定业务桶。编现金流量表的前提是业务分类，"
            "请先确认 summary_cleaner 的模型交付物（nn/_storage/）齐备。")

    log("② 业务分类（summary_cleaner）...")
    from summary_cleaner import pipeline as clean_pipeline
    t_clean = time.perf_counter()
    clean = clean_pipeline.run(
        raw, mapping, clf,
        export_training=export_training,
        progress=lambda pct, msg: log(f"   [{pct:>3}%] {msg}"),
    )
    t_clean = time.perf_counter() - t_clean
    bucket_stat = _apply_bucket_patch(clean, mapping, bucket_patch)
    if bucket_stat["provided"]:
        log(f"   业务桶覆盖：提交 {bucket_stat['provided']} 张 / "
            f"采纳 {bucket_stat['applied']} 张 / 落到 "
            f"{bucket_stat['changed_vouchers']} 张凭证")

    # 凭证级上下文（业务桶 + 置信度），现金流与待复核都要用
    detail = clean.detail
    bucket_of = {str(v): {"业务分类": str(b), "置信度": str(c), "判定来源": str(s)}
                 for v, b, c, s in zip(detail["凭证号"], detail["业务分类"],
                                       detail["置信度"], detail["判定来源"])}

    # ── ③ 现金流判定 ──
    log("③ 编制现金流量表...")
    t_cf = time.perf_counter()
    money_by_vid, money_flags = rows.build_money_rows(raw, mapping)
    cf_by_row: Dict[int, dict] = {}
    counterparts: Dict[int, List[str]] = {}
    counterpart_counts: Dict[int, Dict[str, int]] = {}
    for vid, vrows in money_by_vid.items():
        bucket = bucket_of.get(str(vid), {}).get("业务分类", "")
        for row_no, verdict in judge.judge_voucher(vrows, bucket):
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
            counterpart_counts[row_no] = dict(verdict.counterparty_counts)
    t_cf = time.perf_counter() - t_cf

    def review_payload() -> List[dict]:
        need = [r for r, v in cf_by_row.items() if v.get("needs_review")]
        info = _row_info(raw, mapping, need)
        return judge.make_review_payload(cf_by_row, info, counterparts,
                                         counterpart_counts, bucket_of)

    review_items = review_payload()
    log(f"   程序判定 {len(cf_by_row) - len(review_items)} 条 / "
        f"待复核 {len(review_items)} 条")

    # ── ③b 现金流补丁（对"待复核"的结论）──
    patch_stat: Optional[dict] = None
    if cf_patch:
        cf_by_row, patch_stat = judge.apply_patch(cf_by_row, cf_patch)
        log(f"   应用补丁：提交 {patch_stat['provided']} 条 / "
            f"采纳 {patch_stat['applied']} 条 / 驳回 {len(patch_stat['rejected'])} 条")
        review_items = review_payload()

    cf_stats = _cashflow_stats(money_by_vid, cf_by_row)
    if patch_stat:
        cf_stats["patched_rows"] = patch_stat["applied"]
        cf_stats["patch_rejected"] = patch_stat["rejected"]
        cf_stats["patch_unknown_rows"] = patch_stat["unknown_rows"]
        cf_stats["patch_not_review"] = patch_stat["not_review"]
    log(f"   现金流相关凭证 {cf_stats['money_vouchers']} 张 / "
        f"货币资金分录 {cf_stats['money_rows']} 条 / "
        f"仍需复核 {cf_stats['needs_review_rows']} 条")

    # ── ④ 写回 ──
    log("④ 写回序时账...")
    classified = rows.write_cashflow(clean.df, mapping, money_flags, cf_by_row)

    # ── ⑤ 导出 ──
    log("⑤ 导出结果文件...")
    t_exp = time.perf_counter()
    stmt = export.statement(cf_by_row)
    exported = export.export(raw, classified, mapping, money_flags,
                             cf_by_row, out_dir, stem, statement_frame=stmt)
    t_exp = time.perf_counter() - t_exp

    # ── 统计 ──
    engine = clean.stats.get("engine") or {}
    stats = {
        "file": str(src.resolve()),
        "rows": int(len(raw)),
        "vouchers": int(raw[mapping["voucher_no"]].nunique()),
        "column_mapping": {k: str(v) for k, v in mapping.items()},
        "bucket_counts": clean.stats.get("bucket_counts") or {},
        "confidence_dist": clean.stats.get("confidence_dist") or {},
        "hard_rule_counts": clean.stats.get("hard_rule_counts") or {},
        "model": engine.get("model") or {},
        "upstream": {
            "model_calls": engine.get("model_calls"),
            "modeled_vouchers": engine.get("modeled_vouchers"),
            "dedup": clean.stats.get("dedup") or {},
            "unclassified": engine.get("unclassified"),
            "unknown_subjects": len(clean.stats.get("unknown_subjects") or {}),
        },
        "bucket_patch": bucket_stat,
        "cashflow": cf_stats,
        "cashflow_statement": stmt.to_dict(orient="records"),
        "cashflow_review": review_items,
        "cashflow_review_by_voucher": judge.summarize_review(review_items),
        "output_files": exported["files"],
        "sheet_rows": exported["sheet_rows"],
        "elapsed": {
            "read_s": round(t_read, 2),
            "clean_s": round(t_clean, 2),
            "cashflow_s": round(t_cf, 2),
            "export_s": round(t_exp, 2),
            "total_s": round(time.perf_counter() - t_all, 2),
        },
    }
    log("完成")
    return stats
