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

            original_text = str(row[text_col])
            pattern = str(row.get("pattern", "")) if "pattern" in row.index and pd.notna(row.get("pattern")) else ""

            # 科目名称优先匹配：过滤"次要科目"，只保留核心业务科目
            l2_names = str(row.get("科目名称", "")) if "科目名称" in row.index and pd.notna(row.get("科目名称")) else ""

            # 次要科目：总是出现、不区分业务类型
            SECONDARY_L2 = {
                # 支付方式类
                "美元", "港币", "欧元", "人民币", "日元", "",
                # 银行/现金（所有凭证都有）
                "银行存款", "库存现金", "其它货币资金", "其他货币资金",
                # 往来类（几乎所有凭证都有对方科目）
                "应付账款", "应收账款", "其他应付款", "其他应收款",
                "预付账款", "预收账款", "应收票据", "应付票据",
                # 税费类（采购/销售/费用都有）
                "应交税费", "应交个人所得税", "待抵扣进项税",
            }
            BANK_NOISE = re.compile(r'银行.*\d{5,}|^\d{5,}$')

            core_l2 = []
            for n in l2_names.split():
                n = n.strip()
                if n in SECONDARY_L2:
                    continue
                if BANK_NOISE.match(n):
                    continue
                if len(n) > 25:
                    continue
                core_l2.append(n)
            match_text = " ".join(core_l2) + " " + original_text if core_l2 else original_text

            yield {
                "id": seq,
                "source_file": filepath.name,
                "text": original_text,        # 原始摘要，用于报告
                "match_text": match_text,     # 用于关键词匹配
                "pattern": pattern,
            }


# ---------------------------------------------------------------------------
# 分类与统计
# ---------------------------------------------------------------------------

def _anchor_matches(anchor, match_text: str) -> bool:
    """检查单个锚定项是否满足条件。

    支持两种格式：
    - 字符串：无条件锚定（"重石头"），科目出现在 match_text 中即命中
    - 字典：条件锚定（"轻石头"），科目命中后还需满足 requires_any_account
      或 requires_any_keyword 中的任一条件

    字典格式：
      {
        "account": "制造费用",
        "requires_any_account": ["生产成本"],      // 至少一个同时在 match_text 中
        "requires_any_keyword": ["领料", "退料"]    // 或至少一个关键词在 match_text 中
      }
    """
    if isinstance(anchor, str):
        return anchor in match_text

    # 字典格式：条件锚定
    account = anchor.get("account", "")
    if not account or account not in match_text:
        return False

    # 检查 requires_any_account（OR 关系）
    for req_acct in anchor.get("requires_any_account", []):
        if req_acct in match_text:
            return True

    # 检查 requires_any_keyword（OR 关系）
    for req_kw in anchor.get("requires_any_keyword", []):
        if req_kw in match_text:
            return True

    return False


def _rank_buckets(matched_buckets: dict, preferences: dict, match_text: str) -> list:
    """按偏好分数对命中桶排序。核心桶在前，兜底桶在后。

    偏好 = clarity(桶本身清晰度) + anchor_bonus(是否命中锚定科目)

    锚定支持两种格式（见 _anchor_matches）：
    - 无条件锚定："重石头"科目独自就能沉底（如应付职工薪酬）
    - 条件锚定："轻石头"科目需绑上其他石头（如制造费用+生产成本或制造费用+领料）
    """
    bucket_prefs = preferences.get("buckets", {})
    anchor_bonus_value = preferences.get("anchor_bonus", 10)

    scored = []
    for bucket_name, keywords in matched_buckets.items():
        info = bucket_prefs.get(bucket_name, {})
        if not info:
            info = {}
        clarity = info.get("clarity", 1)
        # 锚定科目加分
        anchor_bonus = 0
        for anchor_item in info.get("anchors", info.get("anchor_accounts", [])):
            if _anchor_matches(anchor_item, match_text):
                anchor_bonus += anchor_bonus_value
                break
        scored.append((clarity + anchor_bonus, len(keywords), bucket_name))
    scored.sort(reverse=True)
    return [name for _, _, name in scored]


