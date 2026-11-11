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

import os

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .nn.data import build_group_summary, extract_voucher_features
from .dedup import dedup_vouchers
from .subjects import scan as scan_subjects
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
    unknown: Dict[str, Any] = field(default_factory=dict)   # 未知科目扫描报告


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
    if not sum_col or sum_col not in df.columns:
        return set()
    markers = {"本年利润", "以前年度损益调整"}
    kws = ("期间损益", "本期损益", "期末损益", "本月损益", "结转损益",
           "损益结转", "结转期间", "结转本期", "结转本月", "结转期末", "期末结转")
    subs = df[s_col].astype(str).str.strip()
    tmp = pd.DataFrame({
        v_col: df[v_col].values,
        "_mark": subs.isin(markers).values,
        "_kw": df[sum_col].fillna("").astype(str)
                  .str.contains("|".join(kws), na=False).values,
        "_subj": subs.values,
    })
    grp = tmp.groupby(v_col)
    cond = grp["_mark"].any() & (grp["_kw"].any() | (grp["_subj"].nunique() >= 5))
    return {str(v) for v in cond[cond].index}


def _cash_internal_vouchers(df: pd.DataFrame, v_col: str, s_col: str) -> set:
    """资金内部往来：一级科目全部是货币资金（银行存款/现金互转）。"""
    cash = {"库存现金", "银行存款", "其他货币资金", "其它货币资金"}
    is_cash = df[s_col].astype(str).str.strip().isin(cash)
    tmp = pd.DataFrame({v_col: df[v_col].values, "_c": is_cash.values})
    cond = tmp.groupby(v_col)["_c"].all()
    return {str(v) for v in cond[cond].index}


def _fx_vouchers(df: pd.DataFrame, v_col: str, s_col: str, sum_col: str) -> set:
    """汇兑损益：财务费用是唯一损益类科目 + 摘要带汇兑词 + 无往来黑名单科目。"""
    other_pl = {"管理费用", "销售费用", "研发费用", "投资收益", "公允价值变动损益",
                "主营业务收入", "其他业务收入", "营业外收入", "主营业务成本",
                "其他业务成本", "其它业务成本", "税金及附加", "营业税金及附加",
                "所得税费用", "营业外支出", "资产减值损失", "信用减值损失",
                "以前年度损益调整"}
    blacklist = {"应付职工薪酬", "应交税费", "应付利息", "应付股利", "应付债券", "应付票据"}
    subs = df[s_col].astype(str).str.strip()
    frames = {
        v_col: df[v_col].values,
        "_fin": (subs == "财务费用").values,
        "_other": subs.isin(other_pl).values,
        "_black": subs.isin(blacklist).values,
    }
    if sum_col and sum_col in df.columns:
        frames["_kw"] = df[sum_col].fillna("").astype(str) \
            .str.contains("汇兑|结汇|收汇", na=False).values
    else:
        frames["_kw"] = False
    grp = pd.DataFrame(frames).groupby(v_col)
    cond = (grp["_fin"].any() & ~grp["_other"].any()
            & ~grp["_black"].any() & grp["_kw"].any())
    return {str(v) for v in cond[cond].index}


