"""推理后处理规则：桶别名重定向 + 低置信度父级回退。

只修正模型的**输出**，不改模型本身。规则来自
`assets/bucket_taxonomy_v10.json` 的两个顶层字段：

  bucket_aliases          旧桶名 → 新桶名（桶合并后的遗留标签）
  low_confidence_fallback 费用子类置信度不足时回退父级

设计约束
- 配置缺失 / 解析失败时全部返回原值，确保不影响现有 18 桶流程。
- 不抛异常：后处理失败不应阻断分类。
- 计数仅在进程内累计，供页面展示；不落盘。

用法::

    from .bucket_rules import postprocess, rules_summary
    bucket, prob, reason = postprocess("投资收益", 0.92, probs)
    # → ("投资", 0.92, "别名:投资收益→投资")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_ASSETS = Path(__file__).resolve().parent / "assets" / "bucket_taxonomy_v10.json"
_RULES: Optional[Dict[str, Any]] = None
_RULES_LOADED = False

# 进程内统计
_STATS = {"remapped": 0, "fallback": 0, "calibrated": 0, "total": 0}

# 别名表中不属于规则的元字段
_META_KEYS = {"_note", "_comment", "_disabled"}


def _load_rules() -> Dict[str, Any]:
    """读取并缓存规则；失败时返回空规则（全部 passthrough）。"""
    global _RULES, _RULES_LOADED
    if _RULES_LOADED:
        return _RULES or {}
    _RULES_LOADED = True
    try:
        with open(_ASSETS, encoding="utf-8") as f:
            cfg = json.load(f)
        aliases = {
            k: v for k, v in (cfg.get("bucket_aliases") or {}).items()
            if k not in _META_KEYS and isinstance(v, str)
        }
        fb = cfg.get("low_confidence_fallback") or {}
        _RULES = {
            "aliases": aliases,
            "fallback_threshold": float(fb.get("threshold") or 0.0),
            "fallback_applies": set(fb.get("applies_to") or []),
            "fallback_target": fb.get("fallback") or "",
            "temperature": float(fb.get("temperature") or 1.0),
        }
    except Exception:  # noqa: BLE001 - 规则缺失不应阻断分类
        _RULES = {
            "aliases": {},
            "fallback_threshold": 0.0,
            "fallback_applies": set(),
            "fallback_target": "",
            "temperature": 1.0,
        }
    return _RULES


def reset_cache() -> None:
    """配置改动后强制重读（UI / 测试用）。"""
    global _RULES, _RULES_LOADED
    _RULES = None
    _RULES_LOADED = False


def reset_stats() -> None:
    for k in _STATS:
        _STATS[k] = 0


def remap_bucket(
    bucket: str,
    prob: Optional[float] = None,
    probs: Optional[Dict[str, float]] = None,
) -> Tuple[str, Optional[float], str]:
    """按别名表重定向桶名；被合并桶的概率并入目标桶。

    Args:
        bucket: 模型/融合给出的桶名
        prob: 该桶的概率（可缺省）
        probs: 全桶概率表；给出时会就地合并概率

    Returns:
        (最终桶名, 最终概率, 说明)。无别名命中时原样返回，说明为空串。
    """
    rules = _load_rules()
    target = rules["aliases"].get(bucket)
    if not target:
        return bucket, prob, ""

    merged = None
    if probs is not None and target in probs:
        # 两桶已合并：概率相加，避免重定向后置信度凭空掉一半
        merged = float(probs.get(target, 0.0)) + float(probs.get(bucket, 0.0))
        probs[target] = merged
        probs.pop(bucket, None)
    elif prob is not None:
        merged = prob

    _STATS["remapped"] += 1
    return target, merged, f"别名:{bucket}→{target}"


def fallback_parent(
    bucket: str,
    prob: Optional[float],
) -> Tuple[str, str]:
    """费用子类置信度不足时回退父级「费用」。

    只对 `low_confidence_fallback.applies_to` 中的桶生效；
    概率为 None（程序判断）时不回退——程序判断本身是规则，不该被降级。
    """
    rules = _load_rules()
    threshold = rules["fallback_threshold"]
    if not threshold or prob is None:
        return bucket, ""
    if bucket not in rules["fallback_applies"]:
        return bucket, ""
    if float(prob) >= threshold:
        return bucket, ""
    _STATS["fallback"] += 1
    return rules["fallback_target"], f"低置信回退:{bucket}({prob:.2f})→{rules['fallback_target']}"


def _calibrate(probs: Dict[str, float], temperature: float) -> Dict[str, float]:
    """对模型 softmax 输出做温度缩放。

    数学上等价于对 logits 除以 T：``softmax(log(p)/T)`` 与
    ``softmax(z/T)`` 只差一个常数偏移，结果相同，因此无需模型暴露 logits。

    为什么需要：v10 模型 8 轮训练后过拟合，输出饱和——验证集 top1 均值
    0.992、>0.99 占 95.4%，任何"置信度阈值"规则都会失效。T=4 由验证集
    NLL 最小化标定（0.451→0.172），top1 准确率不变（94.89%）。

    Args:
        probs: 全桶概率（需覆盖全部类别，只用 top-k 会因归一化失真）
        temperature: >1 软化，<=1 原样返回

    Returns:
        校准后的概率表；输入为空或 T<=1 时返回副本
    """
    if not probs or temperature <= 1.0:
        return dict(probs or {})
    import math

    logs = {k: math.log(max(float(v), 1e-12)) / temperature for k, v in probs.items()}
    peak = max(logs.values())
    exps = {k: math.exp(v - peak) for k, v in logs.items()}
    total = sum(exps.values())
    if total <= 0:
        return dict(probs)
    return {k: v / total for k, v in exps.items()}


def postprocess(
    bucket: str,
    prob: Optional[float] = None,
    probs: Optional[Dict[str, float]] = None,
) -> Tuple[str, Optional[float], str]:
    """统一后处理：别名重定向 → 温度校准 → 低置信回退。

    顺序不能颠倒：
    - 别名合并必须在原始概率空间做（同类的概率要相加）
    - 校准必须在合并之后（否则合并进来的概率会被二次归一化稀释）
    - 回退判断用校准后的概率（原始概率饱和，阈值无效）

    Returns:
        (最终桶, 最终概率, 说明串；无调整时为空串)
    """
    _STATS["total"] += 1
    rules = _load_rules()

    # ① 别名重定向（就地合并 probs）
    b, p, why1 = remap_bucket(bucket, prob, probs)

    # ② 温度校准：只影响置信度，不影响融合选择（融合已在上游完成）
    if probs:
        calibrated = _calibrate(probs, rules["temperature"])
        p = calibrated.get(b, p)
        _STATS["calibrated"] += 1
    elif p is not None and rules["temperature"] > 1.0:
        # 只有单个概率、没有分布时无法准确校准，保持原值
        pass

    # ③ 低置信回退父级
    b2, why2 = fallback_parent(b, p)

    reasons = "；".join(x for x in (why1, why2) if x)
    return b2, p, reasons


def rules_summary() -> Dict[str, Any]:
    """规则与命中统计（页面展示用）。"""
    rules = _load_rules()
    return {
        "aliases": len(rules["aliases"]),
        "fallback_threshold": rules["fallback_threshold"],
        "fallback_applies": len(rules["fallback_applies"]),
        "temperature": rules["temperature"],
        "remapped": _STATS["remapped"],
        "fallback": _STATS["fallback"],
        "calibrated": _STATS["calibrated"],
        "total": _STATS["total"],
    }
