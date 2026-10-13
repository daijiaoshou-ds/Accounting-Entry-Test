# -*- coding: utf-8 -*-
"""序时账清洗 3.0 — 对外唯一命令行入口（AI 用它，不碰内部模块）

这是整个 skill 的「排插」：所有能力都从这里进出。

    status  环境自检：模型 4 件交付物 / torch / GPU / 桶数
    check   读文件 + 认列名 + 校验字段（不加载模型、不写数据）
    run     完整清洗：ETL → 硬规则 → 模型打分 → 写回 → 导出
    sample  从结果里抽 N 条指定置信度的凭证，供 AI 抽样复核

约定：**stdout 输出单行 JSON（前缀 `RESULT `）**，进度信息走 stderr。
AI 解析时只认 `RESULT ` 开头那一行。

用法：
    python -m jc.cli status
    python -m jc.cli check  "序时账.xlsx"
    python -m jc.cli run    "序时账.xlsx" --out "输出目录"
    python -m jc.cli sample "输出\\序时账_分类结果.xlsx" --confidence 高 --n 5
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .config import (ALL_BUCKETS, FIELD_LABELS, HARD_RULE_BUCKETS, MODEL_DIR,
                     RELEASE_NAME, __version__)
from .pipeline import check_file, run_file


# ============================================================================
# 输出约定
# ============================================================================

def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def _emit(payload: Dict[str, Any]):
    print("RESULT " + json.dumps(payload, ensure_ascii=False, default=str))


def _load_json(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ============================================================================
# status
# ============================================================================

def cmd_status(args) -> int:
    """环境自检：模型是否齐全、torch / GPU 情况、桶清单。"""
    from .model import artifacts_status, model_ready, pick_device

    art = artifacts_status()
    try:
        import torch
        torch_ok = True
        cuda = torch.cuda.is_available()
    except ImportError:
        torch_ok, cuda = False, False

    meta = {}
    ckpt = MODEL_DIR / "finance_classifier.pt"
    if ckpt.exists():
        try:
            import torch
            ck = torch.load(ckpt, map_location="cpu", weights_only=True)
            meta = {
                "best_val_acc": ck.get("best_val_acc"),
                "trained_at": ck.get("trained_at"),
                "num_buckets": ck.get("num_buckets"),
                "num_subjects": ck.get("num_subjects"),
                "encoder_model": ck.get("encoder_model"),
                "total_records": ck.get("total_records"),
            }
        except Exception as e:
            meta = {"read_error": f"{type(e).__name__}: {e}"}

    ready = model_ready()
    device = pick_device() if torch_ok else "cpu"

    # 现金流映射表自检（缺表不影响清洗，但 run 时编不出现金流量表）
    cf_info = {}
    try:
        from . import cashflow
        cf_info = cashflow.validate()
    except Exception as e:
        cf_info = {"error": f"{type(e).__name__}: {e}"}

    _emit({
        "command": "status",
        "release": RELEASE_NAME,
        "version": __version__,
        "model_dir": str(MODEL_DIR),
        "model_ready": ready,
        "artifacts": art,
        "model_info": meta,
        "torch_installed": torch_ok,
        "cuda_available": cuda,
        "inference_device_will_be": f"{device}(" + ("fp16" if device == "cuda" else "int8") + ")",
        "buckets": ALL_BUCKETS,
        "bucket_count": len(ALL_BUCKETS),
        "model_bucket_count": len(ALL_BUCKETS) - len(HARD_RULE_BUCKETS),
        "hard_rule_buckets": HARD_RULE_BUCKETS,
        "cashflow": cf_info,
        "next": ("模型就绪，可以 check / run" if ready else
                 "模型不齐：无法分类（3.0 的 18 个业务桶全部依赖模型）。"
                 "请按 SKILL.md 第 1 步下载 4 件交付物到 assets/model/"),
    })
    return 0 if ready else 3


# ============================================================================
# check
# ============================================================================

def cmd_check(args) -> int:
    res = check_file(args.file, override=_load_json(args.set))
    _emit({
        "command": "check",
        "ok": res["ok"],
        "file": res["file"],
        "rows": res["rows"],
        "columns": res["columns"],
        "detected_mapping": res["mapping"],
        "preview": res["preview"],
        "missing_required": res["missing_required"],
        "missing_required_cn": res["missing_required_cn"],
        "unknown_overrides": res["unknown_overrides"],
        "next": ("可以 run" if res["ok"] else
                 "自动检测有缺漏：生成 --set 映射 JSON 后重跑 check 确认"),
    })
    return 0 if res["ok"] else 2


# ============================================================================
# run
# ============================================================================

def cmd_run(args) -> int:
    override = _load_json(args.set)
    try:
        stats = run_file(
            args.file,
            out_dir=args.out,
            override=override,
            chunk_size=args.chunk_size,
            progress=_log,
        )
    except Exception as e:
        _emit({"command": "run", "ok": False, "stage": "运行",
               "error": f"{type(e).__name__}: {e}"})
        return 1

    _emit({
        "command": "run",
        "ok": True,
        "file": str(Path(args.file).resolve()),
        "rows": stats["total_rows"],
        "vouchers": stats["total_vouchers"],
        "classified": stats["classified"],
        "unclassified": stats["unclassified"],
        "coverage": stats["coverage"],
        "elapsed_seconds": stats["elapsed_seconds"],
        "device": stats["model"].get("device"),
        "precision": stats["model"].get("precision"),
        "model_info": {"best_val_acc": stats["model"].get("best_val_acc")},
        "model_vouchers": stats["model_vouchers"],
        "unique_inputs": stats["unique_inputs"],
        "model_calls_saved": stats["model_calls_saved"],
        "hard_rule_hits": stats["hard_rule_hits"],
        "confidence_dist": stats["confidence_dist"],
        "bucket_counts": stats["bucket_counts"],
        "amount_by_bucket": stats["amount_by_bucket"],
        "cashflow": stats.get("cashflow", {}),
        "output_files": stats["output_files"],
        "column_mapping": stats["column_mapping"],
        "next": ("高置信度可直接采信；用 sample 抽查高置信度，"
                 "并逐条复核 _待复核条目.xlsx（中/低置信度）。"
                 "现金流结果见 _现金流量表.xlsx 与 _现金流相关凭证.xlsx；"
                 "若 _现金流待复核.xlsx 存在，先处理它再出表。"
                 "复核结论写入 assets/AI_JUDGMENT_NOTES.md"),
    })
    return 0


# ============================================================================
# sample
# ============================================================================

SAMPLE_COLUMNS = ("凭证号", "业务分类", "置信度", "摘要", "科目",
                  "借方金额", "贷方金额", "制单人")


def cmd_sample(args) -> int:
    """随机抽 N 条指定置信度的凭证，供 AI 抽样复核。"""
    from .config import SUPPORTED_EXT
    from . import etl

    path = Path(args.file)
    if not path.exists():
        _emit({"command": "sample", "ok": False, "error": f"文件不存在: {path}"})
        return 2
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=object)
    elif path.suffix.lower() in SUPPORTED_EXT:
        df = pd.read_excel(path, dtype=object)
    else:
        _emit({"command": "sample", "ok": False,
               "error": f"不支持的格式 {path.suffix}"})
        return 2

    if "置信度" not in df.columns or "业务分类" not in df.columns:
        _emit({"command": "sample", "ok": False,
               "error": "这不是分类结果文件（缺「业务分类」或「置信度」列）",
               "hint": "传 run 导出的 `<文件名>_分类结果.xlsx`",
               "found_columns": [str(c) for c in df.columns]})
        return 2

    want = [args.confidence] if args.confidence else ["高", "中", "低"]
    pool = df[df["置信度"].astype(str).isin(want)]
    if "凭证号" in pool.columns:
        pool = pool.drop_duplicates(subset=["凭证号"], keep="first")

    total = len(pool)
    n = max(1, int(args.n))
    rng = random.Random(args.seed)
    if total > n and args.by_bucket and "业务分类" in pool.columns:
        n_buckets = max(1, pool["业务分类"].nunique())
        per = max(1, n // n_buckets)
        parts = [g.sample(n=min(per, len(g)), random_state=rng.randint(0, 10 ** 6))
                 for _, g in pool.groupby("业务分类")]
        picked = pd.concat(parts).head(n)
    else:
        order = list(range(total))
        rng.shuffle(order)
        picked = pool.iloc[order[:min(n, total)]]

    cols = [c for c in SAMPLE_COLUMNS if c in picked.columns]
    samples = []
    for _, row in picked.iterrows():
        item = {}
        for c in cols:
            v = row[c]
            if pd.isna(v):
                item[str(c)] = None
            elif c in ("借方金额", "贷方金额"):
                item[str(c)] = etl.to_number(v)
            else:
                item[str(c)] = str(v)
        samples.append(item)

    dist = (df.drop_duplicates(subset=["凭证号"]).groupby("置信度").size().to_dict()
            if "凭证号" in df.columns else df["置信度"].value_counts().to_dict())

    _emit({
        "command": "sample",
        "ok": True,
        "file": str(path.resolve()),
        "confidence_filter": want,
        "pool_size": int(total),
        "sampled": len(samples),
        "seed": args.seed,
        "confidence_dist": {str(k): int(v) for k, v in dist.items()},
        "samples": samples,
        "review_guide": (
            "逐条核对「业务分类」。依据优先级："
            "① assets/AI_JUDGMENT_NOTES.md 的已有案例与规则；"
            "② reference/AI_REVIEW_GUIDE.md 的跨桶决策表；"
            "③ assets/bucket_definitions.md 的桶定义与边界。"
            "只能选 20 个桶之一，拿不准标「待人工确认」。"),
        "next": (
            "把结论汇报给用户；规律/新词/确认案例写入 assets/AI_JUDGMENT_NOTES.md"
            "（只追加、写依据）。若抽样发现**成规模**的错误，"
            "说明模型不适配这家公司，应建议补充训练数据后重训，而不是逐条纠错。"),
    })
    return 0


# ============================================================================
# train（仅开发者版：训练数据管线 + 训练模型）
# ============================================================================
#
# 3.0 的标签来源变了：旧版用"程序原生桶"自动导出；3.0 删了程序打分，
# 所以标签 = **AI 复核（含改判）后的最终结果**。
# 因此训练数据分两步：
#   train export   把一次清洗结果 + AI 复核结论导出成"已审"训练数据
#   train merge    把已审数据并入金标准（幂等，可反复执行）
#   train status   看金标准规模 / 分桶分布 / 未审文件
#   train run      微调模型并落盘到 assets/model/（训完推理即生效）

def _require_training() -> Optional[dict]:
    """训练模块是否可用？不可用返回错误 payload（用户版已剔除该模块）。"""
    try:
        import jc.training  # noqa: F401
        return None
    except ImportError:
        return {
            "command": "train",
            "ok": False,
            "error": "训练模块不可用——这个包是发布给用户的版本，已剔除训练代码。",
            "hint": "训练只在开发者版可用（同一份代码，打包时用 "
                    "scripts/package_release.py 剔除 jc/training 与 _dev/）。",
        }


def cmd_train(args) -> int:
    err = _require_training()
    if err:
        _emit(err)
        return 3
    from .config import TRAIN_DIR, MODEL_DIR

    action = args.action

    # ── status：看训练数据现状 ──
    if action == "status":
        from jc.training import (load_merged_records, list_training_files,
                                 RECORDS_FORMAT)
        try:
            gold = load_merged_records()
        except Exception as e:
            gold = []
            _log(f"金标准读取失败：{e}")
        buckets = Counter(r.get("bucket", "") for r in gold)
        files = list_training_files()
        _emit({
            "command": "train status",
            "ok": True,
            "train_dir": str(TRAIN_DIR),
            "model_dir": str(MODEL_DIR),
            "gold_records": len(gold),
            "gold_buckets": dict(buckets.most_common()),
            "format": RECORDS_FORMAT,
            "unreviewed_files": len(files),
            "files": files[:20],
            "next": ("数据够了就跑 `train run`；不够就先清洗几份序时账、"
                     "完成 AI 复核后 `train export` 导出"),
        })
        return 0

    # ── export：把复核后的结果导出为已审训练数据 ──
    if action == "export":
        import pandas as pd
        from jc.training import (extract_training_records, build_hash_training_data)
        from .etl import detect_columns
        src = Path(args.reviewed)
        if not src.exists():
            _emit({"command": "train export", "ok": False,
                   "error": f"文件不存在: {src}"})
            return 2
        df = (pd.read_csv(src, dtype=object) if src.suffix.lower() == ".csv"
              else pd.read_excel(src, dtype=object))
        if "业务分类" not in df.columns:
            _emit({"command": "train export", "ok": False,
                   "error": "缺少「业务分类」列——请传入 AI 复核后的分类结果"})
            return 2
        # 复核后的结果已经是最终标签（不再有"程序原生桶"），按桶聚合导出
        mapping = (json.loads(Path(args.set).read_text(encoding="utf-8"))
                   if args.set else None)
        if not mapping:
            mapping = detect_columns(df)
        records = extract_training_records(df, mapping)
        res = build_hash_training_data(
            df, mapping,
            fingerprint=args.fingerprint or src.stem,
            output_dir=str(TRAIN_DIR / "unreviewed"),
        )
        _emit({
            "command": "train export",
            "ok": True,
            "file": str(src),
            "records": len(records),
            "output": res,
            "next": "`train merge` 把已审数据并入金标准",
        })
        return 0

    # ── merge：并入金标准（幂等）──
    if action == "merge":
        from jc.training import merge_training_data, load_merged_records
        out = merge_training_data()
        gold = load_merged_records()
        _emit({
            "command": "train merge",
            "ok": True,
            "gold_records": len(gold),
            "result": out,
            "next": "`train run` 开始微调（需 NVIDIA 显卡，显存 ≥8GB）",
        })
        return 0

    # ── run：微调 ──
    if action == "run":
        import torch
        if not torch.cuda.is_available():
            _emit({"command": "train run", "ok": False,
                   "error": "训练需要 NVIDIA 显卡（CPU 训练慢到不可用）",
                   "cuda_available": False})
            return 3
        from jc.training import (load_merged_records, split_records,
                                 build_subject_switch_index, build_bucket_index,
                                 resolve_model_dir, FinanceClassifierTrainer)
        gold = load_merged_records()
        if not gold:
            _emit({"command": "train run", "ok": False,
                   "error": "金标准为空，先 train export + train merge"})
            return 2
        train_recs, val_recs = split_records(gold, val_ratio=args.val_ratio)
        subj_index = build_subject_switch_index(train_recs + val_recs)
        bucket_index = build_bucket_index(train_recs + val_recs)
        bucket_to_idx = {v: int(k) for k, v in bucket_index.items()}
        model_dir = resolve_model_dir(args.encoder)
        _log(f"[train] 记录 {len(gold)} → 训练 {len(train_recs)} / 验证 {len(val_recs)}")
        _log(f"[train] 基座模型: {model_dir}")
        trainer = FinanceClassifierTrainer(
            train_records=train_recs, val_records=val_recs,
            subject_to_index=subj_index, bucket_to_idx=bucket_to_idx,
            encoder_model_name=args.encoder,
            strategy=args.strategy,
            epochs=args.epochs,
            batch_size=args.batch_size,
        )
        result = trainer.train()
        _emit({
            "command": "train run",
            "ok": True,
            "result": result,
            "train_records": len(train_recs),
            "val_records": len(val_recs),
            "output_dir": str(MODEL_DIR),
            "next": "新权重已落盘到 assets/model/，下次 run 即生效（无需拷贝）",
        })
        return 0

    _emit({"command": "train", "ok": False, "error": f"未知动作 {action}"})
    return 2


# ============================================================================
# 入口
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m jc.cli",
        description=f"{RELEASE_NAME} — 命令行入口（供 AI 助手调用）")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="环境自检：模型 / torch / GPU / 桶数")
    s.set_defaults(func=cmd_status)

    c = sub.add_parser("check", help="读文件 + 认列名 + 校验字段")
    c.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    c.add_argument("--set", help="字段映射覆盖 JSON")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("run", help="完整清洗并导出结果")
    r.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    r.add_argument("--set", help="字段映射覆盖 JSON")
    r.add_argument("--out", help="输出目录（默认：文件同级「序时账清洗结果」）")
    r.add_argument("--chunk-size", type=int, default=64,
                   help="单次 forward 条数（默认 64，显存紧张可调小）")
    r.set_defaults(func=cmd_run)

    sp = sub.add_parser("sample", help="抽样复核：抽 N 条指定置信度的凭证")
    sp.add_argument("file", help="run 导出的「<文件名>_分类结果.xlsx」")
    sp.add_argument("--confidence", choices=["高", "中", "低"], default=None,
                    help="只抽该置信度（默认三种都抽）")
    sp.add_argument("--n", type=int, default=5, help="抽几条（默认 5）")
    sp.add_argument("--seed", type=int, default=20260101,
                    help="随机种子（默认固定，保证可复现）")
    sp.add_argument("--by-bucket", action="store_true",
                    help="按业务桶分层抽样，避免全落在一个大桶")
    sp.set_defaults(func=cmd_sample)

    # ── train（仅开发者版；发布给用户的包里已剔除该模块）──
    tp = sub.add_parser("train", help="【开发者版】训练数据管线 + 微调模型")
    tsub = tp.add_subparsers(dest="action", required=True)

    t_st = tsub.add_parser("status", help="看金标准规模 / 分桶分布 / 未审文件")
    t_st.set_defaults(func=cmd_train)

    t_ex = tsub.add_parser("export", help="把 AI 复核后的结果导出为已审训练数据")
    t_ex.add_argument("reviewed", help="AI 复核后的分类结果（xlsx/csv）")
    t_ex.add_argument("--set", help="字段映射 JSON（与 run 相同）")
    t_ex.add_argument("--fingerprint", help="指纹名（默认用文件名）")
    t_ex.set_defaults(func=cmd_train)

    t_mg = tsub.add_parser("merge", help="把已审数据并入金标准（幂等）")
    t_mg.set_defaults(func=cmd_train)

    t_rn = tsub.add_parser("run", help="微调模型（需 NVIDIA 显卡，显存 ≥8GB）")
    t_rn.add_argument("--encoder", default="BAAI/bge-large-zh-v1.5",
                      help="基座模型（显存紧张可用 BAAI/bge-base-zh-v1.5）")
    t_rn.add_argument("--strategy", default="full",
                      choices=["full", "lora", "frozen"],
                      help="full=全量微调（默认）/ lora=省显存 / frozen=只训分类头")
    t_rn.add_argument("--epochs", type=int, default=8, help="最大轮数（默认 8）")
    t_rn.add_argument("--batch-size", type=int, default=4, help="批大小（默认 4）")
    t_rn.add_argument("--val-ratio", type=float, default=0.2,
                      help="验证集比例（记录级分层切分，默认 0.2）")
    t_rn.set_defaults(func=cmd_train)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        _emit({"command": args.command, "ok": False,
               "error": f"{type(e).__name__}: {e}",
               "error_type": type(e).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
