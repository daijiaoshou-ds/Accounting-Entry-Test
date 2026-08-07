# -*- coding: utf-8 -*-
"""
数据处理 — 从 V2.1 程序内部状态直接提取训练数据 + PyTorch Dataset

核心数据流（正确路径）:
  V2.1 classify() 运行
    ├─→ classified_df (含"业务分类"列)
    ├─→ keyword_matcher (手工关键词)
    ├─→ word_learner → _storage/auto_words_tier1.json (自动词)
    │
    └─→ [本模块] extract_voucher_data()
          ├─ 逐凭证提取: patterns (科目+方向) + keywords (jieba分词)
          ├─ 用审核后的关键词集过滤 keywords
          └─→ build_training_dataset() → TrainingDataset → DataLoader → 训练

关键词审核:
  - 从 _storage 加载程序发现的自动词 + 手工关键词
  - 开发者审核（增/删）→ 保存到 nn/_storage/reviewed_keywords.json
  - 训练时只用审核后的关键词集来过滤凭证的 keywords

备选路径（保留但降级为辅助）:
  export_training_data() → Excel → 手动修改 → import_training_data()
  仅在需要外部审计或跨机器传输时使用。
"""

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

from .vocab import VocabManager

# ============================================================================
# 分词 — 复用 V2.1 的过滤逻辑，保持一致性
# ============================================================================

_AUTO_WORD_STOP_SET = {
    "支付", "收到", "转入", "转出", "冲销", "调整", "结转",
    "计提", "摊销", "核销", "报销", "付款", "收款", "入账",
    "招行", "工行", "农行", "建行", "中行", "交行", "浦发",
    "兴业", "民生", "光大", "中信", "华夏", "平安银行",
    "招商银行", "工商银行", "农业银行", "建设银行", "中国银行",
    "本月", "本期", "合计", "金额", "凭证", "分录", "发票",
    "月份", "人民币", "美元", "港币", "欧元", "日元",
    "年月", "月份", "上年", "次年", "本年",
    "深圳", "北京", "上海", "广州", "广东", "有限", "公司",
    "有限公", "限公司", "技术", "科技",
}


def tokenize_text(text: str) -> List[str]:
    """jieba 分词 + 7条过滤规则（与 WordFeatureLearner 完全一致）。"""
    try:
        import jieba
    except ImportError:
        return []

    words = jieba.lcut(text)
    filtered = []
    for w in words:
        w = w.strip()
        if len(w) < 2:
            continue
        if re.match(r'^[0-9\.\-\/]+$', w):
            continue
        if re.match(r'^[a-zA-Z]+$', w):
            continue
        if re.match(r'^[A-Za-z0-9\.\-\/]+$', w):
            digits = sum(1 for c in w if c.isdigit())
            if digits / len(w) > 0.5:
                continue
        if len(w) >= 6 and all(c in '0123456789.-/' for c in w):
            continue
        if w in _AUTO_WORD_STOP_SET:
            continue
        filtered.append(w)
    return filtered


# ============================================================================
# Pattern 提取
# ============================================================================

def extract_patterns_from_group(
    group: pd.DataFrame,
    subject_col: str,
    debit_col: str,
    credit_col: str,
) -> List[str]:
    """从一张凭证的分录行中提取 Pattern 列表。

    Pattern 格式: "科目名|方向"
    """
    patterns = set()
    for _, row in group.iterrows():
        subject = str(row[subject_col]).strip() if pd.notna(row[subject_col]) else ""
        if not subject:
            continue

        debit = float(row[debit_col]) if pd.notna(row.get(debit_col, 0)) else 0.0
        credit = float(row[credit_col]) if pd.notna(row.get(credit_col, 0)) else 0.0

        if debit > credit:
            direction = "借"
        elif credit > 0:
            direction = "贷"
        else:
            continue

        patterns.add(f"{subject}|{direction}")

    # 用 " | " 分隔，比逗号更清晰：制造费用|借 | 银行存款|贷
    return sorted(patterns, key=lambda x: ("借" in x, x))


def format_pattern_list(patterns: List[str]) -> str:
    """将 pattern 列表格式化为可读字符串，用 " | " 分隔。"""
    return " | ".join(patterns)


# ============================================================================
# 清理 V2.1 自动词（保留 R 矩阵和纠错记录）
# ============================================================================

