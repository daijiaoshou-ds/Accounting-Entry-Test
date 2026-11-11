"""列出「模型不认识的科目」——维护别名表用。

用法：
    # 看最近一次跑账的未审文件
    python _dev/scripts/list_unknown_subjects.py

    # 指定文件
    python _dev/scripts/list_unknown_subjects.py path/to/xxx.json

输出：归一化之后仍然不认识的科目，按出现次数排序，
并直接打印可以粘进 `summary_cleaner/nn/assets/subject_aliases.json` 的片段。
别名表改完**不用重训**，下次跑账即生效（subject_norm 有缓存，进程重启即可）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from summary_cleaner.nn.subject_norm import normalize_subject  # noqa: E402

UNREVIEWED = ROOT / "summary_cleaner/nn/_storage/training/unreviewed"
VOCAB = ROOT / "summary_cleaner/nn/_storage/subject_to_index.json"


def latest_unreviewed() -> Path:
    files = sorted(UNREVIEWED.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"{UNREVIEWED} 下没有未审文件，先跑一次账")
    return files[-1]


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_unreviewed()
    known = set(json.loads(VOCAB.read_text(encoding="utf-8")))
    data = json.loads(path.read_text(encoding="utf-8"))

    tokens: Counter = Counter()
    total = 0
    for groups in data["buckets"].values():
        for group in groups:
            for rec in group["records"]:
                rel = rec.get("count", 1)
                total += rel
                for s in group["subjects"]:
                    tokens[s] += rel

    def resolve(tok: str) -> str:
        norm = normalize_subject(tok)
        if norm in known or tok not in known:
            return norm
        return tok

    unknown: Counter = Counter()
    for tok, n in tokens.items():
        if resolve(tok) not in known:
            unknown[tok] += n

    print(f"文件: {path.name}")
    print(f"凭证 {total} 条 / 科目令牌 {len(tokens)} 种 / 归一化后仍未知 {len(unknown)} 种"
          f"（{sum(unknown.values())} 次）\n")
    if not unknown:
        print("全部认识，别名表不用动。")
        return

    print(f"{'次数':>6}  科目令牌")
    for tok, n in unknown.most_common():
        print(f"{n:>6}  {tok}")

    names = sorted({tok.split("[")[0].strip() for tok in unknown})
    print("\n# 候选别名（右边请改成系统里已有的标准一级科目，认不准的留空删掉）")
    print(json.dumps({n: "" for n in names}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
