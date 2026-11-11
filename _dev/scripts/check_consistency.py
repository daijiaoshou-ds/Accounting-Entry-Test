# -*- coding: utf-8 -*-
"""桶表 / 关键词 / 现金流映射 / 金标准 四者一致性校验。

重训前置检查：这四份数据只要有一处对不上，模型输出的桶名就会在下游丢失。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAX = ROOT / "summary_cleaner/v2/assets/bucket_taxonomy_v10.json"
KW = ROOT / "summary_cleaner/v2/assets/业务桶与keyword.json"
GOLD = ROOT / "summary_cleaner/nn/_storage/training_data.json"
CFS = [ROOT / "summary-cleaner/assets/cf/bucket_to_cf.json",
       ROOT / "summary_cleaner_v3/assets/cf/bucket_to_cf.json"]

sys.path.insert(0, str(ROOT))

errors, warns = [], []


def main():
    tax = json.loads(TAX.read_text(encoding="utf-8"))
    buckets = list(tax["buckets"])
    parents = list(tax.get("parent_nodes") or {})
    aliases = {k: v for k, v in (tax.get("bucket_aliases") or {}).items()
               if not k.startswith("_")}

    # 1 registry 派生
    from summary_cleaner.v2.config import BUCKET_REGISTRY
    reg = list(BUCKET_REGISTRY)
    missing = [b for b in buckets + parents if b not in reg]
    extra = [b for b in reg if b not in buckets + parents]
    if missing:
        errors.append(f"注册表缺桶: {missing}")
    if extra:
        warns.append(f"注册表多出: {extra}")

    # 2 keyword 资产
    kw = json.loads(KW.read_text(encoding="utf-8"))
    kw_names = [k for k in kw if not k.startswith("_")]
    miss_kw = [b for b in buckets + parents if b not in kw_names]
    if miss_kw:
        errors.append(f"keyword 资产缺桶: {miss_kw}")
    extra_kw = [b for b in kw_names if b not in buckets + parents]
    if extra_kw:
        warns.append(f"keyword 资产多出: {extra_kw}")

    # 3 现金流映射
    for f in CFS:
        if not f.exists():
            warns.append(f"缺文件 {f.relative_to(ROOT)}")
            continue
        m = json.loads(f.read_text(encoding="utf-8"))["map"]
        miss = [b for b in buckets + parents if b not in m]
        if miss:
            errors.append(f"{f.parent.parent.parent.name}/bucket_to_cf 缺: {miss}")

    # 4 金标准桶名
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    gb = list(gold["buckets"])
    unknown = [b for b in gb if b not in buckets + parents]
    if unknown:
        errors.append(f"金标准有桶表外桶名: {unknown}")
    stale = [b for b in gb if b in aliases]
    if stale:
        errors.append(f"金标准仍有旧桶名（应已归一）: {stale}")

    # 5 硬规则桶不应在训练集
    from summary_cleaner.nn.data import HARD_RULE_BUCKETS
    hard_in_gold = [b for b in gb if b in HARD_RULE_BUCKETS]
    if hard_in_gold:
        cnt = {b: sum(r.get("count", 1) for g in gold["buckets"][b] for r in g["records"])
               for b in hard_in_gold}
        warns.append(f"硬规则桶仍在训练集（模型不需要学）: {cnt}")

    # 6 空桶
    empty = [b for b in gb
             if not any(g["records"] for g in gold["buckets"][b])]
    if empty:
        warns.append(f"空桶: {empty}")

    # 7 别名能否被 merge_alias_probs 正确重定向
    from summary_cleaner.v2.bucket_rules import merge_alias_probs
    for old, new in aliases.items():
        probs = {old: 0.9, new: 0.05, "采购": 0.05}
        merge_alias_probs(probs)
        if probs.get(new, 0) < 0.9:
            errors.append(f"别名重定向失效: {old} -> {new}（得到 {probs.get(new)}）")

    print(f"桶表 {len(buckets)} + 父级 {len(parents)} = {len(buckets)+len(parents)}")
    print(f"金标准 {len(gb)} 桶 / {gold['stats']['total_records']} 记录")
    for w in warns:
        print(f"[WARN] {w}")
    for e in errors:
        print(f"[ERROR] {e}")
    print("\n结果:", "通过" if not errors else f"{len(errors)} 项错误")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