def cleanup_auto_words(storage_dir: str = None, dry_run: bool = True) -> Dict[str, Any]:
    """删除 V2.1 _storage 中所有词相关文件，保留 PMI R 矩阵和纠错记录。

    适用场景：程序已处理大量凭证（如 11 万张），R 矩阵可靠，
    但自动词（tier1/tier2）质量不行，需要清空后用小样本重新积累。

    保留的文件:
    - global_counters.json    (PMI 计数器 + 金额统计，不含词数据)
    - corrections.json        (纠错记录)

    删除的文件:
    - auto_words_tier1.json
    - auto_words_tier2.json
    - auto_words_tier3.json
    - word_data.json
    - auto_words/ 目录下所有 {hash}.json

    Args:
        storage_dir: _storage 目录路径（None = 自动检测）
        dry_run: True = 只列出要删的文件，不实际删除

    Returns:
        {dry_run, kept, deleted, errors}
    """
    from summary_cleaner.v2.config import get_storage_dir

    sd = Path(storage_dir) if storage_dir else get_storage_dir()

    KEEP_FILES = {"global_counters.json", "corrections.json"}
    DELETE_PATTERNS = [
        "auto_words_tier1.json",
        "auto_words_tier2.json",
        "auto_words_tier3.json",
        "word_data.json",
    ]

    result = {"dry_run": dry_run, "kept": [], "deleted": [], "errors": []}

    # 1. 删除顶层自动词文件
    for pattern in DELETE_PATTERNS:
        path = sd / pattern
        if path.exists():
            if not dry_run:
                try:
                    path.unlink()
                    result["deleted"].append(str(path))
                except OSError as e:
                    result["errors"].append(f"{path}: {e}")
            else:
                result["deleted"].append(f"{str(path)} (dry-run)")

    # 2. 删除 auto_words/ 目录下所有哈希文件
    hash_dir = sd / "auto_words"
    if hash_dir.exists():
        hash_files = list(hash_dir.glob("*.json"))
        for hf in hash_files:
            if not dry_run:
                try:
                    hf.unlink()
                    result["deleted"].append(str(hf))
                except OSError as e:
                    result["errors"].append(f"{hf}: {e}")
            else:
                result["deleted"].append(f"{str(hf)} (dry-run)")

        # 如果目录空了，删除目录本身
        if not dry_run:
            remaining = list(hash_dir.glob("*"))
            if not remaining:
                try:
                    hash_dir.rmdir()
                except OSError:
                    pass

    # 3. 清理 global_counters.json 中的词相关字段
    counters_path = sd / "global_counters.json"
    if counters_path.exists() and not dry_run:
        try:
            import json
            data = json.loads(counters_path.read_text(encoding="utf-8"))
            # 移除词相关字段
            WORD_KEYS = {
                "word_counts", "word_bucket_counts",
                "auto_scores_tier1", "auto_scores_tier2", "auto_scores_tier3",
                "auto_scores_deleted", "word_sessions", "auto_scores",
            }
            cleaned = {k: v for k, v in data.items() if k not in WORD_KEYS}
            if len(cleaned) < len(data):
                from summary_cleaner.v2.storage_utils import safe_write_json
                safe_write_json(counters_path, cleaned)
                result["deleted"].append(
                    f"{str(counters_path)}: 清理了 {len(data) - len(cleaned)} 个词相关字段"
                )
        except Exception as e:
            result["errors"].append(f"清理 global_counters.json 失败: {e}")

    # 4. 列出保留的文件
    for fname in KEEP_FILES:
        path = sd / fname
        if path.exists():
            result["kept"].append(str(path))

    return result


# ============================================================================
# 关键词审核 — 从 _storage 加载程序发现的词，开发者审核
# ============================================================================

