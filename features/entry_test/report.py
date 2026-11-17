# -*- coding: utf-8 -*-
"""会计分录测试报告（HTML）。

这份东西的读者是项目组：先证账能用（完整性），再讲账在讲什么（画像），最后才是
线索。顺序就是读的顺序，一屏一件事。

四条排版上的决定，都是被"看不懂"逼出来的：

1. **完整性只留四行**。逐月逐序列列十几二十行，人看到一片"通过"就翻过去了，真正
   异常的那一行反而被淹掉；异常写进「说明」里，点名到月。
2. **画像拆成两节**：总览回答"做了哪些活动、多少钱、谁记的"，集中度回答"这个
   活动是不是少数几张凭证撑起来的"。挤在一张十几列的表里，两件事互相盖住。
3. **三层线索分开报，本福特画图**。九个数字的分布表读不出"偏在哪"，柱状图一眼
   就能看出 1 太多、9 太少。三层各管一段（总体看数字分布、个体看节律、核算看
   冲销），合在一张表里还会让人以为它们可比。
4. **凭证明细不进 HTML**（一屏几十个凭证号没人看）。它随报告导出的 Excel 走：
   线索清单、涉及凭证、完整性逐条、业务活动全表、本福特逐位分布。

报告是**自包含**的：样式内联、图是 SVG、无脚本无外链，一个文件发出去就能看，
也能用浏览器的"打印成 PDF"。
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from . import benford as benford_mod
from . import leads as lead_mod

# 结果词 → 语气
TONE = {"通过": "ok", "信息": "mute", "跳过": "mute", "警告": "warn", "问题": "bad",
        "高": "bad", "中": "warn", "低": "mute"}

# 这些列的内容长，给最小宽度，免得被挤成一列一个字
_WIDE = {"证据", "建议程序", "说明", "判读", "主要对手方"}

LAYER_TITLES = {
    "总体层": "总体层 · 本福特：金额的数字分布",
    "个体层": "个体层 · 特征孤立：同一件事的节律",
    "行为层": "核算异常 · 孤儿冲销：冲了却找不到被冲的",
}

# 层 id → （节标题序号, 统计里的 key）
_LAYER_META = {
    "个体层": ("三之二", "指纹"),
    "行为层": ("三之三", "孤儿冲销"),
}


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value or "")


# ============================================================================
# 报告模型
# ============================================================================

def build(state: Dict[str, Any], integrity: Dict[str, Any], profile: Dict[str, Any],
          leads_doc: Optional[Dict[str, Any]] = None,
          generated_at: Optional[str] = None) -> Dict[str, Any]:
    """把四个阶段的结果拼成报告模型。渲染器只认这个结构。"""
    leads_doc = leads_doc or {}
    quality = integrity.get("quality") or {}
    activities = profile.get("activities") or []
    months = integrity.get("months") or []
    items = leads_doc.get("leads") or []
    errors = leads_doc.get("errors") or {}
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

    return {
        "title": "会计分录测试报告",
        "file": file_name,
        "feature": state.get("feature_name") or "会计分录测试",
        "meta": (f"{quality.get('vouchers', 0):,} 张凭证 / {quality.get('rows', 0):,} 行"
                 + (f" · {months[0]['period']}–{months[-1]['period']}" if months else "")),
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "verdict": {"tone": tone, "text": verdict},
        "sections": [
            _integrity_section(integrity),
            _profile_section(profile, activities),
            _leads_intro(items, errors),
            _benford_section(leads_doc),
            _layer_section("个体层", leads_doc),
            _layer_section("行为层", leads_doc),
        ],
    }


def _integrity_section(integrity: Dict[str, Any]) -> Dict[str, Any]:
    rows = integrity.get("summary") or []
    return {
        "id": "integrity",
        "title": "一、总体完整性",
        "note": "证的是总量守恒，不是明细正确——借贷同错的串户不破坏平衡。"
                "这一节过不了，后面的画像与检验都建在流沙上；过了也不代表账没问题。",
        "blocks": [{
            "table": {"columns": ["检查项", "结果", "数值", "说明"],
                      "rows": [[r.get("检查项", ""), r.get("结果", ""), r.get("数值", ""),
                                r.get("说明", "")] for r in rows],
                      "tone_column": 1},
        }],
    }


def _profile_section(profile: Dict[str, Any],
                     activities: List[Dict[str, Any]]) -> Dict[str, Any]:
    stats = profile.get("stats") or {}
    overview: List[List[str]] = []
    concentrate: List[List[str]] = []
    for item in activities:
        overview.append([
            str(item.get("业务活动", "")), str(item.get("域", "")),
            f"{item.get('凭证', 0):,}", str(item.get("笔数占比", "")),
            _money(item.get("金额", 0)), str(item.get("金额占比", "")),
            str(item.get("主要制单人", "")), str(item.get("该人占比", "")),
        ])
        amount_hhi = float(item.get("金额HHI") or 0)
        counter_hhi = float(item.get("对手方HHI") or 0)
        concentrate.append([
            str(item.get("业务活动", "")),
            f"{amount_hhi:.3f}", f"{counter_hhi:.3f}",
            str(item.get("判读", "")), str(item.get("主要对手方", "")),
        ])

    return {
        "id": "profile",
        "title": "二、画像",
        "note": "回答三个问题：这份账都做了哪些经济活动、这些活动的钱怎么分布、"
                "记录是谁做的。业务活动来自上游「序时账清洗」的业务分类——同一份账"
                "只有一套分类。",
        "stats": [
            {"label": "业务活动", "value": f"{stats.get('activities', 0)} 个"},
            {"label": "凭证", "value": f"{stats.get('vouchers', 0):,} 张"},
            {"label": "金额", "value": _money(stats.get("amount", 0))},
            {"label": "未分类", "value": f"{stats.get('unclassified', 0):,} 张"},
        ],
        "blocks": [
            {
                "heading": "2.1 总览",
                "note": "哪些活动、多少笔、多少钱、谁记的账。按金额从大到小排。",
                "table": {"columns": ["业务活动", "域", "凭证", "笔数占比", "金额",
                                      "金额占比", "主要制单人", "该人占比"],
                          "rows": overview, "numeric_from": 2},
            },
            {
                "heading": "2.2 集中度",
                "note": "HHI = Σ 份额²（0–1，越大越集中）。金额那一列说的是「这个"
                        "活动是不是少数几张凭证撑起来的」（0.25 以上算高度集中）；"
                        "对手方那一列说的是「这个活动的钱主要往来于几个科目」。"
                        "它不下结论，只告诉你抽样必须盖住谁。",
                "table": {"columns": ["业务活动", "金额HHI", "对手方HHI",
                                      "判读", "主要对手方"],
                          "rows": concentrate, "numeric_from": 1},
            },
        ],
    }


def _leads_intro(items: List[Dict[str, Any]],
                 errors: Dict[str, str]) -> Dict[str, Any]:
    stats = [
        {"label": "线索", "value": f"{len(items)} 条"},
        {"label": "涉及凭证", "value": f"{len(lead_mod.voucher_rows(items)):,} 张"},
        {"label": "高优先级", "value": f"{sum(1 for i in items if i['优先级'] == '高')} 条"},
    ]
    note = ("三层各管一段，**不能互相比较**：一条本福特超标和一条孤儿冲销不是同一种"
            "东西，排成一个序会给出「第 3 名比第 7 名更该查」的错觉。每条线索都带"
            "统计证据与建议程序——它说的是「这里值得看」，不是「这里有错」。")
    blocks: List[Dict[str, Any]] = []
    if not items:
        blocks.append({"placeholder":
                       "三层检验都没有发现值得单列的东西。这只说明「没有显性异常」——"
                       "真正混在大流里的（高频、小额、规律化）要靠细节测试去函证、"
                       "去盘点、去对合同。"})
    if errors:
        blocks.append({"placeholder":
                       "；".join(f"{name} 没跑成（{msg}）" for name, msg in errors.items())})
    return {
        "id": "leads",
        "title": "三、异常线索",
        "note": note,
        "stats": stats,
        "blocks": blocks,
    }


def _benford_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    stats = (leads_doc.get("stats") or {}).get("本福特") or {}
    sides = stats.get("sides") or {}
    rows = [item for item in (leads_doc.get("leads") or []) if item["层"] == "总体层"]

    chips: List[Dict[str, str]] = []
    for side, info in sides.items():
        chips.append({"label": f"{side}样本", "value": f"{info.get('samples', 0):,} 条"})
        chips.append({"label": f"{side}首位 MAD",
                      "value": f"{info['first']['mad']} · {info['first']['verdict']}"})
        chips.append({"label": f"{side}次位 MAD",
                      "value": f"{info['second']['mad']} · {info['second']['verdict']}"})

    blocks: List[Dict[str, Any]] = []
    if sides:
        blocks.append({"charts": [{"side": side, "data": info}
                                  for side, info in sides.items()]})
    if rows:
        blocks.append({"table": _layer_table(rows)})
    else:
        blocks.append({"placeholder":
                       "本福特没有出线索：整账的数字分布没有偏到值得往下查的程度。"
                       "这是它正常工作的样子——它本来就不是每次都该说话。"})

    footnotes = list(stats.get("skipped") or [])
    footnotes.append(
        "判读用 Nigrini 的 MAD 分档：首位 ≤0.006 接近、≤0.012 可接受、≤0.015 边缘、"
        "再高不可接受；次位更严（0.008 / 0.010 / 0.012）。")
    footnotes.append(
        "只取 ≥10 元的金额并取绝对值，借、贷分开跑：两边业务性质不同（收款 vs 付款），"
        "混在一起两个分布会互相污染。")

    return {
        "id": "layer_benford",
        "title": "三之一 · " + LAYER_TITLES["总体层"],
        "note": "自然产生的金额，首位是 1 的最多、9 的最少。整账偏离**不说明有错**，"
                "只说明数字不像自然长出来的；真正有用的是它把注意力引到某个业务活动"
                "的某个数字上——所以每条线索都写了「谁贡献了这个超额」。",
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _layer_section(layer: str, leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    number, stats_key = _LAYER_META[layer]
    rows = [item for item in (leads_doc.get("leads") or []) if item["层"] == layer]
    info = (leads_doc.get("stats") or {}).get(stats_key) or {}

    footnotes: List[str] = []
    if layer == "个体层":
        chips = [
            {"label": "指纹", "value": f"{info.get('fingerprints', 0):,} 种"},
            {"label": "金额跳变", "value": f"{info.get('jumps', 0)} 条"},
            {"label": "月度断档", "value": f"{info.get('gaps', 0)} 条"},
            {"label": "末期新增", "value": f"{info.get('new', 0)} 条"},
        ]
        footnotes.append(
            f"只出现一次的指纹 {info.get('singletons', 0):,} 种——它们没有节律可比，"
            "这一层对它们失明（偶发业务本来就该偶发）。")
        footnotes.extend(info.get("skipped") or [])
        note = ("同一件事（摘要去掉数字 + 同一套科目方向）在一年里反复发生，金额和"
                "月份应该有节律。这里找的是破坏节律的那几次：金额突然跳上去、"
                "某个月该有却没有、只在最后一个月冒出来。节律断在哪里，就去查那个月"
                "为什么少了一笔。")
    else:
        chips = [
            {"label": "冲销凭证", "value": f"{info.get('reversals', 0)} 张"},
            {"label": "闭环放过", "value": f"{info.get('closed', 0)} 张"},
            {"label": "孤儿冲销", "value": f"{info.get('orphan', 0)} 张"},
        ]
        footnotes.append(
            f"另有 {info.get('negative_without_keyword', 0)} 张凭证有负数金额，但摘要里"
            "没有冲销字样——本层靠关键词识别冲销，看不到它们。")
        note = ("冲销只有两种：**闭环**（本年暂估 → 本年冲销，对本年没有影响，放过）"
                "和**孤儿**（本年只有冲销，整年找不到等额的那笔原始分录）。孤儿的含义"
                "是「冲掉的东西不在这本账上」——多半是冲往年。跨年的收入确认、成本"
                "计提尤其要看：拿这一笔去对上年的账，看上年的暂估是怎么处理的。")

    blocks: List[Dict[str, Any]] = []
    if rows:
        blocks.append({"table": _layer_table(rows)})
    else:
        blocks.append({"placeholder": "这一层没有出线索。"})

    return {
        "id": f"layer_{stats_key}",
        "title": f"{number} · {LAYER_TITLES[layer]}",
        "note": note,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _layer_table(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一层一张表：层名已经在标题里，不再重复一列。"""
    body = []
    for item in rows:
        # 只放第一张 + 条数：凭证号带月份前缀，一列放不下两三个；全量在 Excel
        vouchers = item["凭证"]
        shown = vouchers[0] + (f" 等 {len(vouchers)} 张" if len(vouchers) > 1 else "") \
            if vouchers else "—"
        body.append([
            item["类型"], item["主体"], f"{item['笔数']:,}",
            f"{item['金额']:,.2f}" if item["金额"] else "—",
            item["证据"], shown or "—", item["建议程序"], item["优先级"],
        ])
    return {"columns": ["类型", "主体", "笔数", "金额", "证据", "涉及凭证",
                        "建议程序", "优先级"],
            # 列宽写死：不写的话浏览器会按内容自由分配，结果"证据"那段长文字把
            # 「主体」挤成竖排，一眼看不出这条线索是关于谁的
            "widths": ["12%", "13%", "5%", "9%", "23%", "12%", "20%", "6%"],
            "rows": body, "tone_column": 7, "numeric": [2, 3]}


