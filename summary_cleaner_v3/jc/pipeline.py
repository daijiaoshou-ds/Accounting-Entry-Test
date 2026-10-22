# -*- coding: utf-8 -*-
"""Pipeline —— 3.0 的完整清洗流程编排。

    序时账 Excel
        │
        ├─ ① ETL        读文件、认列名、规范化、抽出模型输入（一凭证一条）
        ├─ ② 硬规则     两条机械规则预分配（资金内部往来 / 汇兑损益）
        ├─ ③ 模型打分   按输入折叠 → 批量推理 → top1 + 置信度
        ├─ ④ 写回       结论逐行写回原序时账
        ├─ ⑤ 现金流     业务桶 → 现金流量项目（纯程序，按货币资金借贷定方向）
        └─ ⑥ 导出       分类结果 / 现金流相关凭证 / 现金流量表 / 复核清单

程序不参与业务桶打分：18 个业务桶全部来自模型，中/低置信度交给 AI 复核。
现金流是清洗之后的第二步，只依赖业务桶，不依赖模型、也不依赖 AI。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from . import cashflow
from . import etl
from . import hard_rules
from . import review_views
from . import units as units_mod
from .config import (ALL_BUCKETS, BUCKET_SRC_FALLBACK, BUCKET_SRC_HARD,
                     BUCKET_SRC_MODEL, BUCKET_SRC_PATCH, COL_BUCKET_SRC,
                     CONF_HIGH, CONF_MID, FIELD_LABELS, HARD_RULE_BUCKETS,
                     MODEL_BUCKETS, REQUIRED_FIELDS)
from .model import JournalClassifier, pick_device


# ============================================================================
# ① 字段检查（不加载模型、不写任何数据）
# ============================================================================

def check_file(path: str | Path,
               override: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """读文件 + 自动认列名 + 校验必需字段。"""
    df = etl.read_journal(path)
    mapping = etl.detect_columns(df)
    mapping, bad = etl.apply_mapping_override(df, mapping, override)

    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    preview = {}
    for key, col in mapping.items():
        samples = [str(v) for v in df[col].dropna().astype(str).head(3)]
        preview[key] = {"列名": col, "样例": samples,
                        "字段": FIELD_LABELS.get(key, key)}
    return {
        "file": str(Path(path).resolve()),
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "mapping": mapping,
        "preview": preview,
        "missing_required": missing,
        "missing_required_cn": [FIELD_LABELS.get(m, m) for m in missing],
        "unknown_overrides": bad,
        "ok": not missing,
    }


# ============================================================================
# 三个层次，别混
# ============================================================================
#
#   ① 切割   序时账 → 按「科目组合 + 摘要」去重 → **单元**（U 个，U << M）
#   ② 打分   模型给每个单元一个桶（一次推理 = 一个单元）
#   ③ 复核   ★ AI 看的是**单元**，不是凭证：M 张凭证 AI 必然复核不过来，
#            U 个单元才可能过一遍
#   ④ 写回   单元 → 桶，铺到具体凭证（凭证号在这一步才出现）
#   ⑤ 现金流 程序拿具体凭证（货币资金行）→ 现金流量项目
#
# 为什么这个顺序不能动：
#   如果先写回凭证、算完现金流，再让 AI 去复核凭证，那 AI 的结论就**没有
#   进入程序状态的通路**——他只能改已导出的 Excel（违规，且现金流列不会重算），
#   或者报告"建议改判"然后交付一张错的表。实测样例 460 条现金流分录里
#   **431 条（93.7%）由「桶+方向」直接判定**，桶一错项目就跟着错，
#   而且候选唯一时程序看起来"很确定"，不会报警。
#
# 所以：**复核结论是写回的输入，不是事后修正。**

def load_unit_patch(path: str | Path) -> Dict[str, Any]:
    """读口径修正表并做格式校验。

    格式（键是**单元键**，所以能跨月复用）：
        {"units": {"其他应收款[借] + 银行存款[贷] :: 预付款张三退回": "单位往来"}}
    也接受 `{"vouchers": ...}` 之外的旧写法谈不上兼容——本文件是本项目内部的，
    格式统一成单元键。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"口径修正表不存在: {p}")
    doc = json.loads(p.read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict):
        raise ValueError("修正表顶层必须是 JSON 对象")
    units = doc.get("units")
    if units is None:
        raise ValueError(
            "修正表缺 \"units\" 字段——形如 "
            "{\"units\": {\"其他应收款[借] + 银行存款[贷] :: 预付款张三退回\": "
            "\"单位往来\"}}（键是单元键，可跨月复用）")
    if not isinstance(units, dict):
        raise ValueError("\"units\" 必须是对象：单元键 -> 业务桶")
    return doc


def _input_key(it: dict) -> tuple:
    """模型输入的折叠键 = 单元的身份。与 model.predict_folded 用的是同一个。

    键里的摘要用**同族代表**（`units.canonicalize` 挂上的 `_rep_summary`）：
    所以"贷款1000万"与"贷款2000万"折叠成同一个键，模型只判一次。
    """
    return etl.voucher_key(it.get("subjects") or [],
                           str(it.get("_rep_summary")
                               or it.get("summary") or ""))


def unit_key_of(it: dict) -> str:
    """单元的**身份键**（`科目组合 :: 归一化摘要`）—— 全流程只此一处定义。

    为什么必须唯一：`build_units` 用它当单元的身份，`--reuse-units` 也用它
    去命中上次的打分。两处各写一遍，迟早会漂（改了归一化规则只改一处），
    那时复用会"命中 0 个"却不报错 —— 最难查的那种 bug。
    """
    rep = str(it.get("_rep_summary") or it.get("summary") or "")
    norm = str(it.get("_rep_norm") or units_mod.normalize_summary(rep))
    return etl.unit_key_text(it.get("subjects") or [], norm)