def load_keywords_from_storage(storage_dir: str = None) -> Dict[str, Any]:
    """从 _storage 加载程序发现的全部关键词（自动词 + 手工关键词）。

    加载来源:
    1. auto_words_tier1.json → 高频自动词
    2. auto_words_tier2.json → 低频自动词
    3. 手工关键词 → 从 config 的 KEYWORD_EXPLICIT_SCORES + buckets JSON

    Returns:
        {
            "auto_tier1": {bucket: {word: info}},   # Tier 1 自动词
            "auto_tier2": {bucket: {word: info}},   # Tier 2 自动词
            "manual_keywords": {bucket: [word]},     # 手工关键词
            "all_words": {bucket: [word]},           # 全部词（合并）
            "total_count": int,
        }
    """
    from summary_cleaner.v2.config import get_storage_dir, BUCKET_SUBJECT_PREFERENCES, KEYWORD_EXPLICIT_SCORES

    sd = Path(storage_dir) if storage_dir else get_storage_dir()

    # 1. 加载自动词 Tier 1
    tier1_path = sd / "auto_words_tier1.json"
    auto_tier1 = {}
    if tier1_path.exists():
        try:
            raw = json.loads(tier1_path.read_text(encoding="utf-8"))
            for bucket, words in raw.items():
                if bucket.startswith("_"):
                    continue
                auto_tier1[bucket] = words
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. 加载自动词 Tier 2
    tier2_path = sd / "auto_words_tier2.json"
    auto_tier2 = {}
    if tier2_path.exists():
        try:
            raw = json.loads(tier2_path.read_text(encoding="utf-8"))
            for bucket, words in raw.items():
                if bucket.startswith("_"):
                    continue
                # Tier 2 可能包含 word_sessions 元数据，过滤掉
                if bucket == "word_sessions":
                    continue
                auto_tier2[bucket] = {k: v for k, v in words.items()
                                      if isinstance(v, dict)}
        except (json.JSONDecodeError, KeyError):
            pass

    # 3. 手工关键词 — 从 KEYWORD_EXPLICIT_SCORES 提取
    manual_kw: Dict[str, Set[str]] = defaultdict(set)
    for kw, bucket_scores in KEYWORD_EXPLICIT_SCORES.items():
        for bucket, score in bucket_scores.items():
            if score > 0:  # 只取正向关键词
                manual_kw[bucket].add(kw)

    # 4. 合并全部词
    all_words: Dict[str, Set[str]] = defaultdict(set)
    for bucket, words in auto_tier1.items():
        for w in words:
            all_words[bucket].add(w)
    for bucket, words in auto_tier2.items():
        for w in words:
            all_words[bucket].add(w)
    for bucket, words in manual_kw.items():
        for w in words:
            all_words[bucket].add(w)

    total = sum(len(words) for words in all_words.values())

    return {
        "auto_tier1": {b: dict(w) for b, w in auto_tier1.items()},
        "auto_tier2": {b: dict(w) for b, w in auto_tier2.items()},
        "manual_keywords": {b: sorted(w) for b, w in manual_kw.items()},
        "all_words": {b: sorted(w) for b, w in all_words.items()},
        "total_count": total,
    }


def save_reviewed_keywords(
    approved_words: Dict[str, List[str]],
    removed_words: List[str],
    added_words: List[str],
    save_dir: str = None,
) -> str:
    """保存审核后的关键词白名单。

    Args:
        approved_words: {bucket: [word1, word2, ...]}  审核通过的关键词
        removed_words: ["word1", ...]  被删除的词
        added_words: ["word1", ...]  手动新增的词
        save_dir: 保存目录（默认 nn/_storage/）

    Returns:
        保存路径
    """
    from summary_cleaner.v2.config import NN_STORAGE_DIR

    sd = Path(save_dir) if save_dir else NN_STORAGE_DIR
    sd.mkdir(parents=True, exist_ok=True)

    data = {
        "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "approved_words": approved_words,
        "removed_words": removed_words,
        "added_by_user": added_words,
        "total_approved": sum(len(words) for words in approved_words.values()),
    }

    path = sd / "reviewed_keywords.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return str(path)


def load_reviewed_keywords(save_dir: str = None) -> Optional[Dict[str, List[str]]]:
    """加载审核后的关键词白名单。

    Returns:
        {bucket: [word1, word2, ...]} 或 None（尚未审核）
    """
    from summary_cleaner.v2.config import NN_STORAGE_DIR

    sd = Path(save_dir) if save_dir else NN_STORAGE_DIR
    path = sd / "reviewed_keywords.json"

    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("approved_words", {})
    except (json.JSONDecodeError, KeyError):
        return None


# ============================================================================
# 核心：从 V2.1 已处理数据直接提取训练记录
# ============================================================================

