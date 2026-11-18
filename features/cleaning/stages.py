# -*- coding: utf-8 -*-
"""序时账清洗 —— **本功能独有**的阶段实现。

    export          程序   写回 + 导出（分类结果 / 凭证明细 / 凭证汇总）
    link            程序   接对方科目分析（可选的第二输入，只搬对方科目行表）

读表、业务分类、复核桶这三段不是清洗独有的——现金流和分录测试都要用同一份，且结果
必须一致，所以它们在 `core/ledger/`。清洗在这里只加"把对方科目贴进结果表"和
"写回成 Excel"两件事。

共享段的扩展口：现金流那类"还要看钱的方向"的功能通过 `review_items(extra=...)`
往复核面里追加自己的判据（见 `features/cashflow/stages.py` 的
`_direction_conflicts`），共用代码不用知道现金流的业务。
"""

from __future__ import annotations

from typing import Any, Dict, List

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


def _attach_contra(ctx: Ctx, result: pd.DataFrame,
                   mapping: Dict[str, str]) -> tuple:
    """把对方科目贴到行级结果表上（没有上游时原样返回）。

    贴进来而不是"另存一张表"：用户抱怨的就是这个——分类在一份产物里、对方科目在
    另一份产物里，要看同一行得自己拿 Excel 去 vlookup。现在一张表里 "业务分类"
    和 "对方科目" 挨着，翻到哪张凭证都看得见它对给了谁。
    """
    p = ctx.path("link", "contra_map.parquet")
    if not p.exists():
        return result, None

    table = pd.read_parquet(p)
    v_col, s_col, c_col = mapping["voucher_no"], mapping["subject"], mapping.get("credit")
    credit = pd.to_numeric(result[c_col], errors="coerce").fillna(0.0) if c_col else 0.0
    keys = pd.DataFrame({
        "__v": result[v_col].astype(str).str.strip(),
        "__s": result[s_col].astype(str).str.strip(),
        "__d": ["贷" if abs(value) > 0 else "借" for value in credit],
    })
    glued = result.join(keys).merge(
        table, left_on=["__v", "__s", "__d"],
        right_on=["凭证号", "科目", "方向"], how="left", sort=False)
    glued = glued.drop(columns=["__v", "__s", "__d", "凭证号", "科目", "方向"],
                       errors="ignore")

    # 放在「业务分类」左边：读一行时，"它是什么业务、钱对给谁"在同一屏
    added = ["对方科目-一级", "对方科目-二级"]
    rest = [c for c in glued.columns if c not in added]
    at = rest.index("业务分类") if "业务分类" in rest else len(rest)
    glued = glued[rest[:at] + added + rest[at:]]

    linked = int(glued["对方科目-一级"].notna().sum())
    return glued, {"rows": int(len(glued)), "linked": linked,
                   "unlinked": int(len(glued) - linked), "groups": int(len(table))}


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

    result, contra_info = _attach_contra(ctx, result, mapping)
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
            # 接了对方科目分析才有：多少行贴上了对方科目（没贴上的多半是空科目行）
            "contra": contra_info or {},
            "export": {"format": written.fmt, "seconds": written.seconds,
                       "hint": written.hint},
            "files": written.paths,
        })


# ============================================================================
# 接对方科目分析（清洗独有：它是管线的第二棒，第一个输入是"对方科目"这份产物）
# ============================================================================

CONTTRA_ROWS = "contra_rows.parquet"


def contra_signature(ctx: Ctx) -> Any:
    """上游「对方科目分析」的内容指纹：导出哈希 + 模式定案哈希。

    只记 job_id 的话，对方科目任务定完案重跑一遍，清洗这边不会重算，
    结果表里躺的还是上一轮的对方科目。
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


def _join_unique(values: List[Any]) -> str:
    """去重保序后用「、」连起来。

    同一行会在对方科目表里出现多次：多借多贷的凭证被拆成一对一对，一个科目
    自然对应好几个对方科目。清洗的结果表是**行级**的，装不下一对一的关系，
    所以这里把"这一行的钱可能对给谁"拼成一个列表；要精确到金额的配对，去翻
    对方科目分析自己的产物（它就在同一个任务里）。
    """
    seen: List[str] = []
    for value in values:
        text = str(value if value is not None else "").strip()
        if text and text != "nan" and text not in seen:
            seen.append(text)
    return "、".join(seen)


def contra_map(rows: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """对方科目行表 → 按「凭证号 + 一级科目 + 方向」压成一行。

    为什么不用科目编码：对方科目表在拆多借多贷时重建了行，编码会空（引擎只保
    留了科目名），按编码对会有 11% 的行挂不上；按「凭证号 + 一级科目 + 方向」
    对是 100%。
    """
    v_col, s_col = mapping.get("voucher_no"), mapping.get("subject")
    c_col = mapping.get("credit")
    if not (v_col and s_col and c_col):
        raise ValueError("字段契约里缺凭证号/科目/贷方金额，接不了对方科目")

    credit = pd.to_numeric(rows[c_col], errors="coerce").fillna(0.0)
    work = pd.DataFrame({
        "凭证号": rows[v_col].astype(str).str.strip(),
        "科目": rows[s_col].astype(str).str.strip(),
        "方向": ["贷" if abs(c) > 0 else "借" for c in credit],
        "对方科目-一级": rows["对方科目-一级"] if "对方科目-一级" in rows.columns else "",
        "对方科目-二级": rows["对方科目-二级"] if "对方科目-二级" in rows.columns else "",
    })
    return (work.groupby(["凭证号", "科目", "方向"], sort=False)
            [["对方科目-一级", "对方科目-二级"]]
            .agg(_join_unique).reset_index())


def run_link_contra(ctx: Ctx) -> StageResult:
    """接对方科目分析：把它的行表和结论搬进来，账本身自己读（read 已跑过）。

    搬而不是引用：本任务要能独立重跑、独立打包，上游被删也不影响。
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
    table = contra_map(rows, ctx.journal.schema.legacy())
    ctx.save("contra_map.parquet", table)

    ctx.log(f"  接对方科目：{len(rows):,} 行 → {len(table):,} 组（凭证·科目·方向）")
    return StageResult(facts={
        "upstream": jid,
        "upstream_feature": feature,
        "rows": int(len(rows)),
        "groups": int(len(table)),
    })
