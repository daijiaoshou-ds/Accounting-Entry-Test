# -*- coding: utf-8 -*-
"""会计分录测试 —— 阶段实现。

    read            程序   读表 + 认列名（复用 cleaning 的那一段）
    detect          程序   业务群聚类 + 异常打分（逐行）+ 逐凭证汇总
    review_anomaly  AI     高/中风险的凭证：真异常、疑似、还是误报   ← 停
    export          程序   出两张表：异常线索（逐凭证）、明细分录（逐行）

为什么复核按「凭证」：引擎打的是行级分，但一张凭证 60 行、每行分数一样，按行问
就是同一件事问 60 遍。导出也按凭证给结论，落到明细行上。

为什么导出不重跑引擎：`detect` 把行级结果按**位置**存成 `risk.parquet`（原表的
index 就是位置），导出直接按位置拼回去。重跑一次要多花 2 秒，还要保证两次跑出
来的结果一模一样——存下来就没这个问题。
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

import journal_io
from jobflow.stage import Ctx, ReviewRequest, StageResult

from ..cleaning import stages as clean_s
from . import engine

REQUIRES = engine.REQUIRES

REVIEW_HELP = (
    "这些凭证被算法判为高/中风险——判断依据是「业务群 + 跨群程度 + 金额重要性」的\n"
    "统计异常，不是账务规则，所以必然有误报（大额正常业务、一次性特殊分录）。\n"
    "逐条看分录，给一个结论：\n"
    "  · 确认异常 → 确实该查（写 {\"key\": 凭证号, \"value\": \"确认异常\", \"note\": 依据}）\n"
    "  · 疑似     → 拿不准，交给人看\n"
    "  · 误报     → 这笔是合理的（说明为什么，note 会进导出表）\n"
    "  · 不改     → 不写进 decisions，导出表里这一列留空\n"
    "整批看完了 → `journal submit <job> --empty --stage review_anomaly`")


# ============================================================================
# ① read
# ============================================================================

def run_read(ctx: Ctx) -> StageResult:
    return clean_s.read_journal(ctx, REQUIRES)


# ============================================================================
# ② detect
# ============================================================================

RISK_COLUMNS = ["voucher_unique_id", "风险等级", "异常类型", "综合得分", "凭证金额"]


def run_detect(ctx: Ctx) -> StageResult:
    result = engine.detect(ctx.journal)
    vouchers = engine.vouchers(result, ctx.journal)

    # 行级结果按位置落盘：原表 index 就是位置（read 阶段是 read_excel，从 0 开始）
    risk = result[RISK_COLUMNS].copy()
    risk["风险等级"] = risk["风险等级"].astype(str).fillna("")
    risk["异常类型"] = risk["异常类型"].astype(str).fillna("")
    risk["综合得分"] = risk["综合得分"].astype(float)
    risk["凭证金额"] = risk["凭证金额"].astype(float)
    ctx.save("risk.parquet", risk)

    # 待复核的凭证把分录一起算好：复核阶段就不必再读一行明细
    picked = engine.candidates(vouchers)
    lines = engine.entries(result, [v["key"] for v in picked])
    for v in picked:
        v["entries"] = lines.get(v["key"], [])

    facts = engine.stats(result, vouchers)
    ctx.save_json("vouchers.json", {"vouchers": vouchers, "stats": facts})
    ctx.log(f"  高风险 {facts['high']} 张、中风险 {facts['medium']} 张")
    return StageResult(facts=facts)


# ============================================================================
# ③ review_anomaly（AI）
# ============================================================================

def _item(v: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "key": v["key"],
        "凭证号": v["voucher"],
        "为什么要看": f"算法判为{engine.risk_of(v)}（综合得分 {v['score']}）",
        "异常类型": v["kind"],
        "凭证金额": v["amount"],
        "业务群": v["group"],
        "涉及业务群": v["groups"],
        "摘要": v.get("summary", ""),
        "分录": v.get("entries", []),
    }


def review_anomaly(ctx: Ctx) -> ReviewRequest:
    doc = ctx.load_json("vouchers.json", "detect")
    vouchers: List[Dict[str, Any]] = doc.get("vouchers") or []

    decided = ctx.decisions("review_anomaly")
    picked = [v for v in engine.candidates(vouchers) if v["key"] not in decided]

    limit = ctx.max_review(25)
    items = [_item(v) for v in picked[:limit]]

    return ReviewRequest(
        items=items,
        value_enum=list(engine.DECISIONS),
        help=REVIEW_HELP,
        key_name="凭证号",
        truncated=len(picked) > len(items),
    )


# ============================================================================
# ④ export
# ============================================================================

def run_export(ctx: Ctx) -> StageResult:
    journal = ctx.journal
    mapping = journal.schema.legacy()
    vcol = mapping.get("voucher_no", "")

    doc = ctx.load_json("vouchers.json", "detect")
    vouchers: List[Dict[str, Any]] = doc.get("vouchers") or []
    info = engine.apply_decisions(vouchers, ctx.decisions("review_anomaly"))
    by_voucher = info["by_voucher"]

    # ① 异常线索（逐凭证，只留值得看的）
    leads = []
    for v in vouchers:
        if not engine.needs_review(v) and v["key"] not in info["by_key"]:
            continue
        leads.append({
            "凭证号": v["voucher"],
            "摘要": v.get("summary", ""),
            "业务群": v["group"],
            "涉及业务群": v["groups"],
            "风险等级": v["risk"],
            "异常类型": v["kind"],
            "综合得分": v["score"],
            "凭证金额": v["amount"],
            "行数": v["rows"],
            "复核结论": info["by_key"].get(v["key"], ""),
        })

    # ② 明细分录（原表 + 风险 + 复核结论，按位置对回去）
    detail = journal.df.copy()
    risk = ctx.load("risk.parquet", "detect").reindex(range(len(detail)))
    for col in ("风险等级", "异常类型", "综合得分", "凭证金额"):
        detail[col] = risk[col].fillna("" if col in ("风险等级", "异常类型") else 0).values
    if vcol:
        detail["复核结论"] = detail[vcol].astype(str).map(by_voucher).fillna("").values

    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    out_dir = ctx.job.dir / "out" / "export"
    written = journal_io.write_sheets(
        {"异常线索": _frame(leads), "明细分录": detail},
        out_dir / f"{stem}_会计分录测试.xlsx", target="download")

    counts: Dict[str, int] = {}
    for value in info["by_key"].values():
        counts[value] = counts.get(value, 0) + 1
    summary = [
        f"{len(vouchers)} 张凭证，高风险 {doc.get('stats', {}).get('high', 0)} 张、"
        f"中风险 {doc.get('stats', {}).get('medium', 0)} 张",
        f"复核结论：{info['applied']} 条"
        + (f"（确认异常 {counts.get('确认异常', 0)}、疑似 {counts.get('疑似', 0)}、"
           f"误报 {counts.get('误报', 0)}）" if info["applied"] else "（这批没改）"),
        f"线索表 {len(leads)} 行，明细表 {len(detail)} 行",
    ]

    return StageResult(
        artifacts={"result": written.paths.get("异常线索"),
                   **{f"sheet:{k}": v for k, v in written.paths.items()}},
        facts={
            "rows": int(len(detail)),
            "vouchers": len(vouchers),
            "leads": len(leads),
            "risk_dist": doc.get("stats", {}).get("risk_dist", {}),
            "decisions_applied": info["applied"],
            "decisions_ignored": info["ignored"],
            "conclusion_counts": counts,
            "summary": summary,
            "export": {"format": written.fmt, "seconds": written.seconds,
                       "hint": written.hint},
            "files": written.paths,
        })


def _frame(rows: List[Dict[str, Any]]):
    """list[dict] → DataFrame（空的时候也要有列，否则导出会少一张表）。"""
    columns = ["凭证号", "摘要", "业务群", "涉及业务群", "风险等级", "异常类型",
               "综合得分", "凭证金额", "行数", "复核结论"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows)
