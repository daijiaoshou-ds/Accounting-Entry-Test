#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未命中业务桶辅助归类建议脚本

对训练过程中未命中任何业务桶的文本，自动提取高频词、聚类，
并给出"补充关键词到现有桶"或"新建业务桶"的建议。
"""

import argparse
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

# 确保控制台输出使用 UTF-8 编码，避免中文乱码
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
from train_bucket_classifier import BucketMatcher, iter_training_records, load_json


# ---------------------------------------------------------------------------
# 高频词提取
# ---------------------------------------------------------------------------

# 中文连续词提取正则：匹配2-8个连续中文字符
_CJK_WORD = re.compile(r'[一-鿿]{2,8}')
# 中英文混合模式：中文 + 字母/数字（如 DHL运费、YK支架）
_CJK_MIXED = re.compile(r'[一-鿿]{2,8}[A-Za-z0-9]+|[A-Za-z0-9]+[一-鿿]{2,8}')


def extract_ngrams(texts, min_len=2, max_len=4, min_freq=2, stopwords=None):
    """从未命中文本中提取高频词。

    使用正则提取连续中文词（2-8字），而非暴力滑动切片。
    相比 n-gram 切片，能正确识别"发票号码"而非"票号/号码"等碎片。

    参数:
        texts: 文本列表
        min_len: 最小长度（仅对回退的 n-gram 模式生效）
        max_len: 最大长度（仅对回退的 n-gram 模式生效）
        min_freq: 最小出现次数
        stopwords: 需要排除的停用词集合

    返回:
        {词: 出现次数} 的字典
    """
    if stopwords is None:
        stopwords = set()

    # 常见无意义停用词（公司名后缀、城市名等）
    default_stopwords = {
        "有限公司", "有限责任公司", "股份有限", "深圳市", "东莞市",
        "广州市", "上海市", "北京市", "香港", "澳门",
        "公司", "有限", "限公", "限公司", "公限",
    }
    stopwords = stopwords | default_stopwords

    def _is_meaningless(t: str) -> bool:
        """过滤无意义的词。"""
        if t.isdigit():
            return True
        if all(c in ' \t\n\r，。、；：""''！？…—·-‐.·/\\()（）[]【】{}#@$%^&*+=~`|<>,' for c in t):
            return True
        if t[0] in './\\':
            return True
        # 文档编号碎片：字母+数字混合
        if len(t) >= 2 and len(t) <= 8:
            alpha_count = sum(1 for c in t if c.isalpha())
            digit_count = sum(1 for c in t if c.isdigit())
            if alpha_count <= 2 and digit_count >= 1 and alpha_count + digit_count == len(t):
                return True
        return False

    term_freq = Counter()

    for text in texts:
        if not isinstance(text, str):
            text = str(text) if pd.notna(text) else ""
        text = text.strip()

        # 1. 提取连续中文词（主要方法）
        for match in _CJK_WORD.finditer(text):
            word = match.group()
            if len(word) >= 2 and not _is_meaningless(word) and word not in stopwords:
                term_freq[word] += 1

        # 2. 补充提取中英混合模式
        for match in _CJK_MIXED.finditer(text):
            word = match.group()
            if not _is_meaningless(word) and word not in stopwords:
                term_freq[word] += 1

        # 3. 回退：对纯中文句子，也做 2-3 字 n-gram（捕获短词）
        clean_cn = _CJK_WORD.sub('', text)
        if len(text) <= 10:
            for n in range(2, 4):
                for i in range(len(text) - n + 1):
                    term = text[i:i + n]
                    if not _is_meaningless(term) and term not in stopwords:
                        if any('一' <= c <= '鿿' for c in term):
                            term_freq[term] += 1

    return {term: freq for term, freq in term_freq.items() if freq >= min_freq}


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

def assign_clusters(unhit_rows, top_terms, max_terms_per_cluster=3):
    """将未命中行按高频词进行聚类。

    每条记录按其包含的高频词组合（最多 max_terms_per_cluster 个）
    分配到对应的聚类中。没有任何高频词匹配的归入"_无显著特征_"。
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


# ---------------------------------------------------------------------------
# 建议生成
# ---------------------------------------------------------------------------

