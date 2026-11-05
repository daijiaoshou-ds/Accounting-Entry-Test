# -*- coding: utf-8 -*-
"""把金标准标签从旧 18 桶重写为新 33 桶（v10），供重新训练使用。

做法：
  1. 备份原 training_data.json → training_data_v9_18buckets.json（只备份一次）
  2. 逐条按 bucket_bridge.map_record 重算新桶
  3. 只保留配置中 train=true 的桶（规则桶不进训练集）
  4. 按 (新桶, 科目组合) 聚合，同摘要 count 累加
  5. 覆盖写回 training_data.json，并在 stats 里记录重建信息

运行：python summary_cleaner/nn/rebuild_training_data.py [--dry-run]
"""

from __future__ import annotations

import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

_THIS = Path(__file__).resolve()
ROOT = _THIS.parents[2]
sys.path.insert(0, str(ROOT))

from summary_cleaner.v2.bucket_bridge import parse_subjects, map_record  # noqa: E402

CFG_PATH = ROOT / "summary_cleaner" / "v2" / "assets" / "bucket_taxonomy_v10.json"
SRC = _THIS.parent / "_storage" / "training_data.json"
BACKUP = _THIS.parent / "_storage" / "training_data_v9_18buckets.json"


def main(dry_run: bool = False):
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    data = json.loads(SRC.read_text(encoding="utf-8"))

    # 训练集 = 所有可输出桶 + 父级「费用」（作为"未细分"类别）
    # 只排除：兜底桶、硬规则桶（程序写死，无学习价值）
    HARD_EXCLUDE = {"待复核", "资金内部往来", "汇兑损益", "期末结转"}
    trainable = {n for n in cfg["buckets"] if n not in HARD_EXCLUDE}
    trainable.add("费用")  # 父级：模型学会"这是费用，但分不清哪类"

    # 新桶 → 科目组合 → 摘要 → count
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    dropped = Counter()
    old_total = 0

    for old_b, groups in data["buckets"].items():
        for g in groups:
            subjects = g.get("subjects", [])
            key = tuple(subjects)
            names, dirs = parse_subjects(subjects)
            for rec in g.get("records", []):
                old_total += 1
                summary = str(rec.get("summary", ""))
                cnt = int(rec.get("count", 1) or 1)
                nb, _why = map_record(old_b, names, dirs, summary, cfg)
                if nb not in trainable:
                    dropped[nb] += 1
                    continue
                grouped[nb][key][summary] += cnt

    # 组装
    buckets = {}
    for nb, by_subj in grouped.items():
        groups = []
        for key, summ in by_subj.items():
            records = [{"summary": s, "count": c} for s, c in summ.items()]
            records.sort(key=lambda r: -r["count"])
            groups.append({"subjects": list(key), "records": records})
        groups.sort(key=lambda g: -sum(r["count"] for r in g["records"]))
        buckets[nb] = groups

    # 统计
    stat = []
    for nb, groups in buckets.items():
        n = sum(len(g["records"]) for g in groups)
        c = sum(r["count"] for g in groups for r in g["records"])
        stat.append((nb, n, c))
    stat.sort(key=lambda x: -x[1])

    # 长尾只提示、不剔除：不足 30 条的由 augment_training_data.py 补样本，
    # 补完一起进训练（33 桶里真正不进训练的只有期末结转/资金内部往来/汇兑损益）
    MIN_SAMPLES = 30
    long_tail = [(nb, n) for nb, n, c in stat if n < MIN_SAMPLES]

    new_total = sum(s[1] for s in stat)
    new_count = sum(s[2] for s in stat)

    print(f"原始 {old_total:,} 条 → 入训练集 {new_total:,} 条（count {new_count:,}）")
    print(f"桶数：{len(buckets)}（可输出桶 + 费用父级）\n")
    print(f"{'桶':<22}{'摘要':>7}{'count':>9}  标记")
    for nb, n, c in stat:
        flag = "  <50 长尾" if n < 50 else ("  <100" if n < 100 else "")
        print(f"{nb:<22}{n:>7,}{c:>9,}{flag}")

    empty = sorted(trainable - set(buckets) - {nb for nb, _ in long_tail})
    if empty:
        print(f"\n无样本的训练桶（需补数据）：{'、'.join(empty)}")

    if long_tail:
        print(f"\n长尾剔除（<{MIN_SAMPLES} 条，改由规则判）：")
        for nb, n in sorted(long_tail, key=lambda x: -x[1]):
            print(f"  {nb:<22}{n:>5,} 条")

    if dropped:
        print(f"\n未入训练集的桶（规则桶/待定）：")
        for k, v in dropped.most_common():
            print(f"  {k:<22}{v:>6,} 条")

    if dry_run:
        print("\n[dry-run] 未写入文件")
        return

    # 备份（只备份一次，避免把已重建的文件当原始备份）
    if not BACKUP.exists():
        shutil.copy2(SRC, BACKUP)
        print(f"\n已备份原文件 → {BACKUP.name}")
    else:
        print(f"\n备份已存在，跳过：{BACKUP.name}")

    data["buckets"] = buckets
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["stats"] = {
        "total_records": new_total,
        "raw_records": old_total,
        "buckets": len(buckets),
        "sim_deduped": data.get("stats", {}).get("sim_deduped", 0),
    }
    data["rebuild"] = {
        "from": "18 桶（v9）",
        "to": "33 桶（v10，仅 train=true）",
        "config": CFG_PATH.name,
        "at": data["updated_at"],
    }

    SRC.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入 {SRC}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main(dry_run="--dry-run" in sys.argv)