# ============================================================================
# HTML
# ============================================================================

_CSS = """
:root{--ink:#1a1d21;--dim:#5b6472;--line:#e3e6ea;--sunken:#f6f7f8}
*{box-sizing:border-box}
body{margin:0;background:#eef0f2;color:var(--ink);
  font:14px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.page{max-width:1120px;margin:0 auto;background:#fff;padding:34px 40px 46px;
  box-shadow:0 1px 3px rgba(0,0,0,.06)}
header h1{margin:0 0 6px;font-size:23px;font-weight:600;letter-spacing:.01em}
.meta{color:var(--dim);font-size:12.5px}
.verdict{margin:14px 0 26px;padding:10px 14px;border-radius:10px;font-size:13.5px;
  border:1px solid var(--line);background:var(--sunken)}
.verdict.ok{border-color:#bfe0cd;background:#f1f9f4;color:#1c7a4b}
.verdict.warn{border-color:#f0dcaa;background:#fdf8ec;color:#8a5a05}
.verdict.bad{border-color:#f2c4c0;background:#fdf2f1;color:#a2261e}
section{margin-top:34px}
section h2{font-size:16px;font-weight:600;margin:0 0 4px;padding-bottom:8px;
  border-bottom:2px solid var(--ink)}
section h2 .count{float:right;font-size:11.5px;font-weight:400;color:var(--dim);
  padding-top:4px}
.note{margin:10px 0 12px;color:var(--dim);font-size:12.5px;line-height:1.8}
h3.sub{font-size:13px;font-weight:600;margin:20px 0 4px}
h3.sub small{font-weight:400;color:var(--dim);margin-left:8px;font-size:11.5px}
.statrow{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 4px}
.stat{flex:1 1 150px;border:1px solid var(--line);border-radius:10px;padding:8px 12px;
  background:var(--sunken)}
.stat b{display:block;font-size:16px;font-weight:600;line-height:1.35;
  font-variant-numeric:tabular-nums}
.stat span{color:var(--dim);font-size:11.5px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:12.5px;
  font-variant-numeric:tabular-nums}
table.fixed{table-layout:fixed}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:top}
thead th{background:var(--sunken);font-weight:600;font-size:12px;color:var(--dim);
  white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbfc}
td.num,th.num{text-align:right;white-space:nowrap}
td.wrap{max-width:300px;white-space:normal}
.ok{color:#1c7a4b}.warn{color:#a8630a}.bad{color:#b3261e;font-weight:600}.mute{color:#6b7280}
.empty{padding:14px 16px;border:1px dashed #d7dbe0;border-radius:10px;color:var(--dim);
  font-size:12.5px;background:var(--sunken);margin-top:8px}
ul.notes{margin:12px 0 0;padding-left:18px;color:var(--dim);font-size:11.5px;line-height:1.85}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));
  gap:14px;margin-top:12px}
.charts figure{margin:0;border:1px solid var(--line);border-radius:10px;
  padding:10px 12px 6px;background:#fff}
footer{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);
  color:var(--dim);font-size:11.5px;line-height:1.8}
@media print{
  body{background:#fff}
  .page{box-shadow:none;max-width:none;padding:0}
  .tablewrap{overflow:visible;border:0}
  tr{break-inside:avoid}
  figure{break-inside:avoid}
}
"""