def run_classification(buckets: dict, bucket_cf_map: dict, training_dir: Path,
                      preferences: dict | None = None):
    """对训练数据目录中的所有记录执行业务桶分类。

    返回命中记录、未命中记录、桶统计和文件统计。
    """
    if preferences is None:
        preferences = {}

    matcher = BucketMatcher(buckets)

    hit_records = []
    miss_records = []
    bucket_counter = Counter()
    file_counter = defaultdict(lambda: {"total": 0, "hit": 0, "miss": 0})

    for record in iter_training_records(training_dir):
        result = matcher.match(record.get("match_text", record["text"]))
        file_counter[record["source_file"]]["total"] += 1

        # 锚定触发：即使关键词没命中，锚定科目命中也可以触发桶
        match_text = record.get("match_text", record["text"])
        bucket_prefs = preferences.get("buckets", {})
        for bucket_name, prefs in bucket_prefs.items():
            for anchor in prefs.get("anchors", prefs.get("anchor_accounts", [])):
                if _anchor_matches(anchor, match_text):
                    if bucket_name not in result["buckets"]:
                        result["buckets"][bucket_name] = []
                    break

        if result["buckets"]:
            file_counter[record["source_file"]]["hit"] += 1
            for bucket_name in result["buckets"].keys():
                bucket_counter[bucket_name] += 1

            # 偏好排序：核心桶在前
            ranked = _rank_buckets(result["buckets"], preferences, match_text)
            primary = ranked[0] if ranked else ""

            cf_parts = []
            for bucket_name in ranked:
                cf_items = bucket_cf_map.get(bucket_name, [])
                cf_parts.append(
                    f"{bucket_name}: {'/'.join(cf_items) if cf_items else '未映射'}"
                )

            hit_records.append({
                "ID": record["id"],
                "来源文件": record["source_file"],
                "文本内容": record["text"],
                "分录Pattern": record.get("pattern", ""),
                "命中业务桶": "、".join(ranked),
                "主分类": primary,
                "命中关键词": "、".join(result["keyword_hits"]),
                "对应现金流项目": "；".join(cf_parts),
            })
        else:
            file_counter[record["source_file"]]["miss"] += 1
            miss_records.append({
                "ID": record["id"],
                "来源文件": record["source_file"],
                "文本内容": record["text"],
                "分录Pattern": record.get("pattern", ""),
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


def load_preferences(buckets_path: Path, preferences_path: Path | None = None) -> dict:
    """加载偏好配置。

    优先从独立的 preferences.json 加载；如果不存在，则从 buckets_seed.json
    中读取 clarity/anchor_accounts（向后兼容旧格式）。
    """
    # 自动推断 preferences 路径
    if preferences_path is None:
        preferences_path = buckets_path.parent / "preferences.json"

    if preferences_path.exists():
        prefs = load_json(preferences_path)
        print(f"已加载偏好配置：{preferences_path.name}（{len(prefs.get('buckets', {}))} 个桶）")
        return prefs

    # 向后兼容：从 buckets_seed.json 读取
    buckets = load_json(buckets_path)
    fallback = {"anchor_bonus": 10, "buckets": {}}
    for name, info in buckets.items():
        if name.startswith("_"):
            continue
        entry = {}
        if "clarity" in info:
            entry["clarity"] = info["clarity"]
        if "anchor_accounts" in info:
            entry["anchors"] = info["anchor_accounts"]
        if entry:
            fallback["buckets"][name] = entry

    if fallback["buckets"]:
        print(f"未找到 preferences.json，从 buckets 文件提取偏好（{len(fallback['buckets'])} 个桶）")
    return fallback


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
    preferences = load_preferences(args.buckets)

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
    results = run_classification(buckets, bucket_cf_map, training_source, preferences)

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

    # 跨桶命中审核：统计多桶命中的组合，方便 AI 检查误匹配
    multi_bucket = Counter()
    multi_samples = defaultdict(list)
    for rec in results["hit_records"]:
        buckets_str = rec.get("命中业务桶", "")
        bucket_list = [b.strip() for b in buckets_str.split("、") if b.strip()]
        if len(bucket_list) >= 2:
            key = " + ".join(sorted(bucket_list))
            multi_bucket[key] += 1
            if len(multi_samples[key]) < 3:
                multi_samples[key].append(rec["文本内容"][:60])

    if multi_bucket:
        print(f"\n__MULTI_BUCKET__{_json.dumps({'total': sum(multi_bucket.values()), 'combos': [{'buckets': k, 'count': v, 'samples': multi_samples[k]} for k, v in multi_bucket.most_common(20)]}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
