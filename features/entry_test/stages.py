# -*- coding: utf-8 -*-
"""会计分录测试 2.0 —— 阶段实现。

    link       程序   接上游清洗的业务分类（可选，options.upstream）
    read       程序   读表 + 认列名（复用 core.ledger 的共用段）
    integrity  程序   第 0 步：总体完整性（凭证号连续 / 按月借贷平衡 / 数据质量
                       / 金额首位分布背景）
    profile    程序   第 1 步：画像（业务活动 + 集中度）
    detect     程序   第 2 步：分层检验（金额孤立 / 活动内科目 / 科目对 /
                       孤儿冲销）→ 线索；开跑前先按白名单剔掉机械凭证
    export     程序   一份 HTML 报告 + 一份 Excel（原序时账 + 异常标记列）

为什么画像**必须**接上游：业务活动就是清洗判出来的业务桶。没有它，画像只剩
"按一级科目统计"，那是科目余额表，不是"这份账在讲什么"。所以缺业务分类时直接
报错并给出两条路（先跑清洗 / 用 --option upstream 接上），不静默降级——静默降级
的产物看起来跟正常的一样，只是内容空了，没人会发现。

为什么断言"完整性不过也继续"：不平衡、缺号本身就是**发现**，不是"不能跑"。
第 0 步的结论进表、进 facts，后面照跑；把它当闸门关掉，等于把发现藏起来。
"""

from __future__ import annotations

from typing import Dict

import pandas as pd

import journal_io
from core.ledger import expand_group_decisions, read_journal
from jobflow.stage import Ctx, StageResult

from . import activity, benford, cash, chains, detail, engine, fingerprint, integrity
from . import leads, matrix, profile, report, whitewash

REQUIRES = engine.REQUIRES

# ============================================================================
# ① read
# ============================================================================

def run_read(ctx: Ctx) -> StageResult:
    return read_journal(ctx, REQUIRES)


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
    overrides = expand_group_decisions(detail, ctx.decisions("review_bucket"))

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
# ④ detect（第 2 步）
# ============================================================================

def _contra_edges(ctx: Ctx, skip: set):
    """上游「对方科目分析」的行表 → 边表。拿不到就返回 None（这一层不做）。"""
    path = ctx.path("link", "contra_rows.parquet")
    if not path.exists():
        return None, None, "没有上游对方科目行表（清洗那一棒没接「对方科目分析」）"
    rows = pd.read_parquet(path)
    if skip:
        rows = rows[~rows["凭证编号"].astype(str).isin(skip)]
    mapping = ctx.journal.schema.legacy()
    edges = matrix.build_edges(rows, {
        "voucher_no": mapping.get("voucher_no") or "凭证编号",
        "subject": mapping.get("subject") or "一级科目",
        "debit": mapping.get("debit") or "借方金额",
        "credit": mapping.get("credit") or "贷方金额",
    })
    return rows, edges, ""


def run_detect(ctx: Ctx) -> StageResult:
    """各块各跑各的，合成一张线索清单。

    为什么一块失败不拖垮全部：各块输入依赖不一样（指纹要摘要、科目对要上游的
    对方科目、孤儿冲销要负数行）。某一块在一份残缺的账上跑不动，不该让其他块的
    发现也消失。

    本福特也在这里跑，但它**只出 stats、不出线索**：它的结论是"整账数字可信度"
    的背景判断，归报告 1.2，不进异常清单、不标凭证行。
    """
    frame = engine.view(ctx.journal)
    bucket_of = activity_of(ctx)

    # 白名单先剔：汇兑损益、结转期末损益、资金内部往来这类凭证的金额和科目组合
    # 由规则决定，三层对它说的每句话都是废话。有上游业务分类时按业务活动判，
    # 否则退回摘要关键词。剔掉多少、为什么剔，都要写进报告。
    frame, white = whitewash.apply(frame, ctx.journal.schema.legacy(), bucket_of)
    skip = {item["凭证号"] for item in white["vouchers"]}
    vouchers = engine.vouchers(frame)

    runners = [("本福特", lambda: benford.detect(frame, bucket_of)),
               ("金额孤立", lambda: fingerprint.detect(frame, vouchers)),
               ("活动科目", lambda: activity.detect(frame, bucket_of)),
               ("现金", lambda: cash.detect(frame)),
               ("孤儿冲销", lambda: chains.detect(frame))]

    errors: Dict[str, str] = {}
    rows, edges, why = _contra_edges(ctx, skip)
    if edges is None:
        errors["科目对"] = why
        ctx.log(f"  科目对这一层没跑：{why}")
    else:
        runners.insert(3, ("科目对", lambda: matrix.detect(
            edges, rows, ctx.journal.schema.legacy())))

    found = []
    stats: Dict[str, Dict] = {}
    for name, runner in runners:
        try:
            result = runner()
            found.extend(result["leads"])
            stats[name] = result["stats"]
        except Exception as error:          # 一层挂了，其余照出
            errors[name] = f"{type(error).__name__}: {error}"
            ctx.log(f"  {name} 没跑成：{errors[name]}")

    ordered = leads.sort_leads(found)
    doc = {"leads": ordered, "stats": stats, "errors": errors,
           "whitewash": white, "counts": _count_by_layer(ordered)}
    ctx.save_json("leads.json", doc)
    ctx.log(f"  线索：{len(ordered)} 条"
            + (f"（{len(errors)} 层未跑成）" if errors else "")
            + (f"；白名单放过 {white['count']} 张" if white["count"] else ""))
    return StageResult(facts={
        "leads": len(ordered),
        "by_layer": doc["counts"],
        "orphan_reversals": (stats.get("孤儿冲销") or {}).get("orphan", 0),
        # 用了上游对方科目的直接证据：任务详情里能看出这一层到底吃到了没有
        "contra_edges": (stats.get("科目对") or {}).get("edges", 0),
        # 本福特的判读放 facts，任务详情里能直接看到，不用翻报告
        "benford": "、".join(
            f"{side} MAD {info['first']['mad']:.4f} {info['first']['verdict']}"
            for side, info in ((stats.get("本福特") or {}).get("sides") or {}).items()),
        # 现金收付：付现/收现各几张、多少钱
        "cash": {key: (stats.get("现金") or {}).get(key, {}).get("count", 0)
                 for key in ("pay", "recv")},
        "whitewash": white["count"],
        "errors": errors,
    })


