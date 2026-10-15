# -*- coding: utf-8 -*-
"""复核视图 —— 把 `units` 那份打分结果换几种"怎么看"。

    units     默认：逐单元清单（按概率升序）。几千条，是**交付复核的原始面**。
    summary   ★ 全局画像：桶分布 / 概率分布 / 族规模 / 最可疑的 20 个。
    families  ★ 按**科目组合**聚族，只列**有少数派**的族。

## 为什么必须有 summary / families

真账实测 **4931 个单元**，而概率已经饱和：93.7% 是 `1.000`，而且
**最终确认的错判里绝大多数也是 `1.000`**。于是"按概率从头往下看"这条
最自然的路**永远看不到它们** —— 上一轮的执行者按 `--limit 60` 看完概率最低的
60 条，只改了 2~3 个；他最后自己写了个只读脚本按科目组合统计桶分布，
在 500 个单元（10% 的复核面）里命中 **47 个改判**。

这件事不该让每个 AI 各写一遍（他第一版脚本还踩了"科目组合是 list"的坑）。
所以把"看同族离群"做成命令。

## ⚠️ 少数派 ≠ 判错

族 = 同一「科目组合」。同一个科目组合里的凭证往往属于同一业务模式，
多数派通常对、少数派才可疑 —— 但这只是**筛子**，不是结论。
真账上最大的族 `应付账款[借] + 银行存款[贷]` 有 693 个单元，
分布是 `存货采购 579 / 长期资产 109 / 日常费用 5`，那 109 个长期资产
（设备款、工程款、软件款、专利费）**全是判对的**：银行付款族的
"长期资产支线"是正常结构。判定要看摘要语义，见 SKILL.md 第 4 步。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

# 正常支线：族里出现这些桶，多半不是错误（写进出参，省得每个 AI 重新踩）
NORMAL_BRANCHES = {
    "长期资产": "设备款 / 工程款 / 软件款 / 专利费 —— 银行付款族的正常支线",
    "押金保证金": "押金 / 保证金 / 投标保证金 —— 银行收付款族的正常支线",
    "职工薪酬": "公积金 / 社保 / 代扣个税 —— 付款族的正常支线",
    "单位往来": "关联方 / 分公司的往来款、代付、代收 —— 正常支线",
}


def unit_brief(u: dict) -> dict:
    """单元的一句话摘要（复核够用，不带明细数组）。"""
    return {
        "单元号": u["单元号"],
        "单元键": u["单元键"],
        "模型桶": u["模型桶"],
        "概率": u["概率"],
        "来源": u["来源"],
        "覆盖凭证数": u["覆盖凭证数"],
        "同族变体数": u.get("同族变体数"),
        "摘要": u["摘要"],
    }


def prob_buckets(units: List[dict]) -> Dict[str, int]:
    """概率分布 —— 用来判断"按概率排序还有没有用"。"""
    out = {"=1.0": 0, "0.95~1": 0, "0.8~0.95": 0, "<0.8": 0, "无概率(硬规则)": 0}
    for u in units:
        p = u.get("概率")
        if p is None:
            out["无概率(硬规则)"] += 1
        elif p >= 1.0:
            out["=1.0"] += 1
        elif p >= 0.95:
            out["0.95~1"] += 1
        elif p >= 0.8:
            out["0.8~0.95"] += 1
        else:
            out["<0.8"] += 1
    return out


def by_combo(units: List[dict]) -> Dict[tuple, List[dict]]:
    """按**科目组合**聚族 —— 族 = 同一组一级科目（如"应付账款[借] + 银行存款[贷]"）。

    两件必须做的事：
      - `科目组合` 是 **list**（不是字符串）：当 dict 键必须先转 tuple
        （上一轮执行者按字符串做主键，撞了 `unhashable type: 'list'`）；
      - 键**排序**：`etl.subject_switches` 目前已经排序，但这个视图不该依赖
        上游的排序习惯 —— 上游哪天换成按行序输出，族就会被悄悄切碎、
        少数派凭空消失（不会报错，只会少报）。
    """
    groups: Dict[tuple, List[dict]] = {}
    for u in units:
        key = tuple(sorted(str(s) for s in (u.get("科目组合") or [])))
        groups.setdefault(key, []).append(u)
    return groups


def _family_row(combo: tuple, members: List[dict], outliers: List[dict]) -> dict:
    bucket_u = Counter(u["模型桶"] for u in members)
    bucket_v: Counter = Counter()
    for u in members:
        bucket_v[u["模型桶"]] += u["覆盖凭证数"]
    return {
        "科目组合": " + ".join(combo),
        "单元数": len(members),
        "覆盖凭证数": sum(u["覆盖凭证数"] for u in members),
        "桶分布": {b: {"单元数": bucket_u[b], "覆盖凭证数": bucket_v[b]}
                   for b, _ in bucket_u.most_common()},
        "少数派": [unit_brief(u) for u in outliers],
    }


def families_view(units: List[dict], min_family: int = 5,
                  outlier_share: float = 0.25, limit: int = 0,
                  note_limit: int = 30) -> Dict[str, Any]:
    """挑出**有少数派**的族 —— 真账上最管用的找错入口。

    Args:
        min_family:    族至少这么大才找少数派（小族里"少数派"没有统计意义）
        outlier_share: 占比不超过它的桶算少数派（默认 0.25；五五开不算）
        limit:         最多返回几个族（0 = 全部有少数派的族）
        note_limit:    每个族最多列几条少数派（族太大时截断并说明）
    """
    groups = by_combo(units)
    fams = []
    for combo, members in groups.items():
        n = len(members)
        if n < max(int(min_family), 2):
            continue
        bucket_u = Counter(u["模型桶"] for u in members)
        if len(bucket_u) < 2:
            continue
        minority = {b for b, k in bucket_u.items() if k / n <= outlier_share}
        if not minority:
            continue
        outliers = [u for u in members if u["模型桶"] in minority]
        fams.append(_family_row(combo, members, outliers))
    fams.sort(key=lambda f: (-len(f["少数派"]), -f["单元数"]))

    total_out = sum(len(f["少数派"]) for f in fams)
    shown = fams if int(limit or 0) <= 0 else fams[:int(limit)]
    for f in shown:
        if len(f["少数派"]) > note_limit:
            f["少数派_已截断"] = (f"只列了前 {note_limit} 条"
                                  f"（本族共 {len(f['少数派'])} 条少数派）")
            f["少数派"] = f["少数派"][:note_limit]

    return {
        "view": "families",
        "unit_count": len(units),
        "科目组合数": len(groups),
        "有少数派的族数": len(fams),
        "少数派单元数": total_out,
        "shown": len(shown),
        "口径": (f"族 = 同一「科目组合」；族内单元数 ≥ {min_family} 且某个桶占比 "
                 f"≤ {outlier_share:g} 才算少数派。按「少数派条数」降序。"),
        "少数派不等于判错": NORMAL_BRANCHES,
        "怎么用": ("先读摘要，看它是不是这个族的**正常支线**（见 "
                   "「少数派不等于判错」）；确实判错的用 run --unit 交回程序"
                   "（一次交完）。成批同类判错就别逐个改 —— 那是该重训模型的信号。"
                   "尾部小桶（单元数少的桶）也值得整桶看完，便宜且命中率高。"),
        "families": shown,
    }


def summary_view(units: List[dict], sim_stat: dict, hard_counts: Counter,
                 vouchers: int, rows: int, min_family: int = 5,
                 outlier_share: float = 0.25, top: int = 20,
                 largest: int = 10) -> Dict[str, Any]:
    """全局画像 —— 复核的**第一步**：先看整体，再决定看哪里。"""
    groups = by_combo(units)
    unit_by_bucket = Counter(u["模型桶"] for u in units)
    vouchers_by_bucket: Counter = Counter()
    for u in units:
        vouchers_by_bucket[u["模型桶"]] += u["覆盖凭证数"]

    fam = families_view(units, min_family=min_family,
                        outlier_share=outlier_share, limit=0)
    biggest = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:largest]
    risky = sorted([u for u in units if u.get("概率") is not None],
                   key=lambda u: (u["概率"], u["单元号"]))[:top]

    dist = prob_buckets(units)
    note = ("① 先看 confidence_dist：`=1.0` 占比高（真账 93.7%）说明"
            "**按概率排序看不到错**，别从 units 清单头部开始读；"
            "② 再跑 `units --families` 看同族离群（少数派才可疑）；"
            "③ 尾部小桶（单元数少的桶）值得整桶看完 —— 便宜且命中率高。")
    if dist["=1.0"] and dist["=1.0"] >= 0.5 * max(len(units), 1):
        note += " 本次 `=1.0` 已占一半以上，请直接走 ②③。"

    return {
        "view": "summary",
        "rows": rows,
        "vouchers": vouchers,
        "unit_count": len(units),
        "model_unit_count": len([u for u in units if u["概率"] is not None]),
        "hard_rule_hits": dict(hard_counts) if hard_counts else {},
        "dedup": {
            "阈值": sim_stat["threshold"] if sim_stat["on"] else None,
            "原样单元数": sim_stat["variants_before"],
            "归族后单元数": sim_stat["units_after"],
            "归族数": sim_stat["families"],
            "抄代表的凭证数": sim_stat["merged_vouchers"],
        },
        "bucket_counts": dict(unit_by_bucket.most_common()),
        "bucket_vouchers": dict(vouchers_by_bucket.most_common()),
        "confidence_dist": dist,
        "family": {
            "科目组合数": len(groups),
            "有少数派的族数": fam["有少数派的族数"],
            "少数派单元数": fam["少数派单元数"],
            "最大的族": [
                {"科目组合": " + ".join(c), "单元数": len(m),
                 "覆盖凭证数": sum(u["覆盖凭证数"] for u in m),
                 "桶分布": dict(Counter(u["模型桶"] for u in m).most_common())}
                for c, m in biggest],
        },
        "最可疑的单元": [unit_brief(u) for u in risky],
        "怎么用": note,
    }


def reuse_labels(units: List[dict]) -> Dict[str, dict]:
    """给 `run --reuse-units` 用的**紧凑**打分表：{单元键: {模型桶, 概率, 来源, 次选}}。

    为什么 summary / families 也要带上它：AI 的正常流程是
    `units --summary`（1 屏）→ `units --families`（几百条）→ 改判 → `run`。
    如果只有"默认视图"的出参能复用，那为了省时间就得**先花一次全量推理**
    去生成默认视图 —— 省时间的手段反而要先付一次全量推理，白搭。
    所以三个视图都带这份标签表（4931 个单元约 300KB，可以接受）。
    """
    return {str(u["单元键"]): {
        "模型桶": u["模型桶"],
        "概率": u["概率"],
        "来源": u["来源"],
        "次选": u.get("次选") or [],
    } for u in units}


def build(view: str, units: List[dict], sim_stat: dict, hard_counts: Counter,
          vouchers: int, rows: int, min_family: int = 5,
          outlier_share: float = 0.25, limit: int = 0) -> Optional[dict]:
    """`units_file` 的视图分发；view == "units" 时返回 None（走默认清单）。"""
    if view == "summary":
        return summary_view(units, sim_stat, hard_counts, vouchers, rows,
                            min_family=min_family, outlier_share=outlier_share)
    if view == "families":
        return families_view(units, min_family=min_family,
                             outlier_share=outlier_share, limit=limit)
    return None


def sim_payload(sim_stat: dict) -> Dict[str, Any]:
    """切割成绩的机器可读版本（字段名英文，和 dedup 的中文键分开）。"""
    return {
        "on": sim_stat["on"],
        "threshold": sim_stat["threshold"] if sim_stat["on"] else None,
        "variants_before": sim_stat["variants_before"],
        "units_after": sim_stat["units_after"],
        "families": sim_stat["families"],
        "merged_vouchers": sim_stat["merged_vouchers"],
        "elapsed_s": sim_stat["elapsed_s"],
    }


def dedup_payload(sim_stat: dict) -> Dict[str, Any]:
    """`units` 出参里的中文 dedup 字段（对外可见，保持原有键名不动）。"""
    return {
        "口径": "同一科目组合内，摘要归一化（忽略数字）后按相似度归族；"
                "同族只看代表摘要，其余抄代表的作业",
        "阈值": sim_stat["threshold"] if sim_stat["on"] else None,
        "原样单元数": sim_stat["variants_before"],
        "归族后单元数": sim_stat["units_after"],
        "归族数": sim_stat["families"],
        "抄代表的凭证数": sim_stat["merged_vouchers"],
    }