def build_units(items: List[dict],
                probs_by_vid: Dict[str, Dict[str, float]],
                top3_by_vid: Dict[str, List],
                assign: Dict[str, str]) -> List[dict]:
    """把逐凭证的输入聚成**单元** —— 流程里的「切割」那一层。

    返回按**首次出现顺序**编号的单元列表（单元号 U001…）。编号只跟文件有关，
    所以同一份序时账在 `units` 和后续 `run` 里拿到的是同一个号。

    每个单元带：单元号 / 单元键 / 科目组合 / 摘要 / 模型桶 / 概率 / 次选 /
    来源 / 覆盖凭证数 / 覆盖凭证号 / 同族变体数 / 同族摘要。**不含金额**——
    单元的定义就是模型看到的那个输入（科目组合 + 代表摘要），金额不属于它的身份。

    **单元键用归一化后的摘要**（数字被忽略）：这样"贷款1000万"与"贷款2000万"
    是同一个键，口径修正表跨月复用才真的稳（金额、月份月月都在变）。
    """
    units: List[dict] = []
    index: Dict[tuple, int] = {}
    raws: List[set] = []          # 每个单元见过的**原样**摘要集合（同族变体数）

    for it in items:
        key = _input_key(it)
        vid = str(it["vid"])
        rep = str(it.get("_rep_summary") or it.get("summary") or "")
        i = index.get(key)
        if i is None:
            if vid in assign:
                bucket, prob, alts = assign[vid], None, []
                source = BUCKET_SRC_HARD
            else:
                top = top3_by_vid.get(vid) or []
                probs = probs_by_vid.get(vid) or {}
                if top:
                    bucket = str(top[0][0])
                    prob = float(top[0][1])
                    alts = [[str(b), round(float(v), 4)] for b, v in top[1:]]
                    source = BUCKET_SRC_MODEL
                elif probs:
                    b, p = max(probs.items(), key=lambda kv: kv[1])
                    bucket, prob, alts = str(b), float(p), []
                    source = BUCKET_SRC_MODEL
                else:
                    # 无摘要 / 模型返回空 → 兜底，等 AI 复核
                    bucket, prob, alts = "其他业务", None, []
                    source = BUCKET_SRC_FALLBACK
            index[key] = len(units)
            raws.append(set())
            units.append({
                "单元号": f"U{len(units) + 1:03d}",
                "单元键": unit_key_of(it),
                "单元键原样": etl.unit_key_text(it.get("subjects"), rep),
                "科目组合": list(it.get("subjects") or []),
                "摘要": rep,
                "模型桶": bucket,
                "概率": None if prob is None else round(prob, 4),
                "次选": alts,
                "来源": source,
                "覆盖凭证号": [],
            })
            i = index[key]
        units[i]["覆盖凭证号"].append(vid)
        raw = str(it.get("summary") or "")
        if raw:
            raws[i].add(raw)

    for u, raw_set in zip(units, raws):
        u["覆盖凭证数"] = len(u["覆盖凭证号"])
        u["同族变体数"] = len(raw_set)
        # 复核时看得见"这一族还包含什么"——只带几个样本，不带全（几千张看不过来）
        u["同族摘要"] = [r for r in sorted(raw_set) if r != u["摘要"]][
            :units_mod.VARIANT_SAMPLES]
    return units


def _norm_unit_key(ref: str) -> str:
    """把「科目组合 :: 摘要」里的摘要归一化，用于宽松匹配手写的单元键。"""
    sep = etl.UNIT_KEY_SEP
    if sep in ref:
        head, tail = ref.split(sep, 1)
        return head + sep + units_mod.normalize_summary(tail)
    return ref


def apply_unit_patch(units: List[dict], classifications: Dict[str, str],
                     sources: Dict[str, str], patch: Optional[dict]
                     ) -> Dict[str, Any]:
    """把 AI 的复核结论（单元 → 桶）落到凭证分类上。

    补丁的键可以写**单元键**，也可以写 `units` 出的**单元号**（U001）——
    后者只是省事的把手，留档与复用一律认单元键。

    单元键是**归一化**的（摘要里数字被忽略），所以：
      - 原样抄 `units` 给的键，一定匹配；
      - 手写的键（带金额/日期）也会先归一化再匹配——跨月复用就靠这个。

    这里不是"事后修正已完成的结果"，而是**写回的输入**：改完桶才写回凭证、
    才算现金流，所以交付物天然自洽（业务分类与现金流量项目永远对得上）。
    """
    stat: Dict[str, Any] = {"provided": 0, "applied": 0, "changed_vouchers": 0,
                            "rejected": [], "unknown_units": [], "applied_units": []}
    if not patch:
        return stat

    raw = patch.get("units") or {}
    stat["provided"] = len(raw)
    valid = set(ALL_BUCKETS)

    by_no = {u["单元号"]: u for u in units}
    by_key: Dict[str, dict] = {}
    for u in units:
        by_key[u["单元键"]] = u
        by_key.setdefault(u.get("单元键原样") or u["单元键"], u)

    def lookup(ref: str):
        return (by_no.get(ref) or by_key.get(ref)
                or by_key.get(_norm_unit_key(ref)))

    for key, spec in raw.items():
        ref = str(key).strip()
        if isinstance(spec, str):
            spec = {"bucket": spec}
        if not isinstance(spec, dict):
            stat["rejected"].append({"单元": ref, "原因": "值必须是对象或字符串"})
            continue
        bucket = str(spec.get("bucket", "")).strip()
        if bucket not in valid:
            stat["rejected"].append({
                "单元": ref,
                "原因": f"不是 20 个业务桶之一：{bucket or '(空)'}"})
            continue
        unit = lookup(ref)
        if unit is None:
            stat["unknown_units"].append(ref)
            continue

        for vid in unit["覆盖凭证号"]:
            classifications[vid] = bucket
            sources[vid] = BUCKET_SRC_PATCH
        stat["applied"] += 1
        stat["changed_vouchers"] += len(unit["覆盖凭证号"])
        stat["applied_units"].append({
            "单元号": unit["单元号"],
            "单元键": unit["单元键"],
            "模型桶": unit["模型桶"],
            "改成": bucket,
            "note": str(spec.get("note", "") or ""),
            "覆盖凭证数": len(unit["覆盖凭证号"]),
            "覆盖凭证号": unit["覆盖凭证号"],
        })

    return stat


# ============================================================================
# ①②③ 切割 + 硬规则 + 模型打分（run 与 units 共用）
# ============================================================================

