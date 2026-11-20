# -*- coding: utf-8 -*-
"""会计分录测试报告（HTML）。

这份东西的读者是项目组：先证账能用（完整性），再讲账在讲什么（画像），最后才是
线索。顺序就是读的顺序，一屏一件事。

四条排版上的决定，都是被"看不懂"逼出来的：

1. **完整性只留四行**。逐月逐序列列十几二十行，人看到一片"通过"就翻过去了，真正
   异常的那一行反而被淹掉；异常写进「说明」里，点名到月。
2. **画像拆成两节**：总览回答"做了哪些活动、多少钱、谁记的"，集中度回答"这个
   活动是不是少数几张凭证撑起来的"。挤在一张十几列的表里，两件事互相盖住。
3. **本福特放在第一节当背景，不当线索**。它能说明"这份账的数字整体像不像自然
   长出来的"，但指不出哪一笔有问题；统计上有说服力、审计上没有指向性。所以它只
   进「总体完整性」1.2，不进异常清单、不标凭证行。
4. **线索分块报，且每块只给总览**。每块各管一段（金额、活动内的科目、科目对、
   冲销），合在一张表里会让人以为它们可比。每层只给"几条、几张、多少钱、
   最突出的是哪一件"——一条线索的证据要占三四行，几十条就把报告变成一张
   没人读完的表，具体是哪几张归 Excel。
5. **凭证明细不进 HTML**（一屏几十个凭证号没人看）。Excel 也不是"报告的附件"：
   它就是用户传进来的那份账，末尾多了「是否异常」「异常原因」几列，筛一下就是
   全部问题凭证——报告回答"该怎么看"，表格回答"是哪几行"。

报告是**自包含**的：样式内联、图是 SVG、不引任何外链，一个文件发出去就能看，
也能用浏览器的"打印成 PDF"。只有一段内联脚本，做三件锦上添花的事（目录高亮、
只看结论、打印）；脚本不参与排版，禁用后报告照样完整可读——它是发给客户、要
归档的东西，不能因为对方禁了脚本就变成白页。
"""

from __future__ import annotations

import html
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

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


def _wan(value: Any) -> str:
    """元 → 万元，报告里的数字卡用这个口径。"""
    try:
        return f"{float(value) / 1e4:,.1f} 万"
    except (TypeError, ValueError):
        return "—"


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
    verdict += f" · 线索 {len(items)} 条" if items else " · 各项检验没发现值得单列的线索"

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
            _integrity_section(integrity, leads_doc),
            _profile_section(profile, activities,
                              (leads_doc.get("stats") or {}).get("科目对", {}).get("grid") or {}),
            _leads_intro(items, errors, leads_doc.get("whitewash") or {}),
            _amount_section(leads_doc),
            _activity_section(leads_doc),
            _pair_section(leads_doc),
            _cash_section(leads_doc),
            _abnormal_section(leads_doc),
        ],
    }


def _integrity_section(integrity: Dict[str, Any],
                       leads_doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rows = integrity.get("summary") or []
    months = integrity.get("months") or []
    benford = ((leads_doc or {}).get("stats") or {}).get("本福特") or {}
    sides = benford.get("sides") or {}
    found = benford.get("found") or []

    blocks: List[Dict[str, Any]] = [{
        "heading": "1.1 借贷与凭证编号",
        "table": {"columns": ["检查项", "结果", "数值", "说明"],
                  "rows": [[r.get("检查项", ""), r.get("结果", ""), r.get("数值", ""),
                            r.get("说明", "")] for r in rows],
                  "badges": [1],
                  "widths": ["14%", "9%", "30%", "47%"]},
        # 月度节律图：四行汇总证明"账是平的"，图回答"钱是哪几个月进来的"。
        # 两件事都属"总体印象"，放同一块里读，不用翻到画像去拼。
        "months": months,
    }]

    if sides:
        marks = "，".join(
            f"{name} {info['first']['mad']:.4f}（{info['first']['verdict']}）"
            for name, info in sides.items())
        note = (f"首位数字的实际分布与期望的差距（MAD）：{marks}。"
                "档位是 ≤0.006 接近、≤0.012 可接受、≤0.015 边缘、再高不可接受。")
        if found:
            top = max(found, key=lambda item: item["mad"])
            note += (f" 偏离主要来自「{top['top_name']}」：它贡献的{top['side']}首位"
                     f" {top['digit']} 比该有的多 {top['excess']:,.0f} 笔，"
                     f"剔掉这块整账回落到 {benford_mod.verdict(top['rest_mad'])}"
                     f"（MAD {top['rest_mad']:.4f}）。")
        elif max(info["first"]["mad"] for info in sides.values()) <= benford_mod.FIRST_MAD[1]:
            note += " 两侧都在正常区间，这一项不用往下看。"
        else:
            note += " 偏离不集中在哪一类业务上，找不到单一来源。"
        blocks.append({
            "heading": "1.2 金额首位分布（背景）",
            "note": note,
            "charts": [{"side": side, "data": info} for side, info in sides.items()],
        })
    else:
        blocks.append({
            "heading": "1.2 金额首位分布（背景）",
            "placeholder": "；".join(benford.get("skipped") or ["样本不足，本福特没跑。"]),
        })

    return {
        "id": "integrity",
        "no": "01",
        "title": "总体完整性",
        "note": "1.1 证的是总量守恒，不是明细正确——借贷同错的串户不破坏平衡。"
                "这一节过不了，后面的画像与检验都建在流沙上；过了也不代表账没问题。"
                "1.2 是背景检查：这份账的金额数字像不像自然长出来的。",
        "blocks": blocks,
        "footnotes": [
            "本福特只用来判断「这份账的数字整体可不可信」，**不产生线索**：它指不出"
            "哪一笔有问题，只能说明某类业务的金额形态偏了。所以它不进异常清单，"
            "Excel 里也不会因为本福特标任何一行。",
            "**只做首位，不做次位。** 次位本来是查凑整，但整数报销、整万转账、"
            "按天计息让次位 0 天然偏高，几乎每份账都会「边缘」，报出来全是噪声。",
            "借、贷分开跑（收款与付款是两种业务），只取 ≥10 元的金额并取绝对值——"
            "关心的是「金额长什么样」，不是方向。",
        ],
    }


def _profile_section(profile: Dict[str, Any],
                     activities: List[Dict[str, Any]],
                     grid: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                "heading": "2.1 金额构成",
                "note": "先看总量怎么切：业务域是桶表里定死的粗分组（经营 / 投资 / "
                        "筹资 / 非现金），跨账可比；下面的排行才是这份账自己的活动。"
                        "两个口径的金额是同一笔钱，占比因此可以直接相加。",
                "composition": activities,
            },
            {
                "heading": "2.2 总览",
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
                "heading": "2.3 集中度",
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
            {
                "heading": "2.4 科目之间的钱怎么流动",
                "note": "行是产出（这笔钱从哪个科目来），列是投入（到哪个科目去）。"
                        "**只在自己的构成里比**：一列加起来是 100%，所以格子里的「24」说的是"
                        "「这一列里有 24% 来自这一行」，不是全账占比。底行可以核对每列都是 100。"
                        "同一个科目对同一个科目（银行存款 ↔ 银行存款这类内部划转）落在对角线上。",
                "matrix": grid or {},
            },
        ],
    }