def extract_voucher_data(
    df: pd.DataFrame,
    column_mapping: Dict[str, str],
    reviewed_keywords: Dict[str, List[str]] = None,
    manual_keywords: List[str] = None,
    skip_buckets: Set[str] = None,
) -> List[Dict]:
    """从 V2.1 已分类数据中逐凭证提取 patterns + keywords。

    这是 NN 训练数据的主要来源——直接从程序处理过的数据提取，
    不需要 Excel 中间层。

    Args:
        df: 含"业务分类"列的原始数据（V2.1 classify 后的输出）
        column_mapping: {
            voucher_no, subject, subject_name, summary, debit, credit
        }
        reviewed_keywords: 审核后的关键词白名单 {bucket: [word]}
                          如果提供，只保留在白名单中的 keyword
        manual_keywords: 手工关键词列表（如 ["材料采购过渡", "承兑", ...]）
                        在凭证原文中做子串匹配，补充 jieba 分词可能切碎的词。
                        例如 jieba 把"材料采购过渡"切成["材料","采购","过渡"]，
                        子串匹配能找回完整的"材料采购过渡"。
        skip_buckets: 跳过这些桶的凭证（如"未分类""其他业务"）

    Returns:
        [
            {
                "voucher_id": str,
                "bucket": str,
                "patterns": ["制造费用|借", "银行存款|贷"],
                "keywords": ["办公费", "车间", "材料采购过渡"],  # jieba + 手工子串
                "summary": str (截断),     # 调试用
            },
            ...
        ]
    """
    v_col = column_mapping["voucher_no"]
    s_col = column_mapping["subject"]
    sn_col = column_mapping.get("subject_name", "")
    sum_col = column_mapping.get("summary", "")
    d_col = column_mapping.get("debit", "")
    c_col = column_mapping.get("credit", "")

    # 硬规则桶（Step 0 预分配，无业务学习价值）
    _HARD_RULE_BUCKETS = {"其他业务", "资金内部往来", "汇兑损益"}
    if skip_buckets is None:
        skip_buckets = {"未分类", "无法分类"} | _HARD_RULE_BUCKETS
    else:
        skip_buckets = set(skip_buckets) | _HARD_RULE_BUCKETS

    # 构建关键词白名单
    # 关键：即使 reviewed_keywords 为空 {}，也要创建空 set，
    # 这样关键词会被严格过滤（而不是 None 导致全部放行）
    approved_set: set = set()
    if reviewed_keywords:
        for words in reviewed_keywords.values():
            approved_set.update(words)
    if manual_keywords:
        for kw in manual_keywords:
            approved_set.add(kw)

    records = []
    skipped_no_pattern = 0
    skipped_no_bucket = 0

    for vid, group in df.groupby(v_col):
        # 业务分类
        bucket = str(group["业务分类"].iloc[0]).strip() if "业务分类" in group.columns else ""
        if not bucket or bucket in skip_buckets:
            skipped_no_bucket += 1
            continue

        # 提取 Patterns
        if s_col in group.columns and d_col in group.columns and c_col in group.columns:
            patterns = extract_patterns_from_group(group, s_col, d_col, c_col)
        else:
            patterns = []

        if not patterns:
            skipped_no_pattern += 1
            continue

        # 拼接凭证文本（摘要 + 科目明细）
        text_parts = []
        if sum_col and sum_col in group.columns:
            text_parts.append(" ".join(group[sum_col].dropna().astype(str)))
        if sn_col and sn_col in group.columns:
            text_parts.append(" ".join(group[sn_col].dropna().astype(str)))
        full_text = " ".join(text_parts)

        # ── 关键词来源 1: jieba 分词 ──
        raw_keywords = tokenize_text(full_text)

        # ── 关键词来源 2: 手工关键词子串匹配 ──
        # jieba 可能把"材料采购过渡"切成碎片，子串匹配能找回完整词
        if manual_keywords:
            for kw in manual_keywords:
                if kw and kw in full_text:  # 简单的子串匹配
                    if kw not in raw_keywords:
                        raw_keywords.append(kw)

        # 去重 + 白名单过滤
        seen = set()
        keywords = []
        for k in raw_keywords:
            if k in seen:
                continue
            seen.add(k)
            if approved_set is not None and k not in approved_set:
                continue
            keywords.append(k)

        # 摘要（调试用）
        summary = ""
        if sum_col and sum_col in group.columns:
            summary = str(group[sum_col].iloc[0])[:100]

        records.append({
            "voucher_id": str(vid),
            "bucket": bucket,
            "patterns": patterns,
            "keywords": keywords,
            "summary": summary,
        })

    if skipped_no_pattern or skipped_no_bucket:
        print(f"  跳过: {skipped_no_bucket} 凭证(无桶), "
              f"{skipped_no_pattern} 凭证(无pattern)")

    return records


