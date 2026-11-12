# -*- coding: utf-8 -*-
"""对方科目分析 —— 阶段实现。

    read            程序   读表 + 认列名（复用 cleaning 的那一段）
    analyze         程序   复杂模式穷举 + 奥卡姆排序 → 候选方案
    review_pattern  AI     同一科目组合给了几个方案，按业务选一个   ← 停
    export          程序   套用选定方案 → 写回 + 导出

为什么复核单位是「模式」不是「凭证」：算法对同一科目组合的所有凭证给同一套配对
拓扑，选一次就作用于这一批。本账 136 个模式里 111 个有多解，按凭证列待办等于把
同一个问题问 13 遍。
"""

from __future__ import annotations

from typing import Any, Dict, List

import journal_io
from jobflow.stage import Ctx, ReviewRequest, StageResult

from ..cleaning import stages as clean_s
from . import engine

REQUIRES = engine.REQUIRES

REVIEW_HELP = (
    "多借多贷的凭证，算法穷举出几种同样成立的「借方科目 ↔ 贷方科目」配对。\n"
    "配对决定写回表里每一行的「对方科目」，选错了这一批凭证全错。\n"
    "  · 认同第 1 案 → 不用写进 decisions（保持原判）\n"
    "  · 要换成第 2/3 案 → 写 {\"key\": 模式 key, \"value\": \"2\", \"note\": 依据}\n"
    "  · value 是方案序号；超过这个模式方案数的会被忽略、按第 1 案走\n"
    "整批看完了不改了 → `journal submit <job> --empty --stage review_pattern` "
    "表示这批不再改，剩下的照第 1 案落表。")


# ============================================================================
# ① read —— 复用清洗的读表，只是字段契约多一个「日期」
# ============================================================================

def run_read(ctx: Ctx) -> StageResult:
    return clean_s.read_journal(ctx, REQUIRES)


# ============================================================================
# ② analyze
# ============================================================================

def run_analyze(ctx: Ctx) -> StageResult:
    proc = engine.build(ctx.journal)
    proc.process_all()
    patterns = engine.solve(proc)

    facts = engine.stats(proc, patterns)
    ctx.save_json("patterns.json", {"patterns": patterns, "stats": facts})
    ctx.log(f"  {facts['patterns']} 个复杂模式，{facts['review_candidates']} 个需要定案")
    return StageResult(facts=facts)


# ============================================================================
# ③ review_pattern（AI）
# ============================================================================

def _item(p: Dict[str, Any]) -> Dict[str, Any]:
    """一条待办 = 一个科目组合 + 它的几个候选配对方案。

    不放「模式」（科目组合全名）：它跟下面的「分录」是同一批科目，重复一遍只是
    把待办撑大。判配对靠的是"哪一笔对应哪一笔"，分录里有金额、方案里有对应关系。
    """
    why = [f"{p['count']} 张凭证"]
    if p.get("gap") == 0:
        why.append("前两名得分并列，算法分不出来")
    elif p.get("solutions", 0) > len(p["options"]):
        why.append(f"共 {p['solutions']} 个可行方案")
    if p.get("timeout"):
        why.append("穷举超时，候选不全")

    options = []
    for opt in p["options"]:
        pairs = "；".join(
            f"{e['from']} → {e['to']} {e['amount']:,.2f}" for e in opt["pairs"][:6])
        more = "" if len(opt["pairs"]) <= 6 else f"；……另 {len(opt['pairs']) - 6} 对"
        options.append(f"方案{opt['rank']}（{opt['score']} 分）：{pairs}{more}")

    return {
        "key": p["key"],
        "为什么要看": "；".join(why),
        "出现次数": p["count"],
        "样张凭证": p["voucher"],
        "摘要": p.get("summary", ""),
        "分录": p["entries"],
        "候选方案": options,
    }


def review_pattern(ctx: Ctx) -> ReviewRequest:
    doc = ctx.load_json("patterns.json", "analyze")
    patterns: List[Dict[str, Any]] = doc.get("patterns") or []

    # 已经定过的模式不再问：否则同一批待办会一轮一轮地回来，永远交不完
    decided = ctx.decisions("review_pattern")
    picked = [p for p in engine.candidates(patterns) if p["key"] not in decided]

    limit = ctx.max_review(25)
    items = [_item(p) for p in picked[:limit]]

    return ReviewRequest(
        items=items,
        value_enum=[str(i) for i in range(1, engine.MAX_OPTIONS + 1)],
        help=REVIEW_HELP,
        key_name="模式",
        truncated=len(picked) > len(items),
    )


# ============================================================================
# ④ export
# ============================================================================

def run_export(ctx: Ctx) -> StageResult:
    proc = engine.build(ctx.journal)
    proc.process_all()
    patterns = engine.solve(proc)          # 同时给 finalize 预热同模式的拓扑缓存

    df, applied = engine.finalize(proc, patterns,
                                  ctx.decisions("review_pattern"), log=ctx.log)

    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    out_dir = ctx.job.dir / "out" / "export"
    written = journal_io.write_sheets(
        {"对方科目分析": df}, out_dir / f"{stem}_对方科目分析.xlsx", target="download")

    # 给人看的两三行话。插件与页面原样转述，不认字段名——新功能照这个格式写即可
    summary = [
        f"{len(patterns)} 个复杂模式，复核指定了 {applied['applied']} 个方案"
        + (f"（{len(applied['clamped'])} 条取值超出方案数，已按第 1 案）"
           if applied["clamped"] else ""),
        f"没定案的行：{applied['unresolved_rows']}"
        + (f"；对不上当前模式的结论 {len(applied['ignored'])} 条已忽略"
           if applied["ignored"] else ""),
    ]

    return StageResult(
        artifacts={"result": written.paths.get("对方科目分析"),
                   **{f"sheet:{k}": v for k, v in written.paths.items()}},
        facts={
            "rows": int(len(df)),
            "vouchers": int(proc.df["_uid"].nunique()),
            "patterns": len(patterns),
            "decisions_applied": applied["applied"],
            "decisions_ignored": applied["ignored"],
            "decisions_clamped": applied["clamped"],
            "unresolved_rows": applied["unresolved_rows"],
            "summary": summary,
            "export": {"format": written.fmt, "seconds": written.seconds,
                       "hint": written.hint},
            "files": written.paths,
        })
