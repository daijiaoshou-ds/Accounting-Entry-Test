# -*- coding: utf-8 -*-
"""会计分录测试报告（HTML）。

为什么导 HTML 不导 xlsx：这份东西的读者是项目组，"看完要签字、要归档"的是一份
**报告**，不是几张能随便排序的 sheet。顺序就是读的顺序——先证账能用（完整性），
再讲账在讲什么（画像），最后才是线索清单。

为什么先不做 PDF：从 HTML 转要 weasyprint（Windows 上还要 GTK），自己画又要再写
一套版式；打磨阶段先让 HTML 一种形式跑通，归档的事后面再说。报告是**自包含**的
（样式内联、无脚本、无外链），一个文件发出去就能看，也能直接用浏览器的"打印成 PDF"。
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from . import leads as lead_mod

# 结果词 → 语气
TONE = {"通过": "ok", "信息": "mute", "跳过": "mute", "警告": "warn", "问题": "bad"}

PROFILE_COLUMNS = ["业务活动", "域", "凭证", "笔数占比", "金额", "金额占比",
                   "主要制单人", "该人占比", "金额HHI", "对手方HHI", "判读", "主要对手方"]

# 这些列的内容长，给最小宽度，免得被挤成一列一个字
_WIDE = {"证据", "涉及凭证", "命中的线索", "主要对手方", "判读", "说明"}


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def build(state: Dict[str, Any], integrity: Dict[str, Any], profile: Dict[str, Any],
          leads_doc: Optional[Dict[str, Any]] = None,
          generated_at: Optional[str] = None) -> Dict[str, Any]:
    """把四个阶段的结果拼成报告模型。渲染器只认这个结构。"""
    leads_doc = leads_doc or {}
    quality = integrity.get("quality") or {}
    activities = profile.get("activities") or []
    stats = profile.get("stats") or {}
    months = integrity.get("months") or []
    items = leads_doc.get("leads") or []
    file_name = (state.get("input") or {}).get("filename") or ""

    problems, warnings = integrity.get("problems", 0), integrity.get("warnings", 0)
    if problems:
        verdict, tone = f"完整性有 {problems} 项问题，先看第一节", "bad"
    elif warnings:
        verdict, tone = f"完整性通过，{warnings} 项警告", "warn"
    else:
        verdict, tone = "完整性通过", "ok"
    if months:
        verdict += f" · {len(months)} 个月借贷都平衡"
    verdict += f" · 线索 {len(items)} 条" if items else " · 三层检验没发现值得单列的线索"

    integrity_rows = [[r.get("检查项", ""), r.get("对象", ""), r.get("结果", ""),
                       r.get("数值", ""), r.get("说明", "")]
                      for r in (integrity.get("rows") or [])]

    activity_rows: List[List[str]] = []
    for item in activities:
        activity_rows.append([
            str(item.get("业务活动", "")), str(item.get("域", "")),
            f"{item.get('凭证', 0):,}", str(item.get("笔数占比", "")),
            _money(item.get("金额", 0)), str(item.get("金额占比", "")),
            str(item.get("主要制单人", "")), str(item.get("该人占比", "")),
            f"{float(item.get('金额HHI') or 0):.3f}", f"{float(item.get('对手方HHI') or 0):.3f}",
            str(item.get("判读", "")), str(item.get("主要对手方", "")),
        ])

    lead_section: Dict[str, Any] = {
        "id": "leads",
        "title": "三、线索清单",
        "note": "三层各管一段：**总体层**看金额的数字分布（本福特），**个体层**看同一件"
                "事在金额与月份上有没有走样（指纹），**行为层**看分录本身合不合理"
                "（孤儿冲销）。每条线索都带统计证据与建议程序——它说的是「这里值得看」，"
                "不是「这里有错」。第二张表是交付用的：一张凭证一行，可以直接拿去抽凭。",
        "tables": [],
    }
    if items:
        lead_section["tables"].append({
            "title": "", "columns": lead_mod.LEAD_COLUMNS,
            "rows": lead_mod.lead_rows(items), "tone_column": 7,
        })
        voucher_table = lead_mod.voucher_rows(items)
        if voucher_table:
            lead_section["tables"].append({
                "title": f"涉及凭证（{len(voucher_table)} 张，按优先级排）",
                "columns": lead_mod.VOUCHER_COLUMNS, "rows": voucher_table,
                "tone_column": 3,
            })
    else:
        lead_section["placeholder"] = (
            "三层检验都没有发现值得单列的东西。这只说明「没有显性异常」——"
            "真正混在大流里的（高频、小额、规律化）要靠细节测试去函证、去盘点、去对合同。")

    notes: List[str] = []
    for name, info in (leads_doc.get("stats") or {}).items():
        if name == "本福特":
            for side, values in (info.get("sides") or {}).items():
                notes.append(f"本福特·{side} {values['samples']:,} 条样本，"
                             f"首位 MAD {values['first']['mad']}"
                             f"（{values['first']['verdict']}）"
                             f"、次位 MAD {values['second']['mad']}"
                             f"（{values['second']['verdict']}）")
            notes.extend(info.get("skipped") or [])
        elif name == "指纹":
            notes.append(f"指纹 {info.get('fingerprints', 0):,} 种，"
                         f"其中只出现一次 {info.get('singletons', 0):,} 种；"
                         f"跳变 {info.get('jumps', 0)} / 断档 {info.get('gaps', 0)}"
                         f" / 新增 {info.get('new', 0)}")
            notes.extend(info.get("skipped") or [])
        elif name == "孤儿冲销":
            notes.append(f"冲销凭证 {info.get('reversals', 0)} 张，"
                         f"闭环 {info.get('closed', 0)} 张、孤儿 {info.get('orphan', 0)} 张；"
                         f"另有 {info.get('negative_without_keyword', 0)} 张有负数金额但摘要"
                         f"不含冲销字样，本层看不到它们")
    for name, error in (leads_doc.get("errors") or {}).items():
        notes.append(f"{name} 没跑成：{error}")
    if notes:
        lead_section["footnotes"] = notes

    return {
        "title": "会计分录测试报告",
        "file": file_name,
        "feature": state.get("feature_name") or "会计分录测试",
        "meta": (f"{quality.get('vouchers', 0):,} 张凭证 / {quality.get('rows', 0):,} 行"
                 + (f" · {months[0]['period']}–{months[-1]['period']}" if months else "")),
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "verdict": {"tone": tone, "text": verdict},
        "sections": [
            {
                "id": "integrity",
                "title": "一、总体完整性",
                "note": "证的是总量守恒，不是明细正确——借贷同错的串户不破坏平衡。"
                        "这一节过不了，后面的画像与检验都建在流沙上；过了也不代表账没问题。",
                "tables": [{"columns": ["检查项", "对象", "结果", "数值", "说明"],
                            "rows": integrity_rows, "tone_column": 2}],
            },
            {
                "id": "profile",
                "title": "二、画像",
                "note": (f"这份账在讲什么：{stats.get('activities', 0)} 个业务活动，"
                         "笔数与金额怎么分布、每个活动由谁记的账、钱主要跟谁往来。"
                         "HHI（Σ 份额²）把「集中度」变成一个数：金额那一列说的是"
                         "「是不是少数几张凭证撑起这个活动」（0.25 以上算高度集中），"
                         "对手方那一列说的是「这个活动的钱主要往来于几个科目」。"),
                "tables": [{"columns": PROFILE_COLUMNS, "rows": activity_rows}],
            },
            lead_section,
        ],
    }


# ============================================================================
# HTML
# ============================================================================

_CSS = """
:root{--ink:#1a1d21;--dim:#5b6472;--line:#e3e6ea;--sunken:#f6f7f8}
*{box-sizing:border-box}
body{margin:0;background:#eef0f2;color:var(--ink);
  font:14px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.page{max-width:1180px;margin:0 auto;background:#fff;padding:34px 38px 46px;
  box-shadow:0 1px 3px rgba(0,0,0,.06)}
header h1{margin:0 0 6px;font-size:23px;font-weight:600;letter-spacing:.01em}
.meta{color:var(--dim);font-size:12.5px}
.verdict{margin:14px 0 26px;padding:10px 14px;border-radius:10px;font-size:13.5px;
  border:1px solid var(--line);background:var(--sunken)}
.verdict.ok{border-color:#bfe0cd;background:#f1f9f4;color:#1c7a4b}
.verdict.warn{border-color:#f0dcaa;background:#fdf8ec;color:#8a5a05}
.verdict.bad{border-color:#f2c4c0;background:#fdf2f1;color:#a2261e}
section{margin-top:32px}
section h2{font-size:16px;font-weight:600;margin:0 0 4px;padding-bottom:8px;
  border-bottom:2px solid var(--ink)}
.note{margin:10px 0 14px;color:var(--dim);font-size:12.5px;line-height:1.75}
h3.sub{font-size:13px;font-weight:600;margin:18px 0 6px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:12.5px;
  font-variant-numeric:tabular-nums}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top}
thead th{background:var(--sunken);font-weight:600;font-size:12px;color:var(--dim);
  white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbfc}
td.wrap{min-width:230px}
.ok{color:#1c7a4b}.warn{color:#a8630a}.bad{color:#b3261e;font-weight:600}.mute{color:#6b7280}
.empty{padding:16px;border:1px dashed #d7dbe0;border-radius:10px;color:var(--dim);
  font-size:13px;background:var(--sunken)}
ul.notes{margin:12px 0 0;padding-left:18px;color:var(--dim);font-size:11.5px;line-height:1.85}
footer{margin-top:34px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11.5px}
@media print{
  body{background:#fff}
  .page{box-shadow:none;max-width:none;padding:0}
  .tablewrap{overflow:visible;border:0}
  tr{break-inside:avoid}
}
"""


def _cell(value: Any, column: str, tone: str = "") -> str:
    text = html.escape(str(value if value is not None else ""))
    classes = []
    if tone:
        classes.append(tone)
    if column in _WIDE:
        classes.append("wrap")
    attr = f' class="{" ".join(classes)}"' if classes else ""
    return f"<td{attr}>{text}</td>"


def _html_table(table: Dict[str, Any]) -> str:
    columns: Sequence[str] = table.get("columns") or []
    rows: Sequence[Sequence[Any]] = table.get("rows") or []
    tone_col = table.get("tone_column", -1)
    title = table.get("title")
    head = "".join(f"<th>{html.escape(str(name))}</th>" for name in columns)
    body: List[str] = []
    for row in rows:
        cells = []
        for index, name in enumerate(columns):
            value = row[index] if index < len(row) else ""
            tone = TONE.get(str(value), "") if index == tone_col else ""
            cells.append(_cell(value, name, tone))
        body.append("<tr>" + "".join(cells) + "</tr>")
    block = (f"<div class='tablewrap'><table><thead><tr>{head}</tr></thead>"
             f"<tbody>{''.join(body)}</tbody></table></div>")
    return (f"<h3 class='sub'>{html.escape(title)}</h3>" if title else "") + block


def _rich(text: str) -> str:
    """说明文字允许 **加粗**（只这一种标记，其余一律转义）。"""
    return html.escape(str(text or "")).replace("**", "").replace("\n", "<br>")


def to_html(model: Dict[str, Any]) -> str:
    parts: List[str] = []
    for section in model.get("sections") or []:
        inner = "".join(_html_table(table) for table in (section.get("tables") or []))
        if section.get("placeholder"):
            inner += f'<div class="empty">{html.escape(section["placeholder"])}</div>'
        if section.get("footnotes"):
            items = "".join(f"<li>{html.escape(note)}</li>" for note in section["footnotes"])
            inner += f'<ul class="notes">{items}</ul>'
        parts.append(f'<section id="{section["id"]}">'
                     f'<h2>{html.escape(section["title"])}</h2>'
                     f'<p class="note">{_rich(section.get("note", ""))}</p>'
                     f"{inner}</section>")

    verdict = model["verdict"]
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(model["title"])} · {html.escape(model["file"])}</title>
<style>{_CSS}</style></head>
<body><div class="page">
<header>
  <h1>{html.escape(model["title"])}</h1>
  <div class="meta">{html.escape(model["file"])} · {html.escape(model["meta"])}</div>
  <div class="meta">生成于 {html.escape(model["generated_at"])}</div>
  <div class="verdict {verdict["tone"]}">{html.escape(verdict["text"])}</div>
</header>
{"".join(parts)}
<footer>{html.escape(model["feature"])} · 本报告由程序生成。每条线索都写清了统计证据与
建议程序，结论要人来下；这份报告里没有「综合得分」这种东西。</footer>
</div></body></html>"""