def suggest_for_cluster(cluster_rows, cluster_key, buckets, bucket_cf_map):
    """针对单个聚类生成操作建议。

    判断该聚类的高频词是否与现有业务桶的关键词存在语义重叠：
    - 有重叠 → 建议补充关键词到现有桶
    - 无重叠 → 建议考虑新建业务桶
    """
    sample_texts = [r["text"] for r in cluster_rows[:5]]
    sample_ids = [r["id"] for r in cluster_rows[:3]]

    overlapping_buckets = []
    cluster_terms = [t.strip() for t in cluster_key.split("+") if t.strip()]

    for bucket_name, bucket_info in buckets.items():
        bucket_keywords = set(bucket_info.get("keywords", []))
        overlap = [
            t for t in cluster_terms
            if any(t in kw or kw in t for kw in bucket_keywords)
        ]
        if overlap:
            overlapping_buckets.append(bucket_name)

    if overlapping_buckets:
        action = "补充关键词到现有业务桶"
        target = "、".join(overlapping_buckets)
        reason = (
            f"该聚类高频词 [{cluster_key}] 与现有桶 [{target}] 语义相关，"
            f"建议补充代表性词汇"
        )
    else:
        action = "考虑新建业务桶"
        target = "（待命名）"
        reason = (
            f"该聚类高频词 [{cluster_key}] 与现有业务桶均无重叠，"
            f"可能是一个独立业务类型"
        )

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
    """构建高频未命中词报告。"""
    texts = [str(r["text"]) for r in unhit_rows]
    terms = extract_ngrams(texts, min_len=2, max_len=4, min_freq=1)
    sorted_terms = sorted(terms.items(), key=lambda x: x[1], reverse=True)[:top_n]

    return [{"未命中高频词": term, "出现次数": freq} for term, freq in sorted_terms]


# ---------------------------------------------------------------------------
# TF-IDF 未命中 vs 已命中 特征词分析
# ---------------------------------------------------------------------------

def _extract_cn_words(texts, min_len=2, max_len=6):
    """从文本列表中提取中文词频率。"""
    freq = Counter()
    for text in texts:
        if not isinstance(text, str):
            text = str(text) if pd.notna(text) else ""
        for match in _CJK_WORD.finditer(text):
            word = match.group()
            if min_len <= len(word) <= max_len and not word.isdigit():
                freq[word] += 1
    return freq


def find_distinctive_terms(unhit_texts, hit_texts, min_freq=3, top_n=50):
    """找出在未命中里高频、已命中里低频的特征词。

    这些词通常指向缺失的业务桶或漏掉的关键词。
    评分公式：uniqueness = (unhit_freq / total_freq) * unhit_freq
    - 第一项衡量"该词在未命中中的集中度"（1.0 = 只在未命中出现）
    - 第二项确保高频词得分更高
    """
    unhit_freq = _extract_cn_words(unhit_texts)
    hit_freq = _extract_cn_words(hit_texts)

    all_terms = set(unhit_freq.keys())
    scores = []

    for term in all_terms:
        uf = unhit_freq.get(term, 0)
        if uf < min_freq:
            continue
        hf = hit_freq.get(term, 0)
        total = uf + hf
        uniqueness = uf / total  # 0.5 = 两边一样多, 1.0 = 只在未命中
        score = uniqueness * uf   # 综合得分
        scores.append({
            "特征词": term,
            "未命中次数": uf,
            "已命中次数": hf,
            "集中度": f"{uniqueness:.0%}",
            "得分": round(score, 1),
            "建议": _suggest_action(term, uniqueness, uf),
        })

    scores.sort(key=lambda x: x["得分"], reverse=True)
    return scores[:top_n]


def _suggest_action(term, uniqueness, freq):
    """根据特征词给建议。"""
    if uniqueness >= 0.9 and freq >= 20:
        return "🔴 强烈建议建新桶或补充关键词（几乎只在未命中出现）"
    elif uniqueness >= 0.7 and freq >= 10:
        return "🟡 建议建新桶或补充关键词（主要在未命中出现）"
    elif uniqueness >= 0.5:
        return "🟢 考虑补充关键词（未命中偏多）"
    else:
        return "⚪ 未命中和已命中分布均匀，可忽略"


def build_distinctive_terms_report(unhit_texts, hit_texts, top_n=50):
    """构建TF-IDF特征词报告（DataFrame格式）。"""
    return find_distinctive_terms(unhit_texts, hit_texts, top_n=top_n)


# ---------------------------------------------------------------------------
# Pattern 分析：从分录模式推断桶归属
# ---------------------------------------------------------------------------

