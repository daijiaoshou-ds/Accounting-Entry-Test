# -*- coding: utf-8 -*-
"""接着上游任务跑：把上游的产物搬进本任务。

管线是「对方科目分析 → 序时账清洗 → 现金流 / 分录测试」。下游接清洗时，`read` 与
`clean` 整体跳过——同一份账不重复分类、不重复复核；上游后来改了桶，`link` 的哈希
会变 → `journal resume` 一次就把下游重算。

**搬而不是引用**：本任务要能独立重跑、独立打包，上游被删也不影响。
"""

from __future__ import annotations

from typing import Any, List

from jobflow import Job
from jobflow.stage import Ctx, StageResult

# 从上游清洗任务搬过来的产物：自己的 read / clean 就不用再跑
# (源相对路径, 落到哪个阶段目录, 改名, 可以缺吗)
UPSTREAM_FILES = (
    ("out/read/raw.parquet", "read", None, False),
    ("out/read/schema.json", "read", None, False),
    ("out/clean/result.parquet", "clean", "result.parquet", False),
    ("out/clean/detail.parquet", "clean", "detail.parquet", False),
    # 上游清洗若本身接过对方科目，这张行表跟着一起搬 —— 分录测试的科目对
    # 要用它；没有也不该让整条链断掉（老任务都没有）
    ("out/link/contra_rows.parquet", "link", None, True),
    ("decisions/review_bucket.json", None, None, True),
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


def same_ledger(ctx: Ctx, up: Job, jid: str) -> None:
    """必须是同一份账：两份对不上还接，结果算的是 A 账、文件名记的是 B 账。"""
    mine = ctx.job.state.get("input") or {}
    theirs = up.state.get("input") or {}
    if mine.get("sha256") and theirs.get("sha256") \
            and mine["sha256"] != theirs["sha256"]:
        raise ValueError(
            f"两份账不是同一份：本任务 {mine.get('filename', '?')}"
            f"（sha {mine['sha256'][:8]}），上游 {jid} 跑的是 "
            f"{theirs.get('filename', '?')}（sha {theirs['sha256'][:8]}）。"
            "接上游就别再传文件（`journal run` 只给 --option upstream），"
            "或者换成上游那份账。")


def run_link(ctx: Ctx) -> StageResult:
    """把上游任务的读表/分类产物与复核结论搬进本任务。

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

    # 必须是同一份账。link 搬的是上游的 out/read，本任务 source/ 里的原件从此不参与
    # 计算——两份对不上还不拦，结果算的是 A 账、文件名和 sha 却记着 B 账，
    # 事后没有任何地方会报错。
    same_ledger(ctx, up, jid)

    copied: List[str] = []
    for src_rel, _stage, new_name, optional in UPSTREAM_FILES:
        src = up.dir / src_rel
        if not src.is_file():
            if optional:
                continue          # 改桶结论、对方科目行表，缺了都正常
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
