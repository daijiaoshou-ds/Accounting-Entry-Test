# -*- coding: utf-8 -*-
"""序时账清洗 —— 单一编排流程（4.0）。

    序时账
      │
      ├─ ① 读表 + 字段映射        程序（io_utils / calamine）
      ├─ ② 科目归一化 + 科目组合   程序（剥后缀 → 别名表映射 → 拼 [借]/[贷] 开关）
      ├─ ③ 模型识别               模型（bge-large 微调 + 分类头，一次批量前向）
      ├─ ④ 程序硬约束             程序（四类机械凭证直接定桶，不进模型）
      └─ ⑤ 写回结果               程序（业务分类/置信度/概率/Top3 + 训练数据导出）

**这里没有"程序打分"，也没有融合。** 业务桶只由模型给，程序的全部职责是
"把输入准备好、把机械凭证摘出去、把结论写回去"。原因（实测）：

- 程序打分（结构分/关键词/金额/自动词/纠错/制单人）全开与全关，最终桶差异为 0；
  与"纯模型"对比，11,456 张凭证只差 3 张（0.03%，都在「费用」父级边界）。
- 因此纠错回路也没有意义：它学的是程序权重，而程序权重不参与决策。

唯一的程序判断是四类机械凭证（期末结转、资金内部往来、汇兑损益、员工借支）：
它们是可枚举的形态，模型学不会也不需要学。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .nn.data import build_group_summary, extract_voucher_features
from .v2 import bucket_rules
from .v2.config import T3_SUBJECTS

# 硬约束桶：由程序直接判定，不送模型
HARD_RULE_BUCKETS = ("期末结转", "资金内部往来", "汇兑损益", "员工借支")

# 置信度档位（按校准后概率）
CONF_HIGH = 0.80
CONF_MID = 0.50

ProgressCb = Optional[Callable[[int, str], None]]


@dataclass
class CleanResult:
    """一次清洗的全部产出。"""
    df: pd.DataFrame                     # 行级结果（原表 + 结论列）
    detail: pd.DataFrame                 # 凭证级明细
    vouchers: pd.DataFrame               # 复核页用的凭证汇总（按校准概率升序）
    stats: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# ② 科目归一化 + 特征
# ============================================================================

def _first_summary(df: pd.DataFrame, v_col: str, sum_col: str) -> Dict[str, str]:
    """每张凭证首行摘要（展示用）。"""
    if not sum_col or sum_col not in df.columns:
        return {}
    head = df[[v_col, sum_col]].drop_duplicates(subset=[v_col], keep="first")
    return {str(v): str(s) for v, s in zip(head[v_col], head[sum_col])}


def _subjects_text(df: pd.DataFrame, v_col: str, s_col: str) -> Dict[str, str]:
    """每张凭证的一级科目（去重、顿号连接，展示用；保留原始写法不做归一）。"""
    if not s_col or s_col not in df.columns:
        return {}
    sub = df[[v_col, s_col]].dropna(subset=[s_col]).astype({s_col: str})
    joined = sub.groupby(v_col)[s_col].agg(lambda x: "、".join(pd.unique(x)))
    return {str(k): v for k, v in joined.items()}


def _amount_by_voucher(df: pd.DataFrame, v_col: str, d_col: str) -> Dict[str, float]:
    """每张凭证的合计金额（借方绝对值之和）。"""
    if not d_col or d_col not in df.columns:
        return {}
    amt = pd.to_numeric(
        df[d_col].astype(str).str.replace(",", "").str.strip(),
        errors="coerce").abs().fillna(0.0)
    return {str(k): float(v) for k, v in amt.groupby(df[v_col]).sum().items()}


# ============================================================================
# ④ 程序硬约束：四类机械凭证
# ============================================================================

def _closing_vouchers(df: pd.DataFrame, v_col: str, s_col: str,
                      sum_col: str) -> set:
    """期末结转：含本年利润/以前年度损益调整，且（摘要含结转损益词 或 科目数≥5）。"""
    out = set()
    if not sum_col or sum_col not in df.columns:
        return out
    markers = {"本年利润", "以前年度损益调整"}
    kws = ("期间损益", "本期损益", "期末损益", "本月损益", "结转损益",
           "损益结转", "结转期间", "结转本期", "结转本月", "结转期末", "期末结转")
    sub = df[[v_col, s_col, sum_col]].copy()
    sub[s_col] = sub[s_col].astype(str).str.strip()
    by_v = sub.groupby(v_col)
    subjects = by_v[s_col].agg(lambda s: set(s.dropna()))
    texts = by_v[sum_col].agg(lambda s: " ".join(s.dropna().astype(str)))
    for vid, subs in subjects.items():
        if not (subs & markers):
            continue
        text = texts.get(vid, "")
        if any(k in text for k in kws) or len(subs) >= 5:
            out.add(str(vid))
    return out


def _cash_internal_vouchers(df: pd.DataFrame, v_col: str, s_col: str) -> set:
    """资金内部往来：一级科目全部是货币资金（银行存款/现金互转）。"""
    cash = {"库存现金", "银行存款", "其他货币资金", "其它货币资金"}
    subs = (df.assign(_s=df[s_col].astype(str).str.strip())
              .groupby(v_col)["_s"].agg(lambda s: set(s.dropna())))
    return {str(v) for v, s in subs.items() if s and s.issubset(cash)}


def _fx_vouchers(df: pd.DataFrame, v_col: str, s_col: str, sum_col: str) -> set:
    """汇兑损益：财务费用是唯一损益类科目 + 摘要带汇兑词 + 无往来黑名单科目。"""
    fx_kw = ("汇兑", "结汇", "收汇")
    other_pl = {"管理费用", "销售费用", "研发费用", "投资收益", "公允价值变动损益",
                "主营业务收入", "其他业务收入", "营业外收入", "主营业务成本",
                "其他业务成本", "其它业务成本", "税金及附加", "营业税金及附加",
                "所得税费用", "营业外支出", "资产减值损失", "信用减值损失",
                "以前年度损益调整"}
    blacklist = {"应付职工薪酬", "应交税费", "应付利息", "应付股利", "应付债券", "应付票据"}
    out = set()
    texts: Dict[str, str] = {}
    if sum_col and sum_col in df.columns:
        texts = (df.groupby(v_col)[sum_col]
                   .agg(lambda s: " ".join(s.dropna().astype(str))).to_dict())
    subs = (df.assign(_s=df[s_col].astype(str).str.strip())
              .groupby(v_col)["_s"].agg(lambda s: set(s.dropna())))
    for vid, s in subs.items():
        if "财务费用" not in s or (s & other_pl) or (s & blacklist):
            continue
        if any(k in texts.get(vid, "") for k in fx_kw):
            out.add(str(vid))
    return out


def _employee_loan_vouchers(df: pd.DataFrame, v_col: str, s_col: str,
                            sum_col: str, d_col: str) -> set:
    """员工借支：其他应收款在借方 + 摘要含借款/借支 + 不含 公司/单位/集团/往来。"""
    out = set()
    if not (sum_col and sum_col in df.columns and d_col and d_col in df.columns):
        return out
    debit = pd.to_numeric(df[d_col], errors="coerce").fillna(0)
    hit = df[s_col].astype(str).str.strip().isin({"其他应收款", "其它应收款"}) & debit.gt(0)
    if not hit.any():
        return out
    texts = (df[hit].groupby(v_col)[sum_col]
               .agg(lambda s: " ".join(s.dropna().astype(str))).to_dict())
    kws = ("借款", "借支", "暂借", "暂支")
    exclude = ("公司", "单位", "集团", "往来")
    for vid, text in texts.items():
        if any(k in text for k in kws) and not any(w in text for w in exclude):
            out.add(str(vid))
    return out


def hard_rule_vouchers(df: pd.DataFrame, mapping: Dict[str, str]) -> Dict[str, str]:
    """四类机械凭证 → {凭证号(str): 桶}。优先级：结转 > 资金内部 > 汇兑 > 借支。"""
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    sum_col = mapping.get("summary", "")
    d_col = mapping.get("debit", "")

    out: Dict[str, str] = {}
    for vid in _employee_loan_vouchers(df, v_col, s_col, sum_col, d_col):
        out[vid] = "员工借支"
    for vid in _fx_vouchers(df, v_col, s_col, sum_col):
        out[vid] = "汇兑损益"
    for vid in _cash_internal_vouchers(df, v_col, s_col):
        out[vid] = "资金内部往来"
    for vid in _closing_vouchers(df, v_col, s_col, sum_col):
        out[vid] = "期末结转"
    return out


# ============================================================================
# ③ 模型识别
# ============================================================================

def _confidence(prob: Optional[float]) -> str:
    if prob is None:
        return "低"
    if prob >= CONF_HIGH:
        return "高"
    if prob >= CONF_MID:
        return "中"
    return "低"


def _top3(probs: Dict[str, float]) -> List[List[Any]]:
    return [[b, round(float(p), 4)]
            for b, p in sorted(probs.items(), key=lambda x: -x[1])[:3]]


def decide(probs: Dict[str, float]) -> Tuple[str, Optional[float], Optional[float],
                                             List[List[Any]], str]:
    """模型概率 → (最终桶, 原始概率, 校准概率, Top3, 后处理说明)。"""
    if not probs:
        return "未分类", None, None, [], ""
    bucket = max(probs, key=probs.get)
    raw = float(probs[bucket])
    top3 = _top3(probs)
    final, cal, note = bucket_rules.postprocess(bucket, raw, probs)
    return final, raw, cal, top3, note


# ============================================================================
# 主流程
# ============================================================================

def run(df: pd.DataFrame,
        mapping: Dict[str, str],
        classifier: Any,
        *,
        export_training: bool = True,
        progress: ProgressCb = None) -> CleanResult:
    """执行一次完整清洗。

    Args:
        df: 原始序时账
        mapping: {voucher_no, subject, subject_name, summary, debit, credit, ...}
        classifier: JournalClassifier（只用来拿已加载的模型实例，不再打分）
        export_training: 是否导出未审训练数据（测试/回放时可关）
        progress: 进度回调 (pct, message)
    """
    def report(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    sum_col = mapping.get("summary", "")
    d_col = mapping.get("debit", "")

    # ① 读表已完成，这里只校验
    if v_col not in df.columns or s_col not in df.columns:
        raise ValueError("缺少凭证号或一级科目列")

    # ④ 先算硬约束（便宜且优先级最高），命中的凭证不必送模型
    report(10, "识别机械凭证（期末结转/资金往来/汇兑/借支）")
    hard = hard_rule_vouchers(df, mapping)

    # ② 科目归一化 + 科目组合（与训练口径同一份代码）
    report(25, "科目归一化与组合提取")
    subjects_map, summary_map = extract_voucher_features(
        df, v_col, s_col, d_col, mapping.get("credit", ""), sum_col)

    # ③ 模型识别：只送硬约束之外的凭证，一次批量前向
    report(40, "模型识别")
    todo = [v for v in summary_map if v not in hard
            and subjects_map.get(v) and summary_map.get(v)]
    nn_probs: Dict[str, Dict[str, float]] = {}
    # 未知科目：归一化之后仍不在模型开关表里的科目。
    # 不降置信度、不退避——模型照样按摘要判，只是把"该维护别名表了"这件事报出来。
    unknown_by_vid: Dict[str, List[str]] = {}
    unknown_counter: Dict[str, int] = {}
    if todo:
        classifier._load_nn_if_available()
        if classifier._nn_available:
            items = [{"summary": summary_map[v], "subjects": subjects_map[v]} for v in todo]
            preds = classifier._nn.predict_batch_full(items)
            for vid, pred in zip(todo, preds):
                probs = dict(pred.get("probs") or {})
                if probs:
                    bucket_rules.merge_alias_probs(probs)
                    nn_probs[vid] = probs
                # 去掉 [借]/[贷] 后缀：维护别名表时只需要科目名，方向是噪音
                unk = sorted({_strip_dir(str(x))
                              for x in (pred.get("unknown_subjects") or []) if x})
                if unk:
                    unknown_by_vid[vid] = unk
                    for name in unk:
                        unknown_counter[name] = unknown_counter.get(name, 0) + 1

    report(75, "汇总判定")
    # ⑤ 逐凭证落结论
    first_summary = _first_summary(df, v_col, sum_col)
    subjects_text = _subjects_text(df, v_col, s_col)
    amounts = _amount_by_voucher(df, v_col, d_col)

    classification: Dict[str, str] = {}
    confidence: Dict[str, str] = {}
    detail_rows: List[Dict[str, Any]] = []
    unclassified = 0

    for vid_raw, group in df.groupby(v_col, sort=False):
        vid = str(vid_raw)
        if vid in hard:
            bucket, raw, cal, top3, note = hard[vid], None, None, [], "硬约束"
            source = "程序硬约束"
        elif vid in nn_probs:
            bucket, raw, cal, top3, note = decide(nn_probs[vid])
            source = "模型"
            unk = unknown_by_vid.get(vid) or []
            if unk:
                note = f"{note}；" if note else ""
                note += f"含未知科目 {len(unk)} 个（建议补别名表）" 
        else:
            # 既没命中硬约束、模型也没给出结果（无摘要/无科目/模型不可用）
            bucket, raw, cal, top3, note = "未分类", None, None, [], "模型无输出"
            source = "未分类"
            unclassified += 1

        classification[vid] = bucket
        confidence[vid] = "高" if vid in hard else _confidence(cal if cal is not None else raw)
        detail_rows.append({
            "凭证号": vid,
            "业务分类": bucket,
            "置信度": confidence[vid],
            "校准概率": None if cal is None else round(float(cal), 4),
            "模型概率": None if raw is None else round(float(raw), 4),
            "模型Top3": _json_dumps(top3),
            "判定来源": source,
            "后处理": note,
            "摘要": (first_summary.get(vid, "") or "")[:100],
            "科目": subjects_text.get(vid, ""),
            "金额": round(amounts.get(vid, 0.0), 2),
            "科目组合": "|".join(subjects_map.get(vid, [])),
            "未知科目": "、".join(unknown_by_vid.get(vid, [])),
        })

    detail = pd.DataFrame(detail_rows)
    vouchers = _vouchers_view(detail)

    # 写回原表（保留行序与原始列）
    out = df.copy()
    keys = out[v_col].astype(str)
    out["业务分类"] = keys.map(classification).fillna("未分类")
    out["置信度"] = keys.map(confidence).fillna("低")
    out["判定来源"] = keys.map({r["凭证号"]: r["判定来源"] for r in detail_rows}).fillna("未分类")
    out["模型概率"] = keys.map(
        {r["凭证号"]: r["模型概率"] for r in detail_rows}).astype("object")
    out["校准概率"] = keys.map(
        {r["凭证号"]: r["校准概率"] for r in detail_rows}).astype("object")
    out["模型Top3"] = keys.map(
        {r["凭证号"]: r["模型Top3"] for r in detail_rows}).fillna("[]")
    out["后处理"] = keys.map(
        {r["凭证号"]: r["后处理"] for r in detail_rows}).fillna("")

    # 训练数据导出（模型持续学习的唯一来源）
    nn_path = None
    if export_training:
        report(90, "导出未审训练数据")
        # 只跳过「期末结转」：它是机械结账，标签会落到"其他业务"污染语义。
        # 员工借支/资金往来/汇兑由桶级规则（DEFAULT_SKIP_BUCKETS）处理，
        # 其中「员工借支」是可训练桶，样本必须留在训练数据里（与 3.x 一致）。
        nn_path = _export_training(
            df=out, mapping=mapping,
            features=(subjects_map, summary_map),
            skip_voucher_ids={v for v, b in hard.items() if b == "期末结转"},
        )

    total = int(len(classification))
    classified = total - unclassified
    stats = {
        "total_vouchers": total,
        "classified_count": classified,
        "coverage": (classified / total) if total else 0.0,
        "bucket_counts": (vouchers["业务分类"].value_counts().to_dict()
                          if len(vouchers) else {}),
        "confidence_dist": _conf_dist(confidence),
        "hard_rule_counts": _count_values(hard.values()),
        "unknown_subjects": {
            "vouchers": len(unknown_by_vid),
            "subjects": [
                {"name": k, "vouchers": v}
                for k, v in sorted(unknown_counter.items(), key=lambda x: -x[1])[:20]
            ],
        },
        "engine": {
            "mode": "纯模型（无程序打分）",
            "model_calls": int(len(nn_probs)),
            "hard_rule_vouchers": int(len(hard)),
            "unclassified": int(unclassified),
            "fallback_to_parent": int(bucket_rules.rules_summary().get("fallback", 0)),
            "model": _model_desc(classifier),
        },
        "nn_training_path": nn_path,
    }
    report(100, "完成")
    return CleanResult(df=out, detail=detail, vouchers=vouchers, stats=stats)


# ============================================================================
# 辅助
# ============================================================================

def _strip_dir(switch: str) -> str:
    """'专项应付款[借]' → '专项应付款'（未知科目汇总时去掉方向）。"""
    if switch.endswith("借]") or switch.endswith("贷]"):
        return switch.rsplit("[", 1)[0]
    return switch


def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _count_values(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _conf_dist(confidence: Dict[str, str]) -> Dict[str, int]:
    out = {"高": 0, "中": 0, "低": 0}
    for level in confidence.values():
        out[level] = out.get(level, 0) + 1
    return out


def _model_desc(classifier: Any) -> Dict[str, Any]:
    nn = getattr(classifier, "_nn", None)
    return {
        "available": bool(getattr(classifier, "_nn_available", False)),
        "loaded": bool(getattr(nn, "is_loaded", False)),
        "device": getattr(nn, "device", None),
        "temperature": bucket_rules.rules_summary().get("temperature"),
    }


def _vouchers_view(detail: pd.DataFrame, columns: Optional[List[str]] = None) -> pd.DataFrame:
    """凭证级汇总：按校准概率升序（最不确定的排最前）。"""
    if detail.empty:
        return detail
    cols = columns or ["凭证号", "摘要", "科目", "金额", "业务分类", "置信度",
                       "校准概率", "模型Top3", "判定来源", "后处理", "未知科目"]
    view = detail[[c for c in cols if c in detail.columns]].copy()
    view["_p"] = pd.to_numeric(view.get("校准概率"), errors="coerce").fillna(-1.0)
    view = view.sort_values("_p").drop(columns="_p").reset_index(drop=True)
    return view


def _export_training(df: pd.DataFrame, mapping: Dict[str, str],
                     features: Tuple[Dict[str, List[str]], Dict[str, str]],
                     skip_voucher_ids: set) -> Optional[str]:
    """把本次结果导出成未审训练数据（给人/AI 复核，复核后并进金标准）。"""
    try:
        from .nn.training_data import build_hash_training_data
        from .v2.config import get_nn_storage_dir
        from .v2.fingerprint import compute_fingerprint

        training_dir = str(Path(get_nn_storage_dir()) / "training" / "unreviewed")
        fingerprint = compute_fingerprint(df, mapping["voucher_no"])
        build_hash_training_data(
            df, mapping, fingerprint=fingerprint, output_dir=training_dir,
            skip_voucher_ids=skip_voucher_ids, features=features,
        )
        return str(Path(training_dir) / f"{fingerprint}.json")
    except Exception as exc:  # noqa: BLE001 - 导出失败不该打断主流程
        print(f"[NN] 训练数据导出失败: {exc}")
        return None