def _leads_intro(items: List[Dict[str, Any]], errors: Dict[str, str],
                 white: Dict[str, Any]) -> Dict[str, Any]:
    # 不放数字卡：条目数在封面 KPI 里，分布在导览卡里。这里再摆一遍
    # 只是让人把同一批数字读第三次。
    stats: List[Dict[str, Any]] = []
    note = ("每块各管一段，**不能互相比较**：一条金额孤立和一条孤儿冲销不是同一种"
            "东西，排成一个序会给出「第 3 名比第 7 名更该查」的错觉。这一节只给每层的"
            "总览。具体是哪几行，在随报告导出的 Excel 里——那就是原账加了"
            "「是否异常」「异常原因」两列，筛「是」就是全部问题行。"
            "金额的数字分布（本福特）不在这里：它只是整账可信度的背景，放在 1.2。")
    blocks: List[Dict[str, Any]] = [{"layer_cards": _layer_cards(items)}]
    if items:
        # 轻重分布单独一条：104 条里 26 条高优先级，这个比例决定了这一节该读多细
        blocks.append({"priority": items})
    if not items:
        blocks.append({"placeholder":
                       "各项检验都没有发现值得单列的东西。这只说明「没有显性异常」——"
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
    """分层导览：一屏看清"哪一块有东西"，点了直接跳过去。"""
    def count(predicate) -> int:
        return sum(1 for item in items if predicate(item))

    return [
        {"href": "#layer_amount", "no": "3.1", "name": "金额特征孤立",
         "note": "某一张跳出同类",
         "count": count(lambda i: i["类型"].startswith("金额孤立"))},
        {"href": "#layer_activity", "no": "3.2", "name": "活动里的科目",
         "note": "这个群不该用它",
         "count": count(lambda i: i["类型"] == "科目组合孤立")},
        {"href": "#layer_pair", "no": "3.3", "name": "科目对孤立",
         "note": "哪条通道不该走",
         "count": count(lambda i: i["类型"] == "科目对孤立")},
        {"href": "#layer_cash", "no": "3.4", "name": "现金收付",
         "note": "抽屉里进出的钱",
         "count": count(lambda i: i["类型"].startswith("现金"))},
        {"href": "#layer_abnormal", "no": "3.5", "name": "孤儿冲销",
         "note": "冲了却找不到被冲的",
         "count": count(lambda i: i["类型"] == "孤儿冲销")},
    ]


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
        "no": "3.1",
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


def _activity_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("活动科目") or {}
    rows = [item for item in (leads_doc.get("leads") or [])
            if item["类型"] == "科目组合孤立"]
    features = info.get("features") or {}
    sizes = info.get("sizes") or {}

    chips = [
        {"label": "业务群", "value": f"{info.get('groups', 0)} 个"},
        {"label": "群 × 科目", "value": f"{info.get('cells', 0):,} 格"},
        {"label": "稀疏 / 跨群", "value": f"{len(rows)} 条"},
    ]

    blocks: List[Dict[str, Any]] = []
    if features:
        blocks.append({
            "heading": "每个群靠什么科目认出来",
            "note": "只列**这个群特有**的科目：别的群很少用、这个群常用。公共科目（银行存款、"
                    "应交税费、应付账款）哪个群都有，不进来——它们说明不了这个群是干什么的。"
                    "先看这张画像对不对，再看下面的线索。",
            "table": {
                "columns": ["业务群", "凭证", "特色科目（其他群少用、本群常用）"],
                # 按 sizes 遍历（不是 features）：本账没有的群也要占一行，
                # 否则读者看到的是一张少了一行的表，以为漏了。
                "rows": [[group, f"{int(sizes.get(group, 0)):,}",
                          "　".join(f"{name} {share}" for name, share in
                                    (features.get(group) or [])[:5])
                          or ("本账没有这类业务" if not sizes.get(group) else "没有专有科目")]
                         for group in (sizes or features)],
                "widths": ["13%", "9%", "78%"],
            },
        })

    if rows:
        top = max(rows, key=lambda x: x["金额"])
        head = (f"{len(rows)} 个组合越出了这个群的常规。最大的一处是「{top['主体']}」："
                f"{_money(top['金额'])}。")
    else:
        head = "四个群里的科目都在常规范围内。"
        blocks.append({"placeholder":
                       "没有哪个群用到了明显不属于它的科目，也没有「占比极低、金额极大」的格子。"})

    footnotes = [
        "**先分群，再比。** 业务桶太细（采购与生产制造天然相邻），所以先并成四个群。"
        "群与对抗关系写在桶表里，不随账变化；其余业务桶不进群、不参与这一层。",
        "**纵向的群内占比按桶等权。** 直接按凭证数相加，3,858 张的采购桶会压过 85 张的"
        "生产制造桶，生产成本在供应链里只剩 0.57%、被当成稀疏。先在桶内算占比、再对群内"
        "各桶取平均，每个桶的分量才一样（等权后是 9.2%）。",
        "**特色科目由点乘矩阵定。** 列剖面问「这个桶多依赖它」（周转材料在采购占 13.2%），"
        "行剖面问「它有多少落在这个桶」（周转材料 1,338 张里 1,271 张在采购）。两者相乘、"
        "按科目归一化成份额，再用**有效桶数 = 1/Σ份额²** 看它摊在几个桶上：≤2 才有家。"
        "这条自带份额下限（份额必然 ≥50%），不用另设阈值。",
        "**为什么两个剖面都要。** 只看量，应交税费 53.5% 落在采购（采购团量大），可销售群的"
        "使用率更高（销项税）；只看使用率，银行存款在筹资群 79%（借款、付息都过银行），"
        "可全账 28% 的凭证都在用它。两种都偏，乘起来才稳。",
        "**筹资群只收纯筹资业务**（借款、增资、利润分配、筹资费用）。利息支出不归任何群——"
        "它装的是计提利息，是机械动作，且会把租赁负债这类带经营属性的科目带进筹资群，"
        "让「筹资 vs 经营」的对抗失去意义。",
        "**两条检测。** 纵向：这个科目在群里占比 ≤1%、金额 ≥100 万（稀疏但大）。"
        "横向：它的家在别的群，而那个群与本群性质对抗（供应链↔销售，三者↔筹资）。"
        "费用群天然模糊（群内第一名只占约一半，其他群都在八成以上），它允许出现供应链、"
        "销售的科目——费用部门领料就是这种形状。",
    ]

    return {
        "id": "layer_activity",
        "no": "3.2",
        "title": "业务活动里的科目",
        "kicker": "个体层 · 这个群该不该用这个科目",
        "note": "一个业务群有它本来长什么样：供应链就是原材料、周转材料，销售就是应收账款、"
                "主营业务收入。某类科目在这个群里只出现一两次、金额却很大，就是这个群的账里"
                "混进了不属于它的东西。",
        "headline": head,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _pair_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("科目对") or {}
    rows = [item for item in (leads_doc.get("leads") or [])
            if item["类型"] == "科目对孤立"]

    chips = [
        {"label": "科目对（边）", "value": f"{info.get('edges', 0):,} 条"},
        {"label": "一级科目", "value": f"{info.get('subjects', 0)} 个"},
        {"label": "罕见通道", "value": f"{len(rows)} 条"},
    ]

    blocks: List[Dict[str, Any]] = []
    if rows:
        top = min(rows, key=lambda x: (x["优先级"] != "高", -x["金额"]))
        head = (f"{len(rows)} 条边符合「对一个科目是罕见通道、对另一个科目却是主要来源」。"
                f"最突出的是「{top['主体']}」，合计 {_money(top['金额'])}。")
    else:
        head = "没有哪条边的两端同时满足「罕见通道 + 主要来源」。"
        blocks.append({"placeholder":
                       "零条不代表科目对都正常，只代表没有哪条边同时踩到这两头。"})

    if info.get("matrix_preview"):
        blocks.append({
            "heading": "常见科目对（对照用）",
            "note": "先把「常规长什么样」摆出来：这些边占了绝大部分笔数，罕见边要相对它们看。",
            "table": {"columns": ["借方科目", "贷方科目", "笔数", "占借方", "占贷方"],
                      "rows": info["matrix_preview"], "numeric": [2, 3, 4],
                      "bars": {2: "auto"},
                      "widths": ["24%", "24%", "20%", "16%", "16%"]},
        })

    footnotes = [
        "两条份额都是**条件概率**，不是统计显著性：σ_row = 这条边占「借方科目全部往来」的"
        "比例，σ_col = 占「贷方科目全部来源」的比例。分母是条件，所以不依赖任何独立性假设。",
        "**判据只有一条**：σ_row 低（对借方罕见）**且** σ_col 高（对贷方是主要来源）。"
        "缺一个都不行——只看前者，所有一次性往来都算；只看后者，会选出一个科目本来的用途。",
        "结转到本年利润/利润分配、成本结转（主营业务成本 ↔ 存货类）、内部账户互转的边不进"
        f"矩阵：它们的稀有是记账规则，不是疑点（这类边 {info.get('structural_skipped', 0)} 条）。",
    ]

    return {
        "id": "layer_pair",
        "no": "3.3",
        "title": "科目对孤立",
        "kicker": "个体层 · 哪条通道不该走",
        "note": "逐笔看凭证看不出「这一对科目不该一起出现」，整本账的边才看得出来："
                "原材料一年动两千次从不走预计负债，而预计负债一年只登台三次、两次是"
                "给原材料当对手方。",
        "headline": head,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _cash_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("现金") or {}
    pay = info.get("pay") or {}
    recv = info.get("recv") or {}
    rows = [item for item in (leads_doc.get("leads") or [])
            if str(item["类型"]).startswith("现金")]

    chips = [
        {"label": "付现", "value": f"{pay.get('count', 0)} 张 / {_wan(pay.get('amount', 0))}"},
        {"label": "收款（含提现）",
         "value": f"{recv.get('count', 0)} 张 / {_wan(recv.get('amount', 0))}"},
        {"label": "门槛（取低）",
         "value": f"付 {_wan(pay.get('threshold', 0))} · 收 {_wan(recv.get('threshold', 0))}"},
    ]

    blocks: List[Dict[str, Any]] = []
    if rows:
        top = max(rows, key=lambda x: x["金额"])
        over = sum(1 for item in rows if item["优先级"] == "高")
        head = (f"现金进出 {len(rows)} 张，其中 {over} 张超过门槛。最大的一笔落在"
                f"{top['主体']}：{_money(top['金额'])}。")
    else:
        head = "这份账里没有现金收付，抽屉没有动过。"
        blocks.append({"placeholder":
                       "库存现金全程没有发生额。这是好事，也说明这一层没有必要往下看。"})

    footnotes = [
        "识别只看一个动作：凭证里「库存现金」有没有动。**贷方 = 现金支付**（钱从抽屉出去），"
        "**借方 = 现金收款**（钱进抽屉）——这里包括从银行提现。正常企业走对公转账，"
        "提几十万现金出来本身就是问题，所以不把提现剔出去。",
        "**门槛取低**：本账该类均值 × 50%，与 **5,000 元** 硬线比，哪个低用哪个。"
        "硬线的依据是「企业很少有超过几千元的现金收支」；仍跟均值比，是因为一份账里"
        "可能有一堆两三千的收支——只看 5,000 会全放过。低于门槛的不删，只排在后面。",
        "**为什么单独一块**：它跟科目组合无关。一张凭证用现金付了 25 万，无论科目怎么配、"
        "落在哪个业务群，这件事本身就要看：票据容易缺、个税容易漏，也是账外资金最常见的出口。",
    ]

    return {
        "id": "layer_cash",
        "no": "3.4",
        "title": "现金收付",
        "kicker": "行为层 · 抽屉里进出的钱",
        "note": "银行付出去的钱有流水、有回单；现金付出去的钱只有一个签名。这一层不看科目配得对不对，"
                "只看有没有直接从抽屉里拿钱——尤其是拿给谁、拿了多少。",
        "headline": head,
        "stats": chips,
        "blocks": blocks,
        "count": len(rows),
        "footnotes": footnotes,
    }


def _abnormal_section(leads_doc: Dict[str, Any]) -> Dict[str, Any]:
    info = (leads_doc.get("stats") or {}).get("孤儿冲销") or {}
    rows = [item for item in (leads_doc.get("leads") or [])
            if item["类型"] == "孤儿冲销"]

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
        "no": "3.5",
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
#
# 版式是"审计卷宗"的样子：深墨蓝封面 + 暖纸内页 + 铜色标尺。三条规矩：
#
# 1. **字号按"在投影上读"给**。原账里的人名、金额是要对着屏幕念的，正文 16px 起，
#    表格 14px，注释 15px；小字只出现在标签上（11–12.5px），且不承载结论。
# 2. **颜色只承担语义**。铜色 = 路标（节号、当前项），墨蓝 = 数据，红绿黄只给
#    通过/警告/问题。业务域用五个固定色，全篇一致，读者记住一次就够。
# 3. **一屏一件���**。封面给结论与四个数，每节先给一句人话，再给图表，最后给表。
#
# 仍是单文件：样式与脚本内联，图是 SVG，不引任何外链。脚本只做三件锦上添花的事
# （目录高亮、隐藏解说、打印），禁用脚本后报告照样完整可读。

_CSS = """
:root{
  /* 墨、纸、线 —— 与"毛利率归因工作台"同一族色 */
  --ink:#16211c;--ink2:#48544c;--muted:#7c7466;--faint:#9a9181;
  --paper:#f4f1ea;--card:#fffdf8;--line:#e6dfd1;--line2:#d8cfbc;
  /* 墨绿（主）+ 浅金（标尺）+ 蓝（对照） */
  --green:#0b6b4f;--green2:#2f7d64;--green-l:#eaf5ee;
  --gold:#a67c3d;--gold2:#c9a86d;--gold-l:#f5ecda;--blue:#315c72;
  /* 语义 */
  --ok:#0b6b4f;--ok-bg:#eaf5ee;--ok-line:#c6e0d2;
  --warn:#8f6118;--warn-bg:#faf0d9;--warn-line:#e4cb98;
  --bad:#b23b2c;--bad-bg:#fbece8;--bad-line:#efc8bf;
  /* 业务域：都在低饱和的暖冷之间，不抢数据 */
  --d1:#1d6550;--d2:#6f827b;--d3:#b8894b;--d4:#315c72;--d5:#b3a894;
  --r:18px;--r-sm:13px;
  --shadow:0 2px 6px rgba(20,32,27,.035),0 22px 54px -34px rgba(20,32,27,.35);
  --sans:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",-apple-system,
    BlinkMacSystemFont,"Segoe UI",sans-serif;
  --mono:"SF Mono","JetBrains Mono",Consolas,"Courier New",monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;color:var(--ink);
  font:16px/1.75 var(--sans);-webkit-font-smoothing:antialiased;
  font-feature-settings:"kern" 1;text-rendering:optimizeLegibility;
  /* 纸底上两团极淡的光：一冷一暖，页面不再是一块平色 */
  background:
    radial-gradient(1100px 560px at 8% -8%,rgba(20,83,66,.06),transparent 60%),
    radial-gradient(900px 480px at 98% 2%,rgba(184,137,75,.07),transparent 56%),
    linear-gradient(180deg,#f8f6f0 0%,#f2efe8 100%);
  background-attachment:fixed}
.sheet{max-width:1180px;margin:0 auto;
  background:linear-gradient(180deg,#fffdf8 0%,#fdfbf6 100%);
  box-shadow:var(--shadow);overflow:hidden}
b,strong{font-weight:650}
a{color:var(--green)}
::selection{background:var(--green-l);color:var(--green)}

/* ── 封面 ───────────────────────────────────────────────────────── */
.cover{position:relative;overflow:hidden;color:#f7f2e6;
  padding:52px 64px 0;
  background:
    radial-gradient(780px 460px at 86% -14%,rgba(201,168,109,.24),transparent 62%),
    radial-gradient(560px 340px at 2% 110%,rgba(64,150,120,.22),transparent 66%),
    linear-gradient(138deg,#12352a 0%,#164033 52%,#0d2921 100%)}
.cover::before{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(460px 460px at 97% -6%,rgba(201,168,109,.13),transparent 64%)}
.cover-inner{position:relative;z-index:1}
.eyebrow{display:flex;align-items:center;gap:10px;font-size:11.5px;font-weight:650;
  letter-spacing:.2em;color:var(--gold2)}
.eyebrow .dot{width:5px;height:5px;border-radius:50%;background:var(--gold2);
  box-shadow:0 0 0 3px rgba(201,168,109,.18)}
.cover h1{margin:20px 0 0;font-size:44px;line-height:1.16;font-weight:650;
  letter-spacing:-.035em;color:#fffdf7}
.cover .lede{margin:15px 0 0;font-size:17px;color:rgba(247,242,230,.78);
  line-height:1.7;max-width:70ch}
.cover .lede b{color:#fffdf7;font-weight:600}

.verdict{display:flex;align-items:center;gap:16px;margin:28px 0 0;
  padding:16px 22px 16px 18px;border-radius:16px;
  background:linear-gradient(180deg,rgba(255,255,255,.075),rgba(255,255,255,.035));
  border:1px solid rgba(216,198,164,.2);
  box-shadow:inset 0 1px 0 rgba(255,255,255,.1)}
.verdict .seal{flex:0 0 38px;width:38px;height:38px;border-radius:50%;display:grid;
  place-items:center;font-size:17px;font-weight:700;line-height:1;
  background:rgba(127,227,180,.16);color:#7fe3b4;
  border:1px solid rgba(127,227,180,.32)}
.verdict b{display:block;font-size:17px;font-weight:650;color:#fffdf7;line-height:1.45}
.verdict span{display:block;font-size:14px;color:rgba(247,242,230,.62)}
.verdict.warn .seal{background:rgba(201,168,109,.16);color:var(--gold2);
  border-color:rgba(201,168,109,.34)}
.verdict.bad .seal{background:rgba(255,159,145,.14);color:#ff9f91;
  border-color:rgba(255,159,145,.3)}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);margin:32px 0 0;
  border-top:1px solid rgba(216,198,164,.16)}
.kpi{padding:22px 24px 24px 0;border-right:1px solid rgba(216,198,164,.14)}
.kpi:last-child{border-right:0;padding-right:0}
.kpi span{display:block;font-size:11.5px;letter-spacing:.1em;font-weight:600;
  color:var(--gold2)}
.kpi b{display:block;margin:9px 0 3px;font-size:40px;line-height:1.04;
  font-weight:620;letter-spacing:-.035em;font-variant-numeric:tabular-nums;
  color:#fbf7ec}
.kpi small{display:block;font-size:12.5px;color:rgba(247,242,230,.55)}
.kpi.bad b{color:#ff9f91}

.cover-foot{position:relative;z-index:1;display:flex;justify-content:space-between;
  gap:20px;flex-wrap:wrap;margin-top:30px;padding:15px 0 17px;
  border-top:1px solid rgba(216,198,164,.14);
  font-size:12.5px;color:rgba(247,242,230,.5);letter-spacing:.01em}

/* ── 目录（吸顶） ───────────────────────────────────────────────── */
.navwrap{position:sticky;top:0;z-index:30;display:flex;align-items:stretch;
  background:rgba(248,246,240,.88);-webkit-backdrop-filter:blur(12px);
  backdrop-filter:blur(12px);border-bottom:1px solid rgba(20,32,27,.09);
  box-shadow:0 1px 0 rgba(255,255,255,.65) inset}
.toc{display:flex;align-items:stretch;gap:2px;padding:0 0 0 56px;flex:1 1 auto;
  min-width:0;overflow-x:auto;scrollbar-width:none}
.toc::-webkit-scrollbar{display:none}
.toc a{position:relative;flex:0 0 auto;padding:15px 10px 13px;font-size:13px;
  color:var(--muted);text-decoration:none;white-space:nowrap;
  border-bottom:2px solid transparent;transition:color .15s}
.toc a b{margin-right:7px;font-weight:600;font-size:12.5px;color:var(--faint);
  font-variant-numeric:tabular-nums;letter-spacing:.04em}
.toc a:hover{color:var(--ink)}
.toc a.on{color:var(--green);border-bottom-color:var(--green)}
.toc a.on b{color:var(--green2)}
.tools{display:flex;align-items:center;gap:8px;flex:0 0 auto;padding:0 24px 0 20px;
  border-left:1px solid rgba(20,32,27,.09);background:rgba(248,246,240,.96)}
.tools button{font:inherit;font-size:12px;color:var(--ink2);cursor:pointer;
  padding:6px 11px;border:1px solid var(--line2);border-radius:999px;
  background:#fffefa;transition:.15s;white-space:nowrap;
  box-shadow:0 1px 2px rgba(20,32,27,.03)}
.tools button:hover{border-color:var(--green2);color:var(--green);background:var(--green-l)}
.tools button[aria-pressed="true"]{background:#12362b;border-color:#12362b;color:#f8f3e7}

/* ── 分节 ───────────────────────────────────────────────────────── */
main{padding:10px 64px 72px}
section{margin-top:58px;scroll-margin-top:78px}
.sec-head{display:flex;align-items:baseline;gap:16px;
  padding-bottom:15px;border-bottom:1px solid var(--line)}
/* 节号是"页边上的编号"，不是徽章：大、细、右缘贴齐，标题从它右边起。
   用 baseline 而不是 flex-end——数字的基线要和标题的基线落在同一条线上 */
.secno{flex:0 0 76px;text-align:right;font-size:44px;line-height:1;font-weight:400;
  letter-spacing:-.04em;color:#9a7038;font-variant-numeric:tabular-nums}
.sec-title{display:flex;flex-direction:column;gap:2px;min-width:0}
section h2{margin:0;font-size:26px;line-height:1.26;font-weight:650;
  letter-spacing:-.028em;color:var(--ink)}
.kicker{font-size:13px;color:var(--muted);letter-spacing:.01em}
.count{margin-left:auto;font-size:12.5px;color:#8b744d;background:#f7f2e8;
  border:1px solid #e5ddce;padding:5px 12px;border-radius:999px;
  font-variant-numeric:tabular-nums;white-space:nowrap;align-self:center}
.note{margin:20px 0 0;max-width:80ch;font-size:15.5px;line-height:1.9;color:var(--ink2);
  text-wrap:pretty}
.note b{color:var(--ink)}
h3.sub{margin:42px 0 0;font-size:15px;font-weight:650;color:var(--ink);letter-spacing:-.005em;
  display:flex;align-items:center;gap:10px}
h3.sub::before{content:"";width:14px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,var(--gold2),rgba(201,168,109,0))}

/* ── 数字卡 ─────────────────────────────────────────────────────── */
.statrow{display:grid;grid-template-columns:repeat(auto-fit,minmax(184px,1fr));
  gap:14px;margin:22px 0 0}
.stat{position:relative;overflow:hidden;padding:16px 18px 17px;
  border:1px solid #e6dfd1;border-radius:var(--r-sm);
  background:linear-gradient(180deg,#fffefb 0%,#faf7f0 100%);
  box-shadow:0 5px 14px rgba(20,32,27,.035)}
.stat::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
  background:#cfc4b0}
.stat span{display:block;font-size:12px;color:#7c7466;letter-spacing:.035em;
  font-weight:600;line-height:1.5}
.stat b{display:block;margin-top:6px;font-size:25px;line-height:1.25;font-weight:640;
  font-variant-numeric:tabular-nums;letter-spacing:-.03em;color:#17231e}
.stat.bad::before{background:var(--bad)}
.stat.bad b{color:var(--bad)}
.stat.bad{border-color:var(--bad-line);background:linear-gradient(180deg,#fffaf8,#fdf2ee)}

/* ── 结论句 ─────────────────────────────────────────────────────── */
p.head{margin:24px 0 0;padding:19px 24px 19px 22px;font-size:16.5px;line-height:1.85;
  color:#1c2a24;text-wrap:pretty;border-radius:0 14px 14px 0;
  background:linear-gradient(100deg,var(--green-l) 0%,rgba(234,245,238,.42) 46%,#fffdf8 100%);
  border-left:3px solid var(--green2)}

/* ── 表格 ───────────────────────────────────────────────────────── */
.tablewrap{margin-top:16px;border:1px solid #e7e0d3;border-radius:16px;
  overflow:hidden;background:#fffefa}
table{width:100%;border-collapse:collapse;font-size:14px;line-height:1.6;
  font-variant-numeric:tabular-nums}
table.fixed{table-layout:fixed}
thead th{background:#f7f2e8;color:#8b8375;font-size:11.5px;font-weight:650;
  letter-spacing:.06em;padding:11px 16px;border-bottom:1px solid #e7e0d3;
  white-space:nowrap;text-align:left}
td{padding:13px 16px;border-bottom:1px dashed #eee8dc;vertical-align:middle;
  text-align:left}
tbody tr:last-child td{border-bottom:0}
tbody tr{transition:background .12s}
tbody tr:hover{background:#fbf8f1}
td:first-child{font-weight:600;color:var(--ink)}
td.num,th.num{text-align:right;white-space:nowrap}
td.wrap{white-space:normal;font-weight:400;color:var(--ink2)}
.badge{display:inline-block;font-size:12px;font-weight:650;padding:3px 10px;
  border-radius:999px;line-height:1.6;white-space:nowrap;letter-spacing:.01em}
.badge.ok{background:var(--ok-bg);color:var(--ok);border:1px solid var(--ok-line)}
.badge.warn{background:var(--warn-bg);color:var(--warn);border:1px solid var(--warn-line)}
.badge.bad{background:var(--bad-bg);color:var(--bad);border:1px solid var(--bad-line)}
.badge.mute{background:#f2eee5;color:#7c7466;border:1px solid #e5ddce}

/* 占比条：数字照写，条只是让"谁大谁小"变成一眼的事 */
.barcell{display:flex;align-items:center;gap:10px;justify-content:flex-end}
.barcell .track{flex:1;min-width:34px;max-width:92px;height:7px;border-radius:999px;
  background:#f0ebe0;overflow:hidden}
.barcell .track i{display:block;height:100%;border-radius:999px;
  background:linear-gradient(90deg,#4d9b80,#1d6550)}
.barcell em{font-style:normal;color:var(--ink2);min-width:46px;text-align:right}

/* HHI 热力：只用单一深绿，不用红绿——集中度高不等于坏 */
.heat{display:inline-block;min-width:58px;text-align:right;padding:3px 9px;
  border-radius:8px;font-variant-numeric:tabular-nums}
.heat.strong{color:#fbf7ec;font-weight:650}

/* ── 图 ─────────────────────────────────────────────────────────── */
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));
  gap:16px;margin-top:20px}
.charts.single{grid-template-columns:1fr}
figure{margin:0;border:1px solid #e6dfd2;border-radius:16px;
  background:linear-gradient(180deg,#fffefa,#fdfaf3);
  padding:18px 20px 10px;
  box-shadow:0 4px 12px rgba(20,32,27,.025)}
figure svg{display:block;width:100%;height:auto}

/* ── 金额构成（域） ─────────────────────────────────────────────── */
.comp{margin-top:20px;border:1px solid #e7e0d3;border-radius:16px;
  background:#fffefa;padding:22px 24px 18px;
  box-shadow:0 4px 12px rgba(20,32,27,.025)}
.comp-bar{display:flex;width:100%;height:36px;border-radius:9px;overflow:hidden;
  gap:2px;background:#f0ebe0;box-shadow:inset 0 1px 2px rgba(20,32,27,.06)}
.comp-bar .seg{display:flex;align-items:center;justify-content:center;font-size:12.5px;
  font-weight:650;color:#fffdf8;letter-spacing:.02em;min-width:0;overflow:hidden}
.comp-bar .seg.d1{background:var(--d1)}.comp-bar .seg.d2{background:var(--d2)}
.comp-bar .seg.d3{background:var(--d3)}.comp-bar .seg.d4{background:var(--d4)}
.comp-bar .seg.d5{background:var(--d5)}
.comp-legend{display:grid;grid-template-columns:1fr 1fr;gap:0 44px;margin-top:16px}
.comp-legend .row{display:grid;grid-template-columns:11px minmax(56px,auto) 1fr auto;
  align-items:center;gap:10px;padding:9px 0;border-bottom:1px dashed #eee8dc;
  font-size:13.5px;white-space:nowrap}
.comp-legend .sw{width:10px;height:10px;border-radius:3px}
.comp-legend .nm{color:var(--ink);font-weight:600}
.comp-legend .vl{color:var(--ink2);font-variant-numeric:tabular-nums;text-align:right}
.comp-legend .pc{color:var(--muted);font-size:12.5px;text-align:right;min-width:86px;
  font-variant-numeric:tabular-nums}

/* ── 排行 ───────────────────────────────────────────────────────── */
.rank{margin-top:22px}
.rank h4{margin:0 0 12px;font-size:12px;font-weight:650;color:#9a9181;
  letter-spacing:.09em}
.rank .row{display:grid;grid-template-columns:26px 132px 1fr 132px;align-items:center;
  gap:14px;padding:9px 0;border-bottom:1px dashed #eee8dc;font-size:14px}
.rank .row:last-child{border-bottom:0}
.rank .rk{font:600 12px/1 var(--mono);color:var(--faint)}
.rank .nm{color:var(--ink);font-weight:600;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.rank .nm i{font-style:normal;font-size:11.5px;color:var(--muted);font-weight:400;
  margin-left:7px}
.rank .bar{height:9px;border-radius:999px;background:#f2eee5;overflow:hidden}
.rank .bar i{display:block;height:100%;border-radius:999px;
  background:linear-gradient(90deg,#4d9b80,#1d6550)}
.rank .vl{text-align:right;color:var(--ink2);font-variant-numeric:tabular-nums;
  font-size:13.5px}
.rank .vl em{font-style:normal;color:var(--muted);margin-left:7px;font-size:12.5px}

/* ── 优先级分布 ─────────────────────────────────────────────────── */
.prio{margin-top:22px;border:1px solid #e7e0d3;border-radius:16px;background:#fffefa;
  padding:18px 22px;box-shadow:0 4px 12px rgba(20,32,27,.025)}
.prio-bar{display:flex;height:13px;border-radius:999px;overflow:hidden;gap:2px;
  background:#f2eee5}
.prio-bar span{display:block;height:100%}
.prio-bar .hi{background:linear-gradient(90deg,#cf6a58,#b23b2c)}
.prio-bar .mid{background:linear-gradient(90deg,#d9b273,#b8894b)}
.prio-bar .lo{background:#c3b9a8}
.prio-legend{display:flex;flex-wrap:wrap;gap:22px;margin-top:12px;font-size:13px;
  color:var(--ink2)}
.prio-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;
  margin-right:7px}
.prio-legend b{font-variant-numeric:tabular-nums;color:var(--ink);margin-left:5px}

/* ── 分层导览 ───────────────────────────────────────────────────── */
.layers{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));
  gap:14px;margin:22px 0 0}
.lcard{position:relative;display:block;text-decoration:none;color:inherit;
  border:1px solid #e6dfd1;border-radius:16px;padding:17px 18px 15px;
  background:linear-gradient(180deg,#fffefb 0%,#faf7f0 100%);
  box-shadow:0 5px 14px rgba(20,32,27,.035);
  transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease;
  overflow:hidden}
.lcard::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
  background:transparent;transition:background .18s}
.lcard:hover{transform:translateY(-1px);border-color:#dcd3c2;
  box-shadow:0 10px 22px rgba(20,32,27,.06)}
.lcard:hover::before{background:var(--green2)}
.lcard .no{font:600 11px/1 var(--mono);color:var(--faint);letter-spacing:.1em}
.lcard .nm{display:block;margin:8px 0 1px;font-size:14.5px;font-weight:650;color:#2c3832}
.lcard .ct{margin-top:2px;font-size:31px;font-weight:640;line-height:1.1;color:#17231e;
  font-variant-numeric:tabular-nums;letter-spacing:-.04em}
.lcard small{display:block;margin-top:4px;font-size:12px;color:#9a9181}
.lcard .mini{display:block;height:4px;border-radius:999px;background:#f0ebe0;
  margin-top:12px;overflow:hidden}
.lcard .mini i{display:block;height:100%;border-radius:999px;
  background:linear-gradient(90deg,#4d9b80,#1d6550)}
.lcard.zero .ct{color:var(--faint)}
.lcard.zero::before{background:var(--line2)}
.lcard.zero .mini i{background:var(--line2)}

/* ── 空态、脚注、页脚 ───────────────────────────────────────────── */
.empty{margin-top:18px;padding:20px 22px;border:1px dashed var(--line2);
  border-radius:14px;background:#faf7f0;color:var(--ink2);font-size:14.5px;line-height:1.9}
ul.notes{margin:26px 0 0;padding:0;list-style:none;color:#9a9181;
  font-size:13px;line-height:1.95;max-width:94ch;text-wrap:pretty}
ul.notes li{position:relative;padding-left:17px;margin-top:6px}
ul.notes li::before{content:"";position:absolute;left:2px;top:.86em;width:4px;height:4px;
  border-radius:50%;background:#cfc4b0}
ul.notes b{color:#5c665f;font-weight:650}

footer{display:flex;gap:40px;flex-wrap:wrap;justify-content:space-between;
  padding:34px 64px 38px;background:#f7f2e8;border-top:1px solid #e6dfd1;
  color:#9b9282;font-size:12.5px;line-height:1.95}
footer b{display:block;color:#3d4842;font-size:13px;font-weight:650;
  margin-bottom:6px;letter-spacing:.01em}
footer div{max-width:48ch}

/* 只看结论：把解说与脚注收起来，表格和图留着 */
body.compact .note,body.compact ul.notes{display:none}

@media (max-width:980px){
  .cover{padding:36px 28px 0}
  .cover h1{font-size:31px}
  .cover .lede{font-size:15.5px}
  main{padding:8px 28px 48px}
  .toc{padding:0 20px}
  .kpis{grid-template-columns:repeat(2,1fr)}
  .kpi{padding:18px 18px 18px 0}
  .kpi:nth-child(2){border-right:0}
  .kpi:nth-child(n+3){border-top:1px solid rgba(216,198,164,.14)}
  .kpi b{font-size:33px}
  .secno{flex:0 0 52px;font-size:30px}
  section h2{font-size:22px}
  footer{padding:28px}
  .rank .row{grid-template-columns:22px 96px 1fr;row-gap:6px}
  .rank .vl{grid-column:2/4;text-align:left}
  .charts{grid-template-columns:1fr}
  .comp-legend{grid-template-columns:1fr!important;grid-template-rows:none!important;
    grid-auto-flow:row!important}
}
/* 窄屏不强压：八列的表和图挤进 430px 只会变成一片碎字，宁可横向滑动 */
@media (max-width:700px){
  .tablewrap{overflow-x:auto}
  table{min-width:640px}
  figure{overflow-x:auto}
  figure svg{min-width:520px}
  .comp-bar{height:28px}
  .comp-bar .seg{font-size:11px}
}

/* ── 2.4 科目对投入产出矩阵 ─────────────────────────────────────── */
.mx-tools{display:flex;justify-content:space-between;align-items:center;gap:14px;
  margin:4px 0 10px;flex-wrap:wrap}
.mx-hint{font-size:12.5px;color:var(--muted)}
.mxseg{display:inline-flex;border:1px solid var(--line2);border-radius:9px;
  overflow:hidden;background:var(--card)}
.mxseg button{border:0;background:transparent;padding:5px 13px;font:inherit;
  font-size:12.5px;color:var(--ink2);cursor:pointer;transition:.15s}
.mxseg button+button{border-left:1px solid var(--line)}
.mxseg button.on{background:var(--green);color:#fff}
.mx-wrap{overflow-x:auto;border:1px solid var(--line);
  border-radius:var(--r-sm);background:var(--card)}
table.mx{border-collapse:separate;border-spacing:0;font-size:8px;min-width:0;
  table-layout:fixed;width:max-content}
table.mx th.c{width:17px;min-width:17px;max-width:17px;
  position:sticky;top:0;z-index:3;background:#f7f3ea;
  border-bottom:1px solid var(--line2);writing-mode:vertical-rl;
  transform:rotate(180deg);padding:6px 0;height:112px;font-weight:500;
  font-size:10px;color:var(--ink2);white-space:nowrap}
table.mx th.r{position:sticky;left:0;z-index:2;background:#f7f3ea;
  border-right:1px solid var(--line2);text-align:right;padding:0 8px;
  height:17px;font-weight:400;font-size:10px;color:var(--ink2);
  white-space:nowrap;min-width:96px;max-width:96px;overflow:hidden}
table.mx th.corner{position:sticky;left:0;top:0;z-index:5;background:#efeade;
  border-right:1px solid var(--line2);border-bottom:1px solid var(--line2);
  font-size:10px;color:var(--muted);padding:4px 8px;text-align:left;
  white-space:nowrap;min-width:96px;max-width:96px;overflow:hidden}
table.mx td{width:17px;min-width:17px;max-width:17px;height:17px;border-right:1px solid rgba(230,223,209,.5);
  border-bottom:1px solid rgba(230,223,209,.5);text-align:center;
  color:var(--ink);font-size:8px;line-height:17px;padding:0;cursor:default}
table.mx td.zero{background:transparent}
table.mx td.diag{box-shadow:inset 0 0 0 1.2px var(--gold2)}
table.mx td:hover{outline:1.6px solid var(--ink);outline-offset:-1.6px}
table.mx tfoot td{position:sticky;bottom:0;z-index:2;background:#f7f3ea;
  border-top:1px solid var(--line2);font-size:8px;color:var(--faint)}
table.mx tfoot th{position:sticky;left:0;bottom:0;z-index:4;background:#efeade;
  border-top:1px solid var(--line2);border-right:1px solid var(--line2);
  text-align:right;padding:0 8px;font-size:10px;color:var(--muted);
  font-weight:500;white-space:nowrap}
@media print{
  body{background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .sheet{box-shadow:none;max-width:none;background:#fffdf8}
  .navwrap{display:none}
  main{padding:0 0 24px}
  section{margin-top:34px}
  .lcard:hover{transform:none;box-shadow:0 5px 14px rgba(20,32,27,.035)}
  tr,figure,.lcard,.stat,.kpi,.comp,.prio{break-inside:avoid}
  section h2,h3.sub{break-after:avoid}
  @page{margin:14mm 12mm}
}
@media (prefers-reduced-motion:reduce){
  *{transition:none!important;animation:none!important;scroll-behavior:auto!important}
}

"""


def _pct(value: Any) -> Optional[float]:
    """'26.5%' → 26.5。读不出来返回 None（那一格就不画条）。"""
    text = str(value or "").strip().rstrip("%").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _wan_value(value: Any) -> float:
    try:
        return float(value) / 1e4
    except (TypeError, ValueError):
        return 0.0


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
        scope = " scope='col'"
        return (f"<th{scope} class='num'>{html.escape(name)}</th>" if index in numeric
                else f"<th{scope}>{html.escape(name)}</th>")

    def body_cell(index: int, name: str, value: Any) -> str:
        text = html.escape(str(value if value is not None else ""))
        classes: List[str] = []
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
                    f"style='background:rgba(11,107,79,{alpha:.3f})'>{text}</span>")
        attr = f" class='{' '.join(classes)}'" if classes else ""
        return f"<td{attr}>{text}</td>"

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


def _axis(peak: float, target: int = 4) -> Tuple[float, float]:
    """返回 (刻度上限, 刻度间隔)。间隔只落在 1/2/2.5/5 ×10^k 上。

    按比例定上限会让刻度变成 12,500 / 37,500 这种读不出来的数；先定一个"整"的
    间隔、再把上限抬到它的整数倍，刻度就都是好念的数。
    """
    if peak <= 0:
        return 1.0, 0.25
    raw = peak / target
    magnitude = 10 ** int(math.floor(math.log10(raw)))
    step = magnitude * 10
    # 倍率给得细一点（含 1.25/1.5）：只有 1/2/5 的话，峰值 50,607 会被抬到
    # 80,000 的上限，柱子在图里只占六成高，斜率全被浪费掉了
    for multiple in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if raw <= magnitude * multiple:
            step = magnitude * multiple
            break
    while step * target < peak:
        step += step
    return step * target, step


def _svg_months(months: List[Dict[str, Any]]) -> str:
    """月度发生额：柱子看钱，折线看凭证数。

    借贷两侧每月都相等（不平衡的月份进不了这张图），所以两根柱是同一根——
    这里画的是**业务节律**：钱在几月堆起来、凭证在几月变碎。双轴容易误读，
    但柱和线各自有自己的刻度标签，且一粗一细，读起来不会串。
    """
    if not months:
        return ""
    width, height = 1000, 330
    left, right, top, bottom = 66, 58, 46, 58
    plot_w = width - left - right
    plot_h = height - top - bottom
    base = height - bottom

    values = [_wan_value(m.get("debit")) for m in months]
    counts = [int(m.get("vouchers") or 0) for m in months]
    vmax, vstep = _axis(max(values) or 1.0)
    cmax, cstep = _axis(max(counts) or 1)

    # 只有两三个月时不要把这几个槽位撑满整幅：柱子相隔半米远，读起来像两张图
    slot = min(92.0, plot_w / len(months))
    x0 = left + (plot_w - slot * len(months)) / 2
    bar_w = min(46.0, slot * 0.46)
    grid = "#ece5d8"
    ink, muted, data, accent = "#48544c", "#9a9181", "#2f7d64", "#b8894b"

    parts: List[str] = [
        f"<svg viewBox='0 0 {width} {height}' role='img' "
        f"aria-label='月度发生额与凭证数'>",
        "<defs><linearGradient id='mb' x1='0' y1='0' x2='0' y2='1'>"
        f"<stop offset='0%' stop-color='#4d9b80'/><stop offset='100%' "
        f"stop-color='#1d6550'/></linearGradient></defs>",
        f"<text x='0' y='14' font-size='14' font-weight='650' fill='{ink}'>"
        f"月度发生额与凭证张数</text>",
        f"<text x='0' y='32' font-size='11.5' fill='{muted}'>单位：万元 / 张</text>",
    ]

    ticks = int(round(vmax / vstep))
    for index in range(ticks + 1):
        frac = index / ticks
        y = base - frac * plot_h
        parts.append(f"<line x1='{left}' y1='{y:.1f}' x2='{width - right}' "
                     f"y2='{y:.1f}' stroke='{grid}' stroke-width='1'/>")
        parts.append(f"<text x='{left - 12}' y='{y + 4:.1f}' font-size='11' "
                     f"fill='{muted}' text-anchor='end'>{vstep * index:,.0f}</text>")
        if cstep * index <= cmax:
            parts.append(f"<text x='{width - right + 12}' y='{y + 4:.1f}' font-size='11' "
                         f"fill='{accent}' text-anchor='start'>"
                         f"{cstep * index:,.0f}</text>")

    # 折线先画，柱子压在上层会盖住线尾；反过来说，线要在柱前面才看得见走势
    points = []
    for index, count in enumerate(counts):
        cx = x0 + slot * (index + 0.5)
        cy = base - (count / cmax) * plot_h * 0.94
        points.append((cx, cy))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    parts.append(f"<polyline points='{path}' fill='none' stroke='{accent}' "
                 f"stroke-width='2' stroke-linejoin='round' stroke-linecap='round' "
                 f"opacity='.85'/>")

    for index, month in enumerate(months):
        cx = x0 + slot * (index + 0.5)
        value = values[index]
        bar_h = value / vmax * plot_h
        tip = (f"{month.get('period')}　借方 {value:,.0f} 万　"
               f"{counts[index]:,} 张凭证")
        parts.append(f"<g><title>{html.escape(tip)}</title>"
                     f"<rect x='{cx - bar_w / 2:.1f}' y='{base - bar_h:.1f}' "
                     f"width='{bar_w:.1f}' height='{max(bar_h, 1.0):.1f}' rx='4' "
                     f"fill='url(#mb)'/></g>")
        label = str(month.get("period") or "")
        parts.append(f"<text x='{cx:.1f}' y='{base + 20}' font-size='11.5' "
                     f"fill='{muted}' text-anchor='middle'>{html.escape(label[-2:])}月</text>")
        parts.append(f"<circle cx='{points[index][0]:.1f}' cy='{points[index][1]:.1f}' "
                     f"r='3.4' fill='#fffefa' stroke='{accent}' stroke-width='2'/>")

    # 只给最高的那根标数：标满一排数字，读者又要逐个比较
    top_index = max(range(len(values)), key=lambda i: values[i])
    top_x = x0 + slot * (top_index + 0.5)
    top_y = base - values[top_index] / vmax * plot_h
    parts.append(f"<text x='{top_x:.1f}' y='{top_y - 9:.1f}' font-size='12' "
                 f"font-weight='650' fill='{data}' text-anchor='middle'>"
                 f"{values[top_index]:,.0f}</text>")

    parts.append(f"<line x1='{left}' y1='{base}' x2='{width - right}' y2='{base}' "
                 f"stroke='#d8cfbc' stroke-width='1.2'/>")

    ly = height - 14
    parts.append(f"<rect x='{left}' y='{ly - 8}' width='10' height='10' rx='2.5' "
                 f"fill='url(#mb)'/>")
    parts.append(f"<text x='{left + 17}' y='{ly + 1}' font-size='11.5' fill='{muted}'>"
                 f"借方发生额</text>")
    parts.append(f"<circle cx='{left + 108}' cy='{ly - 3}' r='3.4' fill='#fffdf8' "
                 f"stroke='{accent}' stroke-width='2'/>")
    parts.append(f"<text x='{left + 118}' y='{ly + 1}' font-size='11.5' fill='{muted}'>"
                 f"凭证张数（右轴）</text>")
    parts.append("</svg>")
    return "".join(parts)


def _svg_chart(title: str, obs: Dict[int, int], expect: Dict[int, float],
               digits: Sequence[int], note: str = "") -> str:
    """一个数字分布图：每个数字两根柱（实际/期望），高度都是占比。

    自己画 SVG 不引图表库，是为了让报告保持单文件：外链一个 echarts，报告离开
    网络就废了，而报告是要发给客户、要归档的。
    """
    # 尺寸按"两列并排时一格多宽"来定（1180 页宽 → 每格约 520）：
    # 画 470 再让它 scale 上去，字号会被放大成大字报；正好画成实际宽度，
    # 文字就是设计时的字号。
    width, height = 470, 300
    left, right, top, bottom = 52, 16, 54, 56
    plot_h = height - top - bottom
    baseline = height - bottom
    total = sum(obs.values()) or 1

    ink, dim, grid = "#16211c", "#9a9181", "#ece5d8"
    obs_fill, exp_fill, exp_edge = "#2f7d64", "#f0e3c9", "#ddc9a8"

    values = [obs.get(d, 0) / total for d in digits]
    exps = [expect.get(d, 0.0) for d in digits]
    peak = max(max(values or [0.0]), max(exps or [0.0])) or 0.01
    # 上沿抬到 10% 的整数倍：刻度就是 0/10/20/30%，不用画一半去标 17.5%
    ymax = math.ceil(peak * 1.06 * 10) / 10

    step = (width - left - right) / len(digits)
    bar = min(14.0, step * 0.3)
    parts: List[str] = [
        f"<svg viewBox='0 0 {width} {height}' role='img' "
        f"aria-label='{html.escape(title)}'>",
        f"<text x='{left - 8}' y='22' font-size='14' font-weight='650' fill='{ink}'>"
        f"{html.escape(title)}</text>",
    ]
    if note:
        parts.append(f"<text x='{width - right}' y='22' font-size='11.5' fill='{dim}' "
                     f"text-anchor='end'>{html.escape(note)}</text>")

    ticks = int(round(ymax * 10))
    for index in range(ticks + 1):
        y = baseline - (index / ticks) * plot_h
        parts.append(f"<line x1='{left - 4}' y1='{y:.1f}' x2='{width - right}' "
                     f"y2='{y:.1f}' stroke='{grid}' stroke-width='1'/>")
        parts.append(f"<text x='{left - 10}' y='{y + 4:.1f}' font-size='10.5' "
                     f"fill='{dim}' text-anchor='end'>{index * 10}%</text>")

    for index, digit in enumerate(digits):
        cx = left + step * (index + 0.5)
        exp_h = exps[index] / ymax * plot_h
        obs_h = values[index] / ymax * plot_h
        parts.append(f"<rect x='{cx - bar - 2.5:.1f}' y='{baseline - exp_h:.1f}' "
                     f"width='{bar:.1f}' height='{max(exp_h, 1.0):.1f}' rx='2' "
                     f"fill='{exp_fill}' stroke='{exp_edge}' stroke-width='.8'/>")
        parts.append(f"<rect x='{cx + 2.5:.1f}' y='{baseline - obs_h:.1f}' "
                     f"width='{bar:.1f}' height='{max(obs_h, 1.0):.1f}' rx='2' "
                     f"fill='{obs_fill}'/>")
        parts.append(f"<text x='{cx:.1f}' y='{baseline + 22}' font-size='12' "
                     f"fill='{dim}' text-anchor='middle'>{digit}</text>")

    parts.append(f"<line x1='{left - 4}' y1='{baseline}' x2='{width - right}' "
                 f"y2='{baseline}' stroke='#d6ccba' stroke-width='1.2'/>")
    # 图例画成两个小方块：只写"深色/浅色"要人去猜哪个是哪个
    ly = height - 16
    parts.append(f"<rect x='{left - 8}' y='{ly - 8}' width='10' height='10' rx='2.5' "
                 f"fill='{obs_fill}'/>")
    parts.append(f"<text x='{left + 9}' y='{ly + 1}' font-size='11.5' fill='{dim}'>实际</text>")
    parts.append(f"<rect x='{left + 52}' y='{ly - 8}' width='10' height='10' rx='2.5' "
                 f"fill='{exp_fill}' stroke='{exp_edge}' stroke-width='.8'/>")
    parts.append(f"<text x='{left + 69}' y='{ly + 1}' font-size='11.5' fill='{dim}'>"
                 f"本福特期望</text>")
    parts.append("</svg>")
    return "".join(parts)


def _html_charts(charts: List[Dict[str, Any]]) -> str:
    """每侧一张首位分布图。次位不做，所以这里没有第二条分支。"""
    figures: List[str] = []
    for chart in charts:
        side = chart.get("side") or ""
        data = chart.get("data") or {}
        block = data.get("first") or {}
        obs = {int(k): int(v) for k, v in (block.get("obs") or {}).items()}
        note = f"MAD {block.get('mad')} · {block.get('verdict')}"
        svg = _svg_chart(f"{side} · 首位", obs, benford_mod.FIRST_EXPECT,
                         list(range(1, 10)), note)
        # 标题在 SVG 里，figure 里不要再写一遍（会变成两行一模一样的字）
        figures.append(f"<figure>{svg}</figure>")
    return f"<div class='charts'>{''.join(figures)}</div>"


_DOMAIN_CLASS = {"经营": "d1", "投资": "d2", "筹资": "d3", "非现金": "d4"}


def _domain_rows(activities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按业务域把活动并起来：总量级的"钱从哪来、往哪去"。

    域比业务桶粗，正好是一个屏幕能说完的粒度；25 个桶全画成饼，谁也认不出。
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for item in activities:
        key = str(item.get("域") or "未标注")
        row = buckets.setdefault(key, {"域": key, "金额": 0.0, "凭证": 0, "活动": 0})
        row["金额"] += float(item.get("金额") or 0)
        row["凭证"] += int(item.get("凭证") or 0)
        row["活动"] += 1
    total = sum(row["金额"] for row in buckets.values()) or 1.0
    rows = sorted(buckets.values(), key=lambda r: -r["金额"])
    for row in rows:
        row["占比"] = row["金额"] / total * 100
        row["cls"] = _DOMAIN_CLASS.get(row["域"], "d5")
    return rows


def _html_composition(activities: List[Dict[str, Any]]) -> str:
    rows = _domain_rows(activities)
    if not rows:
        return ""
    total = sum(row["金额"] for row in rows) or 1.0

    segs = "".join(
        f"<span class='seg {row['cls']}' style='width:{row['占比']:.2f}%' "
        f"title='{html.escape(row['域'])} {row['占比']:.1f}%'>"
        f"{row['占比']:.0f}%</span>" if row["占比"] >= 8 else
        f"<span class='seg {row['cls']}' style='width:{row['占比']:.2f}%' "
        f"title='{html.escape(row['域'])} {row['占比']:.1f}%'></span>"
        for row in rows)

    legend = "".join(
        f"<div class='row'><span class='sw' style='background:var(--{row['cls']})'></span>"
        f"<span class='nm'>{html.escape(row['域'])}</span>"
        f"<span class='vl'>{row['金额'] / 1e4:,.0f} 万</span>"
        f"<span class='pc'>{row['占比']:.1f}% · {row['活动']} 个</span></div>"
        for row in rows)

    # 排行只取前 8：第 9 名往下都是零头，排出来只是让表更长
    top = activities[:8]
    peak = float(top[0].get("金额") or 1) if top else 1.0
    ranking = "".join(
        f"<div class='row'><span class='rk'>{index:02d}</span>"
        f"<span class='nm'>{html.escape(str(item.get('业务活动', '')))}"
        f"<i>{html.escape(str(item.get('域') or '—'))}</i></span>"
        f"<span class='bar'><i style='width:{float(item.get('金额') or 0) / peak * 100:.1f}%'>"
        f"</i></span>"
        f"<span class='vl'>{float(item.get('金额') or 0) / 1e4:,.0f} 万"
        f"<em>{html.escape(str(item.get('金额占比', '')))}</em></span></div>"
        for index, item in enumerate(top, 1))

    return (f"<div class='comp'><div class='comp-bar' role='img' "
            f"aria-label='各业务域金额占比'>{segs}</div>"
            # 图例按列先填：两列并排时，人要竖着读完一列再读下一列，
            # 所以排序也得竖着来，横着填会让"第二大的"跑到右上角去。
            f"<div class='comp-legend' style='grid-auto-flow:column;"
            f"grid-template-rows:repeat({(len(rows) + 1) // 2},auto)'>{legend}</div></div>"
            f"<div class='rank'><h4>金额前 {len(top)} 的活动（共 {len(activities)} 个）</h4>"
            f"{ranking}</div>")


def _html_priority(items: List[Dict[str, Any]]) -> str:
    """优先级分布：一条横条 + 三个数。几十条线索的轻重，比逐条读快得多。"""
    if not items:
        return ""
    counts = {"高": 0, "中": 0, "低": 0}
    for item in items:
        key = str(item.get("优先级") or "")
        if key in counts:
            counts[key] += 1
    total = sum(counts.values()) or 1
    bars = "".join(
        f"<span class='{cls}' style='width:{counts[name] / total * 100:.2f}%' "
        f"title='{name} {counts[name]} 条'></span>"
        for name, cls in (("高", "hi"), ("中", "mid"), ("低", "lo")) if counts[name])
    legend = "".join(
        f"<span><i style='background:var(--{color})'></i>{name}优先级"
        f"<b>{counts[name]}</b> 条</span>"
        for name, color in (("高", "bad"), ("中", "warn"), ("低", "d5"))
        if counts[name])
    return (f"<div class='prio'><div class='prio-bar' role='img' "
            f"aria-label='线索优先级分布'>{bars}</div>"
            f"<div class='prio-legend'>{legend}</div></div>")


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
    """分层导览：一屏看清"哪一块有东西"，点了直接跳过去。

    每张卡上带一条小横条，长度按"本层条数 / 最多那一层"给——五个数字并排时，
    人还是得比大小；画成条就是一眼的事。
    """
    peak = max([int(card.get("count") or 0) for card in cards] or [1]) or 1
    items = "".join(
        f"<a class='lcard{' zero' if not card.get('count') else ''}' "
        f"href='{html.escape(str(card.get('href', '#')))}'>"
        f"<span class='no'>{html.escape(str(card.get('no', '')))}</span>"
        f"<span class='nm'>{html.escape(str(card.get('name', '')))}</span>"
        f"<span class='ct'>{card.get('count', 0)}</span>"
        f"<small>{html.escape(str(card.get('note', '')))}</small>"
        f"<span class='mini'><i style='width:"
        f"{int(card.get('count') or 0) / peak * 100:.1f}%'></i></span></a>"
        for card in cards)
    return f"<div class='layers'>{items}</div>"


def _html_block(block: Dict[str, Any]) -> str:
    out: List[str] = []
    if block.get("layer_cards"):
        out.append(_html_layer_cards(block["layer_cards"]))
    if block.get("priority"):
        out.append(_html_priority(block["priority"]))
    if block.get("heading"):
        out.append(f"<h3 class='sub'>{html.escape(block['heading'])}</h3>")
    if block.get("note"):
        out.append(f"<p class='note'>{_rich(block['note'])}</p>")
    if block.get("months"):
        out.append(f"<div class='charts single'><figure>"
                   f"{_svg_months(block['months'])}</figure></div>")
    if block.get("composition"):
        out.append(_html_composition(block["composition"]))
    if block.get("table"):
        out.append(_html_table(block["table"]))
    if block.get("charts"):
        out.append(_html_charts(block["charts"]))
    if block.get("matrix") is not None:
        out.append(_html_matrix(block["matrix"]))
    if block.get("placeholder"):
        out.append(f"<div class='empty'>{_rich(block['placeholder'])}</div>")
    return "".join(out)


def _mx_bg(value: float, amount: float) -> str:
    """矩阵底色：绿=正、赭红=负。用 sqrt 压一下，否则 20% 与 40% 深浅几乎看不出来。"""
    if amount < 0:
        alpha = 0.05 + 0.62 * min(1.0, (abs(value) ** 0.5) * 1.15)
        return f"rgba(178,59,44,{alpha:.3f})"
    alpha = 0.02 + 0.72 * min(1.0, (max(value, 0.0) ** 0.5) * 1.10)
    return f"rgba(11,107,79,{alpha:.3f})"


def _html_matrix(grid: Dict[str, Any]) -> str:
    """2.4 的方阵：行=产出、列=投入。数字只在 ≥8% 时写，其余靠底色。"""
    subjects = [str(name) for name in (grid.get("subjects") or [])]
    if not subjects:
        return "<div class='empty'>这一层没有跑成（缺上游「对方科目分析」的行表），矩阵画不出来。</div>"
    total = len(subjects)
    col_sum = [float(v) for v in (grid.get("col_sum") or [0.0] * total)]
    row_sum = [float(v) for v in (grid.get("row_sum") or [0.0] * total)]
    cells: Dict[tuple, tuple] = {}
    for item in grid.get("cells") or []:
        i, j, amount = int(item[0]), int(item[1]), float(item[2])
        share_col = amount / col_sum[j] if col_sum[j] else 0.0
        share_row = amount / row_sum[i] if row_sum[i] else 0.0
        cells[(i, j)] = (amount, share_col, share_row)

    head = "".join(f"<th class='c'>{html.escape(name)}</th>" for name in subjects)
    body: List[str] = []
    for i, name in enumerate(subjects):
        tds: List[str] = []
        for j in range(total):
            hit = cells.get((i, j))
            if not hit:
                tds.append("<td class='mx-cell zero'></td>")
                continue
            amount, share_col, share_row = hit
            text = f"{share_col * 100:.0f}" if abs(share_col) >= 0.08 else ("-" if amount < 0 else "")
            tds.append(
                f"<td class='mx-cell{' diag' if i == j else ''}'"
                f" data-c='{share_col:.6f}' data-r='{share_row:.6f}' data-a='{amount:.2f}'"
                f" style='background:{_mx_bg(share_col, amount)}'"
                f" title='{html.escape(name)} → {html.escape(subjects[j])}：{amount / 1e4:,.1f} 万'>"
                f"{text}</td>")
        body.append(f"<tr><th class='r'>{html.escape(name)}</th>{''.join(tds)}</tr>")

    # 底行只放"构成之和"：每格恒为 100（该列各格占比相加），没有投入的列是 —
    foot = "".join(
        f"<td class='mx-foot'>{'100' if col_sum[j] else '—'}</td>" for j in range(total))

    return (
        "<div class='mx-tools'>"
        "<span class='mx-hint'>悬停看金额与占比；格子里的数字是该列（或该行）内的百分比，"
        "不到 8% 只给底色。</span>"
        "<span class='mxseg'>"
        "<button type='button' class='on' data-mx='col'>列构成</button>"
        "<button type='button' data-mx='row'>行构成</button></span></div>"
        f"<div class='mx-wrap'><table class='mx'><thead><tr>"
        f"<th class='corner'>产出 ↓ ／ 投入 →</th>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        f"<tfoot><tr><th class='r'>构成之和</th>{foot}</tr></tfoot></table></div>")


def _section_html(section: Dict[str, Any]) -> str:
    sid = section["id"]
    number = section.get("no") or ""
    kicker = section.get("kicker") or ""
    count = section.get("count")

    # 节号单独成列（大号细体），标题与副标题竖着叠在右边：
    # 编号横着挤在标题前面时，长短不一的「3.1」「01」会把标题推得忽左忽右
    head = [f"<span class='secno'>{html.escape(number)}</span>"] if number else []
    sub = f"<span class='kicker'>{html.escape(kicker)}</span>" if kicker else ""
    head.append(f"<div class='sec-title'>"
                f"<h2>{html.escape(str(section.get('title', '')))}</h2>{sub}</div>")
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

    return (f"<section id='{sid}'><div class='sec-head'>{''.join(head)}</div>"
            f"<p class='note'>{_rich(section.get('note', ''))}</p>"
            f"{''.join(inner)}</section>")


_SCRIPT = """
(function(){
  var links=[].slice.call(document.querySelectorAll('.toc a[href^="#"]'));
  var sections=links.map(function(a){
    return document.getElementById(a.getAttribute('href').slice(1));
  });
  var current=null;
  function mark(){
    // 以视口上沿往下 96px 为判据：正在读的那一节，标题刚被顶到目录下方
    var line=window.scrollY+96, index=-1;
    sections.forEach(function(el,i){if(el&&el.offsetTop<=line){index=i;}});
    var next=index>=0?sections[index]:null;
    if(next===current){return;}
    current=next;
    links.forEach(function(a){a.classList.remove('on');});
    if(next){var i=sections.indexOf(next);if(i>=0){links[i].classList.add('on');}}
  }
  // 直接在 scroll 里算：全篇只有八个分节，读八次 offsetTop 的代价远小于
  // 一层节流带来的"滚到底了高亮还在上一节"。滚动事件本身已由浏览器合并。
  window.addEventListener('scroll',mark,{passive:true});
  window.addEventListener('resize',mark);
  mark();
  var compact=document.getElementById('btn-compact');
  if(compact){
    compact.addEventListener('click',function(){
      var on=document.body.classList.toggle('compact');
      compact.setAttribute('aria-pressed',on?'true':'false');
      compact.textContent=on?'显示解说':'只看结论';
    });
  }
  var print=document.getElementById('btn-print');
  if(print){print.addEventListener('click',function(){window.print();});}

  // ── 2.4 矩阵：列构成 / 行构成切换（只改颜色与数字，不重排表格）
  function mxBg(v,a){
    if(a<0){return 'rgba(178,59,44,'+(0.05+0.62*Math.min(1,Math.sqrt(Math.abs(v))*1.15)).toFixed(3)+')';}
    return 'rgba(11,107,79,'+(0.02+0.72*Math.min(1,Math.sqrt(Math.max(v,0))*1.10)).toFixed(3)+')';
  }
  var mxMode='col';
  function mxPaint(){
    [].forEach.call(document.querySelectorAll('td.mx-cell'),function(td){
      if(td.dataset.c===undefined){return;}
      var v=parseFloat(mxMode==='row'?td.dataset.r:td.dataset.c)||0;
      var a=parseFloat(td.dataset.a)||0;
      td.style.background=mxBg(v,a);
      td.textContent=Math.abs(v)>=0.08?(v*100).toFixed(0):(a<0?'-':'');
    });
  }
  [].forEach.call(document.querySelectorAll('.mxseg button'),function(btn){
    btn.addEventListener('click',function(){
      [].forEach.call(document.querySelectorAll('.mxseg button'),function(b){b.classList.remove('on');});
      btn.classList.add('on'); mxMode=btn.dataset.mx; mxPaint();
    });
  });
  mxPaint();
})();

"""


def to_html(model: Dict[str, Any]) -> str:
    sections = model.get("sections") or []
    body = "".join(_section_html(section) for section in sections)

    toc = "".join(
        f"<a href='#{section['id']}'><b>{html.escape(str(section.get('no') or ''))}</b>"
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
<body><div class="sheet">
<header class="cover"><div class="cover-inner">
  <div class="eyebrow"><i class="dot"></i>{html.escape(model["feature"])} · 审计程序</div>
  <h1>{html.escape(model["title"])}</h1>
  <p class="lede"><b>{html.escape(model["file"])}</b> · {html.escape(model["meta"])}</p>
  <div class="verdict {tone}">
    <span class="seal" aria-hidden="true">{mark}</span>
    <div><b>{html.escape(headline)}</b><span>{html.escape(detail)}</span></div>
  </div>
  <div class="kpis">{kpis}</div>
  <div class="cover-foot">
    <span>生成于 {html.escape(model["generated_at"])}</span>
    <span>结论由项目组判断 · 报告只列风险与证据</span>
  </div>
</div></header>
<nav class="navwrap" aria-label="报告目录">
  <div class="toc">{toc}</div>
  <span class="tools">
    <button id="btn-compact" type="button" aria-pressed="false">只看结论</button>
    <button id="btn-print" type="button">打印 / 存 PDF</button>
  </span>
</nav>
<main>{body}</main>
<footer>
  <div><b>这份报告回答什么</b>本报告由程序生成。每条线索都写清了统计证据，
  结论要人来下；没有「综合得分」，也不给建议——风险摆出来，怎么查是项目组的判断。</div>
  <div><b>问题行在哪</b>Excel 是同一份账加了「是否异常 / 异常原因 / 最高优先级 /
  白名单放行」四列，筛「是」就是全部问题行。</div>
</footer>
</div>
<script>{_SCRIPT}</script>
</body></html>"""
