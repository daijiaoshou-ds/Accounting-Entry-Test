#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
业务桶关键词训练脚本

根据预设的业务桶关键词，对摘要/二级科目文本进行 AC 自动机匹配，
输出命中/未命中明细，用于迭代优化关键词规则。
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


# ---------------------------------------------------------------------------
# AC 自动机（纯 Python 实现，无第三方依赖）
# ---------------------------------------------------------------------------

class PurePythonAC:
    """纯 Python 实现的 Aho-Corasick 自动机，用于多模式字符串匹配。"""

    def __init__(self):
        self.root = {}
        self._built = False

    def add_word(self, word, value):
        """向自动机中添加一个关键词及其关联值。"""
        if not word:
            return
        node = self.root
        for ch in word:
            node = node.setdefault(ch, {})
        node.setdefault("output", []).append(value)

    def make_automaton(self):
        """构建 fail 指针，完成自动机构造。"""
        from collections import deque

        queue = deque()
        self.root["fail"] = self.root

        for ch, node in self.root.items():
            if ch in ("output", "fail"):
                continue
            node["fail"] = self.root
            queue.append(node)

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

                child.setdefault("output", []).extend(
                    child["fail"].get("output", [])
                )
                queue.append(child)

        self._built = True

    def iter(self, text):
        """遍历文本，逐个字符产出匹配到的 (结束位置, 关联值)。"""
        if not self._built:
            raise RuntimeError("必须先调用 make_automaton() 构建自动机")

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
    """业务桶匹配器，使用 AC 自动机将文本与业务桶关键词进行匹配。"""

    def __init__(self, buckets: dict):
        self.buckets = buckets
        self.automaton = PurePythonAC()
        self.keyword_to_buckets = defaultdict(set)

        for bucket_name, bucket_info in buckets.items():
            for keyword in bucket_info.get("keywords", []):
                if not keyword or not isinstance(keyword, str):
                    continue
                self.keyword_to_buckets[keyword].add(bucket_name)
                self.automaton.add_word(keyword, (bucket_name, keyword))

        self.automaton.make_automaton()

    def match(self, text: str) -> dict:
        """对给定文本执行匹配，返回命中的桶和关键词。"""
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
# 数据加载
# ---------------------------------------------------------------------------

def detect_text_column(df: pd.DataFrame, filename: str) -> str:
    """自动检测数据文件中哪一列是文本列。"""
    candidate_names = ["摘要", "科目", "名称", "text", "content"]
    for col in df.columns:
        if str(col).strip() in candidate_names:
            return col

    # 如果以上候选都不匹配，取第一个非序号/ID列
    for col in df.columns:
        col_clean = str(col).strip().lower()
        if col_clean not in ["序号", "id", "编号", "no"]:
            return col

    raise ValueError(f"无法识别文件 {filename} 的文本列，列名为：{list(df.columns)}")


def load_training_file(filepath: Path) -> pd.DataFrame:
    """根据文件扩展名加载训练数据文件。"""
    suffix = filepath.suffix.lower()
    if suffix in [".xlsx", ".xls"]:
        return pd.read_excel(filepath, engine="openpyxl")
    elif suffix == ".csv":
        return pd.read_csv(filepath)
    else:
        raise ValueError(f"不支持的文件格式：{filepath}")


def iter_training_records(training_dir: Path):
    """遍历训练数据目录，逐条产出记录。

    仅处理文件名包含"摘要文本"的文件。每条记录包含：id、来源文件名、文本内容。
    """
    if not training_dir.exists():
        raise FileNotFoundError(f"训练数据目录不存在：{training_dir}")

    pattern = re.compile(r"摘要文本.*", re.IGNORECASE)

    files = sorted(training_dir.iterdir(), key=lambda x: x.name)
    for filepath in files:
        if filepath.is_dir():
            continue
        if not pattern.match(filepath.stem):
            continue

        df = load_training_file(filepath)
        text_col = detect_text_column(df, filepath.name)

        for _, row in df.iterrows():
            seq = row.get("序号", None)
            if pd.isna(seq):
                continue

            # 序号可能是数字（简单编号）或字符串（唯一凭证号如 202401_001）
            if isinstance(seq, (int, float)):
                if float(seq).is_integer():
                    seq = int(seq)
                seq = str(seq)
            else:
                seq = str(seq).strip()

            text = row[text_col]
            yield {
                "id": seq,
                "source_file": filepath.name,
                "text": text,
            }


# ---------------------------------------------------------------------------
# 分类与统计
# ---------------------------------------------------------------------------

