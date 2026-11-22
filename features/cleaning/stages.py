# -*- coding: utf-8 -*-
"""序时账清洗 —— **本功能独有**的阶段实现。

    export          程序   写回 + 导出（分类结果 / 凭证明细 / 凭证汇总）
    link            程序   接对方科目分析（只为把行表传给下游分录测试）

读表、业务分类、复核桶这三段不是清洗独有的——现金流和分录测试都要用同一份，且结果
必须一致，所以它们在 `core/ledger/`。清洗这里只加"写回成 Excel"这一件事。

**交付物里不再贴「对方科目」两列**：对方科目是一对多的（应收账款的贷方可能对
主营业务收入和应交税费两笔），清洗的行级表装不下一对一，压成一行再拼接只是
"看起来像"对方科目分析。真正的配对在对方科目分析自己的产物里；这张行表由
`link` 搬进来只为一件事——下游分录测试的「科目对」那一层要用。
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

import journal_io
from jobflow import Job
from jobflow.stage import Ctx, StageResult

from core.ledger import expand_group_decisions, same_ledger, upstream_id

# 列怎么排、哪些默认折叠由接口层决定（`api/flow_api.py`）——阶段只管内容。

REVIEW_COLUMNS = ["凭证号", "摘要", "科目", "金额", "业务分类", "置信度",
                  "校准概率", "模型Top3", "判定来源", "后处理", "未知科目"]


# ============================================================================
# export（清洗自己的产物）
# ============================================================================

def build_vouchers(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """凭证级汇总（一凭证一行，按校准概率升序：最不确定的在前）。"""
    v_col = mapping["voucher_no"]
    s_col = mapping.get("subject", "")
    sum_col = mapping.get("summary", "")
    d_col = mapping.get("debit", "")

    keep = [v_col] + [c for c in ("业务分类", "置信度", "校准概率", "模型Top3",
                                  "判定来源", "后处理") if c in df.columns]
    head = df[keep].drop_duplicates(subset=[v_col], keep="first").copy()

    if sum_col and sum_col in df.columns:
        first_sum = df[[v_col, sum_col]].drop_duplicates(subset=[v_col], keep="first")
        head = head.merge(first_sum, on=v_col, how="left")
    if s_col and s_col in df.columns:
        subj = (df[[v_col, s_col]].dropna(subset=[s_col])
                .astype({s_col: str}).groupby(v_col)[s_col]
                .agg(lambda s: "、".join(pd.unique(s))))
        head = head.merge(subj.rename("科目"), on=v_col, how="left")
    if d_col and d_col in df.columns:
        amt = pd.to_numeric(df[d_col].astype(str).str.replace(",", "").str.strip(),
                            errors="coerce").abs().fillna(0.0)
        head = head.merge(amt.groupby(df[v_col]).sum().rename("金额"),
                          left_on=v_col, right_index=True, how="left")

    head = head.rename(columns={v_col: "凭证号"})
    if sum_col and sum_col in head.columns:
        head = head.rename(columns={sum_col: "摘要"})
        head["摘要"] = head["摘要"].astype(str).str.slice(0, 100)

    p = pd.to_numeric(head.get("校准概率"), errors="coerce")
    head["_p"] = p.fillna(-1.0)
    head = head.sort_values("_p").drop(columns="_p").reset_index(drop=True)

    for col in REVIEW_COLUMNS:
        if col not in head.columns:
            head[col] = ""
    return head[REVIEW_COLUMNS]


def run_export(ctx: Ctx) -> StageResult:
    j = ctx.journal
    mapping = j.schema.legacy()
    result = ctx.load("result.parquet", "clean")
    detail = ctx.load("detail.parquet", "clean")

    # 改过桶的凭证：业务分类/判定来源换成复核后的（否则交付物与结论对不上）。
    # 复核的结论是按"组代表"给的，这里先铺回组内每张凭证。
    bucket_dec = ctx.decisions("review_bucket")
    changed = 0
    if bucket_dec:
        v_col = mapping["voucher_no"]
        keys = result[v_col].astype(str)
        per_voucher = expand_group_decisions(detail, bucket_dec)
        result = result.copy()
        result["业务分类"] = keys.map(per_voucher).fillna(result["业务分类"])
        hit = keys.isin(per_voucher.keys())
        changed = int(result.loc[hit, v_col].astype(str).nunique())
        if "判定来源" in result.columns:
            result.loc[hit, "判定来源"] = "复核改判"
        else:
            result["判定来源"] = ""

    vouchers = build_vouchers(result, mapping)
    # 组ID / 组代表 是复核用的内部列，不进交付物
    exported = detail.drop(columns=[c for c in ("组ID", "组代表") if c in detail.columns])
    out_dir = ctx.job.dir / "out" / "export"
    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    written = journal_io.write_sheets(
        {"业务分类结果": result, "凭证明细": exported, "凭证汇总": vouchers},
        out_dir / f"{stem}_业务分类.xlsx", target="download")

    # 统计从写回后的表里算：上游模式（clean 被跳过）没有 clean 的 facts 可用
    v_col = mapping["voucher_no"]
    per_voucher = result.drop_duplicates(subset=[v_col], keep="first")
    bucket_counts = per_voucher["业务分类"].value_counts().to_dict()
    confidence = per_voucher["置信度"].value_counts().to_dict()

    return StageResult(
        artifacts={"result": written.paths.get("业务分类结果")
                   or next(iter(written.paths.values())),
                   **{f"sheet:{k}": v for k, v in written.paths.items()}},
        facts={
            "rows": int(len(result)),
            "vouchers": int(len(vouchers)),
            "bucket_counts": {str(k): int(v) for k, v in bucket_counts.items()},
            "confidence_dist": {str(k): int(v) for k, v in confidence.items()},
            "bucket_overrides": changed,          # 被改到的凭证张数（不是结论条数）
            "bucket_override_groups": len(bucket_dec),
            "export": {"format": written.fmt, "seconds": written.seconds,
                       "hint": written.hint},
            "files": written.paths,
        })


# ============================================================================
# 接对方科目分析（不是清洗自己的产物：这张行表是带给下游分录测试的）
# ============================================================================

CONTTRA_ROWS = "contra_rows.parquet"


def contra_signature(ctx: Ctx) -> Any:
    """上游「对方科目分析」的内容指纹：导出哈希 + 模式定案哈希。

    只记 job_id 的话，对方科目任务定完案重跑一遍，清洗这边不会重算，
    下游分录测试拿到的还是上一轮的对方科目。
    """
    jid = upstream_id(ctx)
    if not jid:
        return ""
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        return f"{jid}:missing"
    return [jid, up.stage_state("export").get("hash") or "",
            up.decision_hash("review_pattern")]


def run_link_contra(ctx: Ctx) -> StageResult:
    """把对方科目分析的行表搬进 `out/link/`，原样不动地留给下游。

    搬而不是引用：本任务要能独立重跑、独立打包，上游被删也不影响。
    这张表不进清洗的交付物（行级表装不下一对多的配对，硬压只会像却不对），
    `core/ledger/upstream.run_link` 会把它和分类结果一起搬给分录测试，
    「科目对」那一层要靠它才知道每一行的钱对给了谁。
    """
    jid = upstream_id(ctx)
    if not jid:
        raise ValueError("link 阶段要求 options.upstream 指向一个对方科目分析任务")
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        raise ValueError(f"上游任务不存在：{jid}") from None
    feature = str(up.state.get("feature") or "")
    if feature != "contra":
        raise ValueError(f"清洗只接「对方科目分析」的产物，{jid} 是「{feature}」")
    if up.stage_state("export").get("status") not in ("done", "skipped"):
        raise ValueError(f"上游 {jid} 的对方科目分析还没跑完（当前 {up.status}）")
    same_ledger(ctx, up, jid)

    src = up.dir / "out" / "export" / CONTTRA_ROWS
    if not src.is_file():
        raise ValueError(f"上游任务缺产物：{src}（对方科目分析没有导出这张表）")

    dst = ctx.path("link", CONTTRA_ROWS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())

    rows = pd.read_parquet(src)
    ctx.log(f"  接对方科目：{len(rows):,} 行（留给下游的「科目对」）")
    return StageResult(facts={
        "upstream": jid,
        "upstream_feature": feature,
        "rows": int(len(rows)),
    })
