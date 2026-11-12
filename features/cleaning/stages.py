# -*- coding: utf-8 -*-
"""序时账清洗 —— 阶段实现，同时是**所有下游功能共用的第一段**。

    read            程序   读表 + 认列名（journal_io）
    clean           程序   业务桶（summary_cleaner，纯模型判定）
    review_bucket   AI     复核业务分类  ← 停
    export          程序   写回 + 导出（分类结果 / 凭证明细 / 凭证汇总）

为什么 read / clean / review_bucket 放在"清洗"下而不是各功能各抄一份：
现金流、对方科目、会计分录测试**都需要这一步，且结果必须一致**。以前
`summary_cleaner_v3` 自带一套清洗，同一份账在两边会得出不同业务桶，而且
是静默的。现在只有这一份实现，下游 `from ..cleaning.stages import run_read`。

现金流那类"还要看钱的方向"的功能，通过 `review_items(extra=...)` 往复核面里
追加自己的判据（见 `features/cashflow/stages.py` 的 `_direction_conflicts`），
共享代码不用知道现金流的业务。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

import journal_io
from jobflow import Job
from jobflow.stage import Ctx, ReviewRequest, StageResult

from summary_cleaner import pipeline as clean_pipeline
from summary_cleaner.v2.classifier import SENTINEL_BUCKETS, get_classifier
from summary_cleaner.v2.config import BUCKET_REGISTRY, MERGED_BUCKETS

REQUIRES = ("voucher_id", "subject_l1", "debit", "credit")

# 可指派的业务桶（去掉哨兵与已折叠的子桶）
ASSIGNABLE_BUCKETS = [b for b in BUCKET_REGISTRY
                      if b not in SENTINEL_BUCKETS and b not in MERGED_BUCKETS]

REVIEW_HELP_BUCKET = (
    "这些凭证的业务分类置信度是「中/低」。逐条确认它属于哪个业务桶：\n"
    "  · 认同模型判的 → 不用写进 decisions（它会保持原判）\n"
    "  · 判错了 → 写 {\"key\": 凭证号, \"value\": 正确的桶, \"note\": 依据}\n"
    "整批看完了还有拿不准的 → `journal submit <job> --empty --stage review_bucket` "
    "表示这批不再改，剩下的会在结果里标「需复核」。")

# 列怎么排、哪些默认折叠由接口层决定（`api/flow_api.py`）——阶段只管内容。

def _amt(raw: Any) -> float:
    """金额字符串 → float。真账里有千分位、全角逗号、空串、NaN。"""
    try:
        v = float(str(raw).replace(",", "").replace("，", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v != v else v          # NaN → 0（否则 bool(nan) 是真，会打进分录）


def voucher_lines(ctx: Ctx, vids: List[str],
                  max_entries: int = 14) -> Dict[str, List[str]]:
    """取这些凭证的**完整分录**（按"科目+方向"聚合），给复核用。

    为什么必须给：判业务桶要看整张凭证在干什么。只给「一级科目 + 凭证金额」时，
    两条「银行存款 / 32,000」在纸上完全一样，而对方科目是「管理费用」还是
    「应付账款—设备款」决定了桶完全不同。

    为什么聚合：一张计提工资的凭证能有 40 行（管理费用×6、制造费用×5…），
    逐行列出来只是噪声。按"科目+方向"合并成 `管理费用 借 12,345.67（6 笔）`，
    20 行变 5 行，判桶需要的信息一条不少。

    只读四列（大账 result.parquet 有 85 万行，为了几十张凭证读全表不值）。
    返回 {凭证号: ["管理费用 借 1,234.56（6 笔）", ...]}，按首次出现的顺序。
    """
    mapping = ctx.journal.schema.legacy()
    v_col, s_col = mapping.get("voucher_no"), mapping.get("subject")
    d_col, c_col = mapping.get("debit"), mapping.get("credit")
    if not (v_col and s_col):
        return {}
    p = ctx.path("clean", "result.parquet")
    if not p.exists() or not vids:
        return {}

    cols = [c for c in (v_col, s_col, d_col, c_col) if c]
    df = pd.read_parquet(p, columns=cols)
    key = df[v_col].astype(str)
    sub = df[key.isin(set(vids))]

    out: Dict[str, List[str]] = {}
    for vid, g in sub.groupby(v_col, sort=False):
        entries: List[str] = []          # 保持首次出现顺序
        bucket: Dict[tuple, List[Any]] = {}
        for _, r in g.iterrows():
            subject = str(r[s_col] or "").strip() or "（空科目）"
            debit = _amt(r[d_col]) if d_col else 0.0
            credit = _amt(r[c_col]) if c_col else 0.0
            if debit:
                k, sign = (subject, "借"), debit
            elif credit:
                k, sign = (subject, "贷"), credit
            else:
                k, sign = (subject, "—"), 0.0
            if k not in bucket:
                bucket[k] = [0.0, 0]
                entries.append(k)
            bucket[k][0] += sign
            bucket[k][1] += 1

        lines = []
        for k in entries[:max_entries]:
            subject, side = k
            total, n = bucket[k]
            text = f"{subject} {side} {total:.2f}"
            lines.append(f"{text}（{n} 笔）" if n > 1 else text)
        if len(entries) > max_entries:
            lines.append(f"……另有 {len(entries) - max_entries} 个科目")
        out[str(vid)] = lines
    return out


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
            "上游模型不可用：业务分类的前提是模型，"
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
# ③ review_bucket（AI）—— 共享实现，extra 是下游追加的复核理由
# ============================================================================

ExtraHook = Callable[[Ctx, pd.DataFrame], Dict[str, str]]


def review_items(ctx: Ctx, extra: Optional[ExtraHook] = None,
                 only_extra: bool = False) -> ReviewRequest:
    """业务分类的复核面 = 中/低置信 ∪ extra(ctx, detail) 指出的。

    `extra` 返回 {凭证号: 为什么}；现金流用它把"桶和钱的方向对不上"的凭证也拉进来
    （那些往往置信度很高，只按置信度筛根本没有机会改）。

    `only_extra=True` 时只看 extra：从上游清洗任务起跑时用——中/低置信那批上游
    已经看过一遍了（弃权也是一种结论），但"方向冲突"是上游**不可能发现**的新信息，
    必须在这里给一次机会。
    """
    detail = ctx.load("detail.parquet", "clean")
    decided = ctx.decisions("review_bucket")

    conflict: Dict[str, str] = dict(extra(ctx, detail)) if extra else {}
    vids = detail["凭证号"].astype(str)
    unsure = detail["置信度"].isin(["中", "低"]) if not only_extra else False
    hit = vids.isin(conflict.keys()) if only_extra else (unsure | vids.isin(conflict.keys()))

    view = detail[hit].copy()
    view = view[~view["凭证号"].astype(str).isin(decided)]
    if view.empty:
        return ReviewRequest()

    view["_why"] = ["；".join(filter(None, [
        conflict.get(str(v)),
        f"置信度{conf}（{prob}）"
        if (conf in ("中", "低") and not only_extra) else "",
    ])) for v, conf, prob in zip(view["凭证号"], view["置信度"],
                                 view["校准概率"].fillna("-"))]
    view["_hard"] = [0 if str(v) in conflict else 1 for v in view["凭证号"]]
    view["_p"] = pd.to_numeric(view["校准概率"], errors="coerce").fillna(-1)
    view = view.sort_values(["_hard", "_p"])

    limit = ctx.max_review()
    total = len(view)
    view = view.head(limit)

    lines = voucher_lines(ctx, [str(v) for v in view["凭证号"]])
    items = [{
        "key": str(r["凭证号"]),
        "凭证号": str(r["凭证号"]),
        "为什么要看": str(r["_why"]),
        "摘要": str(r.get("摘要") or "")[:120],
        # 整张凭证的分录：判桶看的是"对方科目在干什么"，不是货币资金那一行
        "分录": lines.get(str(r["凭证号"]), []),
        "科目": str(r.get("科目") or ""),
        "金额": float(r.get("金额") or 0),
        "模型桶": str(r.get("业务分类") or ""),
        "置信度": str(r.get("置信度") or ""),
        "校准概率": None if pd.isna(r.get("校准概率")) else round(float(r["校准概率"]), 4),
        "模型Top3": fmt_top3(r.get("模型Top3")),
        "组内张数": int(r.get("组内张数") or 1),
    } for _, r in view.iterrows()]

    return ReviewRequest(items=items, value_enum=ASSIGNABLE_BUCKETS,
                         help=REVIEW_HELP_BUCKET, key_name="voucher_id",
                         truncated=total > len(items))


def review_bucket(ctx: Ctx) -> ReviewRequest:
    """清洗自己的复核面：只看模型不确定的。"""
    return review_items(ctx)


def fmt_top3(raw: Any, n: int = 3) -> str:
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


# ============================================================================
# ④ export（清洗自己的产物）
# ============================================================================

REVIEW_COLUMNS = ["凭证号", "摘要", "科目", "金额", "业务分类", "置信度",
                  "校准概率", "模型Top3", "判定来源", "后处理", "未知科目"]


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

    # 改过桶的凭证：业务分类/判定来源换成复核后的（否则交付物与结论对不上）
    bucket_dec = ctx.decisions("review_bucket")
    if bucket_dec:
        v_col = mapping["voucher_no"]
        keys = result[v_col].astype(str)
        result = result.copy()
        result["业务分类"] = keys.map(bucket_dec).fillna(result["业务分类"])
        hit = keys.isin(bucket_dec.keys())
        if "判定来源" in result.columns:
            result.loc[hit, "判定来源"] = "复核改判"
        else:
            result["判定来源"] = ""

    vouchers = build_vouchers(result, mapping)
    out_dir = ctx.job.dir / "out" / "export"
    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    written = journal_io.write_sheets(
        {"业务分类结果": result, "凭证明细": detail, "凭证汇总": vouchers},
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
            "bucket_overrides": len(bucket_dec),
            "export": {"format": written.fmt, "seconds": written.seconds,
                       "hint": written.hint},
            "files": written.paths,
        })


# ============================================================================
# 给下游功能用的"接着上游跑"
# ============================================================================

# 从上游任务搬过来的产物：自己的 read / clean 就不用再跑
_UPSTREAM_FILES = (
    ("out/read/raw.parquet", "read", None),
    ("out/read/schema.json", "read", None),
    ("out/clean/result.parquet", "clean", "result.parquet"),
    ("out/clean/detail.parquet", "clean", "detail.parquet"),
    ("decisions/review_bucket.json", None, None),
)


def upstream_id(ctx: Ctx) -> str:
    return str((ctx.options or {}).get("upstream") or "").strip()


def has_upstream(ctx: Ctx) -> bool:
    return bool(upstream_id(ctx))


def upstream_signature(ctx: Ctx) -> Any:
    """上游任务的"内容指纹"：干净阶段哈希 + 改桶结论哈希。

    只把 job_id 放进选项是不够的——同一个 id 下改了桶，下游不会重算。
    """
    jid = upstream_id(ctx)
    if not jid:
        return ""
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        return f"{jid}:missing"
    return [jid, up.stage_state("clean").get("hash") or "",
            up.decision_hash("review_bucket")]


def run_link(ctx: Ctx) -> StageResult:
    """把上游任务的读表/分类产物与复核结论搬进本任务。

    搬而不是"引用"：本任务要能独立重跑、独立打包，上游被删也不影响。
    上游改了桶，`upstream_signature` 会变 → 本阶段重跑（重新搬）→ 下游跟着重算。
    """
    jid = upstream_id(ctx)
    if not jid:
        raise ValueError("link 阶段要求 options.upstream 指向一个已完成的任务")
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        raise ValueError(f"上游任务不存在：{jid}") from None
    # 不要求上游"已完成"：复核到一半也允许接——judge 只会应用已有的改桶结论，
    # 没复核的继续用模型原判。上游之后又改了几条，resume 本任务就会重算。
    if up.stage_state("clean").get("status") not in ("done", "skipped"):
        raise ValueError(f"上游任务 {jid} 的业务分类还没跑完（当前 {up.status}）")

    copied: List[str] = []
    for src_rel, _stage, new_name in _UPSTREAM_FILES:
        src = up.dir / src_rel
        if not src.is_file():
            if "review_bucket" in src_rel:
                continue          # 上游没有改桶结论也正常
            raise ValueError(f"上游任务缺产物：{src_rel}")
        dst = ctx.job.dir / src_rel
        if new_name:                      # result/detail 落到自己的 clean 目录
            dst = ctx.job.dir / "out" / "clean" / new_name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        copied.append(src_rel)

    # journal 对象是懒加载的：作废缓存，让它读刚搬进来的 out/read
    ctx.job.drop_journal_cache()
    j = ctx.journal
    detail = ctx.load("detail.parquet", "clean")
    return StageResult(facts={
        "upstream": jid,
        "upstream_status": up.status,
        "copied": copied,
        "rows": int(len(j)),
        "vouchers": int(len(detail)),
        "bucket_overrides": len(ctx.decisions("review_bucket")),
    })
