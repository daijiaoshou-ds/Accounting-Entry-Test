# -*- coding: utf-8 -*-
"""会计分录测试报告：一份内容模型，两个渲染器（HTML / PDF）。

为什么不导 xlsx 了：这份东西的读者是项目组，"看完要签字、要归档"的是一份**报告**，
不是五张能随便排序的 sheet。表格照样在里面，但顺序是读的顺序——先证账能用
（完整性），再讲账在讲什么（画像），最后才是线索清单。

为什么不从 HTML 转 PDF：venv 里没有 weasyprint（Windows 上还要 GTK），装它等于
给一个只读报告加一条重依赖。所以两个渲染器各写一遍，但**共用同一份内容模型**
（`build()`），HTML 与 PDF 说的是同一件事；代价只是版式没法完全一致。
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# PDF 用的中文字体。reportlab 自带的那几款不含中文，必须注册一份 TTF。
_FONT = (Path(__file__).resolve().parents[2] / "assets" / "fonts" / "simsun.ttc")

# 结果词 → 语气（HTML 上色 / PDF 上色共用）
TONE = {"通过": "ok", "信息": "mute", "跳过": "mute", "警告": "warn", "问题": "bad"}
TONE_COLOR = {"ok": "#1c7a4b", "warn": "#a8630a", "bad": "#b3261e", "mute": "#6b7280"}

# 画像表的列序（HTML 与 PDF 都按它出，别各写一份）
PROFILE_COLUMNS = ["业务活动", "域", "凭证", "笔数占比", "金额", "金额占比",
                   "主要制单人", "该人占比", "金额HHI", "对手方HHI", "判读", "主要对手方"]

# PDF 里列宽权重（主要对手方最长，业务活动次之，数字列最窄）
COLUMN_WEIGHT = {
    "业务活动": 1.25, "域": 0.62, "凭证": 0.55, "笔数占比": 0.7, "金额": 1.05,
    "金额占比": 0.7, "主要制单人": 0.75, "该人占比": 0.7, "金额HHI": 0.62,
    "对手方HHI": 0.72, "判读": 1.5, "主要对手方": 2.0,
    "检查项": 0.9, "对象": 1.0, "结果": 0.55, "数值": 1.3, "说明": 3.2,
}


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


def build(state: Dict[str, Any], integrity: Dict[str, Any], profile: Dict[str, Any],
          generated_at: Optional[str] = None) -> Dict[str, Any]:
    """把三个阶段的结果拼成报告模型。渲染器只认这个结构。"""
    quality = integrity.get("quality") or {}
    activities = profile.get("activities") or []
    stats = profile.get("stats") or {}
    months = integrity.get("months") or []
    file_name = (state.get("input") or {}).get("filename") or ""

    problems, warnings = integrity.get("problems", 0), integrity.get("warnings", 0)
    if problems:
        verdict = {"tone": "bad", "text": f"完整性有 {problems} 项问题，先看第一节"}
    elif warnings:
        verdict = {"tone": "warn", "text": f"完整性通过，{warnings} 项警告"}
    else:
        verdict = {"tone": "ok", "text": "完整性通过"}

    span = ""
    if months:
        span = f"{months[0]['period']}–{months[-1]['period']}（{len(months)} 个月）"
        # 期间已经在 meta 行里了，结论只补"平不平"，不重复一遍日期
        verdict["text"] += f" · {len(months)} 个月借贷都平衡"

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

    return {
        "title": "会计分录测试报告",
        "file": file_name,
        "feature": state.get("feature_name") or "会计分录测试",
        "meta": (f"{quality.get('vouchers', 0):,} 张凭证 / {quality.get('rows', 0):,} 行"
                 + (f" · {span}" if span else "")),
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "verdict": verdict,
        "sections": [
            {
                "id": "integrity",
                "title": "一、总体完整性",
                "note": "证的是总量守恒，不是明细正确——借贷同错的串户不破坏平衡。"
                        "这一节过不了，后面的画像与检验都建在流沙上；过了也不代表账没问题。",
                "table": {"columns": ["检查项", "对象", "结果", "数值", "说明"],
                          "rows": integrity_rows, "tone_column": 2},
            },
            {
                "id": "profile",
                "title": "二、画像",
                "note": (f"这份账在讲什么：{stats.get('activities', 0)} 个业务活动，"
                         "笔数与金额怎么分布、每个活动由谁记的账、钱主要跟谁往来。"
                         "HHI（Σ 份额²）把「集中度」变成一个数：金额那一列说的是"
                         "「是不是少数几张凭证撑起这个活动」，0.25 以上算高度集中；"
                         "对手方那一列说的是「这个活动的钱主要往来于几个科目」。"),
                "table": {"columns": PROFILE_COLUMNS, "rows": activity_rows},
            },
            {
                "id": "leads",
                "title": "三、线索清单",
                "note": "这一节按线索给：每条线索带自己的统计量与建议程序，"
                        "交付时落到具体凭证。",
                "placeholder": "第 2 步（本福特 / 特征孤立 / 科目对 / 核算异常）尚未接入，"
                               "这一节暂时空着。",
            },
        ],
    }


# ============================================================================
# HTML
# ============================================================================

_CSS = """
:root{--ink:#1a1d21;--dim:#5b6472;--line:#e3e6ea;--sunken:#f6f7f8;--brand:#3f51b5}
*{box-sizing:border-box}
body{margin:0;background:#eef0f2;color:var(--ink);
  font:14px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.page{max-width:1080px;margin:0 auto;background:#fff;padding:34px 38px 46px;
  box-shadow:0 1px 3px rgba(0,0,0,.06)}
header h1{margin:0 0 6px;font-size:23px;font-weight:600;letter-spacing:.01em}
.meta{color:var(--dim);font-size:12.5px}
.verdict{margin:14px 0 26px;padding:10px 14px;border-radius:10px;font-size:13.5px;
  border:1px solid var(--line);background:var(--sunken)}
.verdict.ok{border-color:#bfe0cd;background:#f1f9f4;color:#1c7a4b}
.verdict.warn{border-color:#f0dcaa;background:#fdf8ec;color:#8a5a05}
.verdict.bad{border-color:#f2c4c0;background:#fdf2f1;color:#a2261e}
section{margin-top:30px}
section h2{font-size:16px;font-weight:600;margin:0 0 4px;padding-bottom:8px;
  border-bottom:2px solid var(--ink)}
.note{margin:10px 0 14px;color:var(--dim);font-size:12.5px;line-height:1.7}
table{width:100%;border-collapse:collapse;font-size:12.5px;
  font-variant-numeric:tabular-nums}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top}
thead th{background:var(--sunken);font-weight:600;font-size:12px;color:var(--dim);
  white-space:nowrap;position:sticky;top:0}
tbody tr:hover{background:#fafbfc}
td.num{white-space:nowrap}
.ok{color:#1c7a4b}.warn{color:#a8630a}.bad{color:#b3261e;font-weight:600}.mute{color:#6b7280}
.empty{padding:16px;border:1px dashed #d7dbe0;border-radius:10px;color:var(--dim);
  font-size:13px;background:var(--sunken)}
footer{margin-top:34px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11.5px}
@media print{
  body{background:#fff}
  .page{box-shadow:none;max-width:none;padding:0}
  section{break-inside:auto}
  thead th{position:static}
  tr{break-inside:avoid}
}
"""


def _cell(value: Any, column: str, tone: str = "") -> str:
    text = html.escape(str(value if value is not None else ""))
    if tone:
        return f'<td class="{tone}">{text}</td>'
    if column in ("金额", "凭证", "金额HHI", "对手方HHI", "笔数占比", "金额占比",
                  "该人占比", "数值"):
        return f'<td class="num">{text}</td>'
    return f"<td>{text}</td>"


def _html_table(table: Dict[str, Any]) -> str:
    columns: Sequence[str] = table.get("columns") or []
    rows: Sequence[Sequence[Any]] = table.get("rows") or []
    tone_col = table.get("tone_column", -1)
    head = "".join(f"<th>{html.escape(str(name))}</th>" for name in columns)
    body: List[str] = []
    for row in rows:
        cells = []
        for index, name in enumerate(columns):
            value = row[index] if index < len(row) else ""
            tone = TONE.get(str(value), "") if index == tone_col else ""
            cells.append(_cell(value, name, tone))
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


def to_html(model: Dict[str, Any]) -> str:
    """自包含的单文件 HTML：没有外链、没有脚本，能直接发出去、也能直接打印。"""
    parts: List[str] = []
    for section in model.get("sections") or []:
        inner = ""
        if section.get("table"):
            inner = _html_table(section["table"])
        if section.get("placeholder"):
            inner += f'<div class="empty">{html.escape(section["placeholder"])}</div>'
        parts.append(f'<section id="{section["id"]}">'
                     f'<h2>{html.escape(section["title"])}</h2>'
                     f'<p class="note">{html.escape(section.get("note", ""))}</p>'
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
<footer>{html.escape(model["feature"])} · 本报告由程序生成，表格中的每个数字都能在
原表与产物里找到出处。</footer>
</div></body></html>"""


# ============================================================================
# PDF
# ============================================================================

def to_pdf(model: Dict[str, Any], path: Path) -> Path:
    """横版 A4。表格列多，竖版会挤成一列一个字。"""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    if "SimSun" not in pdfmetrics.getRegisteredFontNames():
        if not _FONT.is_file():
            raise ValueError(f"缺少中文字体：{_FONT}（PDF 没法生成）")
        pdfmetrics.registerFont(TTFont("SimSun", str(_FONT), subfontIndex=0))

    title = ParagraphStyle("t", fontName="SimSun", fontSize=17, leading=22,
                           spaceAfter=4, textColor=colors.HexColor("#1a1d21"))
    meta = ParagraphStyle("m", fontName="SimSun", fontSize=9, leading=13,
                          textColor=colors.HexColor("#5b6472"))
    h2 = ParagraphStyle("h2", fontName="SimSun", fontSize=12.5, leading=17,
                        spaceBefore=10, spaceAfter=2, textColor=colors.HexColor("#1a1d21"))
    note = ParagraphStyle("n", fontName="SimSun", fontSize=8.5, leading=12.5,
                          textColor=colors.HexColor("#5b6472"), spaceAfter=6,
                          alignment=TA_LEFT)
    cell = ParagraphStyle("c", fontName="SimSun", fontSize=6.8, leading=9)
    head = ParagraphStyle("ch", fontName="SimSun", fontSize=6.8, leading=9,
                          textColor=colors.HexColor("#3d434b"))

    page = landscape(A4)
    width = page[0] - 2 * 14 * mm
    doc = SimpleDocTemplate(str(path), pagesize=page,
                            leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=13 * mm, bottomMargin=13 * mm,
                            title=model["title"], author="会计分录测试")
    story: List[Any] = [
        Paragraph(html.escape(model["title"]), title),
        Paragraph(html.escape(f'{model["file"]} · {model["meta"]}'), meta),
        Paragraph(html.escape(f'生成于 {model["generated_at"]}'), meta),
        Spacer(1, 6),
        Paragraph(html.escape(model["verdict"]["text"]), note),
    ]

    for section in model.get("sections") or []:
        block: List[Any] = [Paragraph(html.escape(section["title"]), h2),
                            Paragraph(html.escape(section.get("note", "")), note)]
        table = section.get("table")
        if table and table.get("rows"):
            columns = list(table["columns"])
            weights = [COLUMN_WEIGHT.get(name, 1.0) for name in columns]
            total = sum(weights)
            widths = [width * weight / total for weight in weights]
            data = [[Paragraph(html.escape(str(name)), head) for name in columns]]
            tone_col = table.get("tone_column", -1)
            for row in table["rows"]:
                cells = []
                for index in range(len(columns)):
                    value = row[index] if index < len(row) else ""
                    text = html.escape(str(value))
                    tone = TONE.get(str(value), "") if index == tone_col else ""
                    color = TONE_COLOR.get(tone)
                    if color:
                        text = f'<font color="{color}">{text}</font>'
                    cells.append(Paragraph(text, cell))
                data.append(cells)
            flow = Table(data, colWidths=widths, repeatRows=1)
            flow.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f5")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#c9ced6")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e3e6ea")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]))
            block.append(flow)
        if section.get("placeholder"):
            block.append(Paragraph(html.escape(section["placeholder"]), note))
        if not table or len(table["rows"]) <= 12:
            story.append(KeepTogether(block))
        else:
            # 长表不能整体 KeepTogether：它比一页还高，会先撑出一页空白再开表
            story.extend(block)

    doc.build(story)
    return path
