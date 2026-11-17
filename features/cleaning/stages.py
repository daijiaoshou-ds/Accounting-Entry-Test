# -*- coding: utf-8 -*-
"""序时账清洗 —— 阶段实现，同时是**所有下游功能共用的第一段**。

    read            程序   读表 + 认列名（journal_io）
    clean           程序   业务桶（summary_cleaner，纯模型判定）
    review_bucket   AI     复核业务分类  ← 停
    export          程序   写回 + 导出（分类结果 / 凭证明细 / 凭证汇总）

为什么 read / clean / review_bucket 放在"清洗"下而不是各功能各抄一份：
现金流、对方科目、会计分录测试**都需要这一步，且结果必须一致**。以前
`summary_cleaner_v3` 自带一套清洗，同一份账在两边会得出不同业务桶，而且
是静默的。现在只有这一份实现，下游 `from ..cleaning.stages import run_read`。

现金流那类"还要看钱的方向"的功能，通过 `review_items(extra=...)` 往复核面里
追加自己的判据（见 `features/cashflow/stages.py` 的 `_direction_conflicts`），
共享代码不用知道现金流的业务。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

import journal_io
from jobflow import Job
from jobflow.stage import Ctx, ReviewRequest, StageResult

from summary_cleaner import pipeline as clean_pipeline
from summary_cleaner.v2.classifier import SENTINEL_BUCKETS, get_classifier
from summary_cleaner.v2.config import BUCKET_REGISTRY, MERGED_BUCKETS

REQUIRES = ("voucher_id", "subject_l1", "debit", "credit")

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
    "表示这批不再改，剩下的会在结果里标「需复核」。")

# 列怎么排、哪些默认折叠由接口层决定（`api/flow_api.py`）——阶段只管内容。

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


# ============================================================================
# ① read —— 载入序时账（配账时已经解析过，这里只搬）
# ============================================================================

def prepared_dir(options: Dict[str, Any]) -> Optional[Path]:
    """配账阶段落盘的「已解析账」目录（`raw.parquet` + `schema.json`）。

    为什么读表要放在配账那边：读表 + 认列是**账的属性**，一份账只该解析一次。
    四个功能各解析一遍不只是多花几秒——更糟的是各认各的列：同一份账在清洗里
    认对了、在对方科目里认错了一列，两边的结果对不上，而任何地方都不会报错。

    解析出来的是一张 parquet（列有类型、凭证号不会丢前导零、没有编码歧义），
    比 csv 更适合当内部格式：同一张表反复读只要几毫秒，csv 每次都要重新推断
    类型（`0001` 会变成 `1`，`2025-01-01` 会变成字符串或日期，看推断心情）。
    """
    raw = str((options or {}).get("prepared") or "").strip()
    if not raw:
        return None
    folder = Path(raw)
    if not (folder / "raw.parquet").is_file() or not (folder / "schema.json").is_file():
        return None
    return folder


def prepared_signature(ctx: Ctx) -> str:
    """已解析账的内容指纹，进 read 阶段的哈希。

    只在"重新配账"时才会变（换账/改映射后重算 combo 会一并重写 raw.parquet），
    这时下游必须重算；平时它是个常量，不会把历史任务作废。
    """
    folder = prepared_dir(ctx.options or {})
    if folder is None:
        return ""
    digest = hashlib.sha256()
    with open(folder / "raw.parquet", "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def run_read(ctx: Ctx) -> StageResult:
    return read_journal(ctx, REQUIRES)


def read_journal(ctx: Ctx, requires: Any = REQUIRES) -> StageResult:
    """载入账并按 `requires` 校验本功能要用的字段。

    `requires` 是参数而不是常量：对方科目 / 会计分录测试的字段契约比清洗多一个
    「日期」（`journal_io/fields.py` 的 FEATURE_REQUIRES），但载入逻辑一字不差。
    各功能各抄一份，迟早会有一份认列认错。

    两条路：
      · 配过账（`options.prepared` 指到任务的 prepared/）→ 直接载入，不解析原件；
      · 没配过账（命令行直接跑一份账）→ 当场读原件 + 合并手工映射（老路，保留）。
    """
    folder = prepared_dir(ctx.options or {})
    if folder is not None:
        return _load_prepared(ctx, folder, requires)
    return _read_source(ctx, requires)


def _load_prepared(ctx: Ctx, folder: Path, requires: Any) -> StageResult:
    """载入配账时解析好的表。"""
    meta = json.loads((folder / "schema.json").read_text(encoding="utf-8"))
    j = journal_io.Journal(pd.read_parquet(folder / "raw.parquet"),
                           journal_io.JournalSchema(**meta),
                           source=ctx.job.state["input"]["filename"])
    j.require(requires)
    ctx.job.save_journal(j)          # 搬进本任务：任务能独立重跑、独立打包

    vids = j.keys("voucher_id")
    return StageResult(facts={
        "rows": int(len(j)),
        "vouchers": int(vids[vids != ""].nunique()),
        "engine": "parquet",
        "mapping": j.schema.mapping,
        "unmapped": list(j.schema.unmapped),
        "source": "task",
    })


def _read_source(ctx: Ctx, requires: Any) -> StageResult:
    """老路：当场读原件（命令行/没配账时用）。"""
    override = (ctx.options.get("mapping") or {}) if ctx.options else {}
    j = journal_io.read(ctx.source, requires=())     # 先读，再合并手工映射

    if override:
        for key, col in override.items():
            if col not in j.df.columns:
                raise ValueError(f"mapping 里 {key}={col!r}：表里没有这一列")
            j.schema.mapping[key] = col
    j.require(requires)

    ctx.job.save_journal(j)
    vids = j.keys("voucher_id")
    return StageResult(facts={
        "rows": int(len(j)),
        "vouchers": int(vids[vids != ""].nunique()),
        "engine": j.schema.engine,
        "mapping": j.schema.mapping,
        "unmapped": list(j.schema.unmapped),
        "source": "file",
    })


# ============================================================================
# ② clean
# ============================================================================

def run_clean(ctx: Ctx) -> StageResult:
    j = ctx.journal
    clf = get_classifier()
    clf._load_nn_if_available()
    if not getattr(clf, "_nn_available", False):
        raise RuntimeError(
            "上游模型不可用：业务分类的前提是模型，"
            "请先确认 summary_cleaner 的模型交付物（nn/_storage/）齐备")

    res = clean_pipeline.run(j.df, j.schema.legacy(), clf,
                             export_training=False,
                             progress=lambda pct, msg: ctx.log(f"   [{pct:>3}%] {msg}"))
    ctx.save("result.parquet", res.df)
    ctx.save("detail.parquet", res.detail)

    stats = res.stats or {}
    engine = stats.get("engine") or {}
    return StageResult(
        artifacts={"result": str(ctx.path("clean", "result.parquet")),
                   "detail": str(ctx.path("clean", "detail.parquet"))},
        facts={
            "rows": int(len(res.df)),
            "vouchers": int(len(res.detail)),
            "bucket_counts": stats.get("bucket_counts") or {},
            "confidence_dist": stats.get("confidence_dist") or {},
            "hard_rule_counts": stats.get("hard_rule_counts") or {},
            "model_calls": engine.get("model_calls"),
            "modeled_vouchers": engine.get("modeled_vouchers"),
            "unclassified": engine.get("unclassified"),
            "unknown_subjects": len(stats.get("unknown_subjects") or {}),
            "model": engine.get("model") or {},
        })


# ============================================================================
# ③ review_bucket（AI）—— 共享实现，extra 是下游追加的复核理由
# ============================================================================

ExtraHook = Callable[[Ctx, pd.DataFrame], Dict[str, str]]


def _group_rows(view: pd.DataFrame):
    """按"业务等价组"压行：一组一行，返回 [(代表行, 张数, 冲突理由, 成员样张)]。

    为什么按组复核：组里的凭证科目组合相同、摘要去掉数字后相同/相似，引擎本来就是
    "判一次、其余抄作业"（`summary_cleaner/dedup.py`）。逐张问等于把同一道题做 N 遍：
    1 万张同模板凭证会变成 1 万条待办，而人只需要看 1 条——判一条，整组跟着改。

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

    一条待办 = 一组同判的凭证（key 是组代表）。改一条，整组生效——`run_export`
    按 `组代表` 展开回每张凭证。
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