def build_pattern_report(unhit_rows, hit_rows, buckets, min_freq=3, top_n=30):
    """分析未命中项的 pattern，通过一级科目匹配推断归属。

    不再依赖 keyword 匹配的 pattern→bucket 映射（可能被关键词噪声污染），
    而是直接用 bucket 里定义的 accounts 字段做锚定匹配。
    """
    # 构建 bucket 的 account 索引：一级科目 → [桶名列表]
    account_to_buckets = defaultdict(list)
    for bucket_name, info in buckets.items():
        if bucket_name.startswith("_"):
            continue
        for acct in info.get("accounts", []):
            account_to_buckets[acct].append(bucket_name)

    # 统计未命中 pattern 频率
    unhit_patterns = Counter()
    for r in unhit_rows:
        pat = r.get("pattern", "")
        if pat:
            unhit_patterns[pat] += 1

    hit_patterns = Counter()
    for r in hit_rows:
        pat = r.get("pattern", "")
        if pat:
            hit_patterns[pat] += 1

    def _extract_accounts(pattern_str):
        """从 pattern 字符串中提取所有一级科目名。"""
        # pattern格式: "科目A-二级A[借]；科目B-二级B[贷]"
        accounts = set()
        for entry in pattern_str.split("；"):
            if "-" in entry:
                acct = entry.split("-")[0].strip()
                if acct:
                    accounts.add(acct)
        return accounts

    def _infer_bucket(pattern_str):
        """根据 pattern 中的一级科目推断归属桶。"""
        accts = _extract_accounts(pattern_str)
        if not accts:
            return None, 0, "无一级科目"

        # 统计各桶的命中得分
        bucket_scores = Counter()
        for acct in accts:
            matched_buckets = account_to_buckets.get(acct, [])
            for b in matched_buckets:
                bucket_scores[b] += 1

        if not bucket_scores:
            return None, 0, f"科目{accts}未匹配任何桶"

        top_bucket, top_score = bucket_scores.most_common(1)[0]
        total_accts = len(accts)
        confidence = top_score / total_accts * 100

        if confidence >= 80:
            status = f"科目锚定「{top_bucket}」"
            suggestion = f"强烈建议归入「{top_bucket}」（{total_accts}个科目中{top_score}个匹配该桶）"
        elif confidence >= 50:
            other = [b for b, _ in bucket_scores.most_common(3) if b != top_bucket]
            status = f"科目偏「{top_bucket}」"
            suggestion = f"倾向于归入「{top_bucket}」，但也可能{other}"
        else:
            status = f"科目分散({top_bucket}仅{confidence:.0f}%)"
            suggestion = "科目归属分散，需人工判断"

        return top_bucket, confidence, f"{status} | {suggestion}"

    report = []
    for pat, unhit_count in unhit_patterns.most_common(200):
        if unhit_count < min_freq:
            continue
        hit_count = hit_patterns.get(pat, 0)
        top_bucket, confidence, detail = _infer_bucket(pat)

        pattern_preview = pat[:150]
        report.append({
            "分录Pattern": pattern_preview,
            "未命中次数": unhit_count,
            "已命中次数": hit_count,
            "涉及科目": "、".join(sorted(_extract_accounts(pat))),
            "推断归属": top_bucket or "未知",
            "置信度": f"{confidence:.0f}%" if top_bucket else "-",
            "建议操作": detail,
        })

    return report[:top_n]

