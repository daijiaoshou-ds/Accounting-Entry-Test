# -*- coding: utf-8 -*-
"""会计分录测试报告（HTML）。

这份东西的读者是项目组：先证账能用（完整性），再讲账在讲什么（画像），最后才是
线索。顺序就是读的顺序，一屏一件事。

四条排版上的决定，都是被"看不懂"逼出来的：

1. **完整性只留四行**。逐月逐序列列十几二十行，人看到一片"通过"就翻过去了，真正
   异常的那一行反而被淹掉；异常写进「说明」里，点名到月。
2. **画像拆成两节**：总览回答"做了哪些活动、多少钱、谁记的"，集中度回答"这个
   活动是不是少数几张凭证撑起来的"。挤在一张十几列的表里，两件事互相盖住。
3. **三层线索分开报，且每层只给总览**。九个数字的分布表读不出"偏在哪"，柱状图
   一眼就能看出 1 太多、9 太少。三层各管一段（总体看数字分布、个体看金额与科目
   组合、核算看冲销），合在一张表里会让人以为它们可比。每层只给"几条、几张、
   多少钱、最突出的是哪一件"——一条线索的证据要占三四行，几十条就把报告变成一张
   没人读完的表，具体是哪几张归 Excel。
4. **凭证明细不进 HTML**（一屏几十个凭证号没人看）。Excel 也不是"报告的附件"：
   它就是用户传进来的那份账，末尾多了「是否异常」「异常原因」几列，筛一下就是
   全部问题凭证——报告回答"该怎么看"，表格回答"是哪几行"。

报告是**自包含**的：样式内联、图是 SVG、无脚本无外链，一个文件发出去就能看，
也能用浏览器的"打印成 PDF"。
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from . import benford as benford_mod
from . import leads as lead_mod

# 结果词 → 语气
TONE = {"通过": "ok", "信息": "mute", "跳过": "mute", "警告": "warn", "问题": "bad",
        "高": "bad", "中": "warn", "低": "mute"}

# 这些列的内容长，给最小宽度，免得被挤成一列一个字
_WIDE = {"证据", "建议程序", "说明", "判读", "主要对手方"}

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

    hits = len(lead_mod.voucher_rows(items))
    high = sum(1 for item in items if item["优先级"] == "高")
    kpis = [
        {"label": "凭证", "value": f"{quality.get('vouchers', 0):,}",
         "note": f"{quality.get('rows', 0):,} 行分录"},
        {"label": "业务活动", "value": f"{len(activities)}",
         "note": "上游清洗判出的桶"},
        {"label": "线索", "value": f"{len(items)}",
         "note": f"落在 {hits:,} 张凭证上"},
        {"label": "高优先级", "value": f"{high}",
         "note": "先看这几条" if high else "没有",
         "tone": "bad" if high else ""},
    ]

    return {
        "title": "会计分录测试报告",
        "file": file_name,
        "feature": state.get("feature_name") or "会计分录测试",
        "meta": (f"{quality.get('vouchers', 0):,} 张凭证 / {quality.get('rows', 0):,} 行"
                 + (f" · {months[0]['period']}–{months[-1]['period']}" if months else "")),
        "generated_at": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "verdict": {"tone": tone, "text": verdict},
        "kpis": kpis,
        "sections": [
            _integrity_section(integrity),
            _profile_section(profile, activities),
            _leads_intro(items, errors, leads_doc.get("whitewash") or {}),
            _benford_section(leads_doc),
            _amount_section(leads_doc),
            _combo_section(leads_doc),
            _abnormal_section(leads_doc),
        ],
    }


def _integrity_section(integrity: Dict[str, Any]) -> Dict[str, Any]:
    rows = integrity.get("summary") or []
    return {
        "id": "integrity",
        "no": "01",
        "title": "总体完整性",
        "note": "证的是总量守恒，不是明细正确——借贷同错的串户不破坏平衡。"
                "这一节过不了，后面的画像与检验都建在流沙上；过了也不代表账没问题。",
        "blocks": [{
            "table": {"columns": ["检查项", "结果", "数值", "说明"],
                      "rows": [[r.get("检查项", ""), r.get("结果", ""), r.get("数值", ""),
                                r.get("说明", "")] for r in rows],
                      "badges": [1],
                      "widths": ["14%", "9%", "26%", "51%"]},
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
            # 活动级金额取整：审计看到"元"就够了，两位小数只是占宽度
            f"{float(item.get('金额', 0) or 0):,.0f}", str(item.get("金额占比", "")),
            str(item.get("主要制单人", "")), str(item.get("该人占比", "")),
        ])
        amount_hhi = float(item.get("金额HHI") or 0)
        counter_hhi = float(item.get("对手方HHI") or 0)
        concentrate.append([
            str(item.get("业务活动", "")),
            f"{amount_hhi:.3f}", f"{counter_hhi:.3f}",
            str(item.get("主要对手方", "")), str(item.get("判读", "")),
        ])

    return {
        "id": "profile",
        "no": "02",
        "title": "画像",
        "note": "回答三个问题：这份账都做了哪些经济活动、这些活动的钱怎么分布、"
                "记录是谁做的。业务活动来自上游「序时账清洗」的业务分类——同一份账"
                "只有一套分类。",
        # 不放数字卡：凭证数、业务活动数已经在封面的 KPI 里了，这里再摆一遍
        # 只是让人多读一行同样的数。
        "stats": [],
        "blocks": [
            {
                "heading": "2.1 总览",
                "note": "哪些活动、多少笔、多少钱、谁记的账。按金额从大到小排。",
                "table": {"columns": ["业务活动", "域", "凭证", "笔数占比", "金额",
                                      "金额占比", "主要制单人", "该人占比"],
                          "rows": overview, "numeric": [2, 3, 4, 5, 7],
                          # 两列占比画条：25 行数字里谁大谁小，读表要一行行比，
                          # 画成条是"扫一眼"的事
                          # 两列占比共用一个标尺：笔数占比与金额占比要能直接比长短
                          "bars": {3: "auto", 5: "auto"},
                          "bar_scale": max([(_pct(str(i.get("笔数占比", ""))) or 0.0)
                                            for i in activities]
                                           + [(_pct(str(i.get("金额占比", ""))) or 0.0)
                                              for i in activities] or [100.0]) or 100.0,
                          "widths": ["16%", "7%", "6%", "10%", "15%", "15%", "14%", "9%"]},
            },
            {
                "heading": "2.2 集中度",
                "note": "HHI = Σ 份额²（0–1，越大越集中）。金额那一列说的是「这个"
                        "活动是不是少数几张凭证撑起来的」（0.25 以上算高度集中）；"
                        "对手方那一列说的是「这个活动的钱主要往来于几个科目」。"
                        "它不下结论，只告诉你抽样必须盖住谁。",
                "table": {"columns": ["业务活动", "金额HHI", "对手方HHI",
                                      "主要对手方", "判读"],
                          # HHI 用底色深浅表示强度，不用红绿：高集中不等于坏
                          # （利润分配 1.000 是正常的），上语义色会把"数据"说成"结论"
                          "rows": concentrate, "numeric": [1, 2],
                          "heats": [1, 2],
                          "widths": ["15%", "11%", "12%", "34%", "28%"]},
            },
        ],
    }


def _leads_intro(items: List[Dict[str, Any]], errors: Dict[str, str],
                 white: Dict[str, Any]) -> Dict[str, Any]:
    # 不放数字卡：条目数在封面 KPI 里，分布在三层导览卡里。这里再摆一遍
    # 只是让人把同一批数字读第三次。
    stats: List[Dict[str, Any]] = []
    note = ("三层各管一段，**不能互相比较**：一条本福特超标和一条孤儿冲销不是同一种"
            "东西，排成一个序会给出「第 3 名比第 7 名更该查」的错觉。这一节只给每层的"
            "总览。具体是哪几行，在随报告导出的 Excel 里——那就是原账加了"
            "「是否异常」「异常原因」两列，筛「是」就是全部问题行。")
    blocks: List[Dict[str, Any]] = [{"layer_cards": _layer_cards(items)}]
    if not items:
        blocks.append({"placeholder":
                       "三层检验都没有发现值得单列的东西。这只说明「没有显性异常」——"
                       "真正混在大流里的（高频、小额、规律化）要靠细节测试去函证、"
                       "去盘点、去对合同。"})
    if errors:
        blocks.append({"placeholder":
                       "；".join(f"{name} 没跑成（{msg}）" for name, msg in errors.items())})

    footnotes: List[str] = []
    if white.get("count"):
        detail = "、".join(f"{name} {count}" for name, count in
                           (white.get("by_reason") or {}).items())
        footnotes.append(
            f"按风险白名单放过 {white['count']} 张凭证（{detail}）。这几类凭证的金额与"
            "科目组合由记账规则决定——汇率、期末结转、集团内调拨——不携带判断信息，"
            "留在线索里只会占掉注意力；逐张清单在 Excel 的「白名单」表。")

    return {
        "id": "leads",
        "no": "03",
        "title": "异常线索",
        "note": note,
        "stats": stats,
        "blocks": blocks,
        "footnotes": footnotes,
    }


def _layer_cards(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """三层四块的导览：一屏看清"哪一层有东西"，点了直接跳过去。"""
    def count(predicate) -> int:
        return sum(1 for item in items if predicate(item))

    return [
        {"href": "#layer_benford", "no": "3.1", "name": "本福特验证",
         "note": "金额的数字分布",
         "count": count(lambda i: i["层"] == "总体层")},
        {"href": "#layer_amount", "no": "3.2", "name": "金额特征孤立",
         "note": "某一张跳出同类",
         "count": count(lambda i: i["类型"].startswith("金额孤立"))},
        {"href": "#layer_combo", "no": "3.3", "name": "科目组合孤立",
         "note": "哪对科目不该一起出现",
         "count": count(lambda i: i["类型"].startswith("科目组合"))},
        {"href": "#layer_abnormal", "no": "3.4", "name": "孤儿冲销",
         "note": "冲了却找不到被冲的",
         "count": count(lambda i: i["层"] == "行为层")},
    ]


def _summary_table(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """一类一行：几条、涉及几张凭证、多少钱、最高几档。不放具体凭证号。"""
    return {"columns": lead_mod.SUMMARY_COLUMNS,
            "rows": lead_mod.summary_rows(rows),
            "badges": [5], "numeric": [2, 3, 4],
            "widths": ["10%", "21%", "7%", "11%", "24%", "10%"]}


def _benford_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    stats = (leads_doc.get("stats") or {}).get("本福特") or {}
    sides = stats.get("sides") or {}
    rows = [item for item in (leads_doc.get("leads") or []) if item["层"] == "总体层"]

    chips: List[Dict[str, str]] = []
    for side, info in sides.items():
        chips.append({"label": f"{side}样本", "value": f"{info.get('samples', 0):,} 条"})
        chips.append({"label": f"{side}偏离",
                      "value": (f"首位 {info['first']['verdict']}"
                                f" · 次位 {info['second']['verdict']}")})

    blocks: List[Dict[str, Any]] = []
    if sides:
        blocks.append({"charts": [{"side": side, "data": info}
                                  for side, info in sides.items()]})
    if rows:
        blocks.append({"table": _summary_table(rows)})
    else:
        blocks.append({"placeholder":
                       "两侧的数字分布都在可接受区间：没有哪个数字多到该往下查。"
                       "这是本福特正常工作的样子——它本来就不是每次都该说话。"})

    if rows:
        top = max(rows, key=lambda x: x["金额"])
        where = top["类型"].split("·")[-1] if "·" in top["类型"] else ""
        head = (f"{len(rows)} 处偏离值得往下看，最突出的是「{top['主体']}」"
                + (f"（{where}）" if where else "") + "。"
                "图里深色是实际、浅色是期望，偏离看两根柱差多少。")
    else:
        head = "整账的金额数字分布没有被哪一类业务拽偏。"

    footnotes = list(stats.get("skipped") or [])
    footnotes.append(
        "判读用 Nigrini 的 MAD 分档：首位 ≤0.006 接近、≤0.012 可接受、≤0.015 边缘、"
        "再高不可接受；次位更严。偏离本身**不说明有错**，它只把注意力引到某个业务"
        "活动的某个数字上。")
    footnotes.append("只取 ≥10 元的金额并取绝对值，借、贷分开跑：两边的业务性质不同"
                     "（收款 vs 付款），混在一起两个分布会互相污染。")

    return {
        "id": "layer_benford",
        "no": "3.1",
        "title": "本福特验证",
        "kicker": "总体层 · 金额的数字分布",
        "note": "自然产生的金额，首位是 1 的最多、9 的最少。整账偏离不说明有错，只"
                "说明数字不像自然长出来的；有用的是它把你带到某个活动的某个数字上。",
        "headline": head,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _amount_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("金额孤立") or {}
    rows = [item for item in (leads_doc.get("leads") or []) if item["层"] == "个体层"
            and item["类型"].startswith("金额孤立")]

    chips = [
        {"label": "指纹（同类的事）", "value": f"{info.get('fingerprints', 0):,} 类"},
        {"label": "可比对（≥6 张）", "value": f"{info.get('pairs_checked', 0):,} 类"},
        {"label": "金额跳出区间", "value": f"{info.get('jumps', 0)} 张"},
    ]
    if rows:
        top = max(rows, key=lambda x: x["金额"])
        head = (f"{len(rows)} 张凭证的金额跳出了自己这一类事的常态。最突出的一条——"
                f"「{top['主体']}」：{top['证据']}")
    else:
        head = "没有哪一张凭证的金额明显跳出它自己这一类事的区间。"

    footnotes = [
        f"只出现一次的指纹 {info.get('singletons', 0):,} 类——它们没有同类可比，"
        "这一层对它们失明（偶发业务本来就该偶发）。",
        "指纹 = 摘要去掉数字 + 科目组合（带方向）。「新增指纹」与「月度断档」不报："
        "新组合由「科目组合孤立」那一层负责；断档要先知道哪些事是月度必发的，只能靠"
        "关键词猜，误报一次就要人去翻凭证。",
    ]
    footnotes.extend(info.get("skipped") or [])

    return {
        "id": "layer_amount",
        "no": "3.2",
        "title": "金额特征孤立",
        "kicker": "个体层 · 同一件事的金额节律",
        "note": "同一件事（同一套科目、同一种摘要）在一年里反复发生，金额应该在一个"
                "量级上。某一张突然跳出去，就去问它为什么。用中位数与 MAD 判，不用"
                "均值——一条 96 万就能把均值和方差一起拉走，把离群点变成「正常波动」。",
        "headline": head,
        "stats": chips,
        "blocks": [],
        "count": len(rows),
        "footnotes": footnotes,
    }


def _combo_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("科目组合") or {}
    rows = [item for item in (leads_doc.get("leads") or [])
            if item["类型"].startswith("科目组合孤立")]

    chips = [
        {"label": "科目对（边）", "value": f"{info.get('edges', 0):,} 条"},
        {"label": "私人专线", "value": f"{info.get('private', 0)} 条"},
        {"label": "孤立双边", "value": f"{info.get('isolated', 0)} 条"},
        {"label": "枢纽杂音", "value": f"{info.get('hub', 0)} 条"},
    ]

    blocks: List[Dict[str, Any]] = []
    if rows:
        blocks.append({"table": _summary_table(rows)})
        kinds = {}
        for item in rows:
            kinds[item["类型"].split("·")[-1]] = kinds.get(item["类型"].split("·")[-1], 0) + 1
        # 先按优先级、再按金额大：`-金额` 在 max 里是"越小越优先"，所以这里用 min
        top = min(rows, key=lambda x: (x["优先级"] != "高", -x["金额"]))
        head = (f"{len(rows)} 条边的出现方式不寻常（"
                + "、".join(f"{name} {count} 条" for name, count in kinds.items())
                + f"）。最需要看的是「{top['主体']}」，合计 {_money(top['金额'])}。")
    else:
        head = ("没有哪一对科目的出现方式偏离它的边际——这份账的科目结构没有孤立的边。")
        blocks.append({"placeholder":
                       "零条不代表科目对没问题，只代表每条边都落在它的边际里。"})

    if info.get("matrix_preview"):
        blocks.append({
            "heading": "常见科目对（对照用）",
            "note": "先把「常规长什么样」摆出来：这些边占了绝大部分笔数，孤立要相对它们看。",
            "table": {"columns": ["借方科目", "贷方科目", "笔数", "占借方", "占贷方"],
                      "rows": info["matrix_preview"], "numeric": [2, 3, 4],
                      # 笔数画条：哪几条是主干一眼看得出，剩下的"稀有"才有参照
                      "bars": {2: "auto"},
                      "widths": ["24%", "24%", "20%", "16%", "16%"]},
        })

    footnotes = [
        "σ_row = 这条边占借方科目总笔数的比例，σ_col = 占贷方科目总笔数的比例。"
        "**私人专线**：稀有 ∧ 大额 ∧ 一端活跃却极少走这条 ∧ 另一端冷门且被这条边"
        "专享——「这个对手方像是专门为这条通道准备的」。",
        "**孤立双边**：两端都是冷门科目、只出现过一两次。样本量 1 的「专享」是废话，"
        "所以它不算专线，只算要解释。**枢纽杂音**：稀有边汇入一个大枢纽（银行、应付"
        "账款），一次性买专利付现就是这种，看一眼即可。",
        "结转到本年利润/利润分配、成本结转（主营业务成本 ↔ 存货类）的边不进矩阵："
        f"它们的稀有与专享是记账规则，不是疑点（这类边 {info.get('structural_skipped', 0)} 条）。",
    ]

    return {
        "id": "layer_combo",
        "no": "3.3",
        "title": "科目组合孤立",
        "kicker": "个体层 · 哪些科目不该一起出现",
        "note": "逐笔看凭证看不出「这一对科目不该一起出现」。整本账的边才看得出来："
                "原材料一年动两千次，从不走预计负债；而预计负债一年只登台三次，两次"
                "是给原材料当对手方。这种非对称不是单张凭证能说的。",
        "headline": head,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _abnormal_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("孤儿冲销") or {}
    rows = [item for item in (leads_doc.get("leads") or []) if item["层"] == "行为层"]

    chips = [
        {"label": "冲销凭证", "value": f"{info.get('reversals', 0)} 张"},
        {"label": "闭环（本年有母体）", "value": f"{info.get('closed', 0)} 张"},
        {"label": "孤儿冲销", "value": f"{info.get('orphan', 0)} 张"},
    ]
    if rows:
        top = max(rows, key=lambda x: x["金额"])
        head = (f"{info.get('reversals', 0)} 张冲销凭证里，{info.get('closed', 0)} 张在本年"
                f"找到了等额母体、放过；{len(rows)} 张没有。最大的一张冲销"
                f" {_money(top['金额'])}。")
    else:
        head = "本年所有冲销都找到了等额母体，没有孤儿。"

    blocks: List[Dict[str, Any]] = []
    if not rows:
        blocks.append({"placeholder": "这一层没有出线索。"})

    footnotes = [
        "冲销只有两种：**闭环**（本年暂估 → 本年冲销，对本年没有影响，放过）与"
        "**孤儿**（本年只有冲销，整年找不到等额的那笔原始分录）。孤儿的含义是"
        "「冲掉的东西不在这本账上」——多半是冲往年。结转是否另有实提、支付，不进判据："
        "本年暂估 + 冲销已经说明它对本年没有影响。",
        f"另有 {info.get('negative_without_keyword', 0)} 张凭证有负数金额、但摘要里没有"
        "冲销字样——本层靠关键词识别冲销，看不到它们。",
    ]

    return {
        "id": "layer_abnormal",
        "no": "3.4",
        "title": "孤儿冲销",
        "kicker": "核算异常 · 冲了却找不到被冲的",
        "note": "冲销一笔暂估是常规操作，前提是被冲的那笔真的存在。这一层找的是"
                "「只有冲销、没有母体」的那种——冲掉的东西不在这本账上。",
        "headline": head,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


# ============================================================================
# HTML
# ============================================================================

_CSS = """
:root{
  --ink:#151a21;--ink2:#3a4351;--dim:#737d8c;--faint:#9aa3b0;
  --line:#e6e9ee;--line2:#d3d9e1;--paper:#fff;--sunken:#f7f9fb;
  --brand:#1e3a5f;--brand2:#2c5282;--brand-l:#eaf0f7;
  --ok:#0d7a4c;--ok-bg:#eef8f2;--ok-line:#bfe3d0;
  --warn:#8d5509;--warn-bg:#fdf7ea;--warn-line:#efdcb2;
  --bad:#b42318;--bad-bg:#fdf3f2;--bad-line:#f3c9c5;
  --r:12px;
}
*{box-sizing:border-box}
body{margin:0;background:#eef1f5;color:var(--ink);
  font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
  "Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.page{max-width:1060px;margin:0 auto;background:var(--paper);
  border-top:4px solid var(--brand);box-shadow:0 2px 18px rgba(15,23,42,.07)}
