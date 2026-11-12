# -*- coding: utf-8 -*-
"""对方科目分析 —— 引擎适配层。

`features/contra/stages.py` 只跟这个文件打交道：`contra_analyzer` 自己的
`pattern_cache` / `node_map` / `connectivity` 一律不外泄，免得阶段代码哪天跟着
引擎内部一起改。

三件事：

    ① build()    Journal → 引擎要的那张表（全字符串 + `_uid` / `_calc_*`）
    ② solve()    复杂模式 → 候选方案（穷举 + 奥卡姆排序），**同时给 finalize 预热缓存**
    ③ finalize() 选定的方案 → 拆分明细（DataFrame）

为什么 `solve()` 里要"预热"：`finalize_report` 遇到没缓存的模式会自己穷举，
超时给 5 秒/凭证（`core.py:570`），一个高频模式能卡掉几十秒。先算一遍把最优拓扑
塞进 `pattern_cache`，正式生成时同模式的凭证只做金额推导。

为什么候选方案按"模式"而不是按凭证：算法对同一科目组合的所有凭证给同一套拓扑，
136 个模式里只有 111 个存在多解——按凭证列待办会把同一个问题问 13 遍。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from contra_analyzer.algorithm import ExhaustiveSolver
from contra_analyzer.core import ContraProcessor
from contra_analyzer.memory_web import KnowledgeBase
from contra_analyzer.occams_razor import OccamsRazor

# 契约见 `journal_io/fields.py` 的 FEATURE_REQUIRES["contra"]
REQUIRES = ("voucher_id", "date", "subject_l1", "debit", "credit")

# 「对方科目」列里出现这些词 = 算法没定案，不是科目名
UNRESOLVED = ("需人工分析", "无效分录", "SS归一化失败", "缓存丢失")

MAX_OPTIONS = 3          # 一个模式最多列几个候选方案（再多也是噪声）
SOLVE_TIMEOUT = 2.0      # 单个模式穷举的上限（与网页方案阶段一致）
MAX_SOLUTIONS = 200


# ============================================================================
# ① 建处理器
# ============================================================================

def _mapping(journal) -> Dict[str, str]:
    """canonical 映射 → 引擎认的那几个键（`date/voucher_id/subject/detail_subject/...`）。"""
    m = journal.schema.legacy()          # voucher_no / subject / subject_name / debit / credit / date / summary
    out = {
        "date": m.get("date", ""),
        "voucher_id": m.get("voucher_no", ""),
        "subject": m.get("subject", ""),
        "debit": m.get("debit", ""),
        "credit": m.get("credit", ""),
    }
    if m.get("summary"):
        out["summary"] = m["summary"]
    if m.get("subject_name"):
        out["detail_subject"] = m["subject_name"]
    return out


def build(journal) -> ContraProcessor:
    """Journal → 填好 `_uid` / `_calc_*` / `meta_cache` 的处理器。

    等价于引擎的 `load_data(file, mapping)`，只是表从 `journal.raw` 来而不是
    重新 `pd.read_excel` —— 这样手工 `--set` 过的映射、由「金额+借贷方向」折算出来
    的借贷两列，全都和读表阶段一致（重新读文件会拿到一份对不上映射的表）。
    """
    p = ContraProcessor()
    df = journal.raw.copy()                  # 全字符串：凭证号 0001 不丢前导零

    mapping = _mapping(journal)
    for key in ("date", "voucher_id", "subject", "debit", "credit"):
        if not mapping.get(key):
            raise ValueError(f"对方科目分析缺少「{key}」字段的列（读表阶段没认出来）")

    # 引擎硬取 summary（core.py:22），契约里却是可选 —— 缺了就补一列空串
    if "summary" not in mapping:
        col = "__摘要(补)"
        df[col] = ""
        mapping["summary"] = col

    p.mapping = mapping
    p.df = df
    _prepare(p)
    return p


def _prepare(p: ContraProcessor) -> None:
    """引擎 `load_data` 的后半段：唯一凭证号 + 金额 + 科目（一级-明细）+ 元数据。"""
    m, df = p.mapping, p.df

    df["_uid"] = (df[m["date"]].astype(str).str.replace(r"\s+00:00:00", "", regex=True)
                  + "_" + df[m["voucher_id"]].astype(str))

    for src, dst in ((m["debit"], "_calc_debit"), (m["credit"], "_calc_credit")):
        # 去千分位：引擎原版直接 to_numeric，遇到 "1,234.56" 会静默变 0
        text = (df[src].astype(str).str.replace(",", "", regex=False)
                .str.replace("，", "", regex=False).str.strip())
        df[dst] = pd.to_numeric(text, errors="coerce").fillna(0).round(2)

    subj = df[m["subject"]].astype(str).str.strip()
    df["_calc_subj"] = subj
    detail_col = m.get("detail_subject")
    if detail_col and detail_col in df.columns:
        detail = df[detail_col].astype(str).str.strip()
        mask = (detail != "") & (detail != "nan") & (detail != subj)
        df.loc[mask, "_calc_subj"] = subj + "-" + detail

    for uid, group in df.groupby("_uid"):
        first = group.iloc[0]
        summs = group[m["summary"]].dropna().unique()
        p.meta_cache[uid] = {
            "date": first[m["date"]],
            "voucher_id": first[m["voucher_id"]],
            "summary": " | ".join(str(s) for s in summs if str(s).strip()),
        }


# ============================================================================
# ② 模式 → 候选方案
# ============================================================================

def _side_cn(side: str) -> str:
    return {"debit": "借", "credit": "贷"}.get(str(side), str(side))


def _name(node: Dict[str, Any]) -> str:
    return f"{node['subject']}[{_side_cn(node['original_side'])}]"


def _pairs(sol: Dict[str, Dict[str, float]], node_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一个解 → 科目级配对列表（金额以该模式的样张凭证为准）。"""
    out: List[Dict[str, Any]] = []
    for d_id, c_map in sol.items():
        d = node_map.get(d_id)
        if not d:
            continue
        for c_id, amt in c_map.items():
            c = node_map.get(c_id)
            if not c or abs(float(amt)) <= 0.001:
                continue
            out.append({
                "from": d["subject"], "from_side": d["original_side"],
                "to": c["subject"], "to_side": c["original_side"],
                "amount": round(float(amt), 2),
            })
    out.sort(key=lambda e: (-e["amount"], e["from"], e["to"]))
    return out


