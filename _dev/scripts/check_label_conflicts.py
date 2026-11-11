# -*- coding: utf-8 -*-
"""标注一致性检查：同一个键是否被标成了不同桶。

两种键：
  L0  精确键（去空白后的归一化摘要 + 科目组合）—— 必须唯一，否则是重复/矛盾样本
  L1  数字掩码键（把数字换成 # 后再比）—— 允许有冲突，但冲突组就是"缓存不能命中"
      的清单，同时往往暴露真错标

用法：
    ./venv/Scripts/python.exe _dev/scripts/check_label_conflicts.py
    ./venv/Scripts/python.exe _dev/scripts/check_label_conflicts.py --full
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

GOLD = Path("summary_cleaner/nn/_storage/training_data.json")


def L0(s: str) -> str:
    return re.sub(r"\s+", "", s)


def L1(s: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[0-9０-９]+(\.[0-9]+)?", "#", s))


def main() -> None:
    full = "--full" in sys.argv
    data = json.loads(GOLD.read_text(encoding="utf-8"))

    for level, fn in (("L0 精确", L0), ("L1 掩码", L1)):
        index = defaultdict(lambda: defaultdict(int))
        sample = {}
        for bucket, groups in data["buckets"].items():
            for g in groups:
                subjects = tuple(sorted(g["subjects"]))
                for rec in g["records"]:
                    key = (fn(rec["summary"]), subjects)
                    index[key][bucket] += rec.get("count", 1)
                    sample.setdefault(key, rec["summary"])

        conflicts = {k: v for k, v in index.items() if len(v) > 1}
        total = sum(sum(v.values()) for v in conflicts.values())
        print(f"\n=== {level}：冲突 {len(conflicts)} 组 / {total} count ===")
        rows = sorted(conflicts.items(), key=lambda x: -sum(x[1].values()))
        for key, dist in rows[: (len(rows) if full else 8)]:
            print(f"\n  {sample[key][:56]!r}")
            print(f"    科目 {key[1]}")
            print(f"    标签 {dict(dist)}")

    print("\n注：L1 冲突组 = 缓存 C2 层不可命中的键；其中一边明显违反口径的即为错标。")


if __name__ == "__main__":
    main()
