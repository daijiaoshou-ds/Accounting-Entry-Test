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
from typing import Tuple

_ASSET = Path(__file__).resolve().parent / "assets/subject_aliases.json"

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
