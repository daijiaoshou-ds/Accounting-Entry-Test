#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练变更日志脚本

AI 在修改 buckets_seed.json 后调用此脚本，程序自动写入结构化日志。
AI 不需要手动拼 JSON —— 传参数即可。
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def load_log(log_path: Path) -> dict:
    """加载现有日志文件，如果不存在或格式异常则返回空结构。"""
    if not log_path.exists():
        return {"log": []}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return {"log": []}


def save_log(log_data: dict, log_path: Path):
    """保存日志文件。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)


def append_entry(log_path: Path, file_trained: str, action: str,
                 bucket: str, details: str,
                 hit_rate_before: str = "", hit_rate_after: str = ""):
    """向日志追加一条训练变更记录。"""
    log_data = load_log(log_path)

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "file_trained": file_trained,
        "action": action,
        "target_bucket": bucket,
        "details": details,
        "hit_rate_before": hit_rate_before,
        "hit_rate_after": hit_rate_after,
    }

    log_data.setdefault("log", []).append(entry)
    save_log(log_data, log_path)

    print(f"日志已记录：{action} → {bucket}")
    print(f"  训练文件：{file_trained}")
    print(f"  详情：{details}")
    if hit_rate_before:
        print(f"  命中率变化：{hit_rate_before} → {hit_rate_after or '待训练'}")


def main():
    parser = argparse.ArgumentParser(
        description="追加训练变更日志"
    )
    parser.add_argument("--file", type=str, required=True,
                        help="训练的文件名（如：公司A_序时账）")
    parser.add_argument("--action", type=str, required=True,
                        choices=["add_keywords", "new_bucket", "remove_bucket", "merge_buckets"],
                        help="操作类型")
    parser.add_argument("--bucket", type=str, required=True,
                        help="操作的业务桶名称")
    parser.add_argument("--details", type=str, required=True,
                        help="变更描述（如：新增关键词: 网约车, 打车费）")
    parser.add_argument("--hit-rate-before", type=str, default="",
                        help="修改前命中率（如：85.2%）")
    parser.add_argument("--hit-rate-after", type=str, default="",
                        help="修改后命中率（默认留空表示待训练）")
    parser.add_argument("--log-path", type=Path,
                        default=Path("summary-cleaning/assets/training_log.json"),
                        help="日志文件路径（默认：summary-cleaning/assets/training_log.json）")
    args = parser.parse_args()

    append_entry(
        log_path=args.log_path,
        file_trained=args.file,
        action=args.action,
        bucket=args.bucket,
        details=args.details,
        hit_rate_before=args.hit_rate_before,
        hit_rate_after=args.hit_rate_after,
    )


if __name__ == "__main__":
    main()