def _classify(src: Path, override: Optional[Dict[str, str]],
              classifier: Optional[JournalClassifier], chunk_size: int,
              log, sim: float = units_mod.SIM_DEFAULT,
              reuse: Optional[dict] = None) -> Dict[str, Any]:
    """读出序时账、切成单元、给单元打分。**不写回、不算现金流、不导出。**

    这是流程的前半段，也是 `units` 命令的全部内容 —— 它的产出就是
    AI 的**复核面**（`units`）。

    `sim`：同科目组合内的摘要相似度阈值（0 = 关闭，1 = 只并归一化后完全相同的）。
    `reuse`：上一次 `units --out-json` 的出参。命中的单元直接用上次的桶与概率，
    **不再问模型** —— 真实流程是回环的（改判 → 重跑），不这么做每次迭代都要
    重算全量推理（真账实测白花 4.5 分钟）。
    """
    log("① 读取并规范化序时账...")
    raw_df = etl.read_journal(src)
    mapping = etl.detect_columns(raw_df)
    mapping, _bad = etl.apply_mapping_override(raw_df, mapping, override)
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        raise ValueError(
            "必需字段缺失：" + "、".join(FIELD_LABELS.get(m, m) for m in missing)
            + "。请先用 check 确认列名，或用 --set 指定映射")

    df = etl.add_row_index(raw_df)
    items, per_voucher = etl.extract_items(df, mapping)
    log(f"   {len(df)} 行 / {len(items)} 张凭证")

    # ── ② 硬规则（也是按单元判的：只看科目组合+摘要）──
    log("② 硬规则预分配（资金内部往来 / 汇兑损益）...")
    assign = hard_rules.preassign(items)
    hard_counts = Counter(assign.values())
    log(f"   命中 {len(assign)} 张 {dict(hard_counts) if assign else ''}")

    # ── ③ 模型打分（只对没被硬规则拿走的单元）──
    model_items = [it for it in items if it["vid"] not in assign]

    # ── ②b 相似度归族：同科目组合内，摘要"看着是同一件事"的并成一族 ──
    #     硬规则命中的单元单独成组（键里带硬规则桶），免得硬规则结论串到模型单元上。
    sim_stat = units_mod.canonicalize(
        items, threshold=sim,
        block_of=lambda it: (tuple(sorted(str(s) for s in (it.get("subjects") or []))),
                             assign.get(it["vid"], "")))
    if sim_stat["on"]:
        log(f"   摘要归族（同科目组合内，相似度≥{sim_stat['threshold']:.2f}）："
            f"原样 {sim_stat['variants_before']} 个 → {sim_stat['units_after']} 个"
            f"（{sim_stat['families']} 族，{sim_stat['merged_vouchers']} 张凭证抄了代表的作业，"
            f"{sim_stat['elapsed_s']}s）")
        for size, norm, raw in sim_stat["biggest"]:
            log(f"     {size:5d} 张  {norm}")

    probs_by_vid: Dict[str, Dict[str, float]] = {}
    top3_by_vid: Dict[str, List] = {}
    unique_inputs = len(model_items)

    # ── ③a 复用上次的打分（命中就跳过模型）──
    # 键是**单元键**（科目组合 :: 归一化摘要），所以跨月、跨文件也命中；
    # 阈值/os 版本不同只会降低命中率，不会给错答案（命中不了就重新推理）。
    reuse_stat: Dict[str, Any] = {"复用": "未启用", "命中": 0, "未命中": 0,
                                  "重新推理": 0}
    pending = model_items
    if reuse:
        # 复用清单有两种形状，都认：
        #   ① 默认视图的出参：units 是**数组**（每个单元一个对象，带「单元键」）
        #   ② --summary / --families / --lookup 的出参：`unit_labels` 是
        #      {单元键: 标签}（全量）—— 那两种视图刻意不带 units 数组，
        #      否则"一屏看完"就废了
        raw_units = reuse.get("units")
        if isinstance(raw_units, dict):
            cache = {str(k): v for k, v in raw_units.items()}
        elif raw_units:
            cache = {str(u.get("单元键")): u for u in raw_units
                     if isinstance(u, dict) and u.get("单元键")}
        else:
            labels = (reuse.get("unit_labels")
                      or reuse.get("reuse_labels")     # 旧版本出参的键名
                      or {})
            cache = {str(k): v for k, v in labels.items() if isinstance(v, dict)}
        cache_sim = (reuse.get("dedup") or {}).get("阈值")
        reuse_stat["复用"] = "启用"
        reuse_stat["清单单元数"] = len(cache)
        if cache_sim is not None and abs(float(cache_sim) - float(sim)) > 1e-9:
            reuse_stat["警告"] = (
                f"复用文件是按 --sim {cache_sim} 切的，这次是 {sim}："
                f"单元键的覆盖面会变（单元号一定对不上）。")
        pending = []
        hit_keys = set()
        hit_vouchers = 0
        for it in model_items:
            key = unit_key_of(it)
            hit = cache.get(key)
            if not isinstance(hit, dict) or not hit.get("模型桶"):
                pending.append(it)
                continue
            hit_vouchers += 1
            bucket = str(hit["模型桶"])
            if str(hit.get("来源") or "") == BUCKET_SRC_HARD:
                # 硬规则结论不经过模型：直接落到 assign，跟首次运行完全一致
                assign[it["vid"]] = bucket
                continue
            prob = hit.get("概率")
            alts = [[str(b), float(v)] for b, v in (hit.get("次选") or [])]
            top3_by_vid[it["vid"]] = (
                [[bucket, float(prob)]] if prob is not None else []) + alts
            hit_keys.add(key)
        reuse_stat["命中单元数"] = len(hit_keys)
        reuse_stat["命中凭证数"] = hit_vouchers
        reuse_stat["未命中凭证数"] = len(pending)
        reuse_stat["模型信息"] = dict(reuse.get("model") or {})
        log(f"③a 复用上次打分：命中 {len(hit_keys)} 个单元"
            f"（覆盖 {hit_vouchers} 张凭证）/ "
            f"未命中 {len(pending)} 张（未命中的才问模型）")
        if reuse_stat.get("警告"):
            log(f"   ⚠️ {reuse_stat['警告']}")

    if pending:
        if classifier is None:
            log("③ 加载模型...")
            classifier = JournalClassifier()
        log(f"③ 模型打分（{len(pending)} 张 → 按输入折叠）...")
        # 喂给模型的是**同族代表**的文本：变体的 summary 换成代表，
        # 这样模型只判一次，判的就是代表那句话（换的是副本，原件不动）。
        feed = []
        for it in pending:
            rep = str(it.get("_rep_summary") or it.get("summary") or "")
            if rep and rep != str(it.get("summary") or ""):
                cp = dict(it)
                cp["summary"] = rep
                cp.pop("_rep_summary", None)
                cp.pop("_rep_norm", None)
                feed.append(cp)
            else:
                feed.append(it)
        results, inferred, _keys = classifier.predict_folded(
            feed, chunk_size=chunk_size)
        for it, r in zip(pending, results):
            probs_by_vid[it["vid"]] = r["probs"]
            top3_by_vid[it["vid"]] = (classifier.top_k(r["probs"], 3)
                                      if r["probs"] else [])
        reuse_stat["重新推理"] = inferred
        # 复用命中的也算"一次模型调用"——只是发生在上一轮，指标口径保持一致
        unique_inputs = inferred + reuse_stat.get("命中单元数", 0)
        if inferred < len(pending):
            log(f"   输入折叠：{len(pending)} → {inferred} 次推理"
                f"（省 {1 - inferred / len(pending):.0%}）")
    elif reuse:
        unique_inputs = reuse_stat.get("命中单元数", 0)
        log("③ 模型打分：全部命中复用缓存，**没有加载模型、没有推理**")

    # ── 切割：这才是流程的产物，也是 AI 的复核面 ──
    units = build_units(items, probs_by_vid, top3_by_vid, assign)
    log(f"   切割成 {len(units)} 个单元（={unique_inputs} 次推理；"
        f"凭证 {len(items)} 张）")
    # ── 逐凭证的初始结论（写回时用；单元补丁会在它上面覆盖）──
    classifications: Dict[str, str] = {}
    confidences: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    for u in units:
        for vid in u["覆盖凭证号"]:
            classifications[vid] = u["模型桶"]
            sources[vid] = u["来源"]
            p = u["概率"]
            confidences[vid] = (
                "高" if u["来源"] == BUCKET_SRC_HARD
                else "低" if p is None
                else "高" if p >= CONF_HIGH
                else "中" if p >= CONF_MID else "低")

    return {
        "raw_df": raw_df, "df": df, "mapping": mapping,
        "items": items, "per_voucher": per_voucher,
        "assign": assign, "hard_counts": hard_counts,
        "probs_by_vid": probs_by_vid, "top3_by_vid": top3_by_vid,
        "units": units,
        "sim": sim_stat,
        "reuse": reuse_stat,
        "classifications": classifications,
        "confidences": confidences,
        "sources": sources,
        "classifier": classifier,
        "model_vouchers": len(model_items),
        "unique_inputs": unique_inputs,
    }


