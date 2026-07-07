#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
业务桶训练脚本

功能：
1. 读取 CF_compile/training/config/buckets_seed.json 中的业务桶预设关键词
2. 读取 CF_compile/training/assets/training_data/ 下的训练数据文件
   - 支持 摘要文本*.xlsx / 摘要文本*.csv
   - 支持 二级科目文本*.xlsx / 二级科目文本*.csv
3. 使用 AC 自动机对每条文本进行关键词匹配
4. 输出命中/未命中明细到 CF_compile/training/output/

ID 规则：文件名（不含扩展名）_序号
"""

import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

# Windows 控制台默认编码可能不是 UTF-8，强制 stdout 使用 UTF-8 输出中文
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 纯 Python AC 自动机实现（不依赖外部库）
# ---------------------------------------------------------------------------

class PurePythonAC:
    """
    Aho-Corasick 多模式串匹配（纯 Python 实现）
    """

    def __init__(self):
        self.root = {}
        self._built = False

    def add_word(self, word, value):
        if not word:
            return
        node = self.root
        for ch in word:
            node = node.setdefault(ch, {})
        node.setdefault("output", []).append(value)

    def make_automaton(self):
        from collections import deque

        queue = deque()
        self.root["fail"] = self.root

        # 第一层节点的 fail 指向 root
        for ch, node in self.root.items():
            if ch in ("output", "fail"):
                continue
            node["fail"] = self.root
            queue.append(node)

        # BFS 构建失败指针
        while queue:
            current = queue.popleft()
            for ch, child in current.items():
                if ch in ("output", "fail"):
                    continue

                fail_node = current["fail"]
                while fail_node is not self.root and ch not in fail_node:
                    fail_node = fail_node["fail"]

                if ch in fail_node:
                    child["fail"] = fail_node[ch]
                else:
                    child["fail"] = self.root

                # 把 fail 节点的 output 合并到当前节点
                child.setdefault("output", []).extend(
                    child["fail"].get("output", [])
                )
                queue.append(child)

        self._built = True

    def iter(self, text):
        if not self._built:
            raise RuntimeError("必须先调用 make_automaton()")

        node = self.root
        for i, ch in enumerate(text):
            while node is not self.root and ch not in node:
                node = node["fail"]

            if ch in node:
                node = node[ch]
            else:
                node = self.root

            for value in node.get("output", []):
                yield (i, value)


class BucketMatcher:
    """基于 AC 自动机的业务桶匹配器"""

    def __init__(self, buckets: dict):
        self.buckets = buckets
        self.automaton = PurePythonAC()
        self.keyword_to_buckets = defaultdict(set)

        for bucket_name, bucket_info in buckets.items():
            for keyword in bucket_info.get("keywords", []):
                if not keyword or not isinstance(keyword, str):
                    continue
                self.keyword_to_buckets[keyword].add(bucket_name)
                # value 用 tuple：(业务桶名, 关键词)
                self.automaton.add_word(keyword, (bucket_name, keyword))

        self.automaton.make_automaton()

    def match(self, text: str) -> dict:
        """
        匹配一段文本，返回命中的桶和关键词
        返回：{"buckets": {bucket_name: [keywords]}, "keyword_hits": [keywords]}
        """
        if not isinstance(text, str):
            text = str(text) if pd.notna(text) else ""

        bucket_hits = defaultdict(set)
        all_keywords = set()

        for end_pos, (bucket_name, keyword) in self.automaton.iter(text):
            bucket_hits[bucket_name].add(keyword)
            all_keywords.add(keyword)

        return {
            "buckets": {k: sorted(v) for k, v in bucket_hits.items()},
            "keyword_hits": sorted(all_keywords),
        }


# ---------------------------------------------------------------------------
# 文件读取
# ---------------------------------------------------------------------------

def detect_text_column(df: pd.DataFrame, filename: str) -> str:
    """自动检测文本列名"""
    candidate_names = ["摘要", "二级科目", "科目", "名称", "text", "content"]
    for col in df.columns:
        if str(col).strip() in candidate_names:
            return col

    # 如果没命中候选名，找第一列非序号的列
    for col in df.columns:
        col_clean = str(col).strip().lower()
        if col_clean not in ["序号", "id", "编号", "no"]:
            return col

    raise ValueError(f"无法识别文件 {filename} 的文本列，列名为：{list(df.columns)}")


def load_training_file(filepath: Path) -> pd.DataFrame:
    """加载单个训练数据文件"""
    suffix = filepath.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(filepath, engine="openpyxl")
    elif suffix == ".csv":
        return pd.read_csv(filepath)
    else:
        raise ValueError(f"不支持的文件格式：{filepath}")


def iter_training_records(training_dir: Path):
    """
    遍历训练数据目录，产出 (id, source_file, text_type, text) 生成器
    """
    if not training_dir.exists():
        raise FileNotFoundError(f"训练数据目录不存在：{training_dir}")

    pattern = re.compile(r"(摘要文本|二级科目文本).*", re.IGNORECASE)

    files = sorted(training_dir.iterdir(), key=lambda x: x.name)
    for filepath in files:
        if filepath.is_dir():
            continue
        if not pattern.match(filepath.stem):
            continue

        df = load_training_file(filepath)
        text_col = detect_text_column(df, filepath.name)

        # 文本类型识别
        if "摘要" in filepath.stem:
            text_type = "摘要"
        elif "二级科目" in filepath.stem:
            text_type = "二级科目"
        else:
            text_type = "其他"

        for _, row in df.iterrows():
            seq = row.get("序号", None)
            if pd.isna(seq):
                continue
            seq = int(seq) if float(seq).is_integer() else seq
            record_id = f"{filepath.stem}_{seq}"
            text = row[text_col]
            yield {
                "id": record_id,
                "source_file": filepath.name,
                "text_type": text_type,
                "text_column": text_col,
                "text": text,
            }


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def run_classification(buckets: dict, bucket_cf_map: dict, training_dir: Path):
    matcher = BucketMatcher(buckets)

    hit_records = []
    miss_records = []
    bucket_counter = Counter()
    file_counter = defaultdict(lambda: {"total": 0, "hit": 0, "miss": 0})

    for record in iter_training_records(training_dir):
        result = matcher.match(record["text"])

        file_counter[record["source_file"]]["total"] += 1

        if result["buckets"]:
            file_counter[record["source_file"]]["hit"] += 1
            for bucket_name in result["buckets"].keys():
                bucket_counter[bucket_name] += 1

            cf_list = []
            for bucket_name in result["buckets"].keys():
                cf_items = bucket_cf_map.get(bucket_name, [])
                cf_list.append(f"{bucket_name}: {'/'.join(cf_items)}")

            hit_records.append({
                "ID": record["id"],
                "来源文件": record["source_file"],
                "文本类型": record["text_type"],
                "文本内容": record["text"],
                "命中业务桶": "、".join(result["buckets"].keys()),
                "命中关键词": "、".join(result["keyword_hits"]),
                "对应现金流项目": "；".join(cf_list),
            })
        else:
            file_counter[record["source_file"]]["miss"] += 1
            miss_records.append({
                "ID": record["id"],
                "来源文件": record["source_file"],
                "文本类型": record["text_type"],
                "文本内容": record["text"],
            })

    return {
        "hit_records": hit_records,
        "miss_records": miss_records,
        "bucket_counter": bucket_counter,
        "file_counter": dict(file_counter),
    }


def build_summary(results: dict, buckets: dict) -> list:
    total = len(results["hit_records"]) + len(results["miss_records"])
    hit = len(results["hit_records"])
    miss = len(results["miss_records"])
    hit_rate = hit / total if total > 0 else 0

    summary = [
        {"指标": "总记录数", "数值": total},
        {"指标": "命中数", "数值": hit},
        {"指标": "未命中数", "数值": miss},
        {"指标": "命中率", "数值": f"{hit_rate:.2%}"},
    ]

    summary.append({"指标": "---", "数值": "---"})
    summary.append({"指标": "按业务桶统计", "数值": "命中次数"})

    for bucket_name in buckets.keys():
        summary.append({
            "指标": f"  {bucket_name}",
            "数值": results["bucket_counter"].get(bucket_name, 0),
        })

    summary.append({"指标": "---", "数值": "---"})
    summary.append({"指标": "按文件统计", "数值": "总数/命中/未命中"})

    for filename, counts in results["file_counter"].items():
        summary.append({
            "指标": f"  {filename}",
            "数值": f"{counts['total']} / {counts['hit']} / {counts['miss']}",
        })

    return summary


def export_report(results: dict, buckets: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"bucket_training_report_{timestamp}.xlsx"

    hit_df = pd.DataFrame(results["hit_records"])
    miss_df = pd.DataFrame(results["miss_records"])
    summary_df = pd.DataFrame(build_summary(results, buckets))

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="统计摘要", index=False)
        if not hit_df.empty:
            hit_df.to_excel(writer, sheet_name="命中明细", index=False)
        if not miss_df.empty:
            miss_df.to_excel(writer, sheet_name="未命中明细", index=False)

    return output_path


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="业务桶关键词训练脚本")
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
    args = parser.parse_args()

    # 加载配置
    buckets = load_json(args.buckets)
    bucket_cf_map = load_json(args.bucket_cf_map) if args.bucket_cf_map.exists() else {}

    total_keywords = sum(len(v.get("keywords", [])) for v in buckets.values())
    print(f"已加载 {len(buckets)} 个业务桶，共 {total_keywords} 个关键词")
    print(f"业务桶-现金流映射：{len(bucket_cf_map)} 个")

    # 运行分类
    results = run_classification(buckets, bucket_cf_map, args.training_dir)

    total = len(results["hit_records"]) + len(results["miss_records"])
    hit = len(results["hit_records"])
    miss = len(results["miss_records"])
    hit_rate = hit / total if total > 0 else 0

    print(f"\n===== 训练结果 =====")
    print(f"总记录数：{total}")
    print(f"命中数：{hit}")
    print(f"未命中数：{miss}")
    print(f"命中率：{hit_rate:.2%}")

    # 导出报告
    output_path = export_report(results, buckets, args.output_dir)
    print(f"\n报告已保存：{output_path}")


if __name__ == "__main__":
    main()
