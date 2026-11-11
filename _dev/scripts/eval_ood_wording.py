"""摘要丢弃实验的评测：看模型到底有没有学会「科目组合」。

三组测试
--------
A 措辞泛化：成本结转里 发出商品[借]+库存商品[贷] 的样本，把摘要改写成
  训练集里没出现过的说法（交货/移交/出库/发运/配送…）。这是本次翻车的场景。
B 组合优先：把摘要换成无信息的「本月合计」，只给真实科目组合 → 看模型能否
  仅凭组合判对。测的是「组合→桶」学没学会，与措辞无关。
C 常规验证：原始验证样本，确认没有为了泛化把常规准确率做坏。

用法：
    python _dev/scripts/eval_ood_wording.py <save_dir> [--tag 名字]
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from summary_cleaner.nn.training_data import (  # noqa: E402
    load_merged_records, split_records)
from summary_cleaner.nn.inference import FinanceClassifierInference  # noqa: E402

NEUTRAL = "本月合计"

# 训练集里从未出现的交货类说法
TEMPLATES = [
    "交货 - {no},{name}", "交货单 {no}", "移交 {name}", "出库单 {no}",
    "发运 {name}", "配送 {name}", "调拨出库 {no}", "转仓 {name}",
    "交客户 {name}", "交付 {name} 订单{no}",
]


def build_sets():
    records = load_merged_records()
    _, val = split_records(records)

    # A：交货族
    combos = {("发出商品[借]", "库存商品[贷]")}
    src = [r for r in records
           if r["bucket"] == "成本结转" and tuple(sorted(r["subjects"])) in
           {tuple(sorted(c)) for c in combos}]
    random.seed(7)
    picked = random.sample(src, min(120, len(src)))
    set_a = []
    for i, r in enumerate(picked):
        text = r["summary"]
        name = text.split("--")[-1][:8] if "--" in text else "某某科技有限公司"
        tpl = TEMPLATES[i % len(TEMPLATES)]
        set_a.append({
            "summary": tpl.format(no=f"C{i:06d}", name=name),
            "subjects": list(r["subjects"]),
            "bucket": r["bucket"],
        })

    # B：中性文本 + 真实组合，每桶至多 30 条
    by_bucket = defaultdict(list)
    for r in val:
        by_bucket[r["bucket"]].append(r)
    set_b = []
    for b, rs in by_bucket.items():
        for r in random.sample(rs, min(30, len(rs))):
            set_b.append({"summary": NEUTRAL,
                          "subjects": list(r["subjects"]), "bucket": r["bucket"]})

    # C：常规验证
    set_c = random.sample(val, min(2000, len(val)))
    return set_a, set_b, set_c


def evaluate(clf, items):
    ok = 0
    per = Counter()
    wrong = Counter()
    for it in items:
        pred = clf.predict(it["summary"], it["subjects"])["bucket"]
        hit = pred == it["bucket"]
        ok += hit
        per[it["bucket"]] += 1 if hit else 0
        if not hit:
            wrong[(it["bucket"], pred)] += 1
    return ok / max(len(items), 1), per, wrong


def main() -> None:
    save_dir = sys.argv[1] if len(sys.argv) > 1 else "summary_cleaner/nn/_storage"
    tag = save_dir.rstrip("/\\").split("/")[-1].split("\\")[-1]
    set_a, set_b, set_c = build_sets()
    clf = FinanceClassifierInference(storage_dir=save_dir)

    res = {}
    for name, items in (("A 交货措辞", set_a), ("B 中性文本", set_b), ("C 常规验证", set_c)):
        acc, per, wrong = evaluate(clf, items)
        res[name] = acc
        print(f"{name}: {acc:.1%}  ({len(items)} 条)")
        if name == "A 交货措辞":
            for (b, p), n in wrong.most_common(4):
                print(f"     错 → {b}/{p}: {n}")
    # B 的分桶（看哪些桶的组合没学会）
    _, per_b, _ = evaluate(clf, set_b)
    tot_b = Counter(it["bucket"] for it in set_b)
    rows = sorted(((b, per_b[b], tot_b[b]) for b in tot_b),
                  key=lambda x: x[1] / max(x[2], 1))
    print("  B 组最差 6 个桶:",
          ", ".join(f"{b}{per_b[b]}/{tot_b[b]}" for b, _, _ in rows[:6]))
    key = ["成本结转", "生产制造", "采购", "销售", "费用·办公与行政运营"]
    print("  B 组关键桶:",
          ", ".join(f"{b}{per_b[b]}/{tot_b[b]}" for b in key if b in tot_b))

    out = ROOT / "_dev" / f"exp_eval_{tag}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[保存] {out}")


if __name__ == "__main__":
    main()
