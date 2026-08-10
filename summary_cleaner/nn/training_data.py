# -*- coding: utf-8 -*-
"""
训练数据文件管理 — V3.0（BGE 微调）专用 buckets_v2 格式

设计原则:
  - V2.1 classify() 每次跑完自动生成 training/{hash}.json（buckets_v2 格式）
  - 按桶聚合: 桶 → 科目组合 → 摘要列表，方便 AI 按桶审核（只删不改）
  - 人工审核后（reviewed: true）→ 合并为 training_data.json → 训练
  - 训练时展开为扁平 records

文件体系:
  nn/_storage/
  ├── training/                  ← 按哈希分离的"生"训练数据（需人工审核）
  │   ├── {hash1}.json           ← buckets_v2 格式
  │   └── {hash2}.json
  ├── training_data.json          ← 合并后的训练数据（仅从已审核的哈希文件合并）
  ├── fine_tuned/                 ← [训练产物] 微调后 BGE 模型
  ├── finance_classifier.pt       ← [训练产物] 分类头
  ├── subject_to_index.json       ← [训练产物] 科目开关索引
  ├── index_to_bucket.json        ← [训练产物] 桶索引
  └── training_log.json           ← [训练产物] 训练日志

单哈希文件格式 (buckets_v2):
  {
    "fingerprint": "abc123",
    "created_at": "2025-07-25",
    "format": "buckets_v2",
    "reviewed": false,
    "stats": {"total_records": 10, "buckets": 3, "dedup_merged": 2, "conflicts": 0},
    "buckets": {
      "存货采购": [
        {
          "subjects": ["应付账款[借]", "银行存款[贷]"],
          "records": [
            {"summary": "付杭州分公司货款", "count": 3},
            {"summary": "付北京分公司货款", "count": 5}
          ]
        }
      ],
      "费用报销": [ ... ]
    }
  }

审核方式（AI/人工）: 一个桶一个桶看。桶下每个「科目组合」节点及其摘要列表
若不属于该桶 → 直接删除该节点（或删除其中部分摘要）。不修改桶名、不新增记录。
删除后剩下的记录天然属于该桶，训练时展开即可。

旧格式（records_v1，扁平记录）文件会被 merge 自动跳过。
本模块顶层禁止 import torch/transformers —— V2.1 的 classify() 直接引用本模块。
"""

import difflib
import json
import random
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

# 当前训练数据格式版本（buckets_v2: 按桶聚合，AI 按桶审核）
RECORDS_FORMAT = "buckets_v2"

# count 展开上限（防单条重复样本膨胀失控）
MAX_COUNT_EXPAND = 20


# ============================================================================
# 构建单哈希训练数据（records_v1）
# ============================================================================

def _dedup_key(summary: str, subjects: List[str]) -> Tuple[str, str]:
    """去重键: (摘要, 排序后的科目组合)。"""
    return summary, "|".join(sorted(subjects))


_DIGITS_RE = re.compile(r"\d+")


def _normalize_summary(text: str) -> str:
    """摘要归一化: 数字 → '#' 占位符。

    目的: 相似度计算时忽略数字（日期/凭证号/金额对分类无意义）。
    '暂估计提2024年12月份厂房电费' 与 '暂估计提2025年1月份厂房电费'
    归一化后完全相同 → 相似度 100% → 正确合并（同一业务）。
    实测: 原始相似度 35% → 归一化后 95%+。
    """
    return _DIGITS_RE.sub("#", text)


def _dedupe_similar_summaries(
    buckets_data: Dict[str, List[Dict]],
    threshold: float = 0.75,
) -> Dict[str, List[Dict]]:
    """同桶同科目组合内，摘要文本相似度 ≥ threshold 的只保留 1 条（count 累加）。

    背景: 序时账里"计提...25年1月 / 25年2月 / 25年3月..."这类只差期间的
    摘要语义完全相同，AI 审核时逐个看浪费上下文且稀释注意力。
    相似摘要 BGE 编码后向量几乎相同，去重对训练效果影响极小，count 累加
    保留训练权重。

    相似度: 数字归一化（_normalize_summary）后 difflib 字符级 ratio——
    日期/凭证号/金额等数字对分类无意义，忽略后才能反映真实业务相似度。
    保留策略: 保留 count 最大的，其余 count 累加进它（保持总权重不变）。

    Args:
        buckets_data: buckets_v2 聚合格式 {桶: [{subjects, records}]}
        threshold: 相似度阈值（默认 0.75 = 75%）

    Returns:
        去重后的 buckets_data（就地修改）
    """
    for groups in buckets_data.values():
        for group in groups:
            recs = group.get("records", [])
            if len(recs) <= 1:
                continue
            # count 降序，先处理高频（高频胜出）
            recs = sorted(recs, key=lambda r: -r.get("count", 1))
            kept: List[Dict] = []
            kept_norms: List[str] = []
            for rec in recs:
                norm_rec = _normalize_summary(rec["summary"])
                merged = False
                for k, norm_kept in zip(kept, kept_norms):
                    sim = difflib.SequenceMatcher(None, norm_rec, norm_kept).ratio()
                    if sim >= threshold:
                        k["count"] = k.get("count", 1) + rec.get("count", 1)
                        merged = True
                        break
                if not merged:
                    kept.append(dict(rec))
                    kept_norms.append(norm_rec)
            group["records"] = sorted(kept, key=lambda r: -r.get("count", 1))
    return buckets_data


