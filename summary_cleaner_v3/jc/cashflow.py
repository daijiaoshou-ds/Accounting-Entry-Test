# -*- coding: utf-8 -*-
"""现金流 —— 序时账清洗之后的第二步：一键编现金流量表。

口径依据：reference/CASHFLOW_GUIDE.md（用户版包内自带，不依赖仓库设计稿）

    ① 只看现金流相关凭证：含「银行存款 / 库存现金 / 其他货币资金」的**整张**凭证
    ② 方向：货币资金**借方 = 收现，贷方 = 付现**，按**绝对值**判（红字凭证）
    ③ 项目范围：**只由业务桶决定**（清洗阶段已经判好），不需要一级科目表，
       也不需要对方科目
    ④ 选定：桶的候选按方向过滤，剩 1 个就确定；剩多个则需复核
    ⑤ 全程纯程序，不过模型、不过 AI

方向不是装饰：销售收款 100 万、退货 20 万，应当只出现"销售收款 80 万"，
不能出现"销售收款 100 万 + 采购付款 20 万"。所以候选必须先按方向过滤。

程序不做语义猜测：桶的候选过滤后仍有多个时，**不硬选**，标记 needs_review
交给 AI/人 —— 与 3.0 删掉程序打分的思路一致（宁可标注，不要假装会判）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import CF_DIR

# 方向
CF_IN = "收"
CF_OUT = "付"
CF_NONE = "无"

# 判定来源
SRC_DIRECT = "桶唯一"
SRC_DIRECTED = "桶+方向"
SRC_RULED = "科目裁判"
SRC_PATCH = "补丁补充"
SRC_REVIEW = "待复核"
SRC_EMPTY = "无现金流量"
SRC_NO_CASH = "冲销无净流量"
SRC_NONE = "未分类"

# 货币资金科目（一级科目名）
MONEY_FUNDS = {"库存现金", "银行存款", "其他货币资金", "其它货币资金"}


# ============================================================================
# 配置
# ============================================================================

@lru_cache(maxsize=1)
def load_cf_config() -> Dict[str, Any]:
    """加载 assets/cf/ 三件套。缺文件时给出可执行的报错。"""
    p_items = CF_DIR / "cf_items.json"
    p_bucket = CF_DIR / "bucket_to_cf.json"
    p_ruling = CF_DIR / "account_ruling.json"
    missing = [str(p) for p in (p_items, p_bucket) if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "现金流映射表缺失：" + "、".join(missing)
            + "。请先运行 scripts/build_cf_maps.py 生成。")

    items_doc = json.loads(p_items.read_text(encoding="utf-8-sig"))
    bucket_doc = json.loads(p_bucket.read_text(encoding="utf-8-sig"))
    ruling_doc = (json.loads(p_ruling.read_text(encoding="utf-8-sig"))
                  if p_ruling.exists() else {})

    return {
        "items": {str(k): str(v) for k, v in items_doc["items"].items()},
        "sections": {str(k): list(v)
                     for k, v in items_doc.get("sections", {}).items()},
        "bucket_map": {str(k): list(v) for k, v in bucket_doc["map"].items()},
        # {现金流量项目: [一级科目...]}，只在桶定不下来时当裁判用
        "ruling": {str(k): [str(x) for x in v]
                   for k, v in ruling_doc.get("ruling", {}).items()},
    }


def cf_items() -> List[str]:
    """全部现金流量项目，按报表顺序。"""
    cfg = load_cf_config()
    ordered: List[str] = []
    for names in cfg["sections"].values():
        for n in names:
            if n not in ordered:
                ordered.append(n)
    for n in cfg["items"]:
        if n not in ordered:
            ordered.append(n)
    return ordered


def item_direction(item: str) -> str:
    """该现金流量项目是收现还是付现（「汇率变动对现金的影响」为中性）。"""
    return load_cf_config()["items"].get(str(item), "")


def section_of(item: str) -> str:
    for sec, names in load_cf_config()["sections"].items():
        if item in names:
            return sec
    return ""


def bucket_items(bucket: str) -> List[str]:
    """业务桶 → 现金流量项目候选（无方向，来自映射表）。"""
    return list(load_cf_config()["bucket_map"].get(str(bucket).strip(), []))


def _subject_names(subjects: Iterable[str]) -> List[str]:
    """把「科目[借]」这类开关 f-string 还原成裸科目名。"""
    out: List[str] = []
    for s in subjects:
        name = str(s).strip()
        if name.endswith("]") and "[" in name:
            name = name[:name.rindex("[")]
        name = name.strip()
        if name and name not in out:
            out.append(name)
    return out


def rule_by_account(cands: Sequence[str],
                    subjects: Iterable[str]) -> Tuple[str, str]:
    """歧义裁判：拿一级科目在候选里挑一个。

    **只在业务桶 + 方向仍定不出唯一项目时调用。**

    Returns: (选中的项目, 备注)；挑不出来就返回 ("", 原因)

    规则：
      1. 把候选按裁判表映射成"这个候选认哪些一级科目"
      2. 只看凭证里真实出现的科目，命中唯一一个候选 → 采用
      3. 命中 0 个（科目没登记）或 ≥2 个（科目本身也重叠）→ 交给人工
    """
    cfg = load_cf_config()
    ruling = cfg["ruling"]
    present = set(_subject_names(subjects))

    hits: List[str] = []
    for item in cands:
        known = set(ruling.get(item, []))
        if known & present:
            hits.append(item)

    if len(hits) == 1:
        matched = sorted(set(ruling.get(hits[0], [])) & present)
        return hits[0], "一级科目「" + "、".join(matched) + "」裁判"
    if not hits:
        return "", ("一级科目（" + ("、".join(sorted(present)) or "空")
                    + "）不在裁判表里，无法定夺")
    return "", "一级科目同时命中多个候选（" + "、".join(hits) + "），无法定夺"


def filter_by_direction(items: Sequence[str], direction: str) -> List[str]:
    """按收/付方向过滤候选。

    「汇率变动对现金的影响」方向为「中性」，不属于任何一侧，直接排除
    —— 它只在报表里单独列示，不由凭证映射产生。
    """
    if direction not in (CF_IN, CF_OUT):
        return []
    return [i for i in items if item_direction(i) == direction]


def is_money_fund(subject: str) -> bool:
    """该一级科目是不是货币资金（决定要不要编现金流）。"""
    return str(subject).strip() in MONEY_FUNDS


# ============================================================================
# 单行分录
# ============================================================================

@dataclass
class MoneyRow:
    """序时账里的一行（编现金流只需要这几个字段）。"""
    subject: str = ""
    debit: float = 0.0
    credit: float = 0.0
    row_index: int = -1


@dataclass
class RowVerdict:
    """一条货币资金分录的现金流量结论。"""
    cf_item: str = ""
    direction: str = CF_NONE
    amount: float = 0.0          # 带符号：贷方（付现）为负
    source: str = SRC_NONE
    needs_review: bool = False
    no_cash: bool = False        # 同一行内一借一贷冲销，无净流量
    candidates: List[str] = field(default_factory=list)
    bucket: str = ""
    note: str = ""
    counterparty: List[str] = field(default_factory=list)   # 同凭证里的其他一级科目


def net_amount(debit: float, credit: float) -> float:
    """这一行的现金净流量（带符号）。"""
    return round(float(debit) - float(credit), 2)


def direction_of(debit: float, credit: float) -> str:
    """货币资金这一行是收现还是付现。

    口径（严格按理论文档）：**看货币资金科目在哪一方，不看正负号**。

      - 只有借方有数  → 收现（**借方是负数也算收现**：那是"冲收现"，
        冲收现 ≠ 付现。销售收款 100 万、退货 20 万应当表现为
        "销售收款 80 万"，而不是"销售收款 100 万 + 采购付款 20 万"）
      - 只有贷方有数  → 付现（贷方负数同理，是"冲付现"）
      - 借贷两方都有数 → 同一科目在同一行互相冲销，无净流量

    金额始终取 debit - credit，所以红字会自然把对应的现金流项目冲减。
    """
    has_debit = abs(float(debit)) > 0
    has_credit = abs(float(credit)) > 0
    if has_debit and has_credit:
        return CF_NONE          # 同一行一借一贷，冲销
    if has_debit:
        return CF_IN
    if has_credit:
        return CF_OUT
    return CF_NONE              # 借贷都是 0


def signed_amount(debit: float, credit: float) -> float:
    """这一行的现金净流量（保留正负号，红字即负数）。"""
    return net_amount(debit, credit)


# ============================================================================
# 判定
# ============================================================================

def judge_row(bucket: str,
              direction: str,
              subjects: Iterable[str] = ()) -> Tuple[str, str, bool, List[str], str]:
    """给定业务桶 + 方向（+ 歧义时的一级科目），定出现金流量项目。

    Returns: (现金流量项目, 判定来源, 是否需复核, 候选列表, 备注)

    判定顺序：
      1. 桶映射为空          → 无现金流量（折旧摊销 / 资金内部往来 / 汇兑损益）
      2. 方向过滤后只剩 1 个 → 直接确定（**绝大多数凭证走这条**）
      3. 过滤后剩多个        → 拿一级科目当裁判（只在这一步用一级科目）
                               裁判也定不下来 → 待复核
      4. 过滤后为空          → 提示可能漏配，交复核

    为什么一级科目只当裁判：桶是业务语义的唯一依据。若把一级科目变成
    主映射表，就等于两套口径并行，模型口径一变它就成了补丁摞补丁。
    歧义桶出现本身是个信号——说明该把桶拆细、该重训模型了。
    """
    raw = bucket_items(bucket)
    if not raw:
        return "", SRC_EMPTY, False, [], "该业务桶不产生现金流量"

    cands = filter_by_direction(raw, direction)
    if len(cands) == 1:
        src = SRC_DIRECT if len(raw) == 1 else SRC_DIRECTED
        return cands[0], src, False, cands, ""

    word = "收现" if direction == CF_IN else "付现"
    if not cands:
        return "", SRC_REVIEW, True, [], (
            f"业务桶「{bucket}」映射表里是空的（视为不产生现金流量），"
            f"但这张凭证有货币资金{word}。可能是映射表漏配，请人工确认")

    # ── 歧义：拿一级科目当裁判 ──
    picked, why = rule_by_account(cands, subjects)
    if picked:
        return picked, SRC_RULED, False, cands, f"{why}（歧义桶「{bucket}」）"

    return "", SRC_REVIEW, True, cands, (
        f"业务桶「{bucket}」在{word}方向下有 {len(cands)} 个候选，"
        f"{why}；需人工确认：" + "、".join(cands))


# ============================================================================
# 凭证级
# ============================================================================

def voucher_has_money(rows: Sequence[MoneyRow]) -> bool:
    """这张凭证里有没有货币资金科目（有就是现金流相关凭证）。"""
    return any(is_money_fund(r.subject) for r in rows)


def judge_voucher(rows: Sequence[MoneyRow],
                  bucket: str) -> List[Tuple[int, RowVerdict]]:
    """对一张凭证里**每一条货币资金分录**给结论。

    非货币资金行不产生结论，但会随整张凭证一起导出
    （理论文档：要的是"货币资金科目相关的凭证"，不是只留货币资金行）。

    Returns: [(row_index, RowVerdict), ...]
    """
    # 一级科目只在"桶 + 方向仍歧义"时当裁判，这里先备好
    voucher_subjects = [r.subject for r in rows if not is_money_fund(r.subject)]

    out: List[Tuple[int, RowVerdict]] = []
    for r in rows:
        if not is_money_fund(r.subject):
            continue
        if direction_of(r.debit, r.credit) == CF_NONE:
            # 该行借贷两方都有数 → 同一科目在一行内冲销，无净流量
            out.append((r.row_index, RowVerdict(
                cf_item="",
                direction=CF_NONE,
                amount=signed_amount(r.debit, r.credit),
                source=SRC_NO_CASH,
                needs_review=False,
                no_cash=True,
                bucket=str(bucket),
                counterparty=list(voucher_subjects),
                note="该货币资金科目借贷两方都有数（冲销分录），无净现金流量",
            )))
            continue

        direction = direction_of(r.debit, r.credit)
        item, source, review, cands, note = judge_row(
            bucket, direction, voucher_subjects)
        out.append((r.row_index, RowVerdict(
            cf_item=item,
            direction=direction,
            amount=signed_amount(r.debit, r.credit),
            source=source,
            needs_review=review,
            no_cash=False,
            candidates=cands,
            bucket=str(bucket),
            counterparty=list(voucher_subjects),
            note=note,
        )))
    return out


# ============================================================================
# 人工/AI 出补丁（兜底那一层）
# ============================================================================
#
# 设计立场：**最终 Excel 永远由程序落盘**，AI 不手改产出文件。
# AI 只在程序判不出来时（待复核）给出结论，写成一份 JSON 补丁，
# 程序读补丁后重新生成 Excel。这样：
#   - 有确定的接口，AI 不需要"自由发挥"（skill 设计心得的核心二）
#   - 补丁可复现、可审计、可版本管理
#   - 补丁只作用于**歧义/未配**的条目，不会覆盖程序的正常判定

PATCH_SCHEMA = {
    "file": "原序时账文件名（防呆，可选）",
    "rows": {  # 行号 -> {item, note}
        "<行号>": {"item": "<现金流量项目>", "note": "依据（可选）"},
    },
}


def make_review_payload(cf_by_row: Dict[int, dict],
                        row_info: Dict[int, dict],
                        counterparty: Optional[Dict[int, List[str]]] = None
                        ) -> List[dict]:
    """把「现金流待复核」整理成给 AI 的清单（直接进 CLI 出参，不落 Excel）。

    Args:
        cf_by_row:    {行号: 判定 dict}
        row_info:     {行号: {"凭证号", "摘要", "科目", "借方", "贷方"}}
        counterparty: {行号: [对方一级科目...]} —— 复核时最需要的上下文
    """
    counter = counterparty or {}
    out: List[dict] = []
    for row_no in sorted(cf_by_row):
        v = cf_by_row[row_no]
        if not v.get("needs_review"):
            continue
        info = row_info.get(row_no, {})
        out.append({
            "行号": row_no + 1,          # 给 AI 看、也用于补丁键的 1-based 行号
            "凭证号": info.get("凭证号", ""),
            "科目": info.get("科目", ""),        # 被审的这一行（货币资金科目）
            "对方科目": counter.get(row_no, []),  # 同一凭证里的其他一级科目
            "摘要": str(info.get("摘要", ""))[:200],
            "借方金额": info.get("借方", 0.0),
            "贷方金额": info.get("贷方", 0.0),
            "业务分类": v.get("bucket", ""),
            "现金流方向": v.get("direction", ""),
            "候选项目": v.get("candidates") or [],
            "原因": v.get("note", ""),
        })
    return out


def load_patch(path: str | Path) -> Dict[str, Any]:
    """读补丁文件并做格式校验。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"补丁文件不存在: {p}")
    doc = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict):
        raise ValueError("补丁文件顶层必须是 JSON 对象")
    rows = doc.get("rows")
    if rows is None:
        raise ValueError("补丁文件缺 \"rows\" 字段（形如 {\"rows\": {\"12\": {...}}}）")
    if not isinstance(rows, dict):
        raise ValueError("\"rows\" 必须是对象：行号 -> {\"item\": \"...\"}")
    return doc


