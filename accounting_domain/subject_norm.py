"""一级科目归一 —— 剥后缀 + 别名映射，两步各管各的。

流程（与传入数据的顺序一致）
----------------------------
    原始一级科目  →  ① split_suffix 剥后缀  →  ② alias_of 查映射表  →  标准一级科目
                                                        ↓
                                                   拼科目组合 → 送模型

为什么必须做
------------
模型只认识训练数据里出现过的一级科目。真实账里同一科目常写成：

    应交税金 / 应交增值税 / 应缴税费        ← 都该是 应交税费
    基本生产成本 / 辅助生产成本             ← 都该是 生产成本
    研发费用—折旧费 / 销售费用-样品费       ← 剥掉二级后缀
    中行2908（一般户） / 广发银行0178       ← 银行账户名当一级科目用

不归一化的话这些组合模型一个都没见过，科目开关全零，判断力直接掉。
实测一本陌生账：214 种科目令牌里 131 种模型没见过，57.7% 的凭证受影响；
归一后只剩 19 种、17.7%（再排掉「万和」这种把客户名当一级科目的，剩 2.0%）。

收益**不需要重训**：词表还是原来那些，只是把写法折叠回去。

这是**全局口径**，不是模型内部的事
----------------------------------
读表阶段就归一一次（`normalize_journal`），之后四个功能拿到的都是标准名：
四类硬约束（货币资金 / 其他应收款）、现金流找货币资金、对方科目的「模式」、
分录测试的科目对，全都按同一个名字算。否则同一本账会出现"清洗按 应交税费 判、
现金流按 应交税金 找货币资金"这类对不上的事。
名字被改写过的行，原值留在「原科目」列（只在确有改写时才加这一列）。

职责边界
--------
- 本模块只管「一级科目名」。方向（[借]/[贷]）由调用方处理。
- 不做科目组合、不做桶判定、不碰摘要。
- 映射表在 assets/subject_aliases.json，改完重启进程生效（有缓存）。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Tuple

import pandas as pd

_ASSET = Path(__file__).resolve().parent / "assets/subject_aliases.json"

# 归一化改写过名字时，原值留在这一列（只在确有改写时才加，干净账不多一列）
RAW_COLUMN = "原科目"

# 分隔符：中文破折号、各种减号、斜杠、间隔号、顿号、冒号、全半角括号
_SEP = re.compile(r"[—－–\-‐‑―/／·・、：:（(]")


@lru_cache(maxsize=1)
def rules() -> Tuple[Tuple[re.Pattern, str], ...]:
    """[(编译好的正则, 目标科目)]，顺序即优先级（命中第一条即返回）。

    表里每个标准一级科目都有一条记录，``match`` 是它的各种写法。
    ``*`` 是通配，其余字符按字面匹配——真实账常把银行账户名当一级科目
    （中行2908、广发银行0178），不可能逐个枚举，所以支持 ``*银行*``。
    """
    compiled = []
    for rule in _load().get("accounts") or []:
        to = str(rule.get("to") or "").strip()
        if not to:
            continue
        for pat in rule.get("match") or []:
            pat = str(pat).strip()
            if not pat:
                continue
            regex = "^" + re.escape(pat).replace(r"\*", ".*") + "$"
            compiled.append((re.compile(regex), to))
    return tuple(compiled)


def _load() -> dict:
    try:
        return json.loads(_ASSET.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 映射表缺失不能阻断分类
        return {}


def table_path() -> Path:
    """别名表在哪（页面/CLI 要告诉用户改的是哪个文件）。"""
    return _ASSET


def alias_targets() -> "set[str]":
    """别名表里所有 `to` —— 归一能落到的那些标准名。

    这张表是随账维护的，所以它比静态的会计科目表新：2017 年后才有的
    「租赁负债」「使用权资产」这类，科目表里没有，这里早就有了。
    """
    return {str(rule.get("to") or "").strip()
            for rule in (_load().get("accounts") or [])
            if str(rule.get("to") or "").strip()}


def canonical_name(name: "Any") -> str:
    """「其它」统一成「其他」——同一科目的异体写法，不折就永远对不上。"""
    return str(name if name is not None else "").strip().replace("其它", "其他")


def add_aliases(pairs: "dict[str, str]", path: "Any" = None) -> "dict[str, list[str]]":
    """把「账上写法 → 标准一级科目」写进别名表，返回 {目标: [新加的写法]}。

    新条目插到**最前面**：用户明确点了"这个写法归到哪个科目"，就该盖过
    表里已有的通配（否则「中行2908」会被 ``*中行*`` 先吃掉，改了等于没改）。

    写盘走"先写临时文件再 rename"：进程被杀在中间也不会留下半个 JSON——
    这份表是全局的，坏了所有账都归一不了。

    Args:
        pairs: {账上写法: 标准一级科目}
        path: 只给测试用（传临时副本，别改真表）；默认改 assets 里那张。
    """
    target = Path(path) if path else _ASSET
    try:
        table = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        table = {}
    accounts = table.get("accounts")
    if not isinstance(accounts, list):
        accounts = table["accounts"] = []

    added: "dict[str, list[str]]" = {}
    fresh: list = []
    for raw, want in (pairs or {}).items():
        name = str(raw or "").strip()
        to = str(want or "").strip()
        if not name or not to:
            continue
        # 已经有一条"to == 目标"的记录就把这条**整条挪到最前面**：用户点名了
        # 这个写法，就该盖过表里更靠前的通配（否则「中行2908」被 *_中行* 先吃掉，
        # 改了等于没改）。没有就新建一条放最前面。
        same = next((r for r in accounts if str(r.get("to") or "").strip() == to), None)
        if same is None:
            same = {"to": to, "match": []}
        else:
            accounts.remove(same)
        accounts.insert(0, same)
        matches = same.setdefault("match", [])
        if name not in matches:
            matches.insert(0, name)
            added.setdefault(to, []).append(name)
        fresh.append(name)
    if not fresh:
        return added

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(target)
    if path is None:          # 改的是真表才需要清缓存（测试给的是临时副本）
        reset_cache()
    return added


def reset_cache() -> None:
    """映射表改动后调用，强制重读。"""
    rules.cache_clear()


def split_token(token: str) -> Tuple[str, str]:
    """'科目[借]' → ('科目', '[借]')；无方向时后缀为空串。"""
    text = str(token).strip()
    text = text.replace("[借方]", "[借]").replace("[贷方]", "[贷]")
    idx = text.find("[")
    if idx < 0:
        return text, ""
    return text[:idx].strip(), text[idx:]


def split_suffix(name: str) -> str:
    """剥二级后缀：'研发费用—折旧费' → '研发费用'；无分隔符原样返回。

    只在**第一个**分隔符处切——真实账的二级明细常有多层
    （'应交税费—应交增值税—进项税额'），一级科目永远是第一段。
    """
    raw = str(name).strip()
    if not raw:
        return raw
    m = _SEP.search(raw)
    if not m:
        return raw
    stem = raw[:m.start()].strip()
    return stem or raw


def alias_of(name: str) -> str:
    """查映射表：'应交税金' → '应交税费'；查不到原样返回。"""
    raw = str(name).strip()
    if not raw:
        return raw
    for regex, to in rules():
        if regex.match(raw):
            return to
    return raw


def normalize_name(name: str) -> str:
    """剥后缀 + 查映射，两步组合。"""
    return alias_of(split_suffix(name))


def normalize_subject(token: str) -> str:
    """带方向的整令牌归一：'应交税金[借]' → '应交税费[借]'。"""
    name, suffix = split_token(token)
    return f"{normalize_name(name)}{suffix}"


# ============================================================================
# 整列 / 整本账
# ============================================================================

def normalize_series(series: "pd.Series") -> "pd.Series":
    """整列归一：按**唯一写法**查表，11,604 行只跑几十次正则（实测 41ms）。"""
    text = series.where(series.notna(), "").astype(str).str.strip()
    table = {value: normalize_name(value) for value in pd.unique(text)}
    return text.map(table)


def normalize_journal(journal: Any) -> Any:
    """把账里的一级科目换成标准名；确实改写过才追加「原科目」列。

    只在一级科目上做：二级（明细）是"钱对给谁"的细节，归一化会把
    「招商银行深圳龙岗支行…」这类信息抹掉，不能动。

    干净账（标准写法）走这条路径什么都不变——不加列、不改值，下游无感。
    """
    col = journal.schema.get("subject_l1")
    if not col or col not in journal.df.columns:
        return journal
    if RAW_COLUMN in journal.df.columns:      # 已经归一过的账（上游搬过来的）
        return journal

    raw = journal.df[col]
    before = raw.where(raw.notna(), "").astype(str).str.strip()
    after = normalize_series(raw)
    if bool((after == before).all()):
        return journal

    out = journal.df.copy()
    out.insert(list(out.columns).index(col) + 1, RAW_COLUMN, out[col])
    out[col] = after
    journal.df = out
    journal._raw = None                     # 字符串视图的缓存作废

    cols = list(journal.schema.columns)
    if RAW_COLUMN not in cols:
        cols.insert(cols.index(col) + 1 if col in cols else len(cols), RAW_COLUMN)
        journal.schema.columns = tuple(cols)
    return journal