# ============================================================================
# 构建 TrainingDataset
# ============================================================================

def build_training_dataset(
    df: pd.DataFrame = None,
    column_mapping: Dict[str, str] = None,
    voucher_records: List[Dict] = None,
    reviewed_keywords: Dict[str, List[str]] = None,
    vocab: VocabManager = None,
    bucket_filter: Set[str] = None,
) -> Tuple["TrainingDataset", VocabManager, Dict[str, int]]:
    """构建 TrainingDataset。

    两种调用方式:
    A) 传入 df + column_mapping → 自动提取 patterns/keywords
    B) 传入 voucher_records → 使用已提取的数据（extract_voucher_data 的输出）

    Args:
        df: 已分类的原始数据（方式 A）
        column_mapping: 列名映射（方式 A）
        voucher_records: extract_voucher_data() 的输出（方式 B）
        reviewed_keywords: 审核后的关键词白名单
        vocab: 现有词表（None 则自动构建）
        bucket_filter: 只保留这些桶

    Returns:
        (dataset, vocab, bucket_to_idx)
    """
    if voucher_records is None and df is not None and column_mapping is not None:
        voucher_records = extract_voucher_data(
            df, column_mapping, reviewed_keywords
        )

    if voucher_records is None:
        raise ValueError("必须提供 df+column_mapping 或 voucher_records")

    if vocab is None:
        vocab = VocabManager()

    # 构建关键词查找集合
    approved_set: Optional[Set[str]] = None
    if reviewed_keywords:
        approved_set = set()
        for words in reviewed_keywords.values():
            approved_set.update(words)

    records = []
    all_buckets = set()

    for vr in voucher_records:
        bucket = vr["bucket"]
        if bucket_filter and bucket not in bucket_filter:
            continue

        # 转换 Pattern → ID
        pattern_ids = vocab.lookup_patterns(vr["patterns"])

        # 转换 Keyword → ID（过滤 + 只在词表中的）
        raw_keywords = vr["keywords"]
        if approved_set is not None:
            raw_keywords = [k for k in raw_keywords if k in approved_set]
        keyword_ids = vocab.lookup_keywords_strict(raw_keywords)

        records.append({
            "voucher_id": vr["voucher_id"],
            "bucket": bucket,
            "pattern_ids": pattern_ids,
            "keyword_ids": keyword_ids,
        })
        all_buckets.add(bucket)

    bucket_to_idx = {b: i for i, b in enumerate(sorted(all_buckets))}
    dataset = TrainingDataset(records, bucket_to_idx)
    return dataset, vocab, bucket_to_idx


# ============================================================================
# 备选路径：Excel 导出/导入（辅助用）
# ============================================================================

def export_training_data(
    classified_df: pd.DataFrame,
    column_mapping: Dict[str, str],
    output_path: str,
) -> str:
    """导出训练数据 Excel（备选路径——用于外部审计或跨机器传输）。

    导出列: 凭证号 | 业务分类 | 摘要 | Patterns | Keywords | 借方合计
    """
    voucher_records = extract_voucher_data(classified_df, column_mapping)

    rows = []
    for vr in voucher_records:
        rows.append({
            "凭证号": vr["voucher_id"],
            "业务分类": vr["bucket"],
            "摘要": vr.get("summary", ""),
            "Patterns": ", ".join(vr["patterns"]),
            "Keywords": ", ".join(vr["keywords"]),
        })

    export_df = pd.DataFrame(rows)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    export_df.to_excel(output_path, index=False, engine="openpyxl")
    return output_path