def _count_by_layer(items) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        out[item["层"]] = out.get(item["层"], 0) + 1
    return out


# ============================================================================
# ⑤ export
# ============================================================================

def run_export(ctx: Ctx) -> StageResult:
    """写报告（HTML）与标记表（Excel）。

    两个文件的分工不是"正文与附件"：报告是**读**的（一屏一件事，图、结论、总览），
    Excel 是**动手**的——它就是用户传进来的那份账，末尾多几列标记，筛选「是否异常」
    就是全部问题凭证。把报告拆成六张汇总表塞进 Excel，等于把同一批结论印两遍，
    而"一条线索一行"又跟账的行对不上，人还得在两张表之间找第几行。
    """
    report_integrity = ctx.load_json("integrity.json", "integrity") or {}
    report_profile = ctx.load_json("profile.json", "profile") or {}
    report_leads = ctx.load_json("leads.json", "detect") or {}
    model = report.build(ctx.job.state, report_integrity, report_profile, report_leads)

    activities = report_profile.get("activities") or []
    top = activities[0] if activities else {}
    lead_items = report_leads.get("leads") or []

    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    out_dir = ctx.job.dir / "out" / "export"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{stem}_会计分录测试.html"
    html_path.write_text(report.to_html(model), encoding="utf-8")

    xlsx_path = out_dir / f"{stem}_会计分录测试_标记.xlsx"
    written = journal_io.write_sheets(
        detail.build(ctx.journal, report_leads), xlsx_path, target="download",
        # 几万行里挑"是否异常=是"，第一个动作就是筛选
        autofilter=True)

    layers = report_leads.get("counts") or {}
    summary = [
        f"完整性：{'通过' if report_integrity.get('ok') else
                   str(report_integrity.get('problems', 0)) + ' 项问题'}"
        f"（{report_integrity.get('warnings', 0)} 项警告）",
        f"画像：{len(activities)} 个业务活动"
        + (f"，金额最大的是「{top.get('业务活动')}」（占 {top.get('金额占比')}）"
           if top else ""),
        (f"线索：{len(lead_items)} 条（" + "、".join(f"{k} {v}" for k, v in layers.items()) + "）"
         if lead_items else "线索：三层都没发现"),
        "报告：HTML + 标记表 Excel",
    ]

    # 标记表只登记一次：xlsx 是一份文件（一张表），超行数退成 csv 时才一份一张
    artifacts = {"result": str(html_path)}
    if written.fmt == "xlsx":
        artifacts["detail"] = str(xlsx_path)
    else:
        for name, path in written.paths.items():
            artifacts[f"detail:{name}"] = path
    return StageResult(
        artifacts=artifacts,
        facts={
            "rows": int((report_integrity.get("quality") or {}).get("rows", 0)),
            "vouchers": int((report_integrity.get("quality") or {}).get("vouchers", 0)),
            "activities": len(activities),
            "problems": report_integrity.get("problems", 0),
            "warnings": report_integrity.get("warnings", 0),
            "leads": len(lead_items),
            "by_layer": layers,
            # 标记表里有多少张凭证被标了：看一眼就知道那张表值不值得打开
            "flagged_vouchers": len({vid for item in lead_items for vid in item["凭证"]}),
            "bytes": html_path.stat().st_size,
            "summary": summary,
            "files": artifacts,
        })
