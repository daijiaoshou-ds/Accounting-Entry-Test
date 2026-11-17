# -*- coding: utf-8 -*-
"""第 0 步：总体完整性。

证的是「总量守恒」，不是「明细正确」——借贷同错的串户不破坏平衡。这是必要非
充分的地基：它过不了，后面的画像与检验都建在流沙上；它过了，也不代表账没问题。

三项：

1. **凭证号连续性**：按序列（凭证类型前缀）查缺号。缺号不一定是错（作废凭证、
   跳号规则），所以只报「哪一段缺了几个」并给样例，让人去查作废审批。
2. **借贷平衡**：**按月**核，不按凭证。ERP 里单张凭证不平根本录不进去，逐张查
   只会把"导出被截断"这种问题重复一遍；按月核才能发现"少了一个月""某月只导了
   一半"。
3. **数据质量**：空科目、零金额、无日期、缺摘要。这几项直接决定后面几层能不能
   跑——缺摘要就没有指纹，空科目就没法建科目对。

为什么凭证号要"按序列"：真实账本有三种叫法（见 2.0 设计文档）。`2025年1月-记
-0001号` 取"最后一个数字段"当序号；纯数字的 `40001001` 则按**末 4 位**切序号、
前面当类型段——收/付/转 在数字型账里就是这么编的。切错时的表现是"报出一堆缺号"，
所以只有一条序列时宁可写"无法判断"也不硬报。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# 结果词表：前端直接显示，不认颜色代码（这几张表也可能被导出成 Excel 给人看）
OK, WARN, BAD, SKIP, INFO = "通过", "警告", "问题", "跳过", "信息"

# 纯数字凭证号按末几位切序号。4 位是收/付/转型账的常见宽度（4000-1001）。
DIGIT_SEQ_WIDTH = 4


def split_series(vno: Any) -> Optional[Tuple[str, int]]:
    """凭证号 → (类型前缀, 序号)。切不出来返回 None。

    `2025年1月-记-0001号` → ("2025年1月-记-", 1)
    `GSXSZ-001`          → ("GSXSZ-", 1)
    `40001001`           → ("4000", 1001)   # 纯数字：末 4 位是序号
    """
    text = str(vno or "").strip()
    if not text:
        return None
    hit = re.match(r"^(?P<pre>.*?)(?P<seq>\d+)\s*号?$", text)
    if not hit:
        return None
    pre, digits = hit.group("pre"), hit.group("seq")
    if not pre.strip() and len(digits) > DIGIT_SEQ_WIDTH:
        pre, digits = digits[:-DIGIT_SEQ_WIDTH], digits[-DIGIT_SEQ_WIDTH:]
    return pre.strip(), int(digits)


def _series(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    """每条序列：张数、号段、缺号。"""
    seen: Dict[str, List[int]] = {}
    unknown: List[str] = []
    for vno in pd.unique(frame["vid"]):
        cut = split_series(vno)
        if not cut:
            if str(vno or "").strip():
                unknown.append(str(vno))
            continue
        pre, seq = cut
        seen.setdefault(pre, []).append(seq)

    out: List[Dict[str, Any]] = []
    for pre, seqs in seen.items():
        seqs = sorted(set(seqs))
        low, high = seqs[0], seqs[-1]
        missing = [n for n in range(low, high + 1) if n not in set(seqs)]
        out.append({
            "prefix": pre or "（无前缀）",
            "count": len(seqs),
            "low": low,
            "high": high,
            "missing": missing,
            "unknown": len(unknown),
        })
    # 按前缀里的数字排（"2025年10月" 不该排在 "2025年1月" 前面）：纯字符串排序会
    # 把 10/11/12 月顶到最前，而报告里只列前三条序列，排错就等于列错了月份。
    out.sort(key=lambda item: [int(part) for part in re.findall(r"\d+", item["prefix"])]
             or [0])
    return out


def _months(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    """每月借贷合计与差额（带符号求和：红字行是负数，不能取 abs）。"""
    rows: List[Dict[str, Any]] = []
    work = frame[frame["period"].astype(str).str.strip() != ""]
    for period, group in work.groupby("period", sort=True):
        debit = float(group["debit"].sum())
        credit = float(group["credit"].sum())
        diff = debit - credit
        rows.append({
            "period": str(period),
            "debit": round(debit, 2),
            "credit": round(credit, 2),
            "diff": round(diff, 2),
            "vouchers": int(group["vid"].nunique()),
            "rows": int(len(group)),
        })
    return rows


def _quality(frame: pd.DataFrame) -> Dict[str, int]:
    return {
        "rows": int(len(frame)),
        "vouchers": int(frame["vid"].nunique()),
        "empty_subject": int((frame["subject"].astype(str).str.strip() == "").sum()),
        "zero_amount": int(((frame["debit"] == 0) & (frame["credit"] == 0)).sum()),
        "missing_date": int(frame["date"].isna().sum()),
        "missing_period": int((frame["period"].astype(str).str.strip() == "").sum()),
        "missing_summary": int((frame["summary"].astype(str).str.strip() == "").sum()),
    }


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _summary(series: List[Dict[str, Any]], months: List[Dict[str, Any]],
             quality: Dict[str, int]) -> List[Dict[str, str]]:
    """四行汇总：概况 / 凭证号 / 借贷 / 数据质量。异常写在「说明」里，不另起一行。"""
    out: List[Dict[str, str]] = []

    spans = [item["period"] for item in months]
    subjects = quality.get("subjects")
    head = f"{quality['vouchers']:,} 张凭证 / {quality['rows']:,} 行"
    span = (f"{spans[0]}–{spans[-1]}（{len(spans)} 个月）" if spans else "读不出期间")
    out.append({"检查项": "账本概况", "结果": INFO, "数值": head,
                "说明": span + (f" · {subjects} 个一级科目" if subjects else "")})

    # 凭证号：把"几条序列、有没有缺号"压成一句；缺号只列前几段
    missing_total = sum(len(item["missing"]) for item in series)
    if not series:
        out.append({"检查项": "凭证号连续", "结果": SKIP, "数值": "—",
                    "说明": "凭证号里找不到数字段，判断不了连续性"})
    elif missing_total:
        gaps = [f"{item['prefix']} 缺 {len(item['missing'])} 个"
                for item in series if item["missing"]]
        out.append({"检查项": "凭证号连续", "结果": WARN,
                    "数值": f"{len(series)} 条序列 · {missing_total} 个缺号",
                    "说明": "；".join(gaps[:4]) + ("（可能是作废凭证，查作废审批）"
                                                  if len(gaps) <= 4 else " 等")})
    else:
        out.append({"检查项": "凭证号连续", "结果": OK,
                    "数值": f"{len(series)} 条序列 · 无缺号",
                    "说明": "；".join(f"{item['prefix']}{item['low']}–{item['high']}"
                                     for item in series[:3])
                            + (" 等" if len(series) > 3 else "")})

    # 借贷：只报"几个月、最大差多少"；不平的月份点名
    if not months:
        out.append({"检查项": "月度借贷平衡", "结果": BAD, "数值": "—",
                    "说明": "读不出任何会计期间：日期列没认出来，也没有可用的月份列"})
    else:
        worst = max(months, key=lambda item: abs(item["diff"]))
        bad = [item for item in months
               if abs(item["diff"]) > max(0.01, max(abs(item["debit"]),
                                                    abs(item["credit"]), 1.0) * 1e-9)]
        out.append({
            "检查项": "月度借贷平衡",
            "结果": BAD if bad else OK,
            "数值": f"{len(months)} 个月 · 最大差 {_money(worst['diff'])}",
            "说明": ("不平：" + "、".join(f"{item['period']}（{_money(item['diff'])}）"
                                         for item in bad[:4])
                     + (f" 等 {len(bad)} 个月" if len(bad) > 4 else ""))
            if bad else f"借 {_money(sum(m['debit'] for m in months))}"
                        f" · 贷 {_money(sum(m['credit'] for m in months))}",
        })

    # 数据质量：四个数并列一行，有任何一个非零才降成警告
    dirty = [name for name, key in (("空科目", "empty_subject"), ("零金额", "zero_amount"),
                                    ("无日期", "missing_date"), ("缺摘要", "missing_summary"))
             if quality.get(key)]
    out.append({
        "检查项": "数据质量",
        "结果": WARN if dirty else OK,
        "数值": " · ".join(f"{name} {quality.get(key, 0):,}"
                          for name, key in (("空科目", "empty_subject"),
                                            ("零金额", "zero_amount"),
                                            ("无日期", "missing_date"),
                                            ("缺摘要", "missing_summary"))),
        "说明": ("有问题的行会让后面几层对它们失明："
                 + "、".join(dirty)) if dirty else "无空科目、零金额、无日期、缺摘要",
    })
    return out


def check(frame: pd.DataFrame) -> Dict[str, Any]:
    """跑完三项，返回 {rows: 导出用的表, series, months, quality, ok, warnings}。"""
    series = _series(frame)
    months = _months(frame)
    quality = _quality(frame)
    rows: List[Dict[str, Any]] = []

    # ── 抬头一行：这份账是什么 ──
    spans = [item["period"] for item in months]
    subjects = int(frame["subject"].astype(str).str.strip().replace("", pd.NA).nunique())
    quality["subjects"] = subjects
    rows.append({
        "检查项": "账本概况",
        "对象": "—",
        "结果": INFO,
        "数值": f"{quality['vouchers']:,} 张凭证 / {quality['rows']:,} 行",
        "说明": f"期间 {spans[0]}–{spans[-1]}（{len(spans)} 个月）· {subjects} 个一级科目"
        if spans else f"{subjects} 个一级科目",
    })

    # ── ① 凭证号连续性 ──
    for item in series:
        missing = item["missing"]
        if item["count"] <= 1 and not missing:
            result, note = SKIP, "只有一张凭证，判断不了连续性"
        elif missing:
            sample = "、".join(str(n) for n in missing[:6])
            more = f" 等 {len(missing)} 个" if len(missing) > 6 else ""
            result, note = WARN, f"缺号：{sample}{more}（可能是作废凭证，查作废审批）"
        else:
            result, note = OK, "无缺号"
        rows.append({
            "检查项": "凭证号连续",
            "对象": item["prefix"],
            "结果": result,
            "数值": f"{item['count']:,} 张 · {item['low']}–{item['high']}",
            "说明": note,
        })
    if not series:
        rows.append({
            "检查项": "凭证号连续", "对象": "—", "结果": SKIP, "数值": "—",
            "说明": "凭证号里找不到数字段，判断不了连续性",
        })

    # ── ② 借贷平衡（按月）──
    if not months:
        rows.append({
            "检查项": "借贷平衡", "对象": "—", "结果": BAD, "数值": "—",
            "说明": "读不出任何会计期间：日期列没认出来，也没有可用的月份列",
        })
    for item in months:
        scale = max(abs(item["debit"]), abs(item["credit"]), 1.0)
        balanced = abs(item["diff"]) <= max(0.01, scale * 1e-9)
        rows.append({
            "检查项": "借贷平衡",
            "对象": item["period"],
            "结果": OK if balanced else BAD,
            "数值": f"差 {_money(item['diff'])}",
            "说明": f"借 {_money(item['debit'])} · 贷 {_money(item['credit'])}",
        })

    # ── ③ 数据质量 ──
    def note(count: int, text: str) -> str:
        return text if count else "无"

    rows.append({
        "检查项": "空科目", "对象": "—",
        "结果": WARN if quality["empty_subject"] else OK,
        "数值": f"{quality['empty_subject']:,} 行",
        "说明": note(quality["empty_subject"],
                   "这些行没有一级科目，科目对分析会漏掉它们"),
    })
    rows.append({
        "检查项": "零金额", "对象": "—",
        "结果": WARN if quality["zero_amount"] else OK,
        "数值": f"{quality['zero_amount']:,} 行",
        "说明": note(quality["zero_amount"],
                   "借贷都是 0：不影响合计，但会把凭证金额算小"),
    })
    rows.append({
        "检查项": "无日期", "对象": "—",
        "结果": WARN if quality["missing_date"] else OK,
        "数值": f"{quality['missing_date']:,} 行",
        "说明": note(quality["missing_date"],
                   "这些行的期间只能靠月份列兜底；两列都没有的行进不了按月检查"),
    })
    rows.append({
        "检查项": "缺摘要", "对象": "—",
        "结果": WARN if quality["missing_summary"] else OK,
        "数值": f"{quality['missing_summary']:,} 行",
        "说明": note(quality["missing_summary"],
                   "指纹（摘要 + 科目组合）会跳过这些凭证，后面几层对它们失明"),
    })

    return {
        "rows": rows,
        # 报告只读这一份：四行，不列明细。逐条明细留在 Excel 与「rows」里——
        # 12 个月逐月一行摆进报告，人只会看到一片"通过"然后翻过去，反而把
        # 真正异常的那一行淹掉。
        "summary": _summary(series, months, quality),
        "series": series,
        "months": months,
        "quality": quality,
        "ok": not any(item["结果"] == BAD for item in rows),
        "problems": sum(1 for item in rows if item["结果"] == BAD),
        "warnings": sum(1 for item in rows if item["结果"] == WARN),
    }