def run_classification(buckets: dict, bucket_cf_map: dict, training_dir: Path):
    """对训练数据目录中的所有记录执行业务桶分类。

    返回命中记录、未命中记录、桶统计和文件统计。
    """
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

            cf_parts = []
            for bucket_name in result["buckets"].keys():
                cf_items = bucket_cf_map.get(bucket_name, [])
                cf_parts.append(
                    f"{bucket_name}: {'/'.join(cf_items) if cf_items else '未映射'}"
                )

            hit_records.append({
                "ID": record["id"],
                "来源文件": record["source_file"],
                "文本内容": record["text"],
                "命中业务桶": "、".join(result["buckets"].keys()),
                "命中关键词": "、".join(result["keyword_hits"]),
                "对应现金流项目": "；".join(cf_parts),
            })
        else:
            file_counter[record["source_file"]]["miss"] += 1
            miss_records.append({
                "ID": record["id"],
                "来源文件": record["source_file"],
                "文本内容": record["text"],
            })

    return {
        "hit_records": hit_records,
        "miss_records": miss_records,
        "bucket_counter": bucket_counter,
        "file_counter": dict(file_counter),
    }


def build_summary(results: dict, buckets: dict) -> list:
    """构建训练报告的统计摘要数据。"""
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


# ---------------------------------------------------------------------------
# 报告导出
# ---------------------------------------------------------------------------

def export_report(results: dict, buckets: dict, output_dir: Path,
                  file_suffix: str = "") -> Path:
    """将分类结果导出为 Excel 报告文件。

    file_suffix: 可选的文件标识（如 "公司A"），用于区分不同文件的报告。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"bucket_training_report_{timestamp}"
    if file_suffix:
        name = f"{name}_{file_suffix}"
    output_path = output_dir / f"{name}.xlsx"

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


def load_json(path: Path) -> dict:
    """从文件加载 JSON 配置。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    """命令行入口：加载配置 → 执行分类 → 导出报告。"""
    parser = argparse.ArgumentParser(description="业务桶关键词训练脚本")
    parser.add_argument("--buckets", type=Path, default=Path("buckets_seed.json"),
                        help="业务桶关键词配置文件路径")
    parser.add_argument("--bucket-cf-map", type=Path, default=Path("bucket_cf_map.json"),
                        help="业务桶到现金流映射文件路径（可选）")
    parser.add_argument("--training-dir", type=Path, default=Path("training_data"),
                        help="训练数据目录路径（读取目录下所有匹配文件）")
    parser.add_argument("--training-file", type=Path, default=None,
                        help="训练单个文件（优先级高于 --training-dir）")
    parser.add_argument("--output-dir", type=Path, default=Path("output"),
                        help="输出报告目录路径")
    parser.add_argument("--output-suffix", type=str, default=None,
                        help="报告文件名后缀（用于区分不同文件的报告）")
    args = parser.parse_args()

    # 加载配置
    buckets = load_json(args.buckets)
    bucket_cf_map = load_json(args.bucket_cf_map) if args.bucket_cf_map.exists() else {}

    total_keywords = sum(len(v.get("keywords", [])) for v in buckets.values())
    print(f"已加载 {len(buckets)} 个业务桶，共 {total_keywords} 个关键词")

    # 确定训练数据来源：--training-file > --training-dir
    if args.training_file:
        if not args.training_file.exists():
            print(f"错误：训练文件不存在 —— {args.training_file}")
            sys.exit(1)
        # 将单文件复制到临时目录，复用现有训练逻辑
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp())
        import shutil
        shutil.copy(args.training_file, tmp_dir)
        training_source = tmp_dir
        print(f"单文件训练：{args.training_file.name}")
    else:
        training_source = args.training_dir

    # 执行分类
    results = run_classification(buckets, bucket_cf_map, training_source)

    # 清理临时目录
    if args.training_file:
        shutil.rmtree(tmp_dir)

    # 打印统计
    total = len(results["hit_records"]) + len(results["miss_records"])
    hit = len(results["hit_records"])
    miss = len(results["miss_records"])
    hit_rate = hit / total if total > 0 else 0

    print(f"\n总记录数：{total}")
    print(f"命中数：{hit}")
    print(f"未命中数：{miss}")
    print(f"命中率：{hit_rate:.2%}")

    # 导出报告（单文件训练时用文件 stem 做后缀，避免同名覆盖）
    file_suffix = args.training_file.stem if args.training_file else ""
    output_path = export_report(results, buckets, args.output_dir, file_suffix)
    print(f"\n报告已保存：{output_path}")

    # 输出机器可读的 JSON 摘要，方便 AI 收集结果
    import json as _json
    summary = {
        "file": args.training_file.name if args.training_file else "全部文件",
        "total": total,
        "hit": hit,
        "miss": miss,
        "hit_rate": f"{hit_rate:.2%}",
        "report_path": str(output_path),
    }
    print(f"\n__SUMMARY__{_json.dumps(summary, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