def import_training_data(
    excel_path: str,
    vocab: VocabManager = None,
) -> Tuple[List[Dict], VocabManager]:
    """从 Excel 导入训练数据（备选路径）。

    要求 Excel 列: 凭证号, 业务分类, Patterns, Keywords
    """
    df = pd.read_excel(excel_path)

    required_cols = ["凭证号", "业务分类", "Patterns", "Keywords"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少必需列: {missing}")

    if vocab is None:
        vocab = VocabManager()

    records = []
    skipped = 0

    for _, row in df.iterrows():
        bucket = str(row["业务分类"]).strip() if pd.notna(row["业务分类"]) else ""
        if not bucket or bucket in ("未分类", "无法分类"):
            skipped += 1
            continue

        patterns_str = str(row["Patterns"]) if pd.notna(row["Patterns"]) else ""
        patterns = [p.strip() for p in patterns_str.split(",") if p.strip()]

        keywords_str = str(row["Keywords"]) if pd.notna(row["Keywords"]) else ""
        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]

        if not patterns:
            skipped += 1
            continue

        pattern_ids = vocab.lookup_patterns(patterns)
        keyword_ids = vocab.lookup_keywords_strict(keywords)

        records.append({
            "voucher_id": str(row["凭证号"]),
            "bucket": bucket,
            "pattern_ids": pattern_ids,
            "keyword_ids": keyword_ids,
        })

    return records, vocab


# ============================================================================
# PyTorch Dataset
# ============================================================================

class TrainingDataset(Dataset):
    """训练数据集。

    每条记录 = 一张凭证的 Pattern ID 列表 + Keyword ID 列表 + 桶标签。
    """

    def __init__(self, records: List[Dict], bucket_to_idx: Dict[str, int]):
        self.records = records
        self.bucket_to_idx = bucket_to_idx
        self.idx_to_bucket = {v: k for k, v in bucket_to_idx.items()}

        self._bucket_groups: Dict[int, List[int]] = defaultdict(list)
        for i, rec in enumerate(records):
            bucket_idx = bucket_to_idx.get(rec["bucket"])
            if bucket_idx is not None:
                self._bucket_groups[bucket_idx].append(i)

    @property
    def num_buckets(self) -> int:
        return len(self.bucket_to_idx)

    @property
    def bucket_names(self) -> List[str]:
        return sorted(self.bucket_to_idx.keys())

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> Dict:
        rec = self.records[idx]
        bucket_idx = self.bucket_to_idx.get(rec["bucket"], -1)
        return {
            "voucher_id": rec["voucher_id"],
            "bucket": rec["bucket"],
            "bucket_idx": bucket_idx,
            "pattern_ids": rec["pattern_ids"],
            "keyword_ids": rec["keyword_ids"],
        }

    def collate_fn(self, batch: List[Dict]) -> Dict:
        return {
            "voucher_ids": [item["voucher_id"] for item in batch],
            "bucket_indices": torch.tensor(
                [item["bucket_idx"] for item in batch], dtype=torch.long
            ),
            "batch_data": [
                {
                    "pattern_ids": item["pattern_ids"],
                    "keyword_ids": item["keyword_ids"],
                }
                for item in batch
            ],
        }

    def get_bucket_distribution(self) -> Dict[str, int]:
        dist = defaultdict(int)
        for rec in self.records:
            dist[rec["bucket"]] += 1
        return dict(dist)

    def get_stats(self) -> dict:
        dist = self.get_bucket_distribution()
        return {
            "total_vouchers": len(self.records),
            "num_buckets": self.num_buckets,
            "bucket_distribution": dist,
            "vocab_size": {
                "patterns": len(set(
                    p for rec in self.records for p in rec["pattern_ids"]
                )),
                "keywords": len(set(
                    k for rec in self.records for k in rec["keyword_ids"]
                )),
            },
        }

    def split(self, train_ratio: float = 0.8, seed: int = 42):
        """按桶分层拆分训练/验证集。"""
        np.random.seed(seed)
        train_records = []
        val_records = []

        for bucket_idx, indices in self._bucket_groups.items():
            indices = list(indices)
            np.random.shuffle(indices)
            split_point = max(1, int(len(indices) * train_ratio))

            for i in indices[:split_point]:
                train_records.append(self.records[i])
            for i in indices[split_point:]:
                val_records.append(self.records[i])

        np.random.shuffle(train_records)
        np.random.shuffle(val_records)

        return (
            TrainingDataset(train_records, self.bucket_to_idx),
            TrainingDataset(val_records, self.bucket_to_idx),
        )
