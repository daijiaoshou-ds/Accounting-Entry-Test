# -*- coding: utf-8 -*-
"""模型能力体检 · 复核工具链（v10.1）。

三个子命令，串起「纯模型跑账 → AI 复核 → 改训练数据」：

    queue   从 unreviewed 生成复核清单（中/低全查 + 高置信按桶抽样 + 冲突全查）
    analyze 读回填后的清单，输出错因统计报告（混淆对、置信度校准、共性特征）
    apply   把复核结论落到 unreviewed / 金标准，产出可直接合并的文件

设计立场（与项目既有约定一致）：**AI 只填结构化结论，程序负责落盘**。
AI 不直接改训练数据文件，避免自由发挥和不可复现的手工编辑。

用法::

    # 1. 生成复核清单
    ./venv/Scripts/python.exe -m summary_cleaner.nn.review_tools queue \
        summary_cleaner/nn/_storage/training/unreviewed/<hash>.json \
        --high-per-bucket 20 -o _dev/review_queue.json

    # 2. AI 回填 verdict 后，出统计报告
    ./venv/Scripts/python.exe -m summary_cleaner.nn.review_tools analyze \
        _dev/review_queue.json -o _dev/error_report.md

    # 3. 应用结论（改 unreviewed + 可选同步改金标准）
    ./venv/Scripts/python.exe -m summary_cleaner.nn.review_tools apply \
        _dev/review_queue.json \
        summary_cleaner/nn/_storage/training/unreviewed/<hash>.json \
        --gold summary_cleaner/nn/_storage/training_data.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]

# 复核队列里 AI 需要回填的字段（写入清单时给空值，文档里逐一说明）
VERDICT_FIELDS = {
    "verdict": "",            # right / wrong / ambiguous / drop
    "correct_bucket": "",     # verdict=wrong 时必填
    "reason": "",             # 判错原因，一句话
    "tags": [],               # 错因标签，见工作流文档
    "action": "",             # fix_unreviewed / fix_gold / both / none
}

_TAG_HINTS = [
    "摘要无语义", "科目组合歧义", "关键词冲突", "费用六类边界",
    "投资与筹资混淆", "销售与采购混淆", "父级回退不足", "金标准错标",
    "样本不足", "复合凭证一词多义", "其他",
]


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] 已写入 {path}")


def _rid(bucket: str, subjects: List[str], summary: str) -> str:
    key = f"{bucket}|{'|'.join(sorted(subjects))}|{summary}"
    return "R" + hashlib.md5(key.encode("utf-8")).hexdigest()[:10].upper()


def _flatten(data: dict) -> List[dict]:
    """把 buckets_v2 展平成逐 record 列表，带桶与科目组合。"""
    out = []
    for bucket, groups in (data.get("buckets") or {}).items():
        for group in groups:
            subjects = list(group.get("subjects") or [])
            for rec in group.get("records") or []:
                out.append({
                    "rid": _rid(bucket, subjects, rec.get("summary", "")),
                    "bucket": bucket,
                    "subjects": subjects,
                    "summary": rec.get("summary", ""),
                    "count": rec.get("count", 1),
                    "pred": rec.get("pred") or {},
                    **VERDICT_FIELDS.copy(),
                })
    return out


def _level(pred: dict) -> str:
    """按校准概率分档；没有模型判定的（硬规则）返回 '非模型'。"""
    if not pred or (pred.get("sources") and all(
            "模型" not in s for s in pred["sources"])):
        return "非模型"
    p = pred.get("prob_cal_mean")
    if p is None:
        p = pred.get("prob_cal_min")
    if p is None:
        return "未知"
    if p >= 0.8:
        return "高"
    if p >= 0.5:
        return "中"
    return "低"


# ────────────────────────────────────────────────────────────────
# queue
# ────────────────────────────────────────────────────────────────
def cmd_queue(args):
    src = Path(args.unreviewed)
    data = _load(src)
    rows = _flatten(data)

    by_level: Dict[str, List[dict]] = defaultdict(list)
    for r in rows:
        by_level[_level(r["pred"])].append(r)

    # 必查：中/低 + 未知 + 同键判到多个桶（conflicts）
    must = [r for r in rows if _level(r["pred"]) in ("中", "低", "未知")]
    conflict_rows = [r for r in rows if (r["pred"] or {}).get("conflicts")]

    # 高置信：按桶分层抽样，每桶 N 条（不足则全取）
    rng = random.Random(args.seed)
    high_sample = []
    high_by_bucket = defaultdict(list)
    for r in rows:
        if _level(r["pred"]) == "高":
            high_by_bucket[r["bucket"]].append(r)
    for bucket, group in sorted(high_by_bucket.items()):
        n = min(args.high_per_bucket, len(group))
        high_sample.extend(rng.sample(sorted(group, key=lambda x: x["rid"]), n))

    seen, queue = set(), []
    for r in must + conflict_rows + high_sample:
        if r["rid"] in seen:
            continue
        seen.add(r["rid"])
        r["review_reason"] = (
            "conflicts（同键判到多个桶）" if r["pred"].get("conflicts")
            else f"置信度{_level(r['pred'])}，全量复核" if _level(r["pred"]) in ("中", "低", "未知")
            else f"高置信按桶抽查（{r['bucket']}）"
        )
        queue.append(r)

    payload = {
        "source_hash": data.get("fingerprint", src.stem),
        "source_file": str(src),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "instruction": (
            "逐条填写 verdict / correct_bucket / reason / tags / action。"
            "字段名与取值见工作流文档；不要增删条目、不要改 rid。"
        ),
        "tag_hints": _TAG_HINTS,
        "stats": {
            "total_records": len(rows),
            "queue_size": len(queue),
            "by_level": {k: len(v) for k, v in sorted(by_level.items())},
            "must_review": len(must),
            "conflicts": len(conflict_rows),
            "high_sampled": len(high_sample),
            "high_per_bucket": args.high_per_bucket,
            "buckets_in_high_sample": len(high_by_bucket),
        },
        "items": queue,
    }
    _dump(Path(args.output), payload)
    print(f"  总记录 {len(rows)}，入队 {len(queue)}："
          f"中/低 {len(must)} + 冲突 {len(conflict_rows)} + 高置信抽查 {len(high_sample)}")


# ────────────────────────────────────────────────────────────────
# analyze
# ────────────────────────────────────────────────────────────────
_WORD_RE = re.compile(r"[\u4e00-\u9fa5]{2,6}")


def _keywords(texts: List[str], top=8) -> List[str]:
    c = Counter()
    for t in texts:
        c.update(set(_WORD_RE.findall(t)))
    return [w for w, _ in c.most_common(top)]


def cmd_analyze(args):
    q = _load(Path(args.queue))
    items = q["items"]
    filled = [r for r in items if r.get("verdict")]
    wrong = [r for r in filled if r["verdict"] == "wrong"]
    ambiguous = [r for r in filled if r["verdict"] == "ambiguous"]
    drop = [r for r in filled if r["verdict"] == "drop"]

    lines = [
        f"# 模型复核错因报告",
        "",
        f"> 生成：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"> 来源：`{q['source_hash']}`　入队 {len(items)} 条，已回填 {len(filled)} 条",
        "",
        "## 一、总览",
        "",
        "| 项 | 条数 | 占已回填 |",
        "|---|---:|---:|",
    ]
    denom = max(len(filled), 1)
    for name, n in [("判定正确", len(filled) - len(wrong) - len(ambiguous) - len(drop)),
                    ("判错", len(wrong)), ("语义模糊", len(ambiguous)),
                    ("应剔除样本", len(drop))]:
        lines.append(f"| {name} | {n} | {n/denom:.1%} |")

    # 置信度校准评估
    lines += ["", "## 二、置信度是否可信", "",
              "| 档位 | 复核数 | 判错数 | 错判率 |", "|---|---:|---:|---:|"]
    for lvl in ["高", "中", "低", "未知"]:
        sub = [r for r in filled if _level(r["pred"]) == lvl]
        if not sub:
            continue
        w = sum(1 for r in sub if r["verdict"] == "wrong")
        lines.append(f"| {lvl} | {len(sub)} | {w} | {w/len(sub):.1%} |")
    lines += ["", "> 高置信档若错判率明显偏高，说明模型过度自信，"
              "复核时应扩大该档抽查量；低置信档错判率高则属预期。", ""]

    # 混淆对
    lines += ["## 三、混淆对（判成 X → 实为 Y）", ""]
    pairs = Counter((r["bucket"], r.get("correct_bucket") or "未填")
                    for r in wrong)
    if pairs:
        lines += ["| 模型判成 | 实际应为 | 次数 | 典型摘要 |", "|---|---|---:|---|"]
        for (x, y), n in pairs.most_common():
            sample = next((r["summary"][:34] for r in wrong
                           if r["bucket"] == x and
                           (r.get("correct_bucket") or "未填") == y), "")
            lines.append(f"| {x} | {y} | {n} | {sample} |")
    else:
        lines.append("（暂无判错样本或尚未回填）")

    # 错因标签
    lines += ["", "## 四、错因标签分布", ""]
    tags = Counter(t for r in wrong + ambiguous for t in (r.get("tags") or []))
    if tags:
        lines += ["| 标签 | 次数 |", "|---|---:|"]
        for t, n in tags.most_common():
            lines.append(f"| {t} | {n} |")
    else:
        lines.append("（未填标签）")

    # 逐混淆对给共性特征 + 建议
    lines += ["", "## 五、按混淆对的共性特征与优化方向", ""]
    for (x, y), n in pairs.most_common(12):
        sub = [r for r in wrong if r["bucket"] == x and
               (r.get("correct_bucket") or "未填") == y]
        subj = Counter("+".join(sorted(s.split("[")[0] for s in r["subjects"]))
                       for r in sub)
        kws = _keywords([r["summary"] for r in sub])
        reasons = [r.get("reason", "") for r in sub if r.get("reason")]
        lines += [
            f"### {x} → {y}（{n} 条）", "",
            f"- 高频科目组合：{'；'.join(f'{k}×{v}' for k, v in subj.most_common(3)) or '—'}",
            f"- 摘要高频片段：{'、'.join(kws) or '—'}",
            f"- 复核理由样例：{reasons[0][:80] if reasons else '—'}",
            "- 优化方向："
            + ("补该模式的正确样本并核对金标准是否有反向标注；" if n >= 3
               else "个案，改该条标签即可；"),
            "",
        ]

    lines += [
        "## 六、结论落地",
        "",
        "1. `action=fix_gold` / `both` 的条目：程序直接改金标准对应记录。",
        "2. `action=fix_unreviewed` / `both` 的条目：程序改未审文件并置 `reviewed: true`。",
        "3. 之后运行 `merge_training_data()` 把未审文件并入金标准。",
        "",
        "> 本报告只做统计与线索归集；**根因判断与训练策略由复核 AI / 用户给出**。",
        "",
    ]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 已写入 {args.output}（判错 {len(wrong)} 条，混淆对 {len(pairs)} 组）")


# ────────────────────────────────────────────────────────────────
# apply
# ────────────────────────────────────────────────────────────────
def _move_record(data: dict, rid: str, target_bucket: str, drop_it=False) -> Optional[str]:
    """在未审/金标准文件里按 rid 找 record，改桶或删除。返回旧桶名。"""
    for bucket, groups in list((data.get("buckets") or {}).items()):
        for group in groups:
            keep = []
            for rec in group.get("records") or []:
                if _rid(bucket, group.get("subjects") or [],
                        rec.get("summary", "")) != rid:
                    keep.append(rec)
                    continue
                if drop_it:
                    continue  # 删除该样本
                if target_bucket and target_bucket != bucket:
                    tgt = (data["buckets"].setdefault(target_bucket, []))
                    for g2 in tgt:
                        if g2.get("subjects") == group.get("subjects"):
                            g2["records"].append(dict(rec))
                            break
                    else:
                        tgt.append({"subjects": group.get("subjects"),
                                    "records": [dict(rec)]})
                else:
                    keep.append(rec)
            group["records"] = keep
        # 清掉空组
        data["buckets"][bucket] = [g for g in groups if g.get("records")]
        if not data["buckets"][bucket]:
            del data["buckets"][bucket]
    return None


def cmd_apply(args):
    q = _load(Path(args.queue))
    unrev_path = Path(args.unreviewed)
    unrev = _load(unrev_path)

    gold_path = Path(args.gold) if args.gold else None
    gold = _load(gold_path) if gold_path and gold_path.exists() else None
    if args.gold and gold is None:
        print(f"[WARN] 金标准不存在，跳过 fix_gold：{gold_path}")

    stat = Counter()
    for r in q["items"]:
        v = (r.get("verdict") or "").strip()
        act = (r.get("action") or "").strip() or "none"
        if not v or v == "right" or act == "none":
            stat["skip"] += 1
            continue
        target = r.get("correct_bucket") or ""
        drop_it = v == "drop"
        if v == "wrong" and not target and not drop_it:
            print(f"[WARN] {r['rid']} 判错但未填 correct_bucket，跳过")
            stat["invalid"] += 1
            continue
        if act in ("fix_unreviewed", "both"):
            _move_record(unrev, r["rid"], target, drop_it)
            stat["unreviewed"] += 1
        if act in ("fix_gold", "both") and gold is not None:
            _move_record(gold, r["rid"], target, drop_it)
            stat["gold"] += 1
        if act in ("fix_gold", "both") and gold is None:
            stat["gold_skipped"] += 1

    # 复核完成 → 允许 merge_training_data 消费
    unrev["reviewed"] = True
    unrev.setdefault("stats", {})["reviewed_at"] = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S")
    unrev.setdefault("stats", {})["review_source"] = q["source_hash"]
    _dump(unrev_path, unrev)

    if gold is not None and stat["gold"]:
        backup = gold_path.with_name(
            gold_path.stem + ".pre-review-" +
            datetime.now().strftime("%Y%m%d_%H%M%S") + gold_path.suffix)
        backup.write_text(json.dumps(_load(gold_path), ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"[OK] 金标准已备份 {backup}")
        _dump(gold_path, gold)

    print("  应用统计:", dict(stat))
    print("  下一步：调用 merge_training_data() 把已审文件并入金标准。")


def main():
    ap = argparse.ArgumentParser(description="模型能力体检 · 复核工具链")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("queue", help="生成复核清单")
    p.add_argument("unreviewed")
    p.add_argument("--high-per-bucket", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("-o", "--output", default="_dev/review_queue.json")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("analyze", help="错因统计报告")
    p.add_argument("queue")
    p.add_argument("-o", "--output", default="_dev/error_report.md")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("apply", help="应用复核结论")
    p.add_argument("queue")
    p.add_argument("unreviewed")
    p.add_argument("--gold", default=None)
    p.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