# ============================================================================
# units —— 只出复核面（切割 + 打分），不产出文件
# ============================================================================
#
# 三个视图（同一份打分结果，换一种"怎么看"）：units 清单 / summary 全局画像 /
# families 同族离群。**为什么必须有后两个**：真账实测 4931 个单元，而概率已经
# 饱和（93.7% 是 1.000，最终确认的错判里绝大多数也是 1.000）——按概率从头往下看
# 永远看不到它们。"看同族离群"才是真账上管用的办法，见 review_views.py。

def _lookup_payload(all_units: List[dict], src: Path, c: dict,
                    voucher: Optional[str] = None,
                    row_no: Optional[int] = None) -> Dict[str, Any]:
    """反查：给一个**凭证号**或**行号**，告诉你它属于哪个单元。

    为什么要有：复核时经常是"我看到 Excel 里某行判错了 / 现金流某条待复核"，
    想知道该改哪个单元。以前只剩两条路：自己复刻一遍「科目组合 + 摘要归一化
    + 相似度归族」（和程序算得不一样就会把改判落到别的单元上，而且是静默的），
    或者先落一份全量单元清单再按 `覆盖凭证号` 建反查表（真账上多花一次全量
    推理）。所以由程序自己回答。

    `row_no` 用 1-based 的「序时账表体第几行」——和 `cashflow_review.items`
    里的 `行号` 是同一个口径（Excel 工作表行号 = 它 + 1）。
    """
    row_hit = None
    if row_no is not None:
        df = c["df"]
        mask = df["_行号"] == (int(row_no) - 1)
        if not bool(mask.any()):
            return {"view": "lookup", "command": "units", "ok": False,
                    "query": {"行号": row_no},
                    "error": f"序时账表体没有第 {row_no} 行（共 {len(df)} 行；"
                             f"行号口径同 cashflow_review 的「行号」）"}
        r = df[mask].iloc[0]
        mapping = c["mapping"]
        voucher = str(r[mapping["voucher_no"]])
        sum_col = mapping.get("summary", "")
        row_hit = {
            "行号": int(row_no),
            "凭证号": voucher,
            "科目": str(r[mapping["subject"]] or ""),
            "摘要": ("" if not sum_col or sum_col not in df.columns
                     else str(r[sum_col] or "")),
            "借方金额": etl.to_number(r[mapping["debit"]]),
            "贷方金额": etl.to_number(r[mapping["credit"]]),
        }

    q = str(voucher or "").strip()
    hits = [u for u in all_units if q in {str(v) for v in u["覆盖凭证号"]}]
    out: Dict[str, Any] = {
        "view": "lookup",
        "command": "units",
        "ok": True,
        "file": str(src.resolve()),
        "query": row_hit or {"凭证号": q},
        "matched": len(hits),
        "units": [],
        "how_to_use": (
            "判错了就用这里的「单元号」交回程序（可重复，一次交完）："
            "`run \"序时账.xlsx\" --out \"输出\" --unit \"U137=借款筹资\"`。"
            "改单元会把它名下所有凭证一起改 —— 这是对的，模型分不清同单元的凭证。"),
    }
    for u in hits:
        item = dict(review_views.unit_brief(u))
        item["科目组合"] = list(u.get("科目组合") or [])
        item["同族摘要"] = list(u.get("同族摘要") or [])
        vids = list(u.get("覆盖凭证号") or [])
        item["覆盖凭证号_前20"] = vids[:20]
        if len(vids) > 20:
            item["覆盖凭证号_说明"] = f"共 {len(vids)} 张，这里只列前 20 张"
        out["units"].append(item)
    if not hits:
        out["error"] = (f"没有哪个单元的「覆盖凭证号」里含 {q!r}。"
                        f"核对凭证号是否抄错（要原样，包括「记-」这类字）")
    return out


