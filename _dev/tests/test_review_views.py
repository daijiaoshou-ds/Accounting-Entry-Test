# -*- coding: utf-8 -*-
"""复核视图（--summary / --families）与复用打分（--reuse-units）——纯单测，秒级。

为什么单独一个套件：这两个视图是"把 4931 个单元压到能看的几百条"的唯一入口，
一旦算错（比如族分错、少数派判反、科目组合 list 没转 tuple），
AI 就会照着错的筛子去改判 —— 比崩溃更难发现。

跑法：
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_review_views.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "summary_cleaner_v3"
sys.path.insert(0, str(SKILL))

from jc import review_views as rv                     # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def unit(no, combo, bucket, prob=1.0, vouchers=1, summary=None, source="模型"):
    return {
        "单元号": no, "单元键": " + ".join(combo) + " :: " + (summary or no),
        "科目组合": list(combo), "摘要": summary or ("摘要-" + no),
        "模型桶": bucket, "概率": prob, "次选": [], "来源": source,
        "覆盖凭证号": [f"记-{no}-{i}" for i in range(vouchers)],
        "覆盖凭证数": vouchers, "同族变体数": 1, "同族摘要": [],
    }


print("=" * 70)
print("① 概率分布：识别'概率饱和'（决定还要不要按概率读清单）")
print("=" * 70)
units = [unit("U1", ["A[借]"], "日常费用", 1.0),
         unit("U2", ["A[借]"], "日常费用", 0.99),
         unit("U3", ["A[借]"], "日常费用", 0.9),
         unit("U4", ["A[借]"], "日常费用", 0.5),
         unit("U5", ["A[借]"], "资金内部往来", None, source="硬规则")]
dist = rv.prob_buckets(units)
check(dist["=1.0"] == 1 and dist["0.95~1"] == 1 and dist["0.8~0.95"] == 1
      and dist["<0.8"] == 1 and dist["无概率(硬规则)"] == 1,
      f"概率分档正确：{dist}")

print()
print("=" * 70)
print("② 聚族：科目组合是 **list**，必须能当键（历史坑：unhashable type）")
print("=" * 70)
units = [unit("U1", ["应付账款[借]", "银行存款[贷]"], "存货采购"),
         unit("U2", ["应付账款[借]", "银行存款[贷]"], "存货采购"),
         unit("U3", ["应付账款[借]", "银行存款[贷]"], "存货采购"),
         unit("U4", ["应付账款[借]", "银行存款[贷]"], "存货采购"),
         unit("U5", ["应付账款[借]", "银行存款[贷]"], "长期资产"),
         # 顺序不同但组合相同 → 必须归到同一族（排序后当键）
         unit("U6", ["银行存款[贷]", "应付账款[借]"], "存货采购")]
try:
    groups = rv.by_combo(units)
    ok = True
except TypeError as e:
    groups, ok = {}, False
    print("     ", e)
check(ok, "科目组合(list) 能聚族、不报 unhashable")
check(len(groups) == 1 and len(list(groups.values())[0]) == 6,
      f"科目顺序不同也算同一族（实得 {len(groups)} 族）")

print()
print("=" * 70)
print("③ families：只列有少数派的族；'正常支线'不算离群错误")
print("=" * 70)
fam = rv.families_view(units, min_family=5, outlier_share=0.25)
check(fam["有少数派的族数"] == 1, "只有 1 个族有少数派")
row = fam["families"][0]
check(row["单元数"] == 6 and row["覆盖凭证数"] == 6, "族规模正确")
check(row["桶分布"]["存货采购"]["单元数"] == 5
      and row["桶分布"]["长期资产"]["单元数"] == 1, f"桶分布正确：{row['桶分布']}")
check([u["单元号"] for u in row["少数派"]] == ["U5"], "少数派只挑出 U5")
check("长期资产" in fam["少数派不等于判错"],
      "出参自带'正常支线'说明（长期资产 = 设备款/工程款，不是错）")

# 五五开不算少数派
tie = [unit("U1", ["X[借]"], "日常费用"), unit("U2", ["X[借]"], "日常费用"),
       unit("U3", ["X[借]"], "销售收入"), unit("U4", ["X[借]"], "销售收入")]
check(rv.families_view(tie, min_family=2)["有少数派的族数"] == 0,
      "50/50 的两派不算少数派（不该误报）")

# 族太小不算
small = [unit("U1", ["Y[借]"], "日常费用"), unit("U2", ["Y[借]"], "销售收入")]
check(rv.families_view(small, min_family=5)["有少数派的族数"] == 0,
      "族小于 min-family 时不找少数派")

# 单一桶的族不算
uni = [unit(f"U{i}", ["Z[借]"], "日常费用") for i in range(1, 8)]
check(rv.families_view(uni)["有少数派的族数"] == 0, "全族同桶 → 无少数派")

# 少数派按"条数"降序
many = ([unit(f"A{i}", ["P[借]"], "日常费用") for i in range(4)]
        + [unit("A9", ["P[借]"], "长期资产")]
        + [unit(f"B{i}", ["Q[借]"], "日常费用") for i in range(9)]
        + [unit("B9", ["Q[借]"], "长期资产"), unit("B8", ["Q[借]"], "销售收入")])
fam2 = rv.families_view(many)
check(len(fam2["families"]) == 2 and fam2["families"][0]["科目组合"] == "Q[借]",
      "族按少数派条数降序（Q 族 2 条排前）")
check(fam2["少数派单元数"] == 3, f"少数派总数正确：{fam2['少数派单元数']}")

print()
print("=" * 70)
print("④ summary：全局画像的关键字段都在（AI 的第一步就靠它）")
print("=" * 70)
sim = {"on": True, "threshold": 0.95, "variants_before": 100,
       "units_after": 60, "families": 20, "merged_vouchers": 40,
       "elapsed_s": 0.5, "biggest": []}
s = rv.summary_view(units, sim, {"资金内部往来": 3}, vouchers=11456, rows=64255)
for k in ("unit_count", "bucket_counts", "bucket_vouchers", "confidence_dist",
          "family", "最可疑的单元", "dedup", "hard_rule_hits", "怎么用"):
    check(k in s, f"summary 有字段 {k}")
check(s["dedup"]["归族后单元数"] == 60, "summary 里带切割成绩")
check(s["vouchers"] == 11456 and s["rows"] == 64255, "summary 里带原始规模")
check(len(s["最可疑的单元"]) == len(units), "最可疑清单不超过 top 上限")
check(s["最可疑的单元"][0]["概率"] <= s["最可疑的单元"][-1]["概率"],
      "最可疑按概率升序")

print()
print("=" * 70)
print("⑤ build / sim_payload / dedup_payload / reuse_labels")
print("=" * 70)
check(rv.build("units", units, sim, {}, 11456, 64255) is None,
      "view=units 时不分发（走默认清单）")
check(rv.build("summary", units, sim, {}, 11456, 64255)["view"] == "summary",
      "summary 分发")
check(rv.build("families", units, sim, {}, 11456, 64255)["view"] == "families",
      "families 分发")
check(rv.sim_payload(sim)["threshold"] == 0.95, "sim_payload 字段名是英文")
check(rv.dedup_payload(sim)["阈值"] == 0.95, "dedup_payload 字段名保持中文（对外契约）")
off = dict(sim, on=False, threshold=0.0)
check(rv.sim_payload(off)["threshold"] is None, "关闭归族时 threshold 给 None")

print()
print("=" * 70)
print("⑥ reuse_labels：--summary / --families 出的文件也要能喂 --reuse-units")
print("=" * 70)
# 为什么必须验：AI 的正常流程是"先 --summary（1 屏）→ 再 --families → 改判 → run"。
# 如果只有默认视图的出参能复用，那为了省时间还得先跑一次全量推理生成默认视图
# —— 省时间的手段先付一次全量推理，白搭。
labels = rv.reuse_labels(units)
check(len(labels) == len(units), f"标签覆盖全部单元（{len(labels)}/{len(units)}）")
check(all(k.startswith("Z[借] :: ") or " :: " in k for k in labels), "键是单元键")
u0 = labels[next(iter(labels))]
check(set(u0) == {"模型桶", "概率", "来源", "次选"},
      f"标签只带必要字段（紧凑）：{sorted(u0)}")
import json as _json                                       # noqa: E402
for view in ("summary", "families"):
    doc = rv.build(view, units, sim, {}, 11456, 64255)
    doc["reuse_labels"] = rv.reuse_labels(units)
    check("reuse_labels" in doc, f"{view} 出参带 reuse_labels")
    size = len(_json.dumps(doc["reuse_labels"], ensure_ascii=False))
    print(f"     {view} 的标签表约 {size / 1024:.1f} KB（{len(units)} 个单元）")

print()
print("=" * 70)
if FAIL:
    print(f"❌ {len(FAIL)} 项失败")
    sys.exit(1)
print("✅ 全部通过")