a{color:var(--brand2)}

/* ── 封面 ─────────────────────────────────────────── */
.cover{padding:40px 48px 0}
.cover h1{margin:0 0 8px;font-size:27px;font-weight:650;letter-spacing:-.015em;
  line-height:1.25}
.cover .meta{color:var(--dim);font-size:12.5px;line-height:1.7}
.cover .stamp{color:var(--faint);font-size:11.5px;margin-top:2px}
.hero{display:flex;gap:13px;align-items:flex-start;margin:20px 0 0;
  padding:15px 18px;border-radius:var(--r);border:1px solid var(--line);
  background:var(--sunken)}
.hero .mark{flex:0 0 26px;height:26px;border-radius:50%;display:grid;place-items:center;
  font-size:14px;font-weight:700;line-height:1}
.hero b{display:block;font-size:14px;font-weight:650;margin-bottom:1px}
.hero span{font-size:12.5px;color:var(--ink2)}
.hero.ok{background:var(--ok-bg);border-color:var(--ok-line)}
.hero.ok .mark{background:var(--ok);color:#fff}
.hero.ok b{color:var(--ok)}
.hero.warn{background:var(--warn-bg);border-color:var(--warn-line)}
.hero.warn .mark{background:var(--warn);color:#fff}
.hero.warn b{color:var(--warn)}
.hero.bad{background:var(--bad-bg);border-color:var(--bad-line)}
.hero.bad .mark{background:var(--bad);color:#fff}
.hero.bad b{color:var(--bad)}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;margin:18px 0 0;
  background:var(--line);border:1px solid var(--line);border-radius:var(--r);
  overflow:hidden}
.kpi{background:#fff;padding:13px 16px 14px}
.kpi span{display:block;font-size:10.5px;color:var(--dim);letter-spacing:.05em;
  text-transform:none}
.kpi b{display:block;font-size:23px;font-weight:650;line-height:1.3;color:var(--ink);
  font-variant-numeric:tabular-nums}
.kpi small{color:var(--faint);font-size:11px}
.kpi.bad b{color:var(--bad)}

.toc{display:flex;flex-wrap:wrap;gap:7px;margin:18px 0 0;padding:0 0 16px}
.toc a{font-size:12px;color:var(--ink2);text-decoration:none;padding:5px 12px;
  border:1px solid var(--line);border-radius:999px;background:#fff}
.toc a:hover{border-color:var(--brand2);color:var(--brand2);background:var(--brand-l)}

/* ── 分节 ─────────────────────────────────────────── */
main{padding:6px 48px 44px}
section{margin-top:36px;scroll-margin-top:20px}
section h2{display:flex;align-items:baseline;gap:11px;flex-wrap:wrap;
  font-size:17.5px;font-weight:650;letter-spacing:-.01em;margin:0 0 2px;
  padding-bottom:11px;border-bottom:1px solid var(--line)}
.secno{font-size:11.5px;font-weight:650;color:var(--brand2);letter-spacing:.06em;
  background:var(--brand-l);padding:3px 9px;border-radius:7px;
  font-variant-numeric:tabular-nums}
.kicker{font-size:12px;font-weight:400;color:var(--faint)}
.count{margin-left:auto;font-size:11.5px;font-weight:500;color:var(--dim);
  background:var(--sunken);border:1px solid var(--line);padding:2px 10px;
  border-radius:999px;font-variant-numeric:tabular-nums}
.note{margin:12px 0 14px;color:var(--dim);font-size:12.5px;line-height:1.85;
  max-width:80ch;text-wrap:pretty}
h3.sub{font-size:12.5px;font-weight:650;color:var(--ink2);letter-spacing:.02em;
  margin:24px 0 2px;padding-left:9px;border-left:3px solid var(--line2)}

/* ── 三层导览 ─────────────────────────────────────── */
.layers{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0 2px}
.lcard{display:block;text-decoration:none;color:inherit;border:1px solid var(--line);
  border-radius:var(--r);padding:13px 15px 14px;background:#fff}
.lcard:hover{border-color:var(--brand2);background:var(--brand-l)}
.lcard span{display:block;font-size:12px;font-weight:600;color:var(--ink2);
  letter-spacing:.01em}
.lcard b{display:block;font-size:25px;font-weight:650;line-height:1.25;
  font-variant-numeric:tabular-nums;color:var(--brand)}
.lcard small{color:var(--dim);font-size:11.5px}
.lcard.zero b{color:var(--faint)}

/* ── 数字卡 ───────────────────────────────────────── */
.statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
  gap:10px;margin:16px 0 4px}
.stat{position:relative;overflow:hidden;border:1px solid var(--line);
  border-radius:10px;padding:10px 13px 11px;background:var(--sunken)}
.stat::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
  background:var(--brand2);opacity:.55}
.stat span{display:block;font-size:11px;color:var(--dim);line-height:1.5}
.stat b{display:block;font-size:18px;font-weight:650;line-height:1.35;
  font-variant-numeric:tabular-nums}
.stat.bad::before{background:var(--bad);opacity:.9}
.stat.bad b{color:var(--bad)}

/* ── 结论句 ───────────────────────────────────────── */
p.head{margin:16px 0 8px;padding:13px 17px;font-size:13px;line-height:1.85;
  text-wrap:pretty;
  color:var(--ink2);background:linear-gradient(90deg,var(--brand-l),#fff 78%);
  border-left:3px solid var(--brand2);border-radius:0 10px 10px 0}

/* ── 表格 ─────────────────────────────────────────── */
.tablewrap{margin-top:10px;border:1px solid var(--line);border-radius:var(--r);
  overflow:hidden}
table{width:100%;border-collapse:collapse;font-size:12.5px;
  font-variant-numeric:tabular-nums}
table.fixed{table-layout:fixed}
thead th{background:var(--sunken);font-weight:600;font-size:11px;color:var(--dim);
  letter-spacing:.03em;padding:9px 12px;border-bottom:1px solid var(--line2);
  white-space:nowrap;text-align:left}
td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:middle;
  text-align:left}
tbody tr:nth-child(even){background:#fcfdfe}
tbody tr:hover{background:var(--brand-l)}
tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;white-space:nowrap}
td.wrap{white-space:normal}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 9px;
  border-radius:999px;line-height:1.65;white-space:nowrap}
.badge.ok{background:var(--ok-bg);color:var(--ok);border:1px solid var(--ok-line)}
.badge.warn{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-line)}
.badge.bad{background:var(--bad-bg);color:var(--bad);border:1px solid var(--bad-line)}
.badge.mute{background:#f1f3f6;color:var(--dim);border:1px solid var(--line)}

/* 占比条：数字照写，条只是让"谁大谁小"变成一眼的事 */
.barcell{display:flex;align-items:center;gap:9px;justify-content:flex-end}
.barcell .track{flex:1;min-width:36px;max-width:86px;height:6px;border-radius:99px;
  background:#edf0f4;overflow:hidden}
.barcell .track i{display:block;height:100%;border-radius:99px;background:var(--brand2)}
.barcell em{font-style:normal;color:var(--ink2);min-width:42px;text-align:right}

/* HHI 热力：只用单一深色，不用红绿——集中度高不等于坏 */
.heat{display:inline-block;min-width:54px;text-align:right;padding:2px 8px;
  border-radius:6px;font-variant-numeric:tabular-nums}
.heat.strong{color:#fff;font-weight:600}

.empty{padding:15px 17px;border:1px dashed var(--line2);border-radius:10px;
  color:var(--dim);font-size:12.5px;background:var(--sunken);margin-top:10px;
  line-height:1.85}

/* ── 图 ───────────────────────────────────────────── */
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));
  gap:14px;margin-top:16px}
.charts figure{margin:0;border:1px solid var(--line);border-radius:var(--r);
  padding:12px 14px 6px;background:#fff}

/* ── 脚注与页脚 ───────────────────────────────────── */
ul.notes{margin:16px 0 0;padding:0;list-style:none;color:var(--dim);
  font-size:11.5px;line-height:1.9;max-width:92ch;text-wrap:pretty}
ul.notes li{position:relative;padding-left:15px}
ul.notes li::before{content:"";position:absolute;left:3px;top:.78em;width:4px;
  height:4px;border-radius:50%;background:var(--line2)}
ul.notes b{color:var(--ink2);font-weight:600}
footer{margin-top:46px;padding:18px 48px 34px;border-top:1px solid var(--line);
  background:var(--sunken);color:var(--faint);font-size:11.5px;line-height:1.95}

@media (max-width:820px){
  .cover{padding:26px 22px 0}
  main{padding:6px 22px 32px}
  footer{padding:16px 22px 28px}
  .kpis,.layers{grid-template-columns:repeat(2,1fr)}
  .cover h1{font-size:22px}
}
@media print{
  body{background:#fff}
  .page{box-shadow:none;max-width:none;border-top-width:3px}
  .tablewrap{overflow:visible}
  .toc{display:none}
  thead th{position:static}
  tr,figure,.lcard,.stat,.kpi{break-inside:avoid}
  section{break-inside:auto}
  @page{margin:14mm 12mm}
}
"""


def _pct(value: Any) -> Optional[float]:
    """'26.5%' → 26.5。读不出来返回 None（那一格就不画条）。"""
    text = str(value or "").strip().rstrip("%").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _html_table(table: Dict[str, Any]) -> str:
    columns: Sequence[str] = table.get("columns") or []
    rows: Sequence[Sequence[Any]] = table.get("rows") or []
    widths = table.get("widths") or []
    numeric = set(table.get("numeric") or [])
    badges = set(table.get("badges") or [])
    bars = table.get("bars") or {}          # 列索引 → 满格对应的百分比
    heats = set(table.get("heats") or [])   # 列索引 → 按 0..1 上底色
    title = table.get("title")

    def head_cell(index: int, name: str) -> str:
        return (f"<th class='num'>{html.escape(name)}</th>" if index in numeric
                else f"<th>{html.escape(name)}</th>")

    def body_cell(index: int, name: str, value: Any) -> str:
        text = html.escape(str(value if value is not None else ""))
        classes: List[str] = []
        style = ""
        if index in numeric:
            classes.append("num")
        if name in _WIDE:
            classes.append("wrap")

        if index in badges:
            tone = TONE.get(str(value or ""), "mute")
            text = f"<span class='badge {tone}'>{text}</span>"
        elif index in bars:
            pct = _pct(value)
            # "auto" = 拿这一列的最大值当满格。写死 100 的话，25 个活动里最大的
            # 那个也才 26%，所有条都会缩成一小截，"谁大谁小"又看不出来了。
            full = table.get("bar_scale") or bars[index]
            if full == "auto":
                # 同一张表里几列都画条时，标尺必须共用一个：各列各自铺满的话，
                # "48.9% 的条"和"26.5% 的条"会长得一样长，比不画还误导。
                full = max((_pct(row[col]) or 0.0
                            for row in rows for col in bars if col < len(row)),
                           default=0.0) or 100.0
            width = 0.0 if pct is None else max(0.0, min(100.0, pct / float(full) * 100))
            text = (f"<div class='barcell'><span class='track'>"
                    f"<i style='width:{width:.1f}%'></i></span><em>{text}</em></div>")
        elif index in heats:
            try:
                level = max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                level = 0.0
            # 0.04 起步，保证最浅的一格也能看出"有值"；0.55 封顶，再深压不住字
            alpha = 0.045 + 0.5 * level
            strong = " strong" if level >= 0.55 else ""
            # 底色包在 span 上，不加在 td 上：`.heat` 是 inline-block，套到 td
            # 头上会把单元格从 table-cell 变成行内块，整张表会塌掉。
            text = (f"<span class='heat{strong}' "
                    f"style='background:rgba(30,58,95,{alpha:.3f})'>{text}</span>")
        attr = f" class='{' '.join(classes)}'" if classes else ""
        return f"<td{attr}{style}>{text}</td>"

    colgroup = ("<colgroup>" + "".join(f"<col style='width:{w}'>" for w in widths)
                + "</colgroup>") if widths else ""
    head = "".join(head_cell(i, name) for i, name in enumerate(columns))
    body = "".join(
        "<tr>" + "".join(body_cell(i, name, row[i] if i < len(row) else "")
                         for i, name in enumerate(columns)) + "</tr>"
        for row in rows)
    klass = " class='fixed'" if widths else ""
    block = (f"<div class='tablewrap'><table{klass}>{colgroup}"
             f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>")
    return (f"<h3 class='sub'>{html.escape(title)}</h3>" if title else "") + block


def _svg_chart(title: str, obs: Dict[int, int], expect: Dict[int, float],
               digits: Sequence[int], note: str = "") -> str:
    """一个数字分布图：每个数字两根柱（实际/期望），高度都是占比。

    自己画 SVG 不引图表库，是为了让报告保持单文件：外链一个 echarts，报告离开
    网络就废了，而报告是要发给客户、要归档的。
    """
    # 尺寸按"两列并排时一格多宽"来定（1060 页宽 → 每格约 470）：
    # 画 330 再让它 scale 上去，字号会被放大成大字报；正好画成实际宽度，
    # 文字就是设计时的字号。
    width, height = 500, 214
    left, right, top, bottom = 44, 14, 30, 42
    plot_h = height - top - bottom
    baseline = height - bottom
    total = sum(obs.values()) or 1

    ink, dim, grid = "#3a4351", "#8e97a4", "#eef1f5"
    obs_fill, exp_fill, exp_edge = "#2c4a6b", "#dde4ed", "#c8d2de"

    values = [obs.get(d, 0) / total for d in digits]
    exps = [expect.get(d, 0.0) for d in digits]
    peak = max(max(values or [0.0]), max(exps or [0.0])) or 0.01
    ymax = peak * 1.16

    step = (width - left - right) / len(digits)
    bar = min(13.0, step * 0.3)
    parts: List[str] = [
        f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' "
        f"aria-label='{html.escape(title)}'>",
        f"<text x='{left - 6}' y='16' font-size='12.5' font-weight='600' fill='{ink}'>"
        f"{html.escape(title)}</text>",
    ]
    if note:
        parts.append(f"<text x='{width - right}' y='16' font-size='10.5' fill='{dim}' "
                     f"text-anchor='end'>{html.escape(note)}</text>")
    for frac in (0.0, 0.5, 1.0):
        y = baseline - frac * plot_h
        parts.append(f"<line x1='{left - 4}' y1='{y:.1f}' x2='{width - right}' "
                     f"y2='{y:.1f}' stroke='{grid}' stroke-width='1'/>")
        parts.append(f"<text x='{left - 7}' y='{y + 3.6:.1f}' font-size='9.5' "
                     f"fill='{dim}' text-anchor='end'>{ymax * frac * 100:.0f}%</text>")

    for index, digit in enumerate(digits):
        cx = left + step * (index + 0.5)
        exp_h = exps[index] / ymax * plot_h
        obs_h = values[index] / ymax * plot_h
        parts.append(f"<rect x='{cx - bar - 1.5:.1f}' y='{baseline - exp_h:.1f}' "
                     f"width='{bar:.1f}' height='{max(exp_h, 0.8):.1f}' rx='1.5' "
                     f"fill='{exp_fill}' stroke='{exp_edge}' stroke-width='.6'/>")
        parts.append(f"<rect x='{cx + 1.5:.1f}' y='{baseline - obs_h:.1f}' "
                     f"width='{bar:.1f}' height='{max(obs_h, 0.8):.1f}' rx='1.5' "
                     f"fill='{obs_fill}'/>")
        parts.append(f"<text x='{cx:.1f}' y='{baseline + 16}' font-size='11' "
                     f"fill='{dim}' text-anchor='middle'>{digit}</text>")

    parts.append(f"<line x1='{left - 4}' y1='{baseline}' x2='{width - right}' "
                 f"y2='{baseline}' stroke='#c8d2de' stroke-width='1'/>")
    # 图例画成两个小方块：只写"深色/浅色"要人去猜哪个是哪个
    ly = height - 11
    parts.append(f"<rect x='{left - 6}' y='{ly}' width='8' height='8' rx='2' fill='{obs_fill}'/>")
    parts.append(f"<text x='{left + 5}' y='{ly + 7.5}' font-size='10' fill='{dim}'>实际</text>")
    parts.append(f"<rect x='{left + 39}' y='{ly}' width='8' height='8' rx='2' "
                 f"fill='{exp_fill}' stroke='{exp_edge}' stroke-width='.6'/>")
    parts.append(f"<text x='{left + 50}' y='{ly + 7.5}' font-size='10' fill='{dim}'>"
                 f"本福特期望</text>")
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
    """说明文字允许 **加粗**（只这一种标记，其余一律转义）。

    先转义再替换：反过来做，用户数据里的 `<` 会变成标签。
    """
    escaped = html.escape(str(text or ""))
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return escaped.replace("\n", "<br>")


def _html_stats(stats: List[Dict[str, Any]]) -> str:
    if not stats:
        return ""
    cells = "".join(
        f"<div class='stat{' ' + str(item['tone']) if item.get('tone') else ''}'>"
        f"<span>{html.escape(str(item.get('label', '')))}</span>"
        f"<b>{html.escape(str(item.get('value', '')))}</b></div>" for item in stats)
    return f"<div class='statrow'>{cells}</div>"


def _html_layer_cards(cards: List[Dict[str, Any]]) -> str:
    """三层导览：一行四块，数字大、名字清楚，点一下跳到那一节。"""
    items = "".join(
        f"<a class='lcard{' zero' if not card.get('count') else ''}' "
        f"href='{html.escape(str(card.get('href', '#')))}'>"
        f"<span>{html.escape(str(card.get('no', '')))} · "
        f"{html.escape(str(card.get('name', '')))}</span>"
        f"<b>{card.get('count', 0)}</b>"
        f"<small>{html.escape(str(card.get('note', '')))}</small></a>" for card in cards)
    return f"<div class='layers'>{items}</div>"


def _html_block(block: Dict[str, Any]) -> str:
    out: List[str] = []
    if block.get("layer_cards"):
        out.append(_html_layer_cards(block["layer_cards"]))
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


def _section_html(section: Dict[str, Any]) -> str:
    sid = section["id"]
    number = section.get("no") or ""
    kicker = section.get("kicker") or ""
    count = section.get("count")

    head = [f"<span class='secno'>{html.escape(number)}</span>"] if number else []
    head.append(html.escape(str(section.get("title", ""))))
    if kicker:
        head.append(f"<span class='kicker'>{html.escape(kicker)}</span>")
    if count is not None:
        head.append(f"<span class='count'>{count} 条线索</span>")

    inner = [_html_stats(section.get("stats") or [])]
    if section.get("headline"):
        inner.append(f"<p class='head'>{_rich(section['headline'])}</p>")
    for block in section.get("blocks") or []:
        inner.append(_html_block(block))
    if section.get("footnotes"):
        items = "".join(f"<li>{_rich(note)}</li>" for note in section["footnotes"])
        inner.append(f"<ul class='notes'>{items}</ul>")

    return (f"<section id='{sid}'><h2>{''.join(head)}</h2>"
            f"<p class='note'>{_rich(section.get('note', ''))}</p>"
            f"{''.join(inner)}</section>")


def to_html(model: Dict[str, Any]) -> str:
    sections = model.get("sections") or []
    body = "".join(_section_html(section) for section in sections)

    toc = "".join(
        f"<a href='#{section['id']}'>{html.escape(str(section.get('no') or ''))} "
        f"{html.escape(str(section.get('title', '')))}</a>" for section in sections)

    kpis = "".join(
        f"<div class='kpi{' ' + str(kpi['tone']) if kpi.get('tone') else ''}'>"
        f"<span>{html.escape(str(kpi.get('label', '')))}</span>"
        f"<b>{html.escape(str(kpi.get('value', '')))}</b>"
        f"<small>{html.escape(str(kpi.get('note', '')))}</small></div>"
        for kpi in (model.get("kpis") or []))

    verdict = model["verdict"]
    tone = verdict.get("tone") or "ok"
    headline, _, detail = str(verdict.get("text") or "").partition(" · ")
    mark = "✓" if tone == "ok" else "!"

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(model["title"])} · {html.escape(model["file"])}</title>
<style>{_CSS}</style></head>
<body><div class="page">
<header class="cover">
  <h1>{html.escape(model["title"])}</h1>
  <div class="meta">{html.escape(model["file"])} · {html.escape(model["meta"])}</div>
  <div class="stamp">生成于 {html.escape(model["generated_at"])}</div>
  <div class="hero {tone}">
    <span class="mark">{mark}</span>
    <div><b>{html.escape(headline)}</b><span>{html.escape(detail)}</span></div>
  </div>
  <div class="kpis">{kpis}</div>
  <nav class="toc">{toc}</nav>
</header>
<main>{body}</main>
<footer>{html.escape(model["feature"])} · 本报告由程序生成。每条线索都写清了统计证据，
结论要人来下；没有「综合得分」，也不给建议——风险摆出来，怎么查是项目组的判断。<br>
Excel 是同一份账加了「是否异常 / 异常原因 / 最高优先级 / 白名单放行」四列，
筛「是」就是全部问题行。</footer>
</div></body></html>"""