def _entries(node_map: Dict[str, Any]) -> List[str]:
    """样张凭证的分录（按科目+方向聚合，借在前）。"""
    agg: Dict[Tuple[str, str], float] = {}
    for node in node_map.values():
        key = (node["subject"], node["original_side"])
        agg[key] = agg.get(key, 0.0) + float(node.get("original_amount") or 0)
    rows = [((s, side), amt) for (s, side), amt in agg.items() if abs(amt) > 0.001]
    rows.sort(key=lambda r: (0 if r[0][1] == "debit" else 1, -r[1]))
    return [f"{s} {_side_cn(side)} {amt:,.2f}" for (s, side), amt in rows]


def solve(processor: ContraProcessor, *, max_options: int = MAX_OPTIONS,
          timeout: float = SOLVE_TIMEOUT) -> List[Dict[str, Any]]:
    """把所有复杂模式算成候选方案，并把最优拓扑写进 `pattern_cache`。

    返回按"首次出现顺序"排列的模式清单（引擎的 cluster_samples 就是这个顺序）。
    """
    solver = ExhaustiveSolver()
    kb = KnowledgeBase()                       # 引擎无持久化，排序只看奥卡姆得分
    out: List[Dict[str, Any]] = []

    for key_hash, sample in processor.cluster_samples.items():
        node_map = sample.get("node_map") or {}
        sols, is_timeout = solver.calculate_combinations(
            sample["debits"], sample["credits"],
            max_solutions=MAX_SOLUTIONS, timeout=timeout, node_map=node_map)

        ranked = kb.rank_solutions(sols, sample["name"], node_map) if sols else []
        scores = [OccamsRazor.score_solution(s, node_map) for s in ranked]

        # 预热：finalize 时同模式的其余凭证直接复用拓扑，不再穷举
        if ranked:
            processor.cache_pattern_solution(key_hash, ranked[0], node_map)

        options = []
        for rank, sol in enumerate(ranked[:max_options], 1):
            options.append({
                "rank": rank,
                "score": round(float(scores[rank - 1]), 2),
                "pairs": _pairs(sol, node_map),
            })

        uid = str(sample.get("sample_uid") or "")
        meta = processor.meta_cache.get(uid, {})
        out.append({
            "key": "p" + key_hash[:8],
            "name": sample["name"],
            "count": int(sample.get("count") or 1),
            "voucher": str(meta.get("voucher_id") or ""),
            "summary": str(meta.get("summary") or "")[:120],
            "entries": _entries(node_map),
            "options": options,
            "solutions": len(ranked),
            "timeout": bool(is_timeout),
            "gap": round(scores[0] - scores[1], 2) if len(scores) > 1 else None,
        })
    return out


