#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未命中业务桶辅助归类建议脚本

功能：
1. 读取训练数据，用现有 buckets_seed.json 跑一遍分类
2. 对未命中项自动提取高频词、聚类
3. 输出建议报告，帮助 AI / 人工快速判断：
   - 哪些未命中项可以补充关键词到现有业务桶
   - 哪些可能暗示需要新建业务桶

输出：CF_compile/training/output/bucket_suggestion_report_*.xlsx
"""

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

# Windows 控制台编码兼容
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

# 引入训练脚本中的公共函数和类
sys.path.insert(0, str(Path(__file__).parent))
from train_bucket_classifier import (
    BucketMatcher,
    iter_training_records,
    load_json,
)


# ---------------------------------------------------------------------------
# 文本聚类与建议
# ---------------------------------------------------------------------------

def extract_ngrams(texts, min_len=2, max_len=4, min_freq=2, stopwords=None):
    """
    从文本中提取高频字符 n-gram
    """
    if stopwords is None:
        stopwords = set()

    term_freq = Counter()
    for text in texts:
        if not isinstance(text, str):
            text = str(text) if pd.notna(text) else ""
        text = text.strip()
        if len(text) < min_len:
            continue
        for n in range(min_len, min(max_len, len(text)) + 1):
            for i in range(len(text) - n + 1):
                term = text[i:i + n]
                if term in stopwords:
                    continue
                term_freq[term] += 1

    return {term: freq for term, freq in term_freq.items() if freq >= min_freq}


def assign_clusters(unhit_rows, top_terms, max_terms_per_cluster=3):
    """
    给每个未命中行分配聚类标签
    标签 = 该行包含的最高频 term 组合（最多取前 N 个）
    """
    clusters = defaultdict(list)

    for row in unhit_rows:
        text = str(row["text"])
        matched = [term for term in top_terms if term in text]
        if matched:
            key = " + ".join(matched[:max_terms_per_cluster])
        else:
            key = "_无显著特征_"
        row = dict(row)
        row["聚类标签"] = key
        clusters[key].append(row)

    return clusters


def suggest_for_cluster(cluster_rows, cluster_key, buckets, bucket_cf_map):
    """
    对一个聚类给出建议
    """
    sample_texts = [r["text"] for r in cluster_rows[:5]]
    sample_ids = [r["id"] for r in cluster_rows[:3]]

    # 检查聚类关键词是否与现有业务桶关键词有重叠
    overlapping_buckets = []
    cluster_terms = [t.strip() for t in cluster_key.split("+") if t.strip()]

    for bucket_name, bucket_info in buckets.items():
        bucket_keywords = set(bucket_info.get("keywords", []))
        overlap = [t for t in cluster_terms if any(t in kw or kw in t for kw in bucket_keywords)]
        if overlap:
            overlapping_buckets.append(bucket_name)

    if overlapping_buckets:
        action = "补充关键词到现有业务桶"
        target = "、".join(overlapping_buckets)
        reason = f"该聚类高频词 [{cluster_key}] 与现有桶 [{target}] 语义相关，建议把代表性词汇补充为 keyword"
    else:
        action = "考虑新建业务桶"
        target = "（待命名）"
        reason = f"该聚类高频词 [{cluster_key}] 与现有业务桶均无重叠，可能是一个独立业务类型"

    return {
        "聚类标签": cluster_key,
        "未命中数量": len(cluster_rows),
        "代表性文本_1": sample_texts[0] if len(sample_texts) > 0 else "",
        "代表性文本_2": sample_texts[1] if len(sample_texts) > 1 else "",
        "代表性文本_3": sample_texts[2] if len(sample_texts) > 2 else "",
        "样例ID": "、".join(sample_ids),
        "建议操作": action,
        "建议目标": target,
        "建议理由": reason,
    }


def build_top_terms_report(unhit_rows, top_n=30):
    """
    输出全局高频未命中词报表
    """
    texts = [str(r["text"]) for r in unhit_rows]
    terms = extract_ngrams(texts, min_len=2, max_len=4, min_freq=1)
    sorted_terms = sorted(terms.items(), key=lambda x: x[1], reverse=True)[:top_n]

    return [
        {"未命中高频词": term, "出现次数": freq}
        for term, freq in sorted_terms
    ]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_suggestion(buckets: dict, bucket_cf_map: dict, training_dir: Path, top_n_terms: int = 30):
    matcher = BucketMatcher(buckets)

    unhit_rows = []
    for record in iter_training_records(training_dir):
        result = matcher.match(record["text"])
        if not result["buckets"]:
            unhit_rows.append(record)

    if not unhit_rows:
        print("恭喜，没有未命中项！")
        return None

    print(f"未命中项总数：{len(unhit_rows)}")

    # 提取高频词（未命中项少时 min_freq 取 1，避免一个高频词都没有）
    texts = [str(r["text"]) for r in unhit_rows]
    min_freq = 1 if len(unhit_rows) < 5 else 2
    term_freq = extract_ngrams(texts, min_len=2, max_len=4, min_freq=min_freq)
    top_terms = [term for term, _ in sorted(term_freq.items(), key=lambda x: x[1], reverse=True)[:top_n_terms]]

    print(f"提取到高频未命中词 {len(term_freq)} 个，取前 {len(top_terms)} 个用于聚类")

    # 聚类
    clusters = assign_clusters(unhit_rows, top_terms)

    # 生成聚类建议
    cluster_reports = []
    all_clustered_rows = []

    for cluster_key in sorted(clusters.keys(), key=lambda k: len(clusters[k]), reverse=True):
        cluster_rows = clusters[cluster_key]
        cluster_reports.append(
            suggest_for_cluster(cluster_rows, cluster_key, buckets, bucket_cf_map)
        )
        all_clustered_rows.extend(cluster_rows)

    return {
        "cluster_reports": cluster_reports,
        "clustered_rows": all_clustered_rows,
        "top_terms": build_top_terms_report(unhit_rows, top_n=top_n_terms),
        "unhit_count": len(unhit_rows),
    }


def export_suggestion_report(results: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"bucket_suggestion_report_{timestamp}.xlsx"

    cluster_df = pd.DataFrame(results["cluster_reports"])
    detail_df = pd.DataFrame(results["clustered_rows"])
    terms_df = pd.DataFrame(results["top_terms"])

    # 调整列顺序和中文表头
    if not detail_df.empty:
        detail_df = detail_df[["聚类标签", "id", "source_file", "text_type", "text"]]
        detail_df.columns = ["聚类标签", "ID", "来源文件", "文本类型", "文本内容"]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        cluster_df.to_excel(writer, sheet_name="聚类建议", index=False)
        detail_df.to_excel(writer, sheet_name="未命中聚类明细", index=False)
        terms_df.to_excel(writer, sheet_name="高频未命中词", index=False)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="未命中业务桶辅助归类建议脚本")
    parser.add_argument(
        "--buckets",
        type=Path,
        default=Path("CF_compile/training/config/buckets_seed.json"),
        help="业务桶关键词配置文件路径",
    )
    parser.add_argument(
        "--bucket-cf-map",
        type=Path,
        default=Path("CF_compile/training/config/bucket_cf_map.json"),
        help="业务桶到现金流映射文件路径",
    )
    parser.add_argument(
        "--training-dir",
        type=Path,
        default=Path("CF_compile/training/assets/training_data"),
        help="训练数据目录路径",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("CF_compile/training/output"),
        help="输出报告目录路径",
    )
    parser.add_argument(
        "--top-n-terms",
        type=int,
        default=30,
        help="用于聚类的高频词数量",
    )
    args = parser.parse_args()

    buckets = load_json(args.buckets)
    bucket_cf_map = load_json(args.bucket_cf_map) if args.bucket_cf_map.exists() else {}

    print(f"已加载 {len(buckets)} 个业务桶")
    print(f"正在分析未命中项...\n")

    results = run_suggestion(buckets, bucket_cf_map, args.training_dir, args.top_n_terms)

    if results is None:
        return

    output_path = export_suggestion_report(results, args.output_dir)
    print(f"\n建议报告已保存：{output_path}")


if __name__ == "__main__":
    main()