def units_file(path: str | Path,
               override: Optional[Dict[str, str]] = None,
               classifier: Optional[JournalClassifier] = None,
               chunk_size: int = 64,
               limit: int = 0,
               offset: int = 0,
               bucket: Optional[str] = None,
               progress=None,
               sim: float = units_mod.SIM_DEFAULT,
               view: str = "units",
               min_family: int = 5,
               outlier_share: float = 0.25,
               reuse: Optional[dict] = None,
               lookup: Optional[str] = None,
               lookup_row: Optional[int] = None) -> Dict[str, Any]:
    """把序时账切成单元并打分，返回**复核面**（`view` 决定给哪一种看法）。

    刻意**不写回、不算现金流、不导出任何文件**：它是判断的输入，
    不是交付物。AI 看完这里的单元、把结论用 `run --unit` 交回来，
    程序才写回凭证并算现金流。

    `view`：units（默认，逐单元清单）/ summary（全局画像）/ families（同族离群）。
    `lookup` / `lookup_row`：反查（凭证号 / 行号 → 单元），优先于 `view`。
    配 `--reuse-units` 最省 —— 命中缓存时不加载模型、不推理，反查是一条秒回的命令。
    """
    def log(msg):
        if progress:
            progress(msg)

    src = Path(path)
    c = _classify(src, override, classifier, chunk_size, log, sim=sim,
                  reuse=reuse)
    all_units = c["units"]

    # ── 反查：凭证号 / 行号 → 单元（不改任何判定）──
    if lookup or lookup_row:
        out = _lookup_payload(all_units, src, c, voucher=lookup,
                              row_no=lookup_row)
        out["reuse"] = c["reuse"]
        out["model"] = (c["classifier"].meta if c["classifier"] is not None
                        else dict(c["reuse"].get("模型信息") or {}))
        return out

    # ── summary / families：只为"看得见"，不改任何判定 ──
    if view in ("summary", "families"):
        out = review_views.build(
            view, all_units, c["sim"], c["hard_counts"], len(c["items"]),
            int(len(c["df"])), min_family=min_family,
            outlier_share=outlier_share, limit=limit)
        if out is not None:
            out["command"] = "units"
            out["ok"] = True
            out["file"] = str(src.resolve())
            out["column_mapping"] = c["mapping"]
            out["sim"] = review_views.sim_payload(c["sim"])
            out["reuse"] = c["reuse"]
            # 设备/模型信息也要带上：复用这条路会跳过加载模型，
            # 到时只能从这里回填（否则 run 出参里 device 是 null）
            out["model"] = (c["classifier"].meta
                            if c["classifier"] is not None else {})
            # 带上**全部单元**的打分表（`unit_labels`）：这样"最便宜的那次 units"
            # 产出的文件就能直接给 run --reuse-units 用，也能把「凭证号 → 单元键」
            # 对回去（否则为了复用还得先跑一次默认视图 = 白省）。
            out["unit_labels"] = review_views.unit_labels(all_units)
            if view == "families":
                out["dedup"] = review_views.dedup_payload(c["sim"])
            return out

    units = all_units

    if bucket:
        units = [u for u in units if u["模型桶"] == bucket]

    # 概率升序 = 最可疑在前；硬规则单元没有概率（本来无歧义），排最后
    units = sorted(units, key=lambda u: (u["概率"] is None,
                                        u["概率"] if u["概率"] is not None else 0.0))
    total = len(units)
    off = max(0, int(offset or 0))
    lim = int(limit or 0)
    shown = units[off:] if lim <= 0 else units[off:off + lim]

    groups = review_views.by_combo(all_units)
    return {
        "command": "units",
        "view": "units",
        "ok": True,
        "file": str(src.resolve()),
        "rows": int(len(c["df"])),
        "vouchers": int(len(c["items"])),
        "unit_count": total,
        "offset": off,
        "shown": len(shown),
        "sorted_by": "模型概率升序（最可疑的在前）；硬规则单元无概率，排最后",
        "dedup": review_views.dedup_payload(c["sim"]),
        "sim": review_views.sim_payload(c["sim"]),
        "reuse": c["reuse"],
        "unique_inputs": c["unique_inputs"],
        "model_calls_saved": max(c["model_vouchers"] - c["unique_inputs"], 0),
        "hard_rule_hits": dict(c["hard_counts"]) if c["hard_counts"] else {},
        "confidence_dist": review_views.prob_buckets(all_units),
        "bucket_counts": dict(Counter(u["模型桶"] for u in all_units).most_common()),
        "family_count": len(groups),
        "model": (c["classifier"].meta if c["classifier"] is not None else {}),
        "column_mapping": c["mapping"],
        "how_to_use": (
            "这是**切割后的复核面**：序时账按「科目组合 + 摘要」去重、"
            "并在同一科目组合内把相似的摘要归成一族（只留代表），"
            "模型给每个单元一个桶。**你复核的就是这些单元，不是凭证**——"
            "凭证可能上万个；`dedup` 字段告诉你这次的切割成绩，"
            "`同族摘要` 告诉你这一族还包含哪些写法。"
            "核对「模型桶」；判错了用 `run --unit` 把结论交回程序（可重复，"
            "一次交完）：`run \"序时账.xlsx\" --out \"输出\" "
            "--unit \"U137=借款筹资\"`，也可以用单元键："
            "`--unit \"其他应收款[借] + 银行存款[贷] :: 预付款张三退回=单位往来\"`。"
            "程序收到后先改单元，再写回凭证、再算现金流——所以交付物天然自洽。"
            "**不要自己写 JSON/PS 改任何东西**，也不要手改 Excel。"
            "⚠️ 概率饱和：`confidence_dist` 里 `=1.0` 占绝大多数时，"
            "按概率从上往下读**找不到错** —— 请改用 `units --summary`（全局画像）"
            "和 `units --families`（同族离群），那才是真账上管用的入口。"),
        "units": shown,
    }


# ============================================================================
# ④⑤⑥ 完整清洗（复核结论作为输入）
# ============================================================================