def _cell(value: Any, column: str, tone: str = "", numeric: bool = False) -> str:
    text = html.escape(str(value if value is not None else ""))
    classes = []
    if tone:
        classes.append(tone)
    if column in _WIDE:
        classes.append("wrap")
    if numeric:
        classes.append("num")
    attr = f' class="{" ".join(classes)}"' if classes else ""
    return f"<td{attr}>{text}</td>"


def _html_table(table: Dict[str, Any]) -> str:
    columns: Sequence[str] = table.get("columns") or []
    rows: Sequence[Sequence[Any]] = table.get("rows") or []
    tone_col = table.get("tone_column", -1)
    # 哪些列是数字：右对齐 + 不换行。只按"从第几列起"推会误伤后面的长文字列
    # （涉及凭证、建议程序都是文字，被 nowrap 之后会溢出到隔壁格子上）
    numeric = set(table.get("numeric") or range(table.get("numeric_from", len(columns)),
                                                len(columns)))
    title = table.get("title")
    widths = table.get("widths") or []
    colgroup = ("<colgroup>" + "".join(f"<col style='width:{w}'>" for w in widths)
                + "</colgroup>") if widths else ""
    # 表头跟数字列一起右对齐：否则"凭证"靠左、下面的 3,858 靠右，像错位
    head = "".join(
        ("<th class='num'>" if index in numeric else "<th>") + html.escape(str(name))
        + "</th>" for index, name in enumerate(columns))
    body: List[str] = []
    for row in rows:
        cells = []
        for index, name in enumerate(columns):
            value = row[index] if index < len(row) else ""
            tone = TONE.get(str(value), "") if index == tone_col else ""
            cells.append(_cell(value, name, tone, numeric=index in numeric))
        body.append("<tr>" + "".join(cells) + "</tr>")
    klass = " class='fixed'" if widths else ""
    block = (f"<div class='tablewrap'><table{klass}>{colgroup}"
             f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>")
    return (f"<h3 class='sub'>{html.escape(title)}</h3>" if title else "") + block


def _svg_chart(title: str, obs: Dict[int, int], expect: Dict[int, float],
               digits: Sequence[int], note: str = "") -> str:
    """一个数字分布图：每个数字两根柱（实际/期望），高度都是占比。

    自己画 SVG 不引图表库，是为了让报告保持单文件：外链一个 echarts，报告离开
    网络就废了，而报告是要发给客户、要归档的。
    """
    # 尺寸按"两列并排时一格多宽"来定（1120 页宽 → 每格约 500）：
    # 画 330 再让它 scale 上去，字号会被放大成大字报；正好画成实际宽度，
    # 文字就是设计时的字号。
    width, height = 500, 208
    left, right, top, bottom = 44, 14, 30, 38
    plot_h = height - top - bottom
    baseline = height - bottom
    total = sum(obs.values()) or 1

    values = [obs.get(d, 0) / total for d in digits]
    exps = [expect.get(d, 0.0) for d in digits]
    peak = max(max(values or [0.0]), max(exps or [0.0])) or 0.01
    ymax = peak * 1.18

    step = (width - left - right) / len(digits)
    bar = min(14.0, step * 0.32)
    parts: List[str] = [
        f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' "
        f"aria-label='{html.escape(title)}'>",
        f"<text x='{left - 6}' y='15' font-size='12.5' fill='#1a1d21'>"
        f"{html.escape(title)}</text>",
    ]
    if note:
        parts.append(f"<text x='{width - right}' y='15' font-size='11' fill='#5b6472' "
                     f"text-anchor='end'>{html.escape(note)}</text>")
    for frac in (0.0, 0.5, 1.0):
        y = baseline - frac * plot_h
        parts.append(f"<line x1='{left - 4}' y1='{y:.1f}' x2='{width - right}' "
                     f"y2='{y:.1f}' stroke='#e3e6ea' stroke-width='1'/>")
        parts.append(f"<text x='{left - 7}' y='{y + 3.4:.1f}' font-size='10' "
                     f"fill='#8a929c' text-anchor='end'>{ymax * frac * 100:.0f}%</text>")

    for index, digit in enumerate(digits):
        cx = left + step * (index + 0.5)
        exp_h = exps[index] / ymax * plot_h
        obs_h = values[index] / ymax * plot_h
        parts.append(f"<rect x='{cx - bar - 1:.1f}' y='{baseline - exp_h:.1f}' "
                     f"width='{bar:.1f}' height='{max(exp_h, 0.6):.1f}' fill='#ccd3dc'/>")
        parts.append(f"<rect x='{cx + 1:.1f}' y='{baseline - obs_h:.1f}' "
                     f"width='{bar:.1f}' height='{max(obs_h, 0.6):.1f}' fill='#3d4450'/>")
        parts.append(f"<text x='{cx:.1f}' y='{baseline + 15}' font-size='11' "
                     f"fill='#5b6472' text-anchor='middle'>{digit}</text>")

    parts.append(f"<line x1='{left - 4}' y1='{baseline}' x2='{width - right}' "
                 f"y2='{baseline}' stroke='#b9c0c8' stroke-width='1'/>")
    parts.append(f"<text x='{left - 6}' y='{height - 6}' font-size='10' fill='#8a929c'>"
                 f"深色=实际 · 浅色=本福特期望</text>")
    parts.append("</svg>")
    return "".join(parts)


def _html_charts(charts: List[Dict[str, Any]]) -> str:
    figures: List[str] = []
    for chart in charts:
        side = chart.get("side") or ""
        data = chart.get("data") or {}
        for key, label, digits, expect in (
                ("first", "首位", list(range(1, 10)), benford_mod.FIRST_EXPECT),
                ("second", "次位", list(range(0, 10)), benford_mod.SECOND_EXPECT)):
            block = data.get(key) or {}
            obs = {int(k): int(v) for k, v in (block.get("obs") or {}).items()}
            note = f"MAD {block.get('mad')} · {block.get('verdict')}"
            svg = _svg_chart(f"{side} · {label}", obs, expect, digits, note)
            # 标题在 SVG 里，figure 里不要再写一遍（会变成两行一模一样的字）
            figures.append(f"<figure>{svg}</figure>")
    return f"<div class='charts'>{''.join(figures)}</div>"


def _rich(text: str) -> str:
    """说明文字允许 **加粗**（只这一种标记，其余一律转义）。"""
    return html.escape(str(text or "")).replace("**", "").replace("\n", "<br>")


def _html_stats(stats: List[Dict[str, Any]]) -> str:
    if not stats:
        return ""
    cells = "".join(
        f"<div class='stat'><span>{html.escape(str(item.get('label', '')))}</span>"
        f"<b>{html.escape(str(item.get('value', '')))}</b></div>" for item in stats)
    return f"<div class='statrow'>{cells}</div>"


def _html_block(block: Dict[str, Any]) -> str:
    out: List[str] = []
    if block.get("heading"):
        out.append(f"<h3 class='sub'>{html.escape(block['heading'])}</h3>")
    if block.get("note"):
        out.append(f"<p class='note'>{_rich(block['note'])}</p>")
    if block.get("table"):
        out.append(_html_table(block["table"]))
    if block.get("charts"):
        out.append(_html_charts(block["charts"]))
    if block.get("placeholder"):
        out.append(f"<div class='empty'>{_rich(block['placeholder'])}</div>")
    return "".join(out)


def to_html(model: Dict[str, Any]) -> str:
    parts: List[str] = []
    for section in model.get("sections") or []:
        count = section.get("count")
        # 节标题右侧标条数：一眼知道这一层有没有东西
        badge = f"<span class='count'>{count} 条线索</span>" if count is not None else ""
        inner = [_html_stats(section.get("stats") or [])]
        for block in section.get("blocks") or []:
            inner.append(_html_block(block))
        if section.get("footnotes"):
            items = "".join(f"<li>{html.escape(note)}</li>" for note in section["footnotes"])
            inner.append(f'<ul class="notes">{items}</ul>')
        parts.append(f'<section id="{section["id"]}">'
                     f'<h2>{html.escape(section["title"])}{badge}</h2>'
                     f'<p class="note">{_rich(section.get("note", ""))}</p>'
                     f"{''.join(inner)}</section>")

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
建议程序，结论要人来下；这份报告里没有「综合得分」这种东西。<br>
凭证明细（哪张凭证、被哪几条线索命中）与逐条明细在随报告导出的 Excel 里。</footer>
</div></body></html>"""