def _employee_loan_vouchers(df: pd.DataFrame, v_col: str, s_col: str,
                            sum_col: str, d_col: str) -> set:
    """员工借支：其他应收款在借方 + 摘要含借款/借支 + 不含 公司/单位/集团/往来。

    全向量化：原实现 groupby().agg(lambda) 每张凭证调一次 Python，15 万凭证要 8s。
    「拼接后 contains 关键词」等价于「任一行 contains」，所以用 groupby().any() 即可。
    """
    if not (sum_col and sum_col in df.columns and d_col and d_col in df.columns):
        return set()
    debit = pd.to_numeric(df[d_col], errors="coerce").fillna(0)
    hit = df[s_col].astype(str).str.strip().isin({"其他应收款", "其它应收款"}) & debit.gt(0)
    if not hit.any():
        return set()
    sub = df.loc[hit, [v_col, sum_col]]
    text = sub[sum_col].fillna("").astype(str)
    tmp = pd.DataFrame({
        v_col: sub[v_col].values,
        "_kw": text.str.contains("借款|借支|暂借|暂支", na=False).values,
        "_ex": text.str.contains("公司|单位|集团|往来", na=False).values,
    })
    grp = tmp.groupby(v_col)
    ok = grp["_kw"].any() & ~grp["_ex"].any()
    return {str(v) for v in ok[ok].index}


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
        export_training: Optional[bool] = None,
        progress: ProgressCb = None) -> CleanResult:
    """执行一次完整清洗。

    Args:
        df: 原始序时账
        mapping: {voucher_no, subject, subject_name, summary, debit, credit, ...}
        classifier: JournalClassifier（只用来拿已加载的模型实例，不再打分）
        export_training: 是否导出未审训练数据。None（默认）跟随环境变量
            `SUMMARY_CLEANER_EXPORT_TRAINING`（默认关闭）——普通用户只跑账不训练，
            这一步在 90 万行账上要 110s，是最大的单项开销；复核/训练场景显式开启
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
    # 未知科目：在归一化之后、推理之前就能判定（见 subjects.scan）。
    # 不降置信度、不退避——模型照样按摘要判，只是把"该维护别名表了"这件事报出来。
    unknown_report, unknown_by_vid = scan_subjects(
        df, mapping, subjects_map, summary_map)

    nn_probs: Dict[str, Dict[str, float]] = {}
    dedup_stats: Dict[str, Any] = {}
    model_calls = 0
    vid_to_rep_size: Dict[str, int] = {}
    # 代表置信度低于此值时，该组成员各自重判（0 = 关闭，现为默认）。
    # 84.7 万行 A/B：开启多 15% 调用（+6,047 次），只改 8 张桶、复核清单只少 4 张，
    # 且重判的 6,047 张里 91% 仍是"中"置信——大组内本就业务等价，拆开是白算。
    # 需要时显式设 SUMMARY_CLEANER_DEDUP_RECHECK=0.80 打开。详见技术报告 §4.6.2.2。
    dedup_recheck = float(os.getenv("SUMMARY_CLEANER_DEDUP_RECHECK", "0"))
    if todo:
        classifier._load_nn_if_available()
        if classifier._nn_available:
            # 业务等价的凭证只判一次：同科目组合内，摘要去掉数字后相同（或相似度
            # ≥0.95）的直接复用结论。真实账 11,456 凭证 → 约 5,200 次调用。
            reps, vid_to_rep, dedup_stats = dedup_vouchers(
                todo, subjects_map, summary_map)
            vid_to_rep_size = {vid: len(reps[idx]["vids"])
                               for vid, idx in vid_to_rep.items()}
            items = [{"summary": r["summary"], "subjects": r["subjects"]}
                     for r in reps]
            preds = classifier._nn.predict_batch_full(items)
            model_calls = len(items)

            # 第二轮：代表本身不确定的组，组内成员各自再判一次。
            # 理由：去重会把组代表的判定放大到整组，而代表不确定时（概率低）
            # 组内本来就可能分歧（实测"办公桌 43 张 vs 460 张"就是这类）。
            # 代价只落在少数组上——全账高置信 98.8%，所以额外的调用量很小。
            recheck_reps: List[int] = []
            if dedup_recheck > 0:
                for rep_idx, pred in enumerate(preds):
                    p = pred.get("probs") or {}
                    if len(reps[rep_idx]["vids"]) > 1 and p and max(p.values()) < dedup_recheck:
                        recheck_reps.append(rep_idx)
            if recheck_reps:
                extra_items = []
                extra_owner: List[int] = []
                for rep_idx in recheck_reps:
                    for vid in reps[rep_idx]["vids"]:
                        extra_items.append({"summary": summary_map.get(vid, ""),
                                            "subjects": subjects_map.get(vid) or []})
                        extra_owner.append(rep_idx)
                extra_preds = classifier._nn.predict_batch_full(extra_items)
                model_calls += len(extra_items)
                for owner, pred in zip(extra_owner, extra_preds):
                    probs = dict(pred.get("probs") or {})
                    if probs:
                        bucket_rules.merge_alias_probs(probs)
                        reps[owner].setdefault("override", []).append(probs)
                dedup_stats["recheck_groups"] = len(recheck_reps)
                dedup_stats["recheck_calls"] = len(extra_items)

            # 落结论：有 override 的组按成员自己的判定，其余整组抄代表
            for rep_idx, pred in enumerate(preds):
                probs = dict(pred.get("probs") or {})
                if not probs:
                    continue
                bucket_rules.merge_alias_probs(probs)
                overrides = reps[rep_idx].get("override") or []
                if overrides:
                    for vid, p in zip(reps[rep_idx]["vids"], overrides):
                        nn_probs[vid] = p
                else:
                    for vid in reps[rep_idx]["vids"]:
                        nn_probs[vid] = probs

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
            # >1 表示这张的结论来自组内代表（抄作业），张数即同组凭证数；
            # 值越大越值得抽查——同一组只要代表判错，全组跟着错。
            "组内张数": int(vid_to_rep_size.get(vid, 1)),
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
    if export_training is None:
        export_training = _export_training_enabled()
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
        "dedup": dedup_stats,
        "unknown_subjects": unknown_report.get("unknown") or {},
        "unknown_known_sets": unknown_report.get("known") or {},
        "model_only_subjects": unknown_report.get("model_only_subjects") or [],
        "engine": {
            "mode": "纯模型（无程序打分）",
            # model_calls = 真正送进模型的条数（去重后）；modeled_vouchers = 拿到
            # 模型结论的凭证数。两者之比就是去重收益。
            "model_calls": int(model_calls),
            "modeled_vouchers": int(len(nn_probs)),
            "hard_rule_vouchers": int(len(hard)),
            "unclassified": int(unclassified),
            "fallback_to_parent": int(bucket_rules.rules_summary().get("fallback", 0)),
            "model": _model_desc(classifier),
        },
        "embed_cache": (_cache_stats(classifier)),
        "nn_training_path": nn_path,
    }
    report(100, "完成")
    return CleanResult(df=out, detail=detail, vouchers=vouchers,
                       stats=stats, unknown=unknown_report)


# ============================================================================
# 辅助
# ============================================================================

def _json_dumps(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _cache_stats(classifier: Any) -> Dict[str, Any]:
    """摘要编码缓存的命中情况（未启用时返回空 dict）。"""
    cache = getattr(getattr(classifier, "_nn", None), "_cache", None)
    if cache is None:
        return {}
    try:
        return cache.summary_stats()
    except Exception:  # noqa: BLE001 - 统计失败不影响主流程
        return {}


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


def _export_training_enabled() -> bool:
    """训练数据导出开关。默认关闭：普通用户只跑账，训练/复核场景显式开启。"""
    return os.getenv("SUMMARY_CLEANER_EXPORT_TRAINING", "0").strip().lower() in ("1", "true", "yes", "on")


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