def review_bucket(ctx: Ctx) -> ReviewRequest:
    """清洗自己的复核面：只看模型不确定的。"""
    return review_items(ctx)


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


# ============================================================================
# ④ export（清洗自己的产物）
# ============================================================================

REVIEW_COLUMNS = ["凭证号", "摘要", "科目", "金额", "业务分类", "置信度",
                  "校准概率", "模型Top3", "判定来源", "后处理", "未知科目"]


def build_vouchers(df: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """凭证级汇总（一凭证一行，按校准概率升序：最不确定的在前）。"""
    v_col = mapping["voucher_no"]
    s_col = mapping.get("subject", "")
    sum_col = mapping.get("summary", "")
    d_col = mapping.get("debit", "")

    keep = [v_col] + [c for c in ("业务分类", "置信度", "校准概率", "模型Top3",
                                  "判定来源", "后处理") if c in df.columns]
    head = df[keep].drop_duplicates(subset=[v_col], keep="first").copy()

    if sum_col and sum_col in df.columns:
        first_sum = df[[v_col, sum_col]].drop_duplicates(subset=[v_col], keep="first")
        head = head.merge(first_sum, on=v_col, how="left")
    if s_col and s_col in df.columns:
        subj = (df[[v_col, s_col]].dropna(subset=[s_col])
                .astype({s_col: str}).groupby(v_col)[s_col]
                .agg(lambda s: "、".join(pd.unique(s))))
        head = head.merge(subj.rename("科目"), on=v_col, how="left")
    if d_col and d_col in df.columns:
        amt = pd.to_numeric(df[d_col].astype(str).str.replace(",", "").str.strip(),
                            errors="coerce").abs().fillna(0.0)
        head = head.merge(amt.groupby(df[v_col]).sum().rename("金额"),
                          left_on=v_col, right_index=True, how="left")

    head = head.rename(columns={v_col: "凭证号"})
    if sum_col and sum_col in head.columns:
        head = head.rename(columns={sum_col: "摘要"})
        head["摘要"] = head["摘要"].astype(str).str.slice(0, 100)

    p = pd.to_numeric(head.get("校准概率"), errors="coerce")
    head["_p"] = p.fillna(-1.0)
    head = head.sort_values("_p").drop(columns="_p").reset_index(drop=True)

    for col in REVIEW_COLUMNS:
        if col not in head.columns:
            head[col] = ""
    return head[REVIEW_COLUMNS]


def _attach_contra(ctx: Ctx, result: pd.DataFrame,
                   mapping: Dict[str, str]) -> tuple:
    """把对方科目贴到行级结果表上（没有上游时原样返回）。

    贴进来而不是"另存一张表"：用户抱怨的就是这个——分类在一份产物里、对方科目在
    另一份产物里，要看同一行得自己拿 Excel 去 vlookup。现在一张表里 "业务分类"
    和 "对方科目" 挨着，翻到哪张凭证都看得见它对给了谁。
    """
    p = ctx.path("link", "contra_map.parquet")
    if not p.exists():
        return result, None

    table = pd.read_parquet(p)
    v_col, s_col, c_col = mapping["voucher_no"], mapping["subject"], mapping.get("credit")
    credit = pd.to_numeric(result[c_col], errors="coerce").fillna(0.0) if c_col else 0.0
    keys = pd.DataFrame({
        "__v": result[v_col].astype(str).str.strip(),
        "__s": result[s_col].astype(str).str.strip(),
        "__d": ["贷" if abs(value) > 0 else "借" for value in credit],
    })
    glued = result.join(keys).merge(
        table, left_on=["__v", "__s", "__d"],
        right_on=["凭证号", "科目", "方向"], how="left", sort=False)
    glued = glued.drop(columns=["__v", "__s", "__d", "凭证号", "科目", "方向"],
                       errors="ignore")

    # 放在「业务分类」左边：读一行时，"它是什么业务、钱对给谁"在同一屏
    added = ["对方科目-一级", "对方科目-二级"]
    rest = [c for c in glued.columns if c not in added]
    at = rest.index("业务分类") if "业务分类" in rest else len(rest)
    glued = glued[rest[:at] + added + rest[at:]]

    linked = int(glued["对方科目-一级"].notna().sum())
    return glued, {"rows": int(len(glued)), "linked": linked,
                   "unlinked": int(len(glued) - linked), "groups": int(len(table))}


def run_export(ctx: Ctx) -> StageResult:
    j = ctx.journal
    mapping = j.schema.legacy()
    result = ctx.load("result.parquet", "clean")
    detail = ctx.load("detail.parquet", "clean")

    # 改过桶的凭证：业务分类/判定来源换成复核后的（否则交付物与结论对不上）。
    # 复核的结论是按"组代表"给的，这里先铺回组内每张凭证。
    bucket_dec = ctx.decisions("review_bucket")
    changed = 0
    if bucket_dec:
        v_col = mapping["voucher_no"]
        keys = result[v_col].astype(str)
        per_voucher = expand_group_decisions(detail, bucket_dec)
        result = result.copy()
        result["业务分类"] = keys.map(per_voucher).fillna(result["业务分类"])
        hit = keys.isin(per_voucher.keys())
        changed = int(result.loc[hit, v_col].astype(str).nunique())
        if "判定来源" in result.columns:
            result.loc[hit, "判定来源"] = "复核改判"
        else:
            result["判定来源"] = ""

    result, contra_info = _attach_contra(ctx, result, mapping)
    vouchers = build_vouchers(result, mapping)
    # 组ID / 组代表 是复核用的内部列，不进交付物
    exported = detail.drop(columns=[c for c in ("组ID", "组代表") if c in detail.columns])
    out_dir = ctx.job.dir / "out" / "export"
    stem = ctx.job.state["input"]["filename"].rsplit(".", 1)[0]
    written = journal_io.write_sheets(
        {"业务分类结果": result, "凭证明细": exported, "凭证汇总": vouchers},
        out_dir / f"{stem}_业务分类.xlsx", target="download")

    # 统计从写回后的表里算：上游模式（clean 被跳过）没有 clean 的 facts 可用
    v_col = mapping["voucher_no"]
    per_voucher = result.drop_duplicates(subset=[v_col], keep="first")
    bucket_counts = per_voucher["业务分类"].value_counts().to_dict()
    confidence = per_voucher["置信度"].value_counts().to_dict()

    return StageResult(
        artifacts={"result": written.paths.get("业务分类结果")
                   or next(iter(written.paths.values())),
                   **{f"sheet:{k}": v for k, v in written.paths.items()}},
        facts={
            "rows": int(len(result)),
            "vouchers": int(len(vouchers)),
            "bucket_counts": {str(k): int(v) for k, v in bucket_counts.items()},
            "confidence_dist": {str(k): int(v) for k, v in confidence.items()},
            "bucket_overrides": changed,          # 被改到的凭证张数（不是结论条数）
            "bucket_override_groups": len(bucket_dec),
            # 接了对方科目分析才有：多少行贴上了对方科目（没贴上的多半是空科目行）
            "contra": contra_info or {},
            "export": {"format": written.fmt, "seconds": written.seconds,
                       "hint": written.hint},
            "files": written.paths,
        })


# ============================================================================
# 接着上游跑
# ============================================================================
#
# 管线是「对方科目分析 → 序时账清洗 → 现金流 / 会计分录测试」，所以这里有两件
# 不一样的事，都叫 link，因为对用户是同一件事（接着上一棒跑）：
#
#   run_link_contra   清洗接对方科目：账自己读，只搬对方科目那张行表
#   run_link          下游接清洗：把 read/clean 的产物整个搬过来，自己不再读表
#
# 拆成两个函数而不是一个"通用 link"，是因为上游给的产物完全不同：一个给的是
# 「每一行的对方科目」，一个是「整本账 + 业务桶」——写成通用的话，那堆 if 只会
# 藏住"这一棒到底继承了什么"。

CONTTRA_ROWS = "contra_rows.parquet"

# 从上游清洗任务搬过来的产物：自己的 read / clean 就不用再跑
# (源相对路径, 落到哪个阶段目录, 改名, 可以缺吗)
_UPSTREAM_FILES = (
    ("out/read/raw.parquet", "read", None, False),
    ("out/read/schema.json", "read", None, False),
    ("out/clean/result.parquet", "clean", "result.parquet", False),
    ("out/clean/detail.parquet", "clean", "detail.parquet", False),
    # 上游清洗若本身接过对方科目，这张行表跟着一起搬 —— 分录测试的科目对
    # 要用它；没有也不该让整条链断掉（老任务都没有）
    ("out/link/contra_rows.parquet", "link", None, True),
    ("decisions/review_bucket.json", None, None, True),
)


def upstream_id(ctx: Ctx) -> str:
    return str((ctx.options or {}).get("upstream") or "").strip()


def has_upstream(ctx: Ctx) -> bool:
    return bool(upstream_id(ctx))


def upstream_signature(ctx: Ctx) -> Any:
    """上游任务的"内容指纹"：干净阶段哈希 + 改桶结论哈希。

    只把 job_id 放进选项是不够的——同一个 id 下改了桶，下游不会重算。
    """
    jid = upstream_id(ctx)
    if not jid:
        return ""
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        return f"{jid}:missing"
    return [jid, up.stage_state("clean").get("hash") or "",
            up.decision_hash("review_bucket")]


def contra_signature(ctx: Ctx) -> Any:
    """上游「对方科目分析」的内容指纹：导出哈希 + 模式定案哈希。

    同上：只记 job_id 的话，对方科目任务定完案重跑一遍，清洗这边不会重算，
    结果表里躺的还是上一轮的对方科目。
    """
    jid = upstream_id(ctx)
    if not jid:
        return ""
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        return f"{jid}:missing"
    return [jid, up.stage_state("export").get("hash") or "",
            up.decision_hash("review_pattern")]


def _same_ledger(ctx: Ctx, up: Job, jid: str) -> None:
    """必须是同一份账：两份对不上还接，结果算的是 A 账、文件名记的是 B 账。"""
    mine = ctx.job.state.get("input") or {}
    theirs = up.state.get("input") or {}
    if mine.get("sha256") and theirs.get("sha256") \
            and mine["sha256"] != theirs["sha256"]:
        raise ValueError(
            f"两份账不是同一份：本任务 {mine.get('filename', '?')}"
            f"（sha {mine['sha256'][:8]}），上游 {jid} 跑的是 "
            f"{theirs.get('filename', '?')}（sha {theirs['sha256'][:8]}）。"
            "接上游就别再传文件（`journal run` 只给 --option upstream），"
            "或者换成上游那份账。")


def _join_unique(values: List[Any]) -> str:
    """去重保序后用「、」连起来。

    同一行会在对方科目表里出现多次：多借多贷的凭证被拆成一对一对，一个科目
    自然对应好几个对方科目。清洗的结果表是**行级**的，装不下一对一的关系，
    所以这里把"这一行的钱可能对给谁"拼成一个列表；要精确到金额的配对，去翻
    对方科目分析自己的产物（它就在同一个任务里）。
    """
    seen: List[str] = []
    for value in values:
        text = str(value if value is not None else "").strip()
        if text and text != "nan" and text not in seen:
            seen.append(text)
    return "、".join(seen)


def contra_map(rows: pd.DataFrame, mapping: Dict[str, str]) -> pd.DataFrame:
    """对方科目行表 → 按「凭证号 + 一级科目 + 方向」压成一行。

    为什么不用科目编码：对方科目表在拆多借多贷时重建了行，编码会空（引擎只保
    留了科目名），按编码对会有 11% 的行挂不上；按「凭证号 + 一级科目 + 方向」
    对是 100%。
    """
    v_col, s_col = mapping.get("voucher_no"), mapping.get("subject")
    c_col = mapping.get("credit")
    if not (v_col and s_col and c_col):
        raise ValueError("字段契约里缺凭证号/科目/贷方金额，接不了对方科目")

    credit = pd.to_numeric(rows[c_col], errors="coerce").fillna(0.0)
    work = pd.DataFrame({
        "凭证号": rows[v_col].astype(str).str.strip(),
        "科目": rows[s_col].astype(str).str.strip(),
        "方向": ["贷" if abs(c) > 0 else "借" for c in credit],
        "对方科目-一级": rows["对方科目-一级"] if "对方科目-一级" in rows.columns else "",
        "对方科目-二级": rows["对方科目-二级"] if "对方科目-二级" in rows.columns else "",
    })
    return (work.groupby(["凭证号", "科目", "方向"], sort=False)
            [["对方科目-一级", "对方科目-二级"]]
            .agg(_join_unique).reset_index())


def run_link_contra(ctx: Ctx) -> StageResult:
    """接对方科目分析：把它的行表和结论搬进来，账本身自己读（read 已跑过）。

    搬而不是引用：本任务要能独立重跑、独立打包，上游被删也不影响。
    """
    jid = upstream_id(ctx)
    if not jid:
        raise ValueError("link 阶段要求 options.upstream 指向一个对方科目分析任务")
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        raise ValueError(f"上游任务不存在：{jid}") from None
    feature = str(up.state.get("feature") or "")
    if feature != "contra":
        raise ValueError(f"清洗只接「对方科目分析」的产物，{jid} 是「{feature}」")
    if up.stage_state("export").get("status") not in ("done", "skipped"):
        raise ValueError(f"上游 {jid} 的对方科目分析还没跑完（当前 {up.status}）")
    _same_ledger(ctx, up, jid)

    src = up.dir / "out" / "export" / CONTTRA_ROWS
    if not src.is_file():
        raise ValueError(f"上游任务缺产物：{src}（对方科目分析没有导出这张表）")

    dst = ctx.path("link", CONTTRA_ROWS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())

    rows = pd.read_parquet(src)
    table = contra_map(rows, ctx.journal.schema.legacy())
    ctx.save("contra_map.parquet", table)

    ctx.log(f"  接对方科目：{len(rows):,} 行 → {len(table):,} 组（凭证·科目·方向）")
    return StageResult(facts={
        "upstream": jid,
        "upstream_feature": feature,
        "rows": int(len(rows)),
        "groups": int(len(table)),
    })


def run_link(ctx: Ctx) -> StageResult:
    """把上游任务的读表/分类产物与复核结论搬进本任务。

    搬而不是"引用"：本任务要能独立重跑、独立打包，上游被删也不影响。
    上游改了桶，`upstream_signature` 会变 → 本阶段重跑（重新搬）→ 下游跟着重算。
    """
    jid = upstream_id(ctx)
    if not jid:
        raise ValueError("link 阶段要求 options.upstream 指向一个已完成的任务")
    try:
        up = Job.open(jid)
    except FileNotFoundError:
        raise ValueError(f"上游任务不存在：{jid}") from None
    # 不要求上游"已完成"：复核到一半也允许接——judge 只会应用已有的改桶结论，
    # 没复核的继续用模型原判。上游之后又改了几条，resume 本任务就会重算。
    if up.stage_state("clean").get("status") not in ("done", "skipped"):
        raise ValueError(f"上游任务 {jid} 的业务分类还没跑完（当前 {up.status}）")

    # 必须是同一份账。link 搬的是上游的 out/read，本任务 source/ 里的原件从此不参与
    # 计算——两份对不上还不拦，结果算的是 A 账、文件名和 sha 却记着 B 账，
    # 事后没有任何地方会报错。
    _same_ledger(ctx, up, jid)

    copied: List[str] = []
    for src_rel, _stage, new_name, optional in _UPSTREAM_FILES:
        src = up.dir / src_rel
        if not src.is_file():
            if optional:
                continue          # 改桶结论、对方科目行表，缺了都正常
            raise ValueError(f"上游任务缺产物：{src_rel}")
        dst = ctx.job.dir / src_rel
        if new_name:                      # result/detail 落到自己的 clean 目录
            dst = ctx.job.dir / "out" / "clean" / new_name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        copied.append(src_rel)

    # journal 对象是懒加载的：作废缓存，让它读刚搬进来的 out/read
    ctx.job.drop_journal_cache()
    j = ctx.journal
    detail = ctx.load("detail.parquet", "clean")
    return StageResult(facts={
        "upstream": jid,
        "upstream_status": up.status,
        "copied": copied,
        "rows": int(len(j)),
        "vouchers": int(len(detail)),
        "bucket_overrides": len(ctx.decisions("review_bucket")),
    })
