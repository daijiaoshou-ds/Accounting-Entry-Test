# -*- coding: utf-8 -*-
"""现金流量表编写 —— 接入 jobflow 的阶段实现。

    read          程序   读表 + 认列名（journal_io）
    clean         程序   业务桶（summary_cleaner）
    review_bucket AI     复核中/低置信的业务分类  ← 停
    judge         程序   桶 + 方向 → 现金流量项目（应用上面的改桶结论）
    review_cf     AI     复核判不下来的现金流条目   ← 停
    export        程序   应用补丁 → 写 Excel

两个 AI 阶段的语义都是「**返回还没被决定的条目**」：交过一次的 key 不再出现在
待办里，全交完这个阶段就自动结束。这样 `submit` 之后不用额外记状态。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd

import journal_io
from jobflow.stage import Ctx, ReviewRequest, StageResult

from cfw import export as cf_export
from cfw import judge, rows
from cfw.pipeline import cashflow_stats, get_classifier

from summary_cleaner import pipeline as clean_pipeline
from summary_cleaner.v2.classifier import SENTINEL_BUCKETS
from summary_cleaner.v2.config import BUCKET_REGISTRY, MERGED_BUCKETS

REQUIRES = ("voucher_id", "subject_l1", "debit", "credit")

# 可指派的业务桶（去掉哨兵与已折叠的子桶）
ASSIGNABLE_BUCKETS = [b for b in BUCKET_REGISTRY
                      if b not in SENTINEL_BUCKETS and b not in MERGED_BUCKETS]

REVIEW_HELP_BUCKET = (
    "这些凭证分两类：① 置信度「中/低」；② **桶和钱的流向对不上**（如费用桶却收到钱，"
    "多半是退款/往来）——第二类马上会让现金流判不出来，优先处理。\n"
    "逐条确认它属于哪个业务桶：\n"
    "  · 认同模型判的 → 不用写进 decisions（它会保持原判）\n"
    "  · 判错了 → 写 {\"key\": 凭证号, \"value\": 正确的桶, \"note\": 依据}\n"
    "整批看完了还有拿不准的 → `journal submit <job> --empty --stage review_bucket` "
    "表示这批不再改，剩下的会在结果里标「需复核」。")

REVIEW_HELP_CF = (
    "这些现金流条目程序判不下来（方向与桶不符 / 歧义桶没有裁判）。逐条给结论：\n"
    "  · 先看「业务分类」对不对——根因多半是桶判错，那就回去交 review_bucket\n"
    "  · 业务确实如此（退款/赎回等）→ 直接指定 {\"key\": 行号, \"value\": 项目名}\n"
    "  · 拿不准就留着，它会标「需复核」并在交付物里留空。")


# ============================================================================
# ① read
# ============================================================================

def run_read(ctx: Ctx) -> StageResult:
    override = (ctx.options.get("mapping") or {}) if ctx.options else {}
    j = journal_io.read(ctx.source, requires=())     # 先读，再合并手工映射

    if override:
        for key, col in override.items():
            if col not in j.df.columns:
                raise ValueError(f"mapping 里 {key}={col!r}：表里没有这一列")
            j.schema.mapping[key] = col
    j.require(REQUIRES)

    ctx.job.save_journal(j)
    vids = j.keys("voucher_id")
    return StageResult(facts={
        "rows": int(len(j)),
        "vouchers": int(vids[vids != ""].nunique()),
        "engine": j.schema.engine,
        "mapping": j.schema.mapping,
        "unmapped": list(j.schema.unmapped),
    })


# ============================================================================
# ② clean
# ============================================================================

def run_clean(ctx: Ctx) -> StageResult:
    j = ctx.journal
    clf = get_classifier()
    clf._load_nn_if_available()
    if not getattr(clf, "_nn_available", False):
        raise RuntimeError(
            "上游模型不可用：编现金流量表的前提是业务分类，"
            "请先确认 summary_cleaner 的模型交付物（nn/_storage/）齐备")

    res = clean_pipeline.run(j.df, j.schema.legacy(), clf,
                             export_training=False,
                             progress=lambda pct, msg: ctx.log(f"   [{pct:>3}%] {msg}"))
    ctx.save("result.parquet", res.df)
    ctx.save("detail.parquet", res.detail)

    stats = res.stats or {}
    engine = stats.get("engine") or {}
    return StageResult(
        artifacts={"result": str(ctx.path("clean", "result.parquet")),
                   "detail": str(ctx.path("clean", "detail.parquet"))},
        facts={
            "rows": int(len(res.df)),
            "vouchers": int(len(res.detail)),
            "bucket_counts": stats.get("bucket_counts") or {},
            "confidence_dist": stats.get("confidence_dist") or {},
            "hard_rule_counts": stats.get("hard_rule_counts") or {},
            "model_calls": engine.get("model_calls"),
            "modeled_vouchers": engine.get("modeled_vouchers"),
            "unclassified": engine.get("unclassified"),
            "unknown_subjects": len(stats.get("unknown_subjects") or {}),
            "model": engine.get("model") or {},
        })


# ============================================================================
# ③ review_bucket（AI）
# ============================================================================

def _direction_conflicts(ctx: Ctx, detail: pd.DataFrame) -> Dict[str, str]:
    """**将要变成待复核**的凭证：单向桶遇到了反方向的钱。

    为什么要在 judge 之前算出来：这些凭证的桶多半是错的（费用桶却收到钱＝退款/
    往来），而它们往往**置信度很高**——只按"中/低置信"筛复核面，AI 根本没机会
    改它们，现金流层就只能干瞪眼。所以复核面 = 模型不确定的 ∪ 桶和方向对不上的。

    返回 {凭证号: 原因}。判断口径与 `judge.judge_row` 保持一致（同一份映射表）。
    """
    j = ctx.journal
    mapping = j.schema.legacy()
    bucket_of = {str(v): str(b) for v, b in
                 zip(detail["凭证号"], detail["业务分类"])}
    money_by_vid, _ = rows.build_money_rows(j.df, mapping)

    out: Dict[str, str] = {}
    for vid, vrows in money_by_vid.items():
        bucket = bucket_of.get(str(vid), "")
        raw = judge.bucket_items(bucket)
        # 桶不认识 / 桶本来不产生现金流 / 中性项目（汇兑）都不算"方向冲突"
        if not raw or judge.neutral_items(raw):
            continue
        for r in vrows:
            if not judge.is_money_fund(r.subject):
                continue          # 只有货币资金行才有"收/付"可言（别拿对方科目判方向）
            d = judge.direction_of(r.debit, r.credit)
            if d not in (judge.CF_IN, judge.CF_OUT):
                continue
            if not judge.filter_by_direction(raw, d):
                word = "收现" if d == judge.CF_IN else "付现"
                out[str(vid)] = (f"方向与桶不符：桶「{bucket}」只对应"
                                 f"{'、'.join(raw)}，但凭证是{word}")
                break
    return out


def _fmt_top3(raw: Any, n: int = 3) -> str:
    """`[["费用",0.62],["采购",0.37]]` → `费用 0.62 · 采购 0.37`。

    detail 里存的是 JSON 字符串，直接丢给复核表会显示成一坨方括号——
    给 AI 看的东西不该还要它自己解析。
    """
    if not raw:
        return ""
    try:
        pairs = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return str(raw)[:120]
    out = []
    for item in list(pairs)[:n]:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            name, prob = item
            try:
                out.append(f"{name} {float(prob):.2f}")
            except (TypeError, ValueError):
                out.append(str(name))
        else:
            out.append(str(item))
    return " · ".join(out)


def review_bucket(ctx: Ctx) -> ReviewRequest:
    detail = ctx.load("detail.parquet", "clean")
    decided = ctx.decisions("review_bucket")

    conflict = _direction_conflicts(ctx, detail)
    vids = detail["凭证号"].astype(str)
    unsure = detail["置信度"].isin(["中", "低"])
    hit = unsure | vids.isin(conflict.keys())

    view = detail[hit].copy()
    view = view[~view["凭证号"].astype(str).isin(decided)]
    if view.empty:
        return ReviewRequest()

    view["_why"] = ["；".join(filter(None, [
        conflict.get(str(v)),
        f"置信度{conf}（{prob}）" if conf in ("中", "低") else "",
    ])) for v, conf, prob in zip(view["凭证号"], view["置信度"],
                                 view["校准概率"].fillna("-"))]
    view["_hard"] = [0 if str(v) in conflict else 1 for v in view["凭证号"]]
    view["_p"] = pd.to_numeric(view["校准概率"], errors="coerce").fillna(-1)
    view = view.sort_values(["_hard", "_p"])

    limit = ctx.max_review()
    total = len(view)
    view = view.head(limit)

    items = [{
        "key": str(r["凭证号"]),
        "凭证号": str(r["凭证号"]),
        "为什么要看": str(r["_why"]),
        "摘要": str(r.get("摘要") or "")[:120],
        "科目": str(r.get("科目") or ""),
        "金额": float(r.get("金额") or 0),
        "模型桶": str(r.get("业务分类") or ""),
        "置信度": str(r.get("置信度") or ""),
        "校准概率": None if pd.isna(r.get("校准概率")) else round(float(r["校准概率"]), 4),
        "模型Top3": _fmt_top3(r.get("模型Top3")),
        "组内张数": int(r.get("组内张数") or 1),
    } for _, r in view.iterrows()]

    return ReviewRequest(items=items, value_enum=ASSIGNABLE_BUCKETS,
                         help=REVIEW_HELP_BUCKET, key_name="voucher_id",
                         truncated=total > len(items))


# ============================================================================
# ④ judge
# ============================================================================

def run_judge(ctx: Ctx) -> StageResult:
    j = ctx.journal
    detail = ctx.load("detail.parquet", "clean")
    bucket_dec = ctx.decisions("review_bucket")

    bucket_of: Dict[str, Dict[str, Any]] = {}
    for vid, bucket, conf, src in zip(detail["凭证号"], detail["业务分类"],
                                      detail["置信度"], detail["判定来源"]):
        vid = str(vid)
        final = bucket_dec.get(vid)
        bucket_of[vid] = {
            "业务分类": str(final or bucket),
            "置信度": str(conf),
            "判定来源": "复核改判" if final else str(src),
        }

    mapping = j.schema.legacy()
    money_by_vid, _flags = rows.build_money_rows(j.df, mapping)
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

    # 待复核清单在这里一次算好（review_cf 直接读，不重算对方科目）
    need = [r for r, v in cf_by_row.items() if v.get("needs_review")]
    info = _row_info(j.df, mapping, need)
    review_items = judge.make_review_payload(cf_by_row, info, counterparts,
                                             counterpart_counts, bucket_of)

    ctx.save_json("cf.json", {str(k): v for k, v in cf_by_row.items()})
    ctx.save_json("review_items.json", review_items)
    ctx.save_json("bucket_of.json", bucket_of)

    by_source: Dict[str, int] = {}
    for v in cf_by_row.values():
        s = v.get("source") or ""
        by_source[s] = by_source.get(s, 0) + 1

    return StageResult(facts={
        "money_vouchers": len(money_by_vid),
        "money_rows": len(cf_by_row),
        "needs_review_rows": len(review_items),
        "bucket_overrides": len(bucket_dec),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
    })


def _row_info(df: pd.DataFrame, mapping: Dict[str, str],
              row_nos: List[int]) -> Dict[int, dict]:
    v_col, s_col = mapping["voucher_no"], mapping["subject"]
    d_col, c_col = mapping["debit"], mapping["credit"]
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


# ============================================================================
# ⑤ review_cf（AI）
# ============================================================================

def review_cf(ctx: Ctx) -> ReviewRequest:
    raw = ctx.load_json("review_items.json", "judge")
    decided = ctx.decisions("review_cf")
    # judge 的出参用「行号」（1-based，等于 Excel 行），decision 的幂等键就用它
    items = [{"key": str(it.get("行号")), **it} for it in raw
             if str(it.get("行号")) not in decided]
    if not items:
        return ReviewRequest()

    limit = ctx.max_review()
    total = len(items)
    items = items[:limit]
    return ReviewRequest(items=items, value_enum=judge.cf_items(),
                         help=REVIEW_HELP_CF, key_name="row_no",
                         truncated=total > len(items))


# ============================================================================
# ⑥ export
# ============================================================================

def run_export(ctx: Ctx) -> StageResult:
    j = ctx.journal
    mapping = j.schema.legacy()

    cf_by_row: Dict[int, dict] = {
        int(k): v for k, v in ctx.load_json("cf.json", "judge").items()}
    patch = ctx.decisions("review_cf")
    patch_stat = None
    if patch:
        doc = {"rows": {k: {"item": v, "note": "review_cf"}
                        for k, v in patch.items()}}
        cf_by_row, patch_stat = judge.apply_patch(cf_by_row, doc)

    detail = ctx.load("detail.parquet", "clean")
    result = ctx.load("result.parquet", "clean")
    bucket_of = ctx.load_json("bucket_of.json", "judge")

    # 改过桶的凭证：业务分类/判定来源要以 judge 用的那份为准（否则交付物与
    # 现金流结论对不上——这是最容易被忽略的一处自洽性）
    v_col = mapping["voucher_no"]
    by_vid = {v: d["业务分类"] for v, d in bucket_of.items()}
    src_vid = {v: d["判定来源"] for v, d in bucket_of.items()}
    keys = result[v_col].astype(str)
    result = result.copy()
    result["业务分类"] = keys.map(by_vid).fillna(result["业务分类"])
    if "判定来源" in result.columns:
        result["判定来源"] = keys.map(src_vid).fillna(result["判定来源"])
    else:
        result["判定来源"] = keys.map(src_vid).fillna("")

    money_by_vid, money_flags = rows.build_money_rows(j.df, mapping)
    classified = rows.write_cashflow(result, mapping, money_flags, cf_by_row)

    stats = cashflow_stats(money_by_vid, cf_by_row)
    statement = cf_export.statement(cf_by_row)

    out_dir = ctx.job.dir / "out" / "export"
    exported = cf_export.export(j.df, classified, mapping, money_flags,
                                cf_by_row, out_dir,
                                ctx.job.state["input"]["filename"].rsplit(".", 1)[0],
                                statement_frame=statement)

    first = (exported["files"].get("业务分类结果")
             or next(iter(exported["files"].values())))
    facts = {
        "cashflow": stats,
        "cashflow_statement": statement.to_dict(orient="records"),
        "export": {"format": exported["fmt"], "seconds": exported["seconds"],
                   "hint": exported["hint"]},
        "files": exported["files"],
        "patch": {"applied": (patch_stat or {}).get("applied", 0),
                  "rejected": (patch_stat or {}).get("rejected", [])},
    }
    return StageResult(
        artifacts={"result": first,
                   **{f"sheet:{k}": v for k, v in exported["files"].items()}},
        facts=facts)