def apply_patch(cf_by_row: Dict[int, dict], patch: Optional[dict],
                ) -> Tuple[Dict[int, dict], Dict[str, Any]]:
    """把补丁里的结论并进 cf_by_row，返回 (新的 cf_by_row, 应用统计)。

    只认"行号 → 现金流量项目"；项目名必须合法，方向必须与该项目一致，
    否则计入 rejected 而不是硬塞进去。
    """
    stat: Dict[str, Any] = {
        "provided": 0, "applied": 0, "rejected": [], "unknown_rows": [],
        "not_review": [],
    }
    if not patch:
        return cf_by_row, stat

    rows = patch.get("rows") or {}
    stat["provided"] = len(rows)
    cfg = load_cf_config()
    valid = set(cfg["items"])

    out = dict(cf_by_row)
    for key, spec in rows.items():
        try:
            row_no = int(key) - 1           # 补丁用 1-based，内部用 0-based
        except (TypeError, ValueError):
            stat["rejected"].append({"行号": key, "原因": "行号不是整数"})
            continue
        if isinstance(spec, str):
            spec = {"item": spec}
        if not isinstance(spec, dict):
            stat["rejected"].append({"行号": key, "原因": "值必须是对象或字符串"})
            continue

        item = str(spec.get("item", "")).strip()
        if item not in valid:
            stat["rejected"].append({"行号": key, "原因": f"不是合法的现金流量项目：{item}"})
            continue

        cur = out.get(row_no)
        if cur is None:
            stat["unknown_rows"].append(key)
            continue
        if not cur.get("needs_review"):
            # 不覆盖程序的正常判定——补丁只负责兜底
            stat["not_review"].append({"行号": key, "原判定": cur.get("cf_item", "")})
            continue

        direction = cur.get("direction", "")
        if direction in (CF_IN, CF_OUT) and item_direction(item) != direction:
            stat["rejected"].append({
                "行号": key,
                "原因": f"项目方向（{item_direction(item)}）与该笔现金流方向"
                        f"（{direction}）不符",
            })
            continue

        new = dict(cur)
        new.update({
            "cf_item": item,
            "source": SRC_PATCH,
            "needs_review": False,
            "note": str(spec.get("note", "") or "") or "由补丁补充",
        })
        out[row_no] = new
        stat["applied"] += 1

    return out, stat


