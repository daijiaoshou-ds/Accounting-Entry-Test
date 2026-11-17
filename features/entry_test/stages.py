# -*- coding: utf-8 -*-
"""会计分录测试 2.0 —— 阶段实现。

    link       程序   接上游清洗的业务分类（可选，options.upstream）
    read       程序   读表 + 认列名（复用 cleaning 的那一段）
    integrity  程序   第 0 步：总体完整性（凭证号连续 / 按月借贷平衡 / 数据质量）
    profile    程序   第 1 步：画像（业务活动 + 集中度）
    export     程序   一份报告：HTML（现场看）+ PDF（签字归档）

为什么画像**必须**接上游：业务活动就是清洗判出来的业务桶。没有它，画像只剩
"按一级科目统计"，那是科目余额表，不是"这份账在讲什么"。所以缺业务分类时直接
报错并给出两条路（先跑清洗 / 用 --option upstream 接上），不静默降级——静默降级
的产物看起来跟正常的一样，只是内容空了，没人会发现。

为什么断言"完整性不过也继续"：不平衡、缺号本身就是**发现**，不是"不能跑"。
第 0 步的结论进表、进 facts，后面照跑；把它当闸门关掉，等于把发现藏起来。
"""

from __future__ import annotations

from typing import Dict

from jobflow.stage import Ctx, StageResult

from ..cleaning import stages as clean_s
from . import engine, integrity, profile, report

REQUIRES = engine.REQUIRES

# ============================================================================
# ① read
# ============================================================================

def run_read(ctx: Ctx) -> StageResult:
    return clean_s.read_journal(ctx, REQUIRES)


# ============================================================================
# ② integrity（第 0 步）
# ============================================================================

def run_integrity(ctx: Ctx) -> StageResult:
    # 接上游时 read 被跳过，字段契约要在这里补一次：完整性离了日期/借贷就是空跑
    ctx.journal.require(REQUIRES)
    frame = engine.view(ctx.journal)
    report = integrity.check(frame)
    ctx.save_json("integrity.json", report)

    facts = report["quality"]
    verdict = "通过" if report["ok"] else f"{report['problems']} 项问题"
    ctx.log(f"  完整性：{verdict}，{report['warnings']} 项警告"
            f"（{len(report['months'])} 个月 / {len(report['series'])} 条凭证序列）")
    return StageResult(facts={
        "rows": facts["rows"],
        "vouchers": facts["vouchers"],
        "balanced": bool(report["ok"]),
        "problems": report["problems"],
        "warnings": report["warnings"],
        "months": len(report["months"]),
        "series": len(report["series"]),
        "empty_subject": facts["empty_subject"],
        "missing_summary": facts["missing_summary"],
    })


# ============================================================================
# ③ profile（第 1 步）
# ============================================================================

def activity_of(ctx: Ctx) -> Dict[str, str]:
    """{凭证号: 业务活动} —— 上游清洗的**最终**结论（模型 + 复核改判）。

    为什么不是 `result.parquet`：那是 clean 阶段的模型原判，复核改过的桶还没落进去。
    直接用它，同一份账在清洗的交付物里是「费用」、在画像里是「采购」——两处都自称
    是这份账的分类，谁对？所以这里把 `decisions/review_bucket.json`（link 从上游搬
    过来的）按组铺回每张凭证，和清洗导出时的口径一致。
    """
    detail_path = ctx.path("clean", "detail.parquet")
    if not detail_path.exists():
        raise ValueError(
            "这份账还没有业务分类，画像做不了。两条路："
            "① 先在同一个任务上跑一次「序时账清洗」，再回来跑这个功能；"
            "② 用 `--option upstream=<清洗任务 id>` 接着那一份的结果跑。")
    detail = ctx.load("detail.parquet", "clean")
    overrides = clean_s.expand_group_decisions(detail, ctx.decisions("review_bucket"))

    out: Dict[str, str] = {}
    for vid, bucket in zip(detail["凭证号"].astype(str), detail["业务分类"].astype(str)):
        out[vid] = overrides.get(vid) or bucket
    return out


def run_profile(ctx: Ctx) -> StageResult:
    frame = engine.view(ctx.journal)
    vouchers = engine.vouchers(frame)
    doc = profile.build(frame, vouchers, activity_of(ctx))
    ctx.save_json("profile.json", doc)

    stats = doc["stats"]
    ctx.log(f"  画像：{stats.get('activities', 0)} 个业务活动")
    return StageResult(facts={
        "activities": stats.get("activities", 0),
        "unclassified": stats.get("unclassified", 0),
        "amount": stats.get("amount", 0),
    })


# ============================================================================
# ④ export
# ============================================================================

def run_export(ctx: Ctx) -> StageResult:
    """写一份报告。HTML 是主件（能现场看），PDF 是签字件（缺字体时降级）。"""
    report_integrity = ctx.load_json("integrity.json", "integrity") or {}
    report_profile = ctx.load_json("profile.json", "profile") or {}
    model = report.build(ctx.job.state, report_integrity, report_profile)

    activities = report_profile.get("activities") or []
    top = activities[0] if activities else {}
    html_text = report.to_html(model)

    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    out_dir = ctx.job.dir / "out" / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{stem}_会计分录测试.html"
    html_path.write_text(html_text, encoding="utf-8")

    artifacts = {"result": str(html_path), "html": str(html_path)}
    pdf_error = ""
    try:
        pdf_path = report.to_pdf(model, out_dir / f"{stem}_会计分录测试.pdf")
        artifacts["pdf"] = str(pdf_path)
    except Exception as error:          # 缺字体/权限：报告照样给人看，别整单失败
        pdf_error = str(error)
        ctx.log(f"  PDF 没生成：{pdf_error}")

    summary = [
        f"完整性：{'通过' if report_integrity.get('ok') else
                   str(report_integrity.get('problems', 0)) + ' 项问题'}"
        f"（{report_integrity.get('warnings', 0)} 项警告）",
        f"画像：{len(activities)} 个业务活动"
        + (f"，金额最大的是「{top.get('业务活动')}」（占 {top.get('金额占比')}）"
           if top else ""),
        "报告：HTML + PDF" if not pdf_error else f"报告：HTML（PDF 失败：{pdf_error[:60]}）",
    ]

    return StageResult(
        artifacts=artifacts,
        facts={
            "rows": int((report_integrity.get("quality") or {}).get("rows", 0)),
            "vouchers": int((report_integrity.get("quality") or {}).get("vouchers", 0)),
            "activities": len(activities),
            "problems": report_integrity.get("problems", 0),
            "warnings": report_integrity.get("warnings", 0),
            "pdf": "pdf" in artifacts,
            "pdf_error": pdf_error,
            "bytes": html_path.stat().st_size,
            "summary": summary,
            "files": {key: value for key, value in artifacts.items() if key != "html"},
        })