def needs_review(pattern: Dict[str, Any]) -> bool:
    """这个模式值不值得问人。

    问的条件：给了不止一个方案，**且** 满足其一 ——
        · 出现不止一次（选错就错一批）
        · 前两名分数并列（算法自己也分不出来，`gap == 0`）
    """
    if len(pattern["options"]) < 2:
        return False
    return pattern["count"] >= 2 or pattern.get("gap") == 0


def candidates(patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """待复核的模式，按"影响面大 → 越难分"排序。"""
    picked = [p for p in patterns if needs_review(p)]
    picked.sort(key=lambda p: (-p["count"], p.get("gap") if p.get("gap") is not None else 99,
                               -p["solutions"]))
    return picked


# ============================================================================
# ③ 定案 → 拆分明细
# ============================================================================

def _selection(rank: int) -> str:
    """决策序号 → 引擎认的 `"<pattern>-<sol>"`（`core.py:583` 只取第二段）。"""
    return f"0-{int(rank)}"


def finalize(processor: ContraProcessor, patterns: List[Dict[str, Any]],
             choices: Dict[str, int], log=None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """按决策生成最终表。

    Args:
        choices: {模式 key: 方案序号}；没给的模式用算法第 1 方案

    Returns:
        (表, 应用情况)。`applied` 是真正生效的条数；`ignored` 是决策里对不上
        当前模式的 key（多半是上一轮跑留下的）；`clamped` 是方案序号超过该模式
        方案数、回落到第 1 方案的那些。
    """
    log = log or (lambda *_a, **_k: None)
    by_key = {p["key"]: p for p in patterns}

    selections: Dict[str, str] = {}
    ignored: List[str] = []
    clamped: List[str] = []
    for key, rank in (choices or {}).items():
        pat = by_key.get(str(key))
        if not pat:
            ignored.append(str(key))
            continue
        try:
            n = int(rank)
        except (TypeError, ValueError):
            clamped.append(f"{key}:{rank}(不是数字)")
            continue
        if n < 1:
            clamped.append(f"{key}:{rank}")
            continue
        if n > len(pat["options"]):
            clamped.append(f"{key}:{rank}(只有 {len(pat['options'])} 个方案)")
            n = 1
        selections[pat["name"]] = _selection(n)

    if selections:
        log(f"  按复核结论套用 {len(selections)} 个模式的方案")
    df = processor.finalize_report(KnowledgeBase(), log, selections or None)

    # 「对方科目-一级」里出现"需人工分析/无效分录/…"就是这个模式没定案；
    # 这几个词没有 '-'，一级列里原样保留，正好当标记数
    contra_col = "对方科目-一级"
    unresolved = 0
    if contra_col in df.columns:
        text = df[contra_col].astype(str)
        unresolved = int(sum(int(text.str.startswith(u).sum()) for u in UNRESOLVED))
    return df, {
        "applied": len(selections),
        "ignored": ignored,
        "clamped": clamped,
        "unresolved_rows": unresolved,
    }


def stats(processor: ContraProcessor, patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """跑完 analyze 时落进 facts 的几个数（前端时间线、report 都要用）。"""
    picked = candidates(patterns)
    return {
        "vouchers": int(processor.df["_uid"].nunique()),
        "complex_vouchers": int(sum(len(v) for v in processor.complex_clusters.values())),
        "patterns": len(patterns),
        "multi_solution": int(sum(1 for p in patterns if len(p["options"]) > 1)),
        "review_candidates": len(picked),
        "timeout_patterns": int(sum(1 for p in patterns if p["timeout"])),
        "no_solution": int(sum(1 for p in patterns if not p["options"])),
    }
