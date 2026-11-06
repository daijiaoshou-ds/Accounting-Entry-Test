"""科目归一化 —— 在拼「科目组合」之前把一级科目名统一成标准写法。

为什么要做
----------
模型只认识训练数据里出现过的一级科目。真实账里同一科目常写成：

    应交税金 / 应交增值税 / 应缴税费      ← 都该是 应交税费
    基本生产成本 / 辅助生产成本           ← 都该是 生产成本
    研发费用—折旧费 / 研发费用—差旅费     ← 都该是 研发费用
    销售费用-样品费（二级科目挂在后面）

不归一化的话，这些「带后缀 / 换了说法」的组合模型一个都没见过，
科目开关全亮 0，判断力直接掉下来——实测一本陌生账 214 种科目里
131 种是模型没见过的，57.7% 的凭证受影响。

归一化的收益是**不需要重训**：词表还是那 176 个，但能把别名折叠回去。
真遇到新科目（比如「万和」这种把客户名当一级科目用的），
往 assets/subject_aliases.json 里加一条就行，也不用重训。

规则顺序
--------
1. 剥方向（``[借]``/``[借方]`` 等）保留原样
2. 原名整体命中别名表 → 直接映射
3. 按分隔符切出主干：
   a. 主干命中别名表 → 映射
   b. 主干是已知标准一级科目 → 用主干（这就是「剥后缀」）
   c. 主干在 keep 名单里 → 不动
4. 都不命中 → 原样返回（真·新科目，交给别名表维护）
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Tuple

_ASSET = Path(__file__).resolve().parent / "assets/subject_aliases.json"
_VOCAB = Path(__file__).resolve().parent / "_storage/subject_to_index.json"

# 分隔符：中文破折号、各种减号、斜杠、间隔号、顿号、冒号、全半角括号
_SEP = re.compile(r"[—－–\-‐‑―/／·・、：:（(]")

# 兜底的标准一级科目（词表缺失时用；有词表时以词表主干为准并集）
_BUILTIN_FIRST_LEVEL = (
    "库存现金 银行存款 其他货币资金 交易性金融资产 应收票据 应收账款 "
    "预付账款 其他应收款 应收股利 应收利息 应收款项融资 坏账准备 "
    "材料采购 在途物资 原材料 材料成本差异 库存商品 发出商品 商品进销差价 "
    "委托加工物资 周转材料 存货跌价准备 合同资产 合同履约成本 持有待售资产 "
    "长期股权投资 长期应收款 固定资产 累计折旧 固定资产减值准备 在建工程 "
    "工程物资 固定资产清理 生产性生物资产 使用权资产 使用权资产累计折旧 "
    "无形资产 累计摊销 开发支出 商誉 长期待摊费用 递延所得税资产 待处理财产损溢 "
    "短期借款 交易性金融负债 应付票据 应付账款 预收账款 合同负债 应付职工薪酬 "
    "应交税费 应付利息 应付股利 其他应付款 应付利润 应付债券 租赁负债 "
    "长期应付款 预计负债 递延收益 递延所得税负债 股本 实收资本 其他权益工具 "
    "资本公积 库存股 其他综合收益 专项储备 盈余公积 本年利润 利润分配 "
    "生产成本 制造费用 劳务成本 研发支出 主营业务收入 其他业务收入 营业外收入 "
    "投资收益 公允价值变动损益 其他收益 资产处置损益 主营业务成本 其他业务成本 "
    "其他业务支出 税金及附加 营业税金及附加 销售费用 管理费用 研发费用 财务费用 "
    "资产减值损失 信用减值损失 营业外支出 所得税 所得税费用 以前年度损益调整 "
    "应付暂估款 其他应付款暂估 应付账款暂估 成品采购过渡 材料采购过渡"
).split()


def _load_asset() -> Dict:
    try:
        return json.loads(_ASSET.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 别名表缺失不能阻断分类
        return {}


@lru_cache(maxsize=1)
def alias_table() -> Dict[str, str]:
    """{账里的写法: 标准一级科目}。改完 JSON 调 reset_cache() 生效。"""
    aliases = _load_asset().get("aliases") or {}
    return {str(k).strip(): str(v).strip() for k, v in aliases.items()
            if isinstance(v, str) and str(v).strip()}


@lru_cache(maxsize=1)
def keep_names() -> FrozenSet[str]:
    return frozenset(str(x).strip() for x in (_load_asset().get("keep") or []))


@lru_cache(maxsize=1)
def first_level() -> FrozenSet[str]:
    """已知标准一级科目：内置表 ∪ 模型词表的主干。"""
    names = set(_BUILTIN_FIRST_LEVEL)
    try:
        vocab = json.loads(_VOCAB.read_text(encoding="utf-8"))
        for token in vocab:
            name = str(token).split("[")[0].strip()
            if name:
                names.add(name)
    except Exception:  # noqa: BLE001 - 词表还没生成时用内置表
        pass
    return frozenset(names)


def reset_cache() -> None:
    """别名表 / 词表改动后调用，强制重读。"""
    alias_table.cache_clear()
    keep_names.cache_clear()
    first_level.cache_clear()
    patterns.cache_clear()


@lru_cache(maxsize=1)
def patterns() -> Tuple[Tuple[str, str], ...]:
    """[(子串, 标准科目)]，按 JSON 里的顺序匹配。

    真实账常把银行账户名直接当一级科目：`中行2908`、`广发银行0178`、
    `招行10102`。这类不可能逐个枚举，用子串规则兜住。
    """
    out = []
    for rule in _load_asset().get("patterns") or []:
        to = str(rule.get("to") or "").strip()
        if not to:
            continue
        for needle in rule.get("match") or []:
            needle = str(needle).strip()
            if needle:
                out.append((needle, to))
    return tuple(out)


def _match_pattern(name: str) -> str:
    for needle, to in patterns():
        if needle in name:
            return to
    return ""


def split_token(token: str) -> Tuple[str, str]:
    """'科目[借]' → ('科目', '[借]')；无方向时后缀为空串。"""
    text = str(token).strip()
    text = text.replace("[借方]", "[借]").replace("[贷方]", "[贷]")
    idx = text.find("[")
    if idx < 0:
        return text, ""
    return text[:idx].strip(), text[idx:]


def normalize_name(name: str) -> str:
    """一级科目名归一（不含方向）。"""
    raw = str(name).strip()
    if not raw:
        return raw

    aliases = alias_table()
    if raw in aliases:
        return aliases[raw]
    if raw in keep_names():
        return raw
    if raw in first_level():
        return raw

    m = _SEP.search(raw)
    if m:
        stem = raw[:m.start()].strip()
        if stem:
            if stem in aliases:
                return aliases[stem]
            if stem in keep_names():
                return raw
            if stem in first_level():
                return stem
            hit = _match_pattern(stem)
            if hit:
                return hit

    hit = _match_pattern(raw)
    if hit:
        return hit
    return raw


def normalize_subject(token: str) -> str:
    """'应交税金[借]' → '应交税费[借]'；认不出来就原样返回。"""
    name, suffix = split_token(token)
    return f"{normalize_name(name)}{suffix}"