def run_file(path: str | Path,
             out_dir: Optional[str | Path] = None,
             override: Optional[Dict[str, str]] = None,
             classifier: Optional[JournalClassifier] = None,
             chunk_size: int = 64,
             cf_patch: Optional[dict] = None,
             unit_patch: Optional[dict] = None,
             progress=None,
             sim: float = units_mod.SIM_DEFAULT,
             reuse_units: Optional[dict] = None,
             reuse_file: Optional[str] = None) -> Dict[str, Any]:
    """跑完一份序时账：单元 → (复核结论) → 写回凭证 → 编现金流 → 导出一个 Excel。

    Args:
        classifier: 复用已加载的模型实例（批量跑多份文件时传，省去重复加载）
        unit_patch: **复核结论**（AI 对单元的改判），形如
                    {"units": {"<单元号 或 单元键>": "业务桶"}}。
                    它是**写回的输入**，不是事后修正——所以现金流必然按
                    改好的桶算，交付物自洽。
        cf_patch:   现金流补丁（对现金流"待复核"条目的结论），
                    形如 {"rows": {"行号": {"item": "现金流量项目"}}}
        reuse_units: 上一次 `units --out-json` 的出参。命中的单元直接用上次的
                    桶与概率，**跳过模型推理**——真实流程是回环的（改判→重跑），
                    不这么做每次迭代都要重算全量推理。
        progress:   可选回调 progress(str)，用于向用户报进度
    """
    def log(msg):
        if progress:
            progress(msg)

    t_all = time.perf_counter()
    src = Path(path)
    out_dir = Path(out_dir) if out_dir else src.parent / "序时账清洗结果"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    c = _classify(src, override, classifier, chunk_size, log, sim=sim,
                  reuse=reuse_units)
    raw_df, df, mapping = c["raw_df"], c["df"], c["mapping"]
    items, per_voucher, units = c["items"], c["per_voucher"], c["units"]
    classifications, confidences, sources = (c["classifications"],
                                            c["confidences"], c["sources"])
    probs_by_vid, top3_by_vid = c["probs_by_vid"], c["top3_by_vid"]
    assign, hard_counts = c["assign"], c["hard_counts"]
    classifier, model_vouchers = c["classifier"], c["model_vouchers"]
    unique_inputs = c["unique_inputs"]

    # ── ③b 复核结论（单元 → 桶）—— 这是写回的**输入**，不是事后修正 ──
    unit_stat = apply_unit_patch(units, classifications, sources, unit_patch)
    if unit_stat["provided"]:
        log(f"③b 应用复核结论：提交 {unit_stat['provided']} 个单元 / "
            f"采纳 {unit_stat['applied']} 个 / "
            f"落到 {unit_stat['changed_vouchers']} 张凭证 / "
            f"驳回 {len(unit_stat['rejected'])} 个"
            + (f" / 单元没找到 {len(unit_stat['unknown_units'])} 个"
               if unit_stat["unknown_units"] else ""))
        for a in unit_stat["applied_units"]:
            log(f"   {a['单元号']} {a['模型桶']} → {a['改成']}"
                f"（覆盖 {a['覆盖凭证数']} 张凭证）")
        if unit_stat["applied"]:
            log("   写回与现金流都会用改后的桶（见 ⑤⑥）")

    # ── ③c 把生效的口径修正落盘 ──
    # 两个刻意的选择：
    #   ① 落盘时把**单元号换算成单元键**。单元号只在本次文件里有意义（U002 是
    #      这个文件里第 2 个单元），单元键（科目组合 :: 摘要）才能跨月复用——
    #      而"跨月复用"正是这张表存在的理由。
    #   ② 只记**采纳**的，不记被驳回/没找到的，免得错误条目永远留在表里。
    # 另外与已有文件**合并**：分两轮改判时不会把前一轮的结论冲掉。
    # 表是程序写的 —— AI 只交结论，不写文件。
    patch_files: Dict[str, str] = {}
    if unit_patch:
        p = out_dir / "口径修正.json"
        merged: Dict[str, str] = {}
        if p.exists():
            try:
                old = json.loads(p.read_text(encoding="utf-8-sig"))
                merged.update({str(k): str(v)
                               for k, v in (old.get("units") or {}).items()})
            except Exception:
                pass                       # 旧文件坏了就重写，不因此失败
        for a in unit_stat["applied_units"]:
            merged[a["单元键"]] = a["改成"]
        p.write_text(json.dumps({
            "_说明": ("口径修正表：键是**单元键**（科目组合 :: 摘要），可跨月复用——"
                      "下个月出现同样的单元会自动命中。由程序写出，人工也可维护。"),
            "units": merged,
            "_本次改判": unit_stat["applied_units"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        patch_files["口径修正"] = str(p)
    if cf_patch:
        p = out_dir / "现金流补丁.json"
        p.write_text(json.dumps(cf_patch, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        patch_files["现金流补丁"] = str(p)

    # ── ④ 现金流（业务桶 → 现金流量项目，纯程序）──
    log("④ 编制现金流量表...")
    money_by_vid, money_flags = etl.build_money_rows(df, mapping)
    cf_by_row: Dict[int, dict] = {}
    counterparts: Dict[int, List[str]] = {}
    counterpart_counts: Dict[int, Dict[str, int]] = {}
    for vid, rows in money_by_vid.items():
        bucket = classifications.get(vid, "")
        for row_no, verdict in cashflow.judge_voucher(rows, bucket):
            cf_by_row[row_no] = {
                "cf_item": verdict.cf_item,
                "direction": verdict.direction,
                "amount": verdict.amount,
                "source": verdict.source,
                "needs_review": verdict.needs_review,
                "no_cash": verdict.no_cash,
                "candidates": verdict.candidates,
                "bucket": verdict.bucket,
                "note": verdict.note,
            }
            counterparts[row_no] = list(verdict.counterparty)
            counterpart_counts[row_no] = dict(verdict.counterparty_counts)

    # 待复核清单（给 AI 兜底用，走 CLI 出参，不进给用户的 Excel）
    row_info = _row_info(df, mapping)
    # ★ 凭证号 → 单元：让每条待复核能直接对回 `--unit` 的把手。
    #   以前只给 行号/凭证号，执行者为了"这条该改哪个单元"得自己去 units
    #   清单里建反查表（真账上他为此多跑了一次全量 units）。
    #   注意用 `模型桶`（模型原始判定），不是 classifications——后者可能已被改判。
    unit_of = {vid: {"单元号": u["单元号"], "单元键": u["单元键"],
                     "模型桶": u["模型桶"]}
               for u in units for vid in u["覆盖凭证号"]}
    review_items = cashflow.make_review_payload(
        cf_by_row, row_info, counterparts, counterpart_counts, unit_of)
    log(f"   程序判定 {len(cf_by_row) - len(review_items)} 条 / "
        f"待复核 {len(review_items)} 条")

    # ── ④b 应用现金流补丁（人工/AI 对"待复核"的结论）──
    if cf_patch:
        cf_by_row, patch_stat = cashflow.apply_patch(cf_by_row, cf_patch)
        log(f"   应用补丁：提交 {patch_stat['provided']} 条 / "
            f"采纳 {patch_stat['applied']} 条 / "
            f"驳回 {len(patch_stat['rejected'])} 条")
    else:
        patch_stat = None
    review_items = cashflow.make_review_payload(
        cf_by_row, row_info, counterparts, counterpart_counts, unit_of)

    cf_stats = _cashflow_stats(money_by_vid, cf_by_row)
    cf_stats["patched_rows"] = (patch_stat or {}).get("applied", 0)
    cf_stats["patch_rejected"] = (patch_stat or {}).get("rejected", [])
    cf_stats["patch_unknown_rows"] = (patch_stat or {}).get("unknown_rows", [])
    cf_stats["patch_not_review"] = (patch_stat or {}).get("not_review", [])
    log(f"   现金流相关凭证 {cf_stats['money_vouchers']} 张 / "
        f"货币资金分录 {cf_stats['money_rows']} 条 / "
        f"仍需复核 {cf_stats['needs_review_rows']} 条")

    # ── ⑤ 写回 ──
    log("⑤ 写回序时账...")
    classified = etl.write_back(df, mapping, classifications, confidences,
                               sources)
    classified = etl.write_cashflow(classified, mapping, money_flags, cf_by_row)

    # ── ⑥ 导出（一个 Excel，多 sheet）──
    log("⑥ 导出结果文件...")
    exported = _export(classified, per_voucher, probs_by_vid, top3_by_vid,
                       classifications, confidences, hard_counts, mapping,
                       out_dir, stem, unique_inputs, model_vouchers,
                       money_by_vid, money_flags, cf_by_row, raw_df=raw_df)

    stats = _stats(classified, mapping, classifications, confidences,
                   hard_counts, per_voucher)
    stats["elapsed_seconds"] = round(time.perf_counter() - t_all, 1)
    stats["output_files"] = exported["files"]
    stats["sheet_rows"] = exported["sheet_rows"]
    stats["cashflow_statement"] = exported["cashflow_statement"]
    stats["cashflow_review"] = review_items
    stats["cashflow_review_by_unit"] = cashflow.summarize_review(review_items)
    stats["column_mapping"] = mapping
    stats["model"] = (classifier.meta if classifier is not None
                      # 全部命中复用缓存时压根没加载模型：设备信息从复用清单里取
                      else dict(c["reuse"].get("模型信息") or {}))
    stats["unique_inputs"] = unique_inputs
    stats["model_calls_saved"] = max(model_vouchers - unique_inputs, 0)
    stats["unit_count"] = len(units)
    stats["dedup"] = review_views.dedup_payload(c["sim"])
    stats["reuse"] = dict(c["reuse"])
    if reuse_file:
        stats["reuse"]["来源文件"] = str(reuse_file)
    stats["cashflow"] = cf_stats
    stats["unit_patch"] = unit_stat
    stats["patch_files"] = patch_files
    return stats


def _row_info(df: pd.DataFrame, mapping: Dict[str, str]) -> Dict[int, dict]:
    """{行号: {凭证号, 摘要, 科目, 借方, 贷方}} —— 给待复核清单补上下文。"""
    v_col = mapping["voucher_no"]
    s_col = mapping["subject"]
    d_col = mapping["debit"]
    c_col = mapping["credit"]
    sum_col = mapping.get("summary", "")
    has_sum = bool(sum_col) and sum_col in df.columns

    out: Dict[int, dict] = {}
    for (row_no, vid, subj, debit, credit, text) in zip(
            df["_行号"], df[v_col], df[s_col], df[d_col], df[c_col],
            df[sum_col] if has_sum else [""] * len(df)):
        out[int(row_no)] = {
            "凭证号": str(vid),
            "科目": "" if subj is None else str(subj).strip(),
            "摘要": "" if text is None else str(text),
            "借方": etl.to_number(debit),
            "贷方": etl.to_number(credit),
        }
    return out



# ============================================================================
# 现金流统计
# ============================================================================

def _cashflow_stats(money_by_vid: Dict[str, List], cf_by_row: Dict[int, dict]
                    ) -> Dict[str, Any]:
    """现金流结果概况：多少张相关凭证、多少条分录、多少条要复核。

    `rows_without_item`（项目为空的行）**不是一个告警**，它必然包含
    `needs_review_rows` 那几条（待复核本来就没有项目），再加"业务桶本身
    不产生现金流量"的行（资金内部往来 = 自家银行账户互转、折旧摊销）。
    所以这里把它拆开报，免得执行者每轮都去追这个数（旧文档写反了：
    说"期望 ≤ needs_review_rows"，实际它**恒 ≥** needs_review_rows）。
    """
    by_item: Dict[str, float] = {}
    by_section: Dict[str, float] = {}
    review_rows = 0
    empty_rows = 0
    wash_rows = 0
    empty_no_cash = 0        # 桶不产生现金流量（正常）
    empty_review = 0         # 待复核（已在 needs_review_rows 里）
    empty_unclassified = 0   # 未分类（异常）
    for row_no, v in cf_by_row.items():
        if v.get("no_cash"):
            wash_rows += 1
            continue
        item = v.get("cf_item") or ""
        if not item:
            empty_rows += 1
            if v.get("source") == cashflow.SRC_NONE:
                empty_unclassified += 1
            elif v.get("needs_review"):
                empty_review += 1
            else:
                empty_no_cash += 1
        else:
            by_item[item] = by_item.get(item, 0.0) + float(v.get("amount") or 0)
            sec = cashflow.section_of(item) or "其他"
            by_section[sec] = by_section.get(sec, 0.0) + float(v.get("amount") or 0)
        if v.get("needs_review"):
            review_rows += 1

    return {
        "money_vouchers": len(money_by_vid),
        "money_rows": len(cf_by_row),
        "wash_rows": wash_rows,
        "needs_review_rows": review_rows,
        # 项目为空的货币资金行 = 正常(桶不产生现金流) + 待复核 + 未分类
        "rows_without_item": empty_rows,
        "rows_without_item_detail": {
            "桶不产生现金流量(正常)": empty_no_cash,
            "待复核": empty_review,
            "未分类(异常)": empty_unclassified,
        },
        "rows_without_item_note": (
            "正常值 = 「桶不产生现金流量」那一项（资金内部往来=自家账户互转、"
            "折旧摊销）+ 「待复核」那一项（本来就没项目，等同 needs_review_rows）。"
            "**只要「未分类(异常)」是 0 就不用管**；它不是告警指标，不用每轮去追。"),
        "amount_by_item": {k: round(v, 2) for k, v in
                           sorted(by_item.items(), key=lambda kv: -abs(kv[1]))},
        "amount_by_section": {k: round(v, 2) for k, v in by_section.items()},
        "net_cash": round(sum(by_item.values()), 2),
    }


# ============================================================================
# 导出（一个 Excel，多 sheet）
# ============================================================================
#
# 设计立场（skill 设计心得 · 核心二）：
#   落盘是"确定的活"，由程序负责，AI 不参与、也不手改产出文件。
#   用户拿到的是**一份**交付物，不是一个结果一个 xlsx。
#
# 版面刻意简单，只呈现结果：
#   ① 原始序时账      —— 用户给什么就是什么，一个字不改
#   ② 业务分类结果    —— ① + 业务分类/置信度 + 现金流量项目
#   ③ 货币资金凭证    —— 与现金流有关的整张凭证 + 现金流量项目
#
# 没有"待复核"sheet：待复核条目走 CLI 出参（JSON）给 AI，
# 不塞进给用户的交付物里。AI 的结论通过补丁回到程序，再重出本文件。

ADDED_COLUMNS = ("业务分类", COL_BUCKET_SRC, "置信度", "现金流量项目",
                 "现金流方向", "现金流判定", "现金流复核",
                 "是否现金流相关凭证")


def _raw_view(classified: pd.DataFrame, mapping: Dict[str, str],
              rename_subject: bool = True) -> pd.DataFrame:
    """还原成"用户原始序时账"：只保留用户的列，顺序不变。

    rename_subject：是否把科目列**统一成程序口径「一级科目」**。
      - 第①张 sheet（原始序时账）必须 **False**：用户给什么就是什么，
        连列名都不改，否则"原始"两个字就名不副实。
      - 第②③张 sheet **True**：那两张是程序产出，把科目列统一成「一级科目」，
        这样不管用户原来叫「科目」还是「存货科目」，下游都有个稳定列名可找。

    注意：这里只改科目这一列的**名字**，值一个不动；其余列保留用户原列名。
    （早先这段注释把方向写反了，害得下游按「科目」找列、静默丢列——别改回去。）
    """
    cols = [c for c in classified.columns
            if not str(c).startswith("_") and c not in ADDED_COLUMNS]
    out = classified[cols].copy()
    if (rename_subject and mapping.get("subject")
            and mapping["subject"] in out.columns):
        out = out.rename(columns={mapping["subject"]: "一级科目"})
    return out


def _money_voucher_view(classified: pd.DataFrame, mapping: Dict[str, str],
                        money_flags: Dict[str, bool]) -> pd.DataFrame:
    """第③张表：含货币资金科目的**整张**凭证（不是只留货币资金行）。

    编现金流要看的正是货币资金的对方在干什么，所以整张凭证都留着；
    但只给货币资金那一行填「现金流量项目」——这是会计惯例。
    """
    v_col = mapping["voucher_no"]
    hit = classified[v_col].astype(str).map(money_flags).fillna(False)
    out = classified[hit].copy()
    out = out.drop(columns=[c for c in ("是否现金流相关凭证",) if c in out.columns])
    extra = {c: out[c].values for c in ("业务分类", COL_BUCKET_SRC,
                                        "现金流量项目", "现金流方向",
                                        "现金流判定", "现金流复核")
             if c in out.columns}
    return _raw_view(out, mapping).assign(**extra)


def _cashflow_statement(cf_by_row: Dict[int, dict]) -> pd.DataFrame:
    """现金流量表：按项目聚合，收付分列，末尾一行净增加额。"""
    rows = []
    for v in cf_by_row.values():
        item = v.get("cf_item") or ""
        if not item or v.get("no_cash"):
            continue
        amt = float(v.get("amount") or 0)
        rows.append({
            "现金流量项目": item,
            "活动类型": cashflow.section_of(item) or "其他",
            "_in": amt if amt > 0 else 0.0,
            "_out": -amt if amt < 0 else 0.0,
        })
    if not rows:
        return pd.DataFrame(columns=["活动类型", "现金流量项目", "收现",
                                     "付现", "笔数", "净额"])

    agg = (pd.DataFrame(rows)
           .groupby(["活动类型", "现金流量项目"], as_index=False)
           .agg(收现=("_in", "sum"), 付现=("_out", "sum"), 笔数=("_in", "size")))
    agg["净额"] = agg["收现"] - agg["付现"]

    # 按现金流量表标准顺序排（经营 / 投资 / 筹资 / 其他）
    sec_order = {s: i for i, s in enumerate(cashflow.load_cf_config()["sections"])}
    item_order = {n: i for i, n in enumerate(cashflow.cf_items())}
    agg["_s"] = agg["活动类型"].map(sec_order).fillna(9)
    agg["_i"] = agg["现金流量项目"].map(item_order).fillna(999)
    agg = agg.sort_values(["_s", "_i"]).drop(columns=["_s", "_i"])

    total = pd.DataFrame([{
        "活动类型": "合计",
        "现金流量项目": "现金及现金等价物净增加额",
        "收现": round(float(agg["收现"].sum()), 2),
        "付现": round(float(agg["付现"].sum()), 2),
        "笔数": int(agg["笔数"].sum()),
        "净额": round(float(agg["净额"].sum()), 2),
    }])
    return pd.concat([agg, total], ignore_index=True)


def _export(classified: pd.DataFrame, per_voucher: pd.DataFrame,
            probs_by_vid, top3_by_vid, classifications, confidences,
            hard_counts, mapping, out_dir: Path, stem: str,
            unique_inputs: int, model_items: int,
            money_by_vid: Optional[Dict[str, List]] = None,
            money_flags: Optional[Dict[str, bool]] = None,
            cf_by_row: Optional[Dict[int, dict]] = None,
            raw_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """写出**一个** Excel（多 sheet），返回 {files, sheet_rows}。"""
    cf_by_row = cf_by_row or {}
    money_flags = money_flags or {}
    base = raw_df if raw_df is not None else classified

    sheets: Dict[str, pd.DataFrame] = {}
    # ① 原始序时账：用户给什么就是什么，列名也不动
    sheets["原始序时账"] = _raw_view(base, mapping, rename_subject=False)
    # ② 业务分类结果 + ③ 货币资金凭证：程序产出，内部名还回用户口径
    #    注意：_raw_view 会把 ADDED_COLUMNS 全部剥掉（那是给①用的），
    #    所以这里必须**逐个**把新增列装回去——漏一个就会静默丢列。
    sheets["业务分类结果"] = _raw_view(classified, mapping).assign(
        业务分类=classified["业务分类"].values,
        分类判定=classified[COL_BUCKET_SRC].values,
        置信度=classified["置信度"].values,
        现金流量项目=classified["现金流量项目"].values,
        现金流方向=classified["现金流方向"].values,
        现金流判定=classified["现金流判定"].values,
        # 「现金流复核」以前只在 write_cashflow 里造出来，却**没被装回这张表**
        # （_raw_view 会把 ADDED_COLUMNS 全剥掉，这里只装回了 6 个，漏了它）。
        # 结果：交付物里看不出哪几行是程序判不下来的，只能靠 现金流判定==待复核 去猜。
        现金流复核=classified["现金流复核"].values,
    )
    if money_flags:
        mv = _money_voucher_view(classified, mapping, money_flags)
        if not mv.empty:
            sheets["货币资金凭证"] = mv

    path = out_dir / f"{stem}_结果.xlsx"
    etl.write_excel_sheets(sheets, path)

    return {
        "files": {"结果": str(path)},
        "sheet_rows": {k: int(len(v)) for k, v in sheets.items()},
        "cashflow_statement": _cashflow_statement(cf_by_row),
    }


def _hard_vids(hard_counts, classifications) -> set:
    return {v for v, b in classifications.items() if b in HARD_RULE_BUCKETS}


# ============================================================================
# 统计
# ============================================================================

def _stats(classified: pd.DataFrame, mapping: Dict[str, str],
           classifications: Dict[str, str], confidences: Dict[str, str],
           hard_counts: Counter, per_voucher: pd.DataFrame) -> Dict[str, Any]:
    v_col = mapping["voucher_no"]
    bucket_counts = dict(Counter(classifications.values()))
    conf_dist = dict(Counter(confidences.values()))

    amount_col = mapping.get("debit", "")
    amount_by_bucket = {}
    if amount_col and amount_col in classified.columns:
        nums = pd.to_numeric(
            classified[amount_col].astype(str).str.replace(",", "").str.strip(),
            errors="coerce").abs().fillna(0)
        tmp = classified.assign(_amt=nums)
        amount_by_bucket = {
            str(b): round(float(v), 2)
            for b, v in tmp.groupby("业务分类")["_amt"].sum().items()
        }

    n = len(classifications)
    unclassified = bucket_counts.get("未分类", 0)
    hard_total = sum(hard_counts.values()) if hard_counts else 0

    return {
        "total_vouchers": n,
        "total_rows": int(len(classified)),
        "classified": n - unclassified,
        "unclassified": unclassified,
        "coverage": round((n - unclassified) / n, 4) if n else 0.0,
        "bucket_counts": bucket_counts,
        "amount_by_bucket": amount_by_bucket,
        "confidence_dist": conf_dist,
        "hard_rule_hits": dict(hard_counts) if hard_counts else {},
        "hard_rule_total": hard_total,
        "model_vouchers": n - hard_total,
        "bucket_count": len(ALL_BUCKETS),
    }
