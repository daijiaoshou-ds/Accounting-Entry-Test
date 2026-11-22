# -*- coding: utf-8 -*-
"""复核面：业务分类待办怎么切、怎么压组、怎么铺回。

四个功能的"复核业务桶"是同一张表，只是筛选理由不同（清洗看置信度，现金流还要看
桶和钱的方向对不对）。差异通过 `extra` 钩子注入，共用代码不知道现金流的业务。

**复核的粒度是"业务等价组"不是"张"**：同科目组合、摘要去掉数字后相同的凭证被引擎
压成一组（`summary_cleaner/dedup.py`），判一次抄全组，复核也按组问——一条待办带
「张数」「样张」，交一条整组跟着改。1 万张同模板凭证 = 1 条待办，不是 1 万条。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

import journal_io
from jobflow.stage import Ctx, ReviewRequest

from ..classify.classifier import SENTINEL_BUCKETS
from ..classify.config import BUCKET_REGISTRY, MERGED_BUCKETS

# 可指派的业务桶（去掉哨兵与已折叠的子桶）
ASSIGNABLE_BUCKETS = [b for b in BUCKET_REGISTRY
                      if b not in SENTINEL_BUCKETS and b not in MERGED_BUCKETS]

REVIEW_HELP_BUCKET = (
    "这些待办每条是一「组」业务等价的凭证（同科目组合、摘要去数字后相同），"
    "条目的「张数」是组里有几张、「样张」是其中几张的凭证号——**改一条，整组生效**。\n"
    "逐组确认它属于哪个业务桶：\n"
    "  · 认同模型判的 → 不用写进 decisions（它会保持原判）\n"
    "  · 判错了 → 写 {\"key\": 条目的 key（组代表凭证号）, \"value\": 正确的桶, \"note\": 依据}\n"
    "整批看完了还有拿不准的 → `journal submit <job> --empty --stage review_bucket` "
    "表示这批不再改：结果里保持模型原判，置信度仍是中/低，按这两列就能筛出来。")

ExtraHook = Callable[[Ctx, pd.DataFrame], Dict[str, str]]


def _amt(raw: Any) -> float:
    """金额字符串 → float（千分位、全角逗号、空串、NaN 交给 journal_io.amount）。"""
    return journal_io.amount(raw)


def voucher_lines(ctx: Ctx, vids: List[str],
                  max_entries: int = 14) -> Dict[str, List[str]]:
    """取这些凭证的**完整分录**（按"科目+方向"聚合），给复核用。

    为什么必须给：判业务桶要看整张凭证在干什么。只给「一级科目 + 凭证金额」时，
    两条「银行存款 / 32,000」在纸上完全一样，而对方科目是「管理费用」还是
    「应付账款—设备款」决定了桶完全不同。

    为什么聚合：一张计提工资的凭证能有 40 行（管理费用×6、制造费用×5…），
    逐行列出来只是噪声。按"科目+方向"合并成 `管理费用 借 12,345.67（6 笔）`，
    20 行变 5 行，判桶需要的信息一条不少。

    只读四列（大账 result.parquet 有 85 万行，为了几十张凭证读全表不值）。
    返回 {凭证号: ["管理费用 借 1,234.56（6 笔）", ...]}，按首次出现的顺序。
    """
    mapping = ctx.journal.schema.legacy()
    v_col, s_col = mapping.get("voucher_no"), mapping.get("subject")
    d_col, c_col = mapping.get("debit"), mapping.get("credit")
    if not (v_col and s_col):
        return {}
    p = ctx.path("clean", "result.parquet")
    if not p.exists() or not vids:
        return {}

    cols = [c for c in (v_col, s_col, d_col, c_col) if c]
    df = pd.read_parquet(p, columns=cols)
    key = df[v_col].astype(str)
    sub = df[key.isin(set(vids))]

    out: Dict[str, List[str]] = {}
    for vid, g in sub.groupby(v_col, sort=False):
        entries: List[str] = []          # 保持首次出现顺序
        bucket: Dict[tuple, List[Any]] = {}
        for _, r in g.iterrows():
            # 空科目是常事（空白行/半截表头）：NA 不能进布尔判断，统一走 text()
            subject = journal_io.text(r[s_col], "（空科目）")
            debit = _amt(r[d_col]) if d_col else 0.0
            credit = _amt(r[c_col]) if c_col else 0.0
            if debit:
                k, sign = (subject, "借"), debit
            elif credit:
                k, sign = (subject, "贷"), credit
            else:
                k, sign = (subject, "—"), 0.0
            if k not in bucket:
                bucket[k] = [0.0, 0]
                entries.append(k)
            bucket[k][0] += sign
            bucket[k][1] += 1

        lines = []
        for k in entries[:max_entries]:
            subject, side = k
            total, n = bucket[k]
            text = f"{subject} {side} {total:.2f}"
            lines.append(f"{text}（{n} 笔）" if n > 1 else text)
        if len(entries) > max_entries:
            lines.append(f"……另有 {len(entries) - max_entries} 个科目")
        out[str(vid)] = lines
    return out


def _group_rows(view: pd.DataFrame):
    """按"业务等价组"压行：一组一行，返回 [(代表行, 张数, 冲突理由, 成员样张)]。

    没有 `组ID`（老任务/别的功能）时退化成逐行，行为不变。
    """
    if "组ID" not in view.columns:
        return [(row, 1, str(row.get("_conf") or ""), [str(row["凭证号"])])
                for _, row in view.iterrows()]
    out = []
    for _gid, block in view.groupby("组ID", sort=False):
        head = block.iloc[0]
        why = next((str(c) for c in block["_conf"] if c), "")
        out.append((head, len(block), why, [str(v) for v in block["_vid"].tolist()[:3]]))
    return out


def expand_group_decisions(detail: pd.DataFrame, dec: Dict[str, str]) -> Dict[str, str]:
    """把按"组代表"交的结论铺到组内每一张凭证。

    复核是按组问的（一条待办 = 一组同判的凭证），结论的 key 是组代表；写回时要落到
    每一张。老数据（直接按凭证号交的）原样保留，两种口径可以混用。
    """
    out: Dict[str, str] = {}
    if "组代表" in detail.columns and "凭证号" in detail.columns:
        for vid, rep in zip(detail["凭证号"].astype(str), detail["组代表"].astype(str)):
            if rep in dec:
                out[vid] = dec[rep]
    for key, value in dec.items():
        out.setdefault(str(key), value)
    return out


def review_items(ctx: Ctx, extra: Optional[ExtraHook] = None,
                 only_extra: bool = False) -> ReviewRequest:
    """业务分类的复核面 = 中/低置信 ∪ extra(ctx, detail) 指出的，**按业务等价组**给。

    `extra` 返回 {凭证号: 为什么}；现金流用它把"桶和钱的方向对不上"的凭证也拉进来
    （那些往往置信度很高，只按置信度筛根本没有机会改）。

    `only_extra=True` 时只看 extra：从上游清洗任务起跑时用——中/低置信那批上游
    已经看过一遍了（弃权也是一种结论），但"方向冲突"是上游**不可能发现**的新信息，
    必须在这里给一次机会。

    一条待办 = 一组同判的凭证（key 是组代表）。改一条，整组生效——导出的阶段
    按 `组代表` 展开回每张凭证（`expand_group_decisions`）。
    """
    detail = ctx.load("detail.parquet", "clean")
    decided = ctx.decisions("review_bucket")

    conflict: Dict[str, str] = dict(extra(ctx, detail)) if extra else {}
    vids = detail["凭证号"].astype(str)
    unsure = detail["置信度"].isin(["中", "低"]) if not only_extra else False
    hit = vids.isin(conflict.keys()) if only_extra else (unsure | vids.isin(conflict.keys()))

    view = detail[hit].copy()
    view["_vid"] = view["凭证号"].astype(str)
    view["_conf"] = [conflict.get(v, "") for v in view["_vid"]]
    # 已经交过结论的整组跳过：决策的 key 是组代表（老数据是凭证号）
    key_col = "组代表" if "组代表" in view.columns else "凭证号"
    view = view[~view[key_col].astype(str).isin(decided)]
    if view.empty:
        return ReviewRequest()

    rows = _group_rows(view)
    if not rows:
        return ReviewRequest()

    def why_of(row, why: str, size: int) -> str:
        conf = journal_io.text(row.get("置信度"))
        prob = row.get("校准概率")
        parts = [why]
        if conf in ("中", "低") and not only_extra:
            parts.append(f"置信度{conf}（{'-' if pd.isna(prob) else prob}）")
        if size > 1:
            parts.append(f"同组 {size} 张同判")
        return "；".join(filter(None, parts))

    def probability(row) -> float:
        value = pd.to_numeric(row.get("校准概率"), errors="coerce")
        return -1.0 if pd.isna(value) else float(value)

    rows.sort(key=lambda item: (0 if item[2] else 1, -probability(item[0])))

    limit = ctx.max_review()
    total = len(rows)
    rows = rows[:limit]

    def rep_of(row) -> str:
        return journal_io.text(row.get("组代表"), "") or journal_io.text(row.get("凭证号"))

    keys = [rep_of(row) for row, _size, _why, _members in rows]
    lines = voucher_lines(ctx, keys)

    items = []
    for (row, size, why, members), key in zip(rows, keys):
        items.append({
            "key": key,
            "凭证号": key,
            # 一条待办覆盖几张凭证；样张给出组内前几张的凭证号，方便抽查
            "张数": size,
            "样张": members,
            "为什么要看": why_of(row, why, size),
            "摘要": journal_io.text(row.get("摘要"))[:120],
            # 整张凭证的分录：判桶看的是"对方科目在干什么"，不是货币资金那一行
            "分录": lines.get(key, []),
            "科目": journal_io.text(row.get("科目")),
            "金额": journal_io.amount(row.get("金额")),
            "模型桶": journal_io.text(row.get("业务分类")),
            "置信度": journal_io.text(row.get("置信度")),
            "校准概率": None if pd.isna(row.get("校准概率")) else round(float(row["校准概率"]), 4),
            "模型Top3": fmt_top3(row.get("模型Top3")),
        })

    return ReviewRequest(items=items, value_enum=ASSIGNABLE_BUCKETS,
                         help=REVIEW_HELP_BUCKET, key_name="voucher_id",
                         truncated=total > len(items))


def fmt_top3(raw: Any, n: int = 3) -> str:
    """`[["费用",0.62],["采购",0.37]]` → `费用 0.62 · 采购 0.37`。

    detail 里存的是 JSON 字符串，直接丢给复核表会显示成一坨方括号——
    给 AI 看的东西不该还要它自己解析。
    """
    if not raw:
        return ""
    try:
        pairs = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return str(raw)[:120]
    out = []
    for item in list(pairs)[:n]:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            name, prob = item
            try:
                out.append(f"{name} {float(prob):.2f}")
            except (TypeError, ValueError):
                out.append(str(name))
        else:
            out.append(str(item))
    return " · ".join(out)