# ============================================================================
# 自检
# ============================================================================

def validate() -> Dict[str, Any]:
    """校验映射表与模块口径是否自洽（status / 自检用）。"""
    cfg = load_cf_config()
    problems: List[str] = []

    for bkt, items in cfg["bucket_map"].items():
        for it in items:
            if it not in cfg["items"]:
                problems.append(f"业务桶「{bkt}」引用了未知项目「{it}」")
    for it, d in cfg["items"].items():
        if d not in (CF_IN, CF_OUT, "中性"):
            problems.append(f"项目「{it}」方向非法：{d}")

    non_cash = [b for b, v in cfg["bucket_map"].items() if not v]
    # 歧义桶 = 同一方向下候选 >1。这些桶要靠一级科目当裁判，
    # 本身也是个信号：桶没拆够细（该重训模型把桶拆开）。
    ambiguities: List[Dict[str, Any]] = []
    single_dir: List[str] = []
    for bkt, items in cfg["bucket_map"].items():
        if not items:
            continue
        for d, word in ((CF_IN, "收现"), (CF_OUT, "付现")):
            cands = filter_by_direction(items, d)
            if len(cands) > 1:
                ruled = [c for c in cands if c in cfg["ruling"]]
                ambiguities.append({
                    "bucket": bkt,
                    "direction": word,
                    "candidates": cands,
                    "ruled": ruled,
                    "unruled": [c for c in cands if c not in cfg["ruling"]],
                })
        ins = filter_by_direction(items, CF_IN)
        outs = filter_by_direction(items, CF_OUT)
        if not ins or not outs:
            single_dir.append(bkt)

    # 裁判表登记了但实际用不到的项目 = 冗余，提醒清理
    used = {c for a in ambiguities for c in a["candidates"]}
    idle_ruling = sorted(set(cfg["ruling"]) - used)
    unruled = sorted({c for a in ambiguities for c in a["unruled"]})

    return {
        "problem_count": len(problems),
        "problems": problems,
        "cf_item_count": len(cfg["items"]),
        "bucket_count": len(cfg["bucket_map"]),
        "non_cash_buckets": non_cash,
        "one_way_buckets": sorted(single_dir),
        "ambiguous_buckets": ambiguities,
        "ruling_items": sorted(cfg["ruling"]),
        "ruling_idle": idle_ruling,
        "ambiguous_without_ruling": unruled,
        "cf_dir": str(CF_DIR),
    }