def _aggregate_by_bucket(records: List[Dict]) -> Dict[str, List[Dict]]:
    """扁平 records → 按桶聚合: {桶: [{subjects, records: [{summary, count}]}]}。

    桶下按科目组合分组，同组合的摘要合并为列表（AI 按桶审核友好）。
    """
    buckets: Dict[str, Dict[str, Dict]] = {}
    for rec in records:
        subj_key = "|".join(sorted(rec["subjects"]))
        bucket_groups = buckets.setdefault(rec["bucket"], {})
        group = bucket_groups.setdefault(subj_key, {
            "subjects": rec["subjects"],
            "records": [],
        })
        group["records"].append({
            "summary": rec["summary"],
            "count": rec.get("count", 1),
        })

    # 桶内按科目组合排序；组合内按 count 降序（高频优先）
    result: Dict[str, List[Dict]] = {}
    for bucket, groups in buckets.items():
        result[bucket] = sorted(
            groups.values(),
            key=lambda g: -sum(r["count"] for r in g["records"]),
        )
    return result


def build_hash_training_data(
    df: pd.DataFrame,
    column_mapping: Dict[str, str],
    fingerprint: str = "",
    output_dir: str = None,
    skip_buckets: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """从 V2.1 分类结果提取训练记录 → 去重 → 按桶聚合 → 写入未审目录。

    返回数据 dict（同时写盘 training/unreviewed/{fingerprint}.json）。
    """
    from .data import DEFAULT_SKIP_BUCKETS, extract_training_records

    if output_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_dir = str(Path(NN_STORAGE_DIR) / "training" / "unreviewed")

    # 提取 [摘要, 科目组合, 桶] 训练记录
    raw_records = extract_training_records(
        df, column_mapping, skip_buckets=skip_buckets or DEFAULT_SKIP_BUCKETS,
    )

    # ── 文件内去重: 同键同桶 count 累加；同键不同桶 → count 多的胜出 ──
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    dedup_merged = 0
    conflicts = 0

    for rec in raw_records:
        key = _dedup_key(rec["summary"], rec["subjects"])
        if key in merged:
            dedup_merged += 1
            existing = merged[key]
            if existing["bucket"] != rec["bucket"]:
                conflicts += 1
                # 冲突: 谁 count 大谁留下（先到先得为 1）
                if rec.get("count", 1) > existing.get("count", 1):
                    existing["bucket"] = rec["bucket"]
            existing["count"] = existing.get("count", 1) + 1
        else:
            merged[key] = {
                "summary": rec["summary"],
                "subjects": rec["subjects"],
                "bucket": rec["bucket"],
                "count": 1,
            }

    records = sorted(
        merged.values(),
        key=lambda r: (r["bucket"], r["summary"]),
    )

    # 按桶聚合 + 相似摘要去重（同桶同组合 >75% 只留 1，count 累加）
    buckets_data = _aggregate_by_bucket(records)
    _dedupe_similar_summaries(buckets_data)

    # 去重后的实际摘要数（stats 用）
    deduped_records = _flatten_buckets(buckets_data)
    data = {
        "fingerprint": fingerprint or "unknown",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "format": RECORDS_FORMAT,
        "reviewed": False,
        "stats": {
            "total_records": len(deduped_records),
            "raw_records": len(records),
            "buckets": len(buckets_data),
            "dedup_merged": dedup_merged,
            "sim_deduped": len(records) - len(deduped_records),
            "conflicts": conflicts,
        },
        "buckets": buckets_data,
    }

    # 写入文件
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{fingerprint or 'unknown'}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 训练数据已保存: {path}")
    print(f"   {len(deduped_records)} 条记录（相似去重 {len(records) - len(deduped_records)} 条）, "
          f"{len(buckets_data)} 桶, 精确去重 {dedup_merged} 条, 冲突 {conflicts} 条")

    return data


# ============================================================================
# 合并所有已审核的哈希文件
# ============================================================================

def _merge_rec(
    merged: Dict[Tuple[str, str], Dict[str, Any]],
    summary: str,
    subjects: List[str],
    bucket: str,
    count: int,
):
    """合并一条记录进跨文件 dict（同键同桶累加；不同桶记入候选，最终众数裁决）。"""
    key = _dedup_key(summary, subjects)
    if key in merged:
        entry = merged[key]
        entry["buckets"][bucket] = entry["buckets"].get(bucket, 0) + count
        entry["total"] = entry.get("total", 0) + count
    else:
        merged[key] = {
            "summary": summary,
            "subjects": subjects,
            "buckets": {bucket: count},
            "total": count,
        }


def merge_training_data(
    training_dir: str = None,
    only_reviewed: bool = True,
    output_path: str = None,
) -> Dict[str, Any]:
    """合并已审核的未审文件进金标准 training_data.json（幂等，合并后删除文件）。

    新设计（用户确认的文件结构）:
      - 只有未审目录 training/unreviewed/（哈希命名，V2.1 自动生成）
      - 已审数据（AI 审完标 reviewed 的 + 置信度 high 自动并入的）**直接合并进
        training_data.json**，合并后从未审目录删除文件 —— 不保留已审文件副本，
        training_data.json 是唯一已审金标准，持续扩大
      - 已合并过的哈希（金标准 source_hashes 中已存在）跳过并删除，保证幂等

    跨文件去重: 同键同桶 count 累加；同键不同桶 → count 加权众数胜出。

    Args:
        training_dir: 未审目录（默认 nn/_storage/training/unreviewed/）
        only_reviewed: True = 只合并 reviewed: true 的文件
        output_path: 金标准输出（默认 nn/_storage/training_data.json）

    Returns:
        金标准数据 dict
    """
    if training_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        training_dir = str(Path(NN_STORAGE_DIR) / "training" / "unreviewed")
    if output_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_path = str(Path(NN_STORAGE_DIR) / "training_data.json")

    training_dir = Path(training_dir)
    if not training_dir.exists():
        print(f"[WARN] 未审目录不存在: {training_dir}")
        return {"records": [], "source_hashes": []}

    # 金标准已有来源（幂等去重用）
    existing_hashes: Set[str] = set()
    if Path(output_path).exists():
        try:
            old = json.loads(Path(output_path).read_text(encoding="utf-8"))
            for h in old.get("source_hashes", []):
                existing_hashes.add(str(h).replace("[auto]", ""))
        except (json.JSONDecodeError, KeyError):
            pass

    # 跨文件合并: 同键 → {桶: count} 字典，最终按 count 加权众数裁决
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    source_hashes = []
    skipped_unreviewed = 0
    skipped_legacy = 0
    conflicts = 0
    consumed_files = []

    for f in sorted(training_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            skipped_legacy += 1
            continue

        if "buckets" not in data:
            skipped_legacy += 1
            continue

        fingerprint = data.get("fingerprint", f.stem)

        # 幂等: 已并入过金标准的文件直接消费（跳过合并，只删除）
        if fingerprint in existing_hashes:
            f.unlink()
            consumed_files.append(f.name)
            print(f"   [SKIP] 已并入过金标准，删除: {f.name}")
            continue

        if only_reviewed and not data.get("reviewed", False):
            skipped_unreviewed += 1
            continue

        source_hashes.append(fingerprint)
        for rec in _flatten_buckets(data["buckets"]):
            _merge_rec(merged, rec["summary"], rec["subjects"], rec["bucket"],
                       rec.get("count", 1))

        # 合并后消费: 删除已并入的未审文件（金标准已是唯一已审存储）
        f.unlink()
        consumed_files.append(f.name)

    # 与现有金标准合并（同键 count 累加）——必须在 records 构建之前！
    # 否则旧金标准数据不会被写入输出（上次 bug: 金标准被空数据覆盖）
    if Path(output_path).exists():
        try:
            old = json.loads(Path(output_path).read_text(encoding="utf-8"))
            for rec in _flatten_buckets(old.get("buckets", {})):
                _merge_rec(merged, rec["summary"], rec["subjects"], rec["bucket"],
                           rec.get("count", 1))
        except (json.JSONDecodeError, KeyError):
            pass

    # 冲突裁决: count 加权众数
    conflict_stats = {"total_conflicts": 0, "resolved_by_majority": 0}
    records = []
    for key, entry in merged.items():
        bucket, count = max(entry["buckets"].items(), key=lambda kv: kv[1])
        if len(entry["buckets"]) > 1:
            conflict_stats["total_conflicts"] += 1
            if entry["buckets"][bucket] >= entry["total"] / 2:
                conflict_stats["resolved_by_majority"] += 1
        records.append({
            "summary": entry["summary"],
            "subjects": entry["subjects"],
            "bucket": bucket,
            "count": count,
        })

    records.sort(key=lambda r: (r["bucket"], r["summary"]))

    # 按桶聚合 + 相似摘要去重
    buckets_data = _aggregate_by_bucket(records)
    _dedupe_similar_summaries(buckets_data)
    deduped = _flatten_buckets(buckets_data)
    buckets = sorted({r["bucket"] for r in deduped})

    # source_hashes = 历史全部来源（含已消费的）
    all_hashes = sorted(existing_hashes | set(source_hashes))

    merged_data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "format": RECORDS_FORMAT,
        "source_hashes": all_hashes,
        "total_hashes": len(all_hashes),
        "skipped_unreviewed": skipped_unreviewed,
        "skipped_legacy_format": skipped_legacy,
        "conflict_stats": conflict_stats,
        "stats": {
            "total_records": len(deduped),
            "raw_records": len(records),
            "buckets": len(buckets),
            "sim_deduped": len(records) - len(deduped),
        },
        "buckets": buckets_data,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)

    print(f"[OK] 已合并进金标准: {output_path}")
    print(f"   本次并入 {len(source_hashes)} 份文件, 删除 {len(consumed_files)} 份已消费文件")
    print(f"   金标准累计: {len(deduped)} 条记录, {len(buckets)} 桶, {len(all_hashes)} 份来源")
    if skipped_unreviewed:
        print(f"   跳过 {skipped_unreviewed} 个未审核文件")
    if skipped_legacy:
        print(f"   跳过 {skipped_legacy} 个旧格式文件")

    return merged_data


# ============================================================================
# 加载 / 查看 / 审核
# ============================================================================

def _flatten_buckets(buckets_data: Dict[str, List[Dict]]) -> List[Dict[str, Any]]:
    """buckets_v2 聚合格式 → 扁平 records（训练用）。"""
    records = []
    for bucket, groups in buckets_data.items():
        for group in groups:
            subjects = group.get("subjects", [])
            for rec in group.get("records", []):
                records.append({
                    "summary": rec["summary"],
                    "subjects": subjects,
                    "bucket": bucket,
                    "count": rec.get("count", 1),
                })
    return records


def load_merged_records(file_path: str = None) -> List[Dict[str, Any]]:
    """读取合并后的扁平 records 列表（buckets_v2 展开；缺失/旧格式 → []）。"""
    if file_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        file_path = str(Path(NN_STORAGE_DIR) / "training_data.json")

    path = Path(file_path)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        return []

    if "buckets" in data:
        return _flatten_buckets(data["buckets"])
    if "records" in data:
        return data["records"]  # 旧 records_v1 兼容
    return []


def list_training_files(training_dir: str = None) -> List[Dict]:
    """列出未审目录中所有哈希训练文件及其审核状态（buckets_v2 格式统计）。"""
    if training_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        training_dir = str(Path(NN_STORAGE_DIR) / "training" / "unreviewed")

    training_dir = Path(training_dir)
    if not training_dir.exists():
        return []

    files = []
    for f in sorted(training_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            continue

        buckets_data = data.get("buckets", {})
        if buckets_data:
            total_records = sum(
                len(rec.get("records", []))
                for groups in buckets_data.values()
                for rec in groups
            )
            files.append({
                "filename": f.name,
                "fingerprint": data.get("fingerprint", ""),
                "reviewed": data.get("reviewed", False),
                "format": data.get("format", "buckets_v2"),
                "records": total_records,
                "buckets": len(buckets_data),
                "created_at": data.get("created_at", ""),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        else:
            # 旧格式（records_v1 / legacy）: 标记旧格式
            old_records = data.get("records", [])
            files.append({
                "filename": f.name,
                "fingerprint": data.get("fingerprint", ""),
                "reviewed": data.get("reviewed", False),
                "format": data.get("format", "legacy"),
                "records": len(old_records),
                "buckets": 0,
                "created_at": data.get("created_at", ""),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })

    return files


def mark_reviewed(fingerprint: str, reviewed: bool = True,
                  training_dir: str = None) -> bool:
    """标记某哈希文件为已审核/未审核。"""
    if training_dir is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        training_dir = str(Path(NN_STORAGE_DIR) / "training" / "unreviewed")

    path = Path(training_dir) / f"{fingerprint}.json"
    if not path.exists():
        print(f"[WARN] 文件不存在: {path}")
        return False

    data = json.loads(path.read_text(encoding="utf-8"))
    data["reviewed"] = reviewed
    data["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if reviewed else ""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    status = "已审核" if reviewed else "待审核"
    print(f"[OK] {path.name} → {status}")
    return True


# ============================================================================
# 索引构建（训练时用）
# ============================================================================

def build_subject_switch_index(records: List[Dict]) -> Dict[str, int]:
    """收集全部 '科目[方向]' 开关 → 排序 → {科目[方向]: index}。"""
    switches: Set[str] = set()
    for rec in records:
        switches.update(rec.get("subjects", []))
    return {s: i for i, s in enumerate(sorted(switches))}


def build_bucket_index(records: List[Dict]) -> Dict[str, int]:
    """收集全部桶 → 排序 → {桶: index}。"""
    buckets = sorted({rec["bucket"] for rec in records})
    return {b: i for i, b in enumerate(buckets)}


# ============================================================================
# 训练/验证划分
# ============================================================================

def split_records(
    records: List[Dict],
    train_ratio: float = 0.8,
    seed: int = 42,
) -> Tuple[List[Dict], List[Dict]]:
    """按桶分层划分训练/验证集（80/20），count 展开（上限 MAX_COUNT_EXPAND）。

    每个样本一条记录（count 条重复样本会被展开，确保 count 大的
    模式在训练中贡献更多）。

    Returns:
        (train_records, val_records) — 展开后的样本列表
    """
    random.seed(seed)

    # 按桶分组
    by_bucket: Dict[str, List[Dict]] = defaultdict(list)
    for rec in records:
        by_bucket[rec["bucket"]].append(rec)

    train_samples: List[Dict] = []
    val_samples: List[Dict] = []

    for bucket, bucket_records in by_bucket.items():
        # 展开 + 打乱
        expanded = []
        for rec in bucket_records:
            n = min(rec.get("count", 1), MAX_COUNT_EXPAND)
            for _ in range(n):
                expanded.append(rec)
        random.shuffle(expanded)

        n_val = max(1, round(len(expanded) * (1 - train_ratio))) if len(expanded) > 1 else 0
        val_samples.extend(expanded[:n_val])
        train_samples.extend(expanded[n_val:])

    print(f"[OK] 数据划分: 训练 {len(train_samples)} 条, 验证 {len(val_samples)} 条"
          f"（按桶分层, 桶数 {len(by_bucket)}）")
    return train_samples, val_samples


# ============================================================================
# AI 审核指南生成（供 Claude Code 会话按桶审核用）
# ============================================================================

# 桶的业务含义（用户编写，供 AI 理解每个桶装什么业务）
# 注意: 桶名以 BUCKET_REGISTRY 为准（与训练数据一致）
BUCKET_MEANINGS = {
    "职工薪酬": "职工薪酬，字面意思，比如发放工资、计提工资等",
    "税费": "税费，字面意思，比如支付增值税、计提所得税等",
    "存货采购": "存货采购，字面意思，比如采购原材料、支付供应商货款等",
    "长期资产": "一切与长期资产相关的活动，比如采购固定资产、装修活动等",
    "销售收入": "销售收入，字面意思，比如销售货物、收到客户款项等",
    "借款筹资": "借款筹资，字面意思，比如借款、收到股东注资等",
    "利息收支": "字面意思，比如支付或计提利息等",
    "费用报销": "一切与日常费用有关的活动，比如支付办公费、差旅费等",
    "押金保证金": "一切与支付押金有关的活动，比如支付租金保证金、投标保证金等",
    "投资本金": "一切与投资支出有关的活动，比如长期投资等",
    "分红股利": "字面意思，比如分红",
    "生产制造": "字面意思，一切与生产制造有关的活动",
    "折旧摊销": "一切与长期资产折旧摊销有关的活动，比如固定资产折旧、无形资产摊销、使用权资产折旧等",
    "研发": "一切与研发有关的活动",
    "政府补助": "一切与政府补助有关的活动",
}


def generate_ai_review_guide(output_path: str = None) -> str:
    """生成 AI 审核指南（桶定义 + 审核规则）。

    自动排除硬规则桶（其他业务/资金内部往来/汇兑损益）——它们由 V2.1 代码
    if 语句写死分配，从不进入训练数据，指南里不应列出（避免 AI 困惑）。
    只保留会出现在训练数据中的语义桶。

    Args:
        output_path: 输出路径（默认 nn/_storage/training/AI_REVIEW_GUIDE.md）

    Returns:
        指南文本
    """
    from summary_cleaner.v2.config import BUCKET_REGISTRY
    from summary_cleaner.v2.config import load_buckets_json
    from .data import HARD_RULE_BUCKETS

    # 桶关键词资产（含每个桶的关键词列表）
    kw_data = {}
    try:
        kw_data = load_buckets_json()
    except Exception:
        kw_data = {}

    lines = []
    lines.append("# 训练数据 AI 审核指南（buckets_v2）")
    lines.append("")
    lines.append("你正在审核「序时账业务分类」训练数据。数据按桶聚合组织，你的任务是：")
    lines.append("**一个桶一个桶看，发现不属于该桶的记录，直接从桶里删除。**")
    lines.append("")
    lines.append("## 文件格式")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "fingerprint": "哈希", "reviewed": false,')
    lines.append('  "buckets": {')
    lines.append('    "存货采购": [')
    lines.append("      {")
    lines.append('        "subjects": ["应付账款[借]", "银行存款[贷]"],   ← 科目组合（借/贷）')
    lines.append('        "records": [')
    lines.append('          {"summary": "付杭州分公司货款", "count": 3, "confidence": "high"},')
    lines.append('          {"summary": "付北京分公司货款", "count": 5, "confidence": "low"}')
    lines.append("        ]")
    lines.append("      }")
    lines.append("    ]")
    lines.append("  }")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("- **buckets** = 按桶聚合。每个桶下是一组「科目组合节点」")
    lines.append("- **subjects** = 该组合的科目+方向（如 应付账款[借]、银行存款[贷]）——业务结构线索")
    lines.append("- **records** = 该科目组合下的摘要列表（整句，最重要线索），count = 出现次数（高频更该审准）")
    lines.append("- **confidence** = 置信度标记（可选）：high = 与金标准一致不用细看；low = 重点审")
    lines.append("")
    lines.append("## 置信度拆分（重要）")
    lines.append("")
    lines.append("置信度比对程序会把与已审核金标准高度一致的记录")
    lines.append("（同科目组合 + 同桶 + 摘要相似度≥75%）**自动并入金标准 training_data.json**，")
    lines.append("**你不需要看它们**，也不会读入你的上下文。")
    lines.append("")
    lines.append("主文件（未审目录中的 `{hash}.json`）仅剩低置信度记录，")
    lines.append("**你审核的就是这份**。记录带 `confidence: \"low\"` 标记（或无标记，按常规审核）。")
    lines.append("")
    lines.append("## 审核规则（核心）")
    lines.append("")
    lines.append("1. 一次看一个桶。对照下方桶定义，判断桶下每个「科目组合 + 摘要」是否真的属于该桶")
    lines.append("2. **不属于该桶** → 直接删除：")
    lines.append("   - 整个科目组合节点都不对 → 删除该组合节点（连同其 records）")
    lines.append("   - 组合对但个别摘要不对（如混入其他业务）→ 删除该摘要节点")
    lines.append("3. **拿不准 → 删除**（训练数据宁缺毋滥，只留下最明确、稳健的样本。")
    lines.append("   存疑样本会教坏模型，宁可少一点数据）")
    lines.append("4. **只删不改**：不要修改桶名，不要新增记录，不要移动记录到别的桶")
    lines.append("   - 删除是唯一操作。保留的都是你确认正确的样本")
    lines.append("5. 审完把该文件的 reviewed 改为 true")
    lines.append("6. 保留其他所有字段与结构")
    lines.append("")
    lines.append("## 桶定义（只会出现在训练数据中的桶）")
    lines.append("")

    def add_bucket(name):
        info = BUCKET_REGISTRY.get(name, {})
        clarity = info.get("clarity", "")
        subjects = list(info.get("subjects", {}).keys())
        lines.append(f"### {name}" + (f"（清晰度 {clarity}）" if clarity else ""))
        meaning = BUCKET_MEANINGS.get(name)
        if meaning:
            lines.append(f"- 它的含义：{meaning}")
        if subjects:
            lines.append(f"- 主要科目: " + "、".join(subjects[:12]))
        item = kw_data.get(name)
        kws = (item or {}).get("keywords", []) if isinstance(item, dict) else (item or [])
        if kws:
            lines.append(f"- 关键词: " + "、".join(kws[:15]))
        lines.append("")

    for name in kw_data:
        if name not in HARD_RULE_BUCKETS:
            add_bucket(name)
    for name in BUCKET_REGISTRY:
        if name not in kw_data and name not in HARD_RULE_BUCKETS:
            add_bucket(name)

    guide = "\n".join(lines)

    if output_path is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        output_path = str(Path(NN_STORAGE_DIR) / "training" / "AI_REVIEW_GUIDE.md")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(guide)
    print(f"[OK] AI 审核指南已生成: {output_path}")
    return guide


# ============================================================================
# 置信度参考（主动学习）: 未审数据 vs 已审金标准数据
# ============================================================================

def compute_review_confidence(
    unreviewed_path: str,
    golden_records: Optional[List[Dict]] = None,
    threshold: float = 0.75,
) -> Dict[str, Any]:
    """将未审数据与已审金标准比对，high 记录直接并入金标准（AI 不读）。

    原理（用户设计的主动学习流程）:
      - 已审数据 = 金标准（AI + 人工确认过的正确分类，training_data.json）
      - 新跑的数据若与金标准高度一致 → 分类置信度高 → AI 不用细看
      - 只把低置信度的给 AI 重点审

    关键设计（避免 high 白占上下文）:
      high 记录**从主文件物理移出，直接合并进金标准 training_data.json**——
      AI 审核时只读主文件（仅剩 low 记录），high 完全不进 AI 上下文。
      不存在 approved 中间文件：只要已审（人工或自动），就进金标准。

    命中条件（全部满足）:
      1. 科目组合完全相等（subjects 排序后相同）
      2. 桶完全相等（bucket 相同）
      3. 摘要文本相似度 >= threshold（difflib 字符级，默认 75%）
         —— 不同公司基本不会命中，同公司跨年份序时账才可能命中

    文件变化:
      - 主文件 {hash}.json: 仅保留 low 记录（+ confidence: "low" 标记），供 AI 审核
      - 金标准 training_data.json: high 记录直接并入（merge 同键 count 累加）
      - 无金标准时（第一份数据）: 全部 low，仅提示（AI 全审）

    Args:
        unreviewed_path: 未审训练数据文件路径（buckets_v2 格式）
        golden_records: 已审金标准扁平记录；None 时自动读
            nn/_storage/training_data.json
        threshold: 摘要相似度阈值（默认 0.75）

    Returns:
        {"high": n, "low": m, "high_ratio": x, "golden_source": "..."}
    """
    if golden_records is None:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        golden_path = Path(NN_STORAGE_DIR) / "training_data.json"
        golden_records = load_merged_records(str(golden_path))
        golden_source = str(golden_path)
    else:
        golden_source = "传入记录"

    if not golden_records:
        print("[WARN] 无已审金标准数据（training_data.json 为空），"
              "全部标为 low。请先审核一份数据并合并。")
        golden_source = "空"

    # 金标准索引: (bucket, 科目组合键) → [摘要列表]
    # 科目组合键 = 排序后的科目开关字符串（不含摘要文本）
    golden_index: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for rec in golden_records:
        subj_key = "|".join(sorted(rec.get("subjects", [])))
        golden_index[(rec["bucket"], subj_key)].append(rec["summary"])

    unreviewed_path = Path(unreviewed_path)
    data = json.loads(unreviewed_path.read_text(encoding="utf-8"))
    if "buckets" not in data:
        raise ValueError(f"不是 buckets_v2 格式: {unreviewed_path}")

    high = 0
    low = 0
    total = 0

    # 拆分后的主文件 buckets（只留 low）与 approved buckets（high）
    low_buckets: Dict[str, List[Dict]] = {}
    high_buckets: Dict[str, List[Dict]] = {}

    for bucket, groups in data["buckets"].items():
        low_groups: List[Dict] = []
        high_groups: List[Dict] = []
        for group in groups:
            subj_key = "|".join(sorted(group.get("subjects", [])))
            golden_sums = golden_index.get((bucket, subj_key), [])
            low_recs: List[Dict] = []
            high_recs: List[Dict] = []
            for rec in group.get("records", []):
                total += 1
                if not golden_sums:
                    rec["confidence"] = "low"
                    low_recs.append(rec)
                    low += 1
                    continue
                # 摘要相似度比对（找到任一 >= threshold 即命中）
                matched = False
                for gs in golden_sums:
                    sim = difflib.SequenceMatcher(
                        None, rec["summary"], gs
                    ).ratio()
                    if sim >= threshold:
                        matched = True
                        break
                if matched:
                    high_recs.append(rec)
                    high += 1
                else:
                    rec["confidence"] = "low"
                    low_recs.append(rec)
                    low += 1
            if low_recs:
                low_groups.append({"subjects": group["subjects"], "records": low_recs})
            if high_recs:
                high_groups.append({"subjects": group["subjects"], "records": high_recs})
        if low_groups:
            low_buckets[bucket] = low_groups
        if high_groups:
            high_buckets[bucket] = high_groups

    # 主文件: 只保留 low（AI 审核只看这里）
    data["buckets"] = low_buckets
    data["stats"] = {
        "total_records": low,
        "buckets": len(low_buckets),
        "auto_approved": high,
        "confidence_split": True,
    }
    with open(unreviewed_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # high 记录直接并入金标准 training_data.json（AI 完全不读）
    if high > 0:
        from summary_cleaner.v2.config import NN_STORAGE_DIR
        golden_path = Path(NN_STORAGE_DIR) / "training_data.json"
        # 读现有金标准（无则从空开始）
        golden_merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if golden_path.exists():
            try:
                old = json.loads(golden_path.read_text(encoding="utf-8"))
                for rec in _flatten_buckets(old.get("buckets", {})):
                    _merge_rec(golden_merged, rec["summary"], rec["subjects"],
                               rec["bucket"], rec.get("count", 1))
            except (json.JSONDecodeError, KeyError):
                pass
        # 并入 high 记录
        for rec in _flatten_buckets(high_buckets):
            _merge_rec(golden_merged, rec["summary"], rec["subjects"],
                       rec["bucket"], rec.get("count", 1))
        # 写回金标准（保留原有 source_hashes 并追加本次指纹）
        # 注意: _merge_rec 的 value 结构是 {summary, subjects, buckets(众数), total}，
        # 需要按 merge_training_data 的众数裁决逻辑展开
        all_records = []
        for entry in golden_merged.values():
            bucket, count = max(entry["buckets"].items(), key=lambda kv: kv[1])
            all_records.append({
                "summary": entry["summary"],
                "subjects": entry["subjects"],
                "bucket": bucket,
                "count": count,
            })
        all_records.sort(key=lambda r: (r["bucket"], r["summary"]))
        gb = _aggregate_by_bucket(all_records)
        _dedupe_similar_summaries(gb)
        # 注意: 不把本文件 fingerprint 加入 source_hashes —— high 只是部分并入，
        # 文件还有 low 记录待 AI 审核。等 AI 审完 merge 时才算完整消费并记入来源。
        # 否则 merge 会因幂等判断（fingerprint 已存在）跳过该文件的 low 记录。
        old_hashes = []
        if golden_path.exists():
            try:
                old = json.loads(golden_path.read_text(encoding="utf-8"))
                old_hashes = list(old.get("source_hashes", []))
            except (json.JSONDecodeError, KeyError):
                pass
        golden_data = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "format": RECORDS_FORMAT,
            "source_hashes": sorted(old_hashes),
            "total_hashes": len(old_hashes),
            "stats": {
                "total_records": len(_flatten_buckets(gb)),
                "buckets": len(gb),
            },
            "buckets": gb,
        }
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        with open(golden_path, "w", encoding="utf-8") as f:
            json.dump(golden_data, f, ensure_ascii=False, indent=2)
        print(f"[OK] high={high} 条已直接并入金标准: {golden_path}")

    result = {
        "high": high,
        "low": low,
        "high_ratio": round(high / total, 4) if total else 0.0,
        "total": total,
        "golden_source": golden_source,
    }
    print(f"[OK] 置信度拆分完成: high={high} ({result['high_ratio']:.1%}) 已并入金标准"
          f"（AI 不读）, low={low} 留在主文件供 AI 审核")
    return result
