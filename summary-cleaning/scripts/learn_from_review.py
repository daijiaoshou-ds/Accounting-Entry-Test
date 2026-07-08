#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从用户修正中学习

用法：
  1. dry-run（默认）：分析修正文件，输出结构化 JSON 给 AI 看
     python learn_from_review.py 已分类.xlsx

  2. apply：实际修改 buckets_seed.json + 写入 review 笔记
     python learn_from_review.py 已分类.xlsx --apply

AI 工作流：
  用户 review 完 → AI 跑 dry-run → 读 JSON → 向用户确认 → 跑 --apply
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

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    except Exception:
        pass

REVIEW_NOTES_PATH = Path(__file__).parent.parent / "assets" / "review_notes.md"
CJK_WORD = re.compile(r'[一-鿿]{2,6}')


def extract_new_keywords(texts, min_len=2):
    """从修正文本中提取候选关键词。"""
    kw_counter = defaultdict(int)
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        for match in CJK_WORD.finditer(str(text)):
            word = match.group()
            if len(word) >= min_len and not word.isdigit():
                kw_counter[word] += 1
    return kw_counter


def analyze(reviewed_path: Path) -> dict:
    """分析用户修正文件，返回结构化结果。"""
    df = pd.read_excel(reviewed_path, engine="openpyxl")

    if "用户修正" not in df.columns:
        return {"error": "文件中没有'用户修正'列"}

    corrected = df[
        df["用户修正"].notna() &
        (df["用户修正"].astype(str).str.strip() != "")
    ]
    if len(corrected) == 0:
        return {"correction_count": 0, "corrections": []}

    # 按 (原分类 → 修正后) 分组
    corrections = defaultdict(list)
    for _, row in corrected.iterrows():
        original = str(row.get("摘要分类", "")).strip()
        corrected_to = str(row["用户修正"]).strip()
        summary = str(row.get("摘要", row.get("文本内容", "")))
        reason = str(row.get("修正原因", ""))
        key = f"{original} → {corrected_to}"
        corrections[key].append({
            "summary": summary,
            "reason": reason,
        })

    # 构建结构化结果
    result = {
        "reviewed_file": str(reviewed_path.name),
        "correction_count": len(corrected),
        "corrections": [],
    }

    for key, items in sorted(corrections.items(), key=lambda x: len(x[1]), reverse=True):
        orig, target = key.split(" → ", 1)
        texts = [item["summary"] for item in items]
        reasons = [
            item["reason"] for item in items
            if item["reason"] and str(item["reason"]).strip()
        ]
        new_kws = Counter(extract_new_keywords(texts))
        suggested_kws = [
            kw for kw, freq in new_kws.most_common(10) if freq >= 2
        ]

        result["corrections"].append({
            "original_bucket": orig,
            "corrected_bucket": target,
            "count": len(items),
            "reasons": reasons[:5],
            "sample_summaries": texts[:3],
            "suggested_keywords": suggested_kws[:8],
        })

    return result


def apply_changes(analysis: dict, buckets_path: Path) -> list:
    """应用修改到 buckets_seed.json，返回实际修改列表。"""
    if analysis.get("error") or analysis.get("correction_count", 0) == 0:
        return []

    with open(buckets_path, "r", encoding="utf-8") as f:
        buckets = json.load(f)

    applied = []
    for corr in analysis["corrections"]:
        bucket = corr["corrected_bucket"]
        if bucket not in buckets:
            continue
        existing = set(buckets[bucket].get("keywords", []))
        added = [kw for kw in corr["suggested_keywords"] if kw not in existing]
        if added:
            buckets[bucket]["keywords"].extend(added)
            applied.append({
                "bucket": bucket,
                "added_keywords": added,
            })

    if applied:
        with open(buckets_path, "w", encoding="utf-8") as f:
            json.dump(buckets, f, ensure_ascii=False, indent=2)

    return applied


def write_review_note(analysis: dict, applied: list):
    """追加固定范式的 review 笔记到 assets/review_notes.md。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = analysis.get("reviewed_file", "未知文件")
    total = analysis.get("correction_count", 0)

    lines = [
        f"## {now}",
        "",
        f"- **review 文件**: {filename}",
        f"- **修正条数**: {total} 条",
        "",
    ]

    if analysis.get("corrections"):
        lines.append("### 修正明细")
        lines.append("")
        lines.append("| 原分类 | 修正为 | 数量 | 用户原因 | 新增关键词 |")
        lines.append("|--------|--------|------|----------|-----------|")
        for corr in analysis["corrections"]:
            reasons = "；".join(corr.get("reasons", [])[:2]) or "-"
            kws = "、".join(corr.get("suggested_keywords", [])[:5]) or "-"
            lines.append(
                f"| {corr['original_bucket']} | {corr['corrected_bucket']} "
                f"| {corr['count']} | {reasons} | {kws} |"
            )
        lines.append("")

    if applied:
        lines.append("### 已应用的修改")
        lines.append("")
        for a in applied:
            kws = "、".join(a["added_keywords"])
            lines.append(f"- **{a['bucket']}** 新增关键词: {kws}")
        lines.append("")

    # 追加到文件
    REVIEW_NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if REVIEW_NOTES_PATH.exists():
        existing = REVIEW_NOTES_PATH.read_text(encoding="utf-8")

    # 如果文件为空或只有标题，加标题
    if not existing.strip() or not existing.startswith("# 用户修正记录"):
        header = "# 用户修正记录\n\n> 每次用户 review 后自动追加。按时间倒序。\n\n"
        existing = header + existing

    REVIEW_NOTES_PATH.write_text(existing.rstrip() + "\n\n" + "\n".join(lines), encoding="utf-8")
    print(f"review 笔记已追加: {REVIEW_NOTES_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="从用户 review 中学习：分析修正 → 更新 buckets → 记录笔记"
    )
    parser.add_argument("reviewed_file", type=Path, help="用户 review 过的导出文件")
    parser.add_argument("--buckets", type=Path,
                        default=Path("summary-cleaning/assets/buckets_seed.json"),
                        help="buckets_seed.json 路径")
    parser.add_argument("--apply", action="store_true",
                        help="实际应用修改并写笔记（默认仅分析）")
    args = parser.parse_args()

    if not args.reviewed_file.exists():
        print(json.dumps({"error": f"文件不存在: {args.reviewed_file}"}, ensure_ascii=False))
        sys.exit(1)

    # 1. 分析
    analysis = analyze(args.reviewed_file)

    if args.apply:
        # 2. 应用修改
        applied = apply_changes(analysis, args.buckets)
        analysis["applied"] = applied
        # 3. 写笔记
        if applied:
            write_review_note(analysis, applied)
        else:
            print("没有需要应用的关键词修改。")

    # 4. 输出结构化 JSON（AI 直接读）
    print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