def run_suggestion(
    buckets: dict,
    bucket_cf_map: dict,
    training_dir: Path,
    top_n_terms: int = 30
):
    """执行完整的建议流程：匹配 → 收集未命中 → 提取高频词 → 聚类 → 生成建议。

    返回包含聚类报告、聚类明细、高频词报告和未命中总数的字典。
    如果没有任何未命中项，返回 None。
    """
    matcher = BucketMatcher(buckets)

    # 收集命中与未命中，同时建立 pattern→bucket 映射
    unhit_rows = []
    hit_rows = []
    pattern_to_buckets = defaultdict(Counter)  # pattern → {桶名: 出现次数}

    for record in iter_training_records(training_dir):
        result = matcher.match(record["text"])
        pat = record.get("pattern", "")
        if result["buckets"]:
            hit_rows.append(record)
            if pat:
                for bucket_name in result["buckets"]:
                    pattern_to_buckets[pat][bucket_name] += 1
        else:
            unhit_rows.append(record)

    if not unhit_rows:
        print("没有未命中项。")
        return None

    print(f"未命中项总数：{len(unhit_rows)}（已命中 {len(hit_rows)}）")

    # TF-IDF 特征词分析：找出在未命中中高频、已命中中低频的词
    unhit_texts = [str(r["text"]) for r in unhit_rows]
    hit_texts = [str(r["text"]) for r in hit_rows]
    distinctive_terms = build_distinctive_terms_report(unhit_texts, hit_texts, top_n=50)
    print(f"TF-IDF 特征词：{len(distinctive_terms)} 个")

    # Pattern 分析：用 bucket 内 accounts 字段做科目锚定推断归属
    pattern_report = build_pattern_report(unhit_rows, hit_rows, buckets)
    print(f"未命中高频 Pattern：{len(pattern_report)} 个")

    # 提取高频 n-gram
    texts = unhit_texts
    min_freq = 1 if len(unhit_rows) < 5 else 2
    term_freq = extract_ngrams(texts, min_len=2, max_len=4, min_freq=min_freq)
    top_terms = [
        term for term, _ in sorted(
            term_freq.items(), key=lambda x: x[1], reverse=True
        )[:top_n_terms]
    ]

    print(f"提取到高频未命中词 {len(term_freq)} 个")

    # 聚类
    clusters = assign_clusters(unhit_rows, top_terms)

    # 为每个聚类生成建议
    cluster_reports = []
    all_clustered_rows = []

    for cluster_key in sorted(
        clusters.keys(), key=lambda k: len(clusters[k]), reverse=True
    ):
        cluster_rows = clusters[cluster_key]
        cluster_reports.append(
            suggest_for_cluster(cluster_rows, cluster_key, buckets, bucket_cf_map)
        )
        all_clustered_rows.extend(cluster_rows)

    return {
        "cluster_reports": cluster_reports,
        "clustered_rows": all_clustered_rows,
        "top_terms": build_top_terms_report(unhit_rows, top_n=top_n_terms),
        "distinctive_terms": distinctive_terms,
        "pattern_report": pattern_report,
        "unhit_count": len(unhit_rows),
        "hit_count": len(hit_rows),
    }


def export_suggestion_report(results: dict, output_dir: Path) -> Path:
    """将聚类建议结果导出为 Excel 报告文件。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"bucket_suggestion_report_{timestamp}.xlsx"

    cluster_df = pd.DataFrame(results["cluster_reports"])
    detail_df = pd.DataFrame(results["clustered_rows"])
    terms_df = pd.DataFrame(results["top_terms"])
    distinctive_df = pd.DataFrame(results.get("distinctive_terms", []))
    pattern_df = pd.DataFrame(results.get("pattern_report", []))

    # 整理明细列
    if not detail_df.empty:
        detail_df = detail_df[["聚类标签", "id", "source_file", "text"]]
        detail_df.columns = ["聚类标签", "ID", "来源文件", "文本内容"]

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        cluster_df.to_excel(writer, sheet_name="聚类建议", index=False)
        detail_df.to_excel(writer, sheet_name="未命中聚类明细", index=False)
        terms_df.to_excel(writer, sheet_name="高频未命中词", index=False)
        if not distinctive_df.empty:
            distinctive_df.to_excel(writer, sheet_name="未命中特征词_TFIDF", index=False)
        if not pattern_df.empty:
            pattern_df.to_excel(writer, sheet_name="未命中高频Pattern", index=False)

    return output_path


def main():
    """命令行入口：加载配置 → 收集未命中 → 聚类 → 生成建议报告。"""
    parser = argparse.ArgumentParser(description="未命中业务桶辅助归类建议脚本")
    parser.add_argument("--buckets", type=Path, default=Path("buckets_seed.json"),
                        help="业务桶关键词配置文件路径")
    parser.add_argument("--bucket-cf-map", type=Path, default=Path("bucket_cf_map.json"),
                        help="业务桶到现金流映射文件路径（可选）")
    parser.add_argument("--training-dir", type=Path, default=Path("training_data"),
                        help="训练数据目录路径")
    parser.add_argument("--output-dir", type=Path, default=Path("output"),
                        help="输出报告目录路径")
    parser.add_argument("--top-n-terms", type=int, default=30,
                        help="用于聚类的高频词数量")
    args = parser.parse_args()

    # 加载配置
    buckets = load_json(args.buckets)
    bucket_cf_map = load_json(args.bucket_cf_map) if args.bucket_cf_map.exists() else {}

    print(f"已加载 {len(buckets)} 个业务桶\n")

    # 执行建议流程
    results = run_suggestion(buckets, bucket_cf_map, args.training_dir, args.top_n_terms)

    if results is None:
        return

    # 导出报告
    output_path = export_suggestion_report(results, args.output_dir)
    print(f"\n建议报告已保存：{output_path}")


if __name__ == "__main__":
    main()
