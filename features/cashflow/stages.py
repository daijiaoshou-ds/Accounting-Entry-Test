# -*- coding: utf-8 -*-
"""现金流量表编写 —— 接入 jobflow 的阶段实现。

    link           程序   从上游清洗任务搬产物与改桶结论（可选，options.upstream）
    read           程序   读表 + 认列名 ┐
    clean          程序   业务桶（模型）  ├ 复用 features.cleaning.stages
    review_bucket  AI     复核业务分类   ┘
    judge          程序   桶 + 方向 → 现金流量项目（应用上面的改桶结论）
    review_cf      AI     复核判不下来的现金流条目
    export         程序   应用补丁 → 写 Excel

前四段**不是本功能独有的**：清洗、将来的对方科目/会计分录测试都用同一份实现
（`features/cleaning/stages.py`）。这里只加了两样东西：① 复核面多一类"桶和钱的
方向对不上"的凭证；② 编表与导出的业务。

两种起跑方式：
- 直接上传（默认）：read → clean → 复核桶 → 编表 …
- 从已有的清洗任务起跑（`options.upstream=<job_id>`）：link 搬产物，
  read/clean/复核桶整体跳过——**同一份账不重复分类、不重复复核**。
  上游后来改了桶，`link` 的哈希会变 → `journal resume` 一次就把现金流重算了。

两个 AI 阶段的语义都是「**返回还没被决定的条目**」：交过一次的 key 不再出现在
待办里，全交完这个阶段就自动结束。这样 `submit` 之后不用额外记状态。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from jobflow.stage import Ctx, ReviewRequest, StageResult

from cfw import export as cf_export
from cfw import judge, rows
from cfw.pipeline import cashflow_stats

from ..cleaning import stages as clean_s

REQUIRES = clean_s.REQUIRES

REVIEW_HELP_BUCKET = (
    "这些凭证分两类：① 置信度「中/低」；② **桶和钱的流向对不上**（如费用桶却收到钱，"
    "多半是退款/往来）——第二类马上会让现金流判不出来，优先处理。\n"
    "  · 认同模型判的 → 不用写进 decisions（它会保持原判）\n"
    "  · 判错了 → 写 {\"key\": 凭证号, \"value\": 正确的桶, \"note\": 依据}\n"
    "整批看完了还有拿不准的 → `journal submit <job> --empty --stage review_bucket`。")

REVIEW_HELP_CF = (
    "这些现金流条目程序判不下来（方向与桶不符 / 歧义桶没有裁判）。逐条给结论：\n"
    "  · 先看「业务分类」对不对——根因多半是桶判错，那就回去交 review_bucket\n"
    "  · 业务确实如此（退款/赎回等）→ 直接指定 {\"key\": 行号, \"value\": 项目名}\n"
    "  · 拿不准就留着，它会标「需复核」并在交付物里留空。")


# ============================================================================
# ① 复核业务分类（共享实现 + 多判一类：方向冲突）
# ============================================================================

def _direction_conflicts(ctx: Ctx, detail: pd.DataFrame) -> Dict[str, str]:
    """**将要变成待复核**的凭证：单向桶遇到了反方向的钱。

    为什么要在 judge 之前算出来：这些凭证的桶多半是错的（费用桶却收到钱＝退款/
    往来），而它们往往**置信度很高**——只按"中/低置信"筛复核面，AI 根本没机会
    改它们，现金流层就只能干瞪眼。所以复核面 = 模型不确定的 ∪ 桶和方向对不上的。

    返回 {凭证号: 原因}。判断口径与 `judge.judge_voucher` 共用同一份映射表。
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


def review_bucket(ctx: Ctx) -> ReviewRequest:
    """接上游时只看"方向冲突"：中/低置信那批上游已经给过结论了。

    注意 up.waive 不算改判——那批凭证的业务分类仍按模型原判，只是在现金流层面
    会走"待复核"。这里补的是上游**不可能**发现的一类（要看钱的方向）。
    """
    return clean_s.review_items(ctx, extra=_direction_conflicts,
                                only_extra=clean_s.has_upstream(ctx))


# ============================================================================
# ② judge
# ============================================================================

def run_judge(ctx: Ctx) -> StageResult:
    j = ctx.journal
    detail = ctx.load("detail.parquet", "clean")
    # 复核结论按"组代表"交（一条待办 = 一组同判的凭证），先铺回每张凭证
    bucket_dec = clean_s.expand_group_decisions(detail, ctx.decisions("review_bucket"))

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
# ③ review_cf（AI）
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
# ④ export
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
