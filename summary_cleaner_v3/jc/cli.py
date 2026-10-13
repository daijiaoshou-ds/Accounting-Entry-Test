# -*- coding: utf-8 -*-
"""序时账清洗 3.0 — 对外唯一命令行入口（AI 用它，不碰内部模块）

这是整个 skill 的「排插」：所有能力都从这里进出。

    status  环境自检：模型 4 件交付物 / torch / GPU / 桶数
    check   读文件 + 认列名 + 校验字段（不加载模型、不写数据）
    units   ★ 把序时账切成**单元**（科目组合+摘要）并打分 —— AI 的复核面
    run     写回凭证 + 编现金流 + 导出一个 Excel（复核结论作为输入）

流程（顺序不能反）：

    序时账 --units--> 单元清单 --AI 复核--> run --unit ... --> 交付物

约定：**stdout 输出单行 JSON（前缀 `RESULT `）**，进度信息走 stderr。
AI 解析时只认 `RESULT ` 开头那一行。

用法：
    python -m jc.cli status
    python -m jc.cli check  "序时账.xlsx"
    python -m jc.cli units  "序时账.xlsx"
    python -m jc.cli run    "序时账.xlsx" --out "输出目录" --unit "U137=借款筹资"
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

from .config import (ALL_BUCKETS, FIELD_LABELS, HARD_RULE_BUCKETS,
                     MODEL_DIR, RELEASE_NAME, __version__)
from .pipeline import check_file, load_unit_patch, run_file, units_file


# ============================================================================
# 输出约定
# ============================================================================

def _log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def _ensure_utf8_stdout():
    """出参被重定向/管道时，把 stdout 钉成 UTF-8。

    RESULT 行里全是中文。Windows 上 Python 默认按控制台代码页（GBK）编码 stdout，
    于是调用方按 UTF-8 解就是乱码或直接 UnicodeDecodeError——**而且这取决于
    调用方怎么起进程**（`-X utf8` 写成两个参数才生效，写成一个 `"-X utf8"`
    会被 Python 忽略）。以前的文档靠"命令前记得加 -X utf8"来躲，那是把一件
    确定的事推给人，早晚会漏。

    这里只在 stdout **不是终端**时改（即被管道/重定向、有人在解析）：
      - 管道/重定向 → UTF-8，机器解析永远一致；
      - 真终端 → 保持平台默认，人在 cmd 里直接跑仍然不乱码。
    """
    try:
        if not sys.stdout.isatty():
            enc = (sys.stdout.encoding or "").lower().replace("-", "")
            if enc != "utf8":
                sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        # 老 Python 没有 reconfigure、或 stdout 被替换过 —— 不值得因此失败
        pass


def _emit(payload: Dict[str, Any]):
    print("RESULT " + json.dumps(payload, ensure_ascii=False, default=str))


def _load_json(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


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

def _parse_pairs(pairs, what: str) -> Dict[str, str]:
    """把 ["键=值", ...] 解析成 dict。

    这是给 AI 的**提交接口**：不写文件、不拼 JSON，一条命令把结论全交回来。
    格式错就当场报清楚，别让 AI 去猜为什么没生效。
    """
    out: Dict[str, str] = {}
    for raw in pairs or []:
        p = str(raw)
        if "=" not in p:
            raise ValueError(f"{what}参数格式应为 \"键=值\"，收到：{p!r}")
        k, v = p.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k or not v:
            raise ValueError(f"{what}参数的键/值不能为空：{p!r}")
        out[k] = v
    return out


def cmd_run(args) -> int:
    override = _load_json(args.set)

    # ── 现金流补丁：文件 + 命令行，合并（命令行优先）──
    cf_patch = None
    if args.cashflow_patch:
        from . import cashflow
        try:
            cf_patch = cashflow.load_patch(args.cashflow_patch)
        except Exception as e:
            _emit({"command": "run", "ok": False, "stage": "读补丁",
                   "error": f"{type(e).__name__}: {e}",
                   "hint": "补丁形如 {\"rows\": {\"12\": {\"item\": \"支付的各项税费\", "
                           "\"note\": \"依据\"}}}"})
            return 2

    # ── 口径修正表：文件 + 命令行，合并（命令行优先）──
    try:
        unit_patch = (load_unit_patch(args.unit_patch)
                      if args.unit_patch else None)
    except Exception as e:
        _emit({"command": "run", "ok": False, "stage": "读口径修正表",
               "error": f"{type(e).__name__}: {e}",
               "hint": "修正表形如 {\"units\": {\"其他应收款[借] + 银行存款[贷] :: "
                       "预付款张三退回\": \"单位往来\"}}（键是**单元键**，可跨月复用）"})
        return 2

    # ── 命令行内联改判（AI 的主路径：不写文件、不拼 JSON、天然批量）──
    try:
        inline_unit = _parse_pairs(args.unit, "复核结论")
        inline_cf = _parse_pairs(args.cashflow, "现金流补丁")
    except ValueError as e:
        _emit({"command": "run", "ok": False, "stage": "解析命令行补丁",
               "error": str(e),
               "hint": ("复核结论：--unit \"单元号=业务桶\" 或 "
                        "--unit \"单元键=业务桶\"；"
                        "现金流：--cashflow \"行号=现金流量项目\"。"
                        "两个都可以重复多次，一次把结论交完。")})
        return 2

    if inline_unit or unit_patch:
        rows = dict((unit_patch or {}).get("units") or {})
        for ref, bkt in inline_unit.items():
            rows[ref] = bkt if isinstance(rows.get(ref), str) else {
                "bucket": bkt, "note": "AI 复核改判（命令行提交）"}
        unit_patch = {"units": rows}
    if inline_cf or cf_patch:
        rows = dict((cf_patch or {}).get("rows") or {})
        for row_no, item in inline_cf.items():
            rows[row_no] = {"item": item, "note": "AI 复核补录（命令行提交）"}
        cf_patch = {"rows": rows}

    try:
        stats = run_file(
            args.file,
            out_dir=args.out,
            override=override,
            chunk_size=args.chunk_size,
            cf_patch=cf_patch,
            unit_patch=unit_patch,
            progress=_log,
        )
    except Exception as e:
        _emit({"command": "run", "ok": False, "stage": "运行",
               "error": f"{type(e).__name__}: {e}"})
        return 1

    cf = stats.get("cashflow", {})
    review = stats.get("cashflow_review", [])
    up = stats.get("unit_patch", {})
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
        "unit_count": stats.get("unit_count"),
        "unique_inputs": stats["unique_inputs"],
        "model_calls_saved": stats["model_calls_saved"],
        "hard_rule_hits": stats["hard_rule_hits"],
        "confidence_dist": stats["confidence_dist"],
        "bucket_counts": stats["bucket_counts"],
        "amount_by_bucket": stats["amount_by_bucket"],
        "cashflow": cf,
        "unit_patch": {
            "provided": up.get("provided", 0),
            "applied": up.get("applied", 0),
            "changed_vouchers": up.get("changed_vouchers", 0),
            "applied_units": up.get("applied_units", []),
            "rejected": up.get("rejected", []),
            "unknown_units": up.get("unknown_units", []),
            "how_to_fix": (
                "复核结论用可重复的 --unit 交回来，键可以是单元号也可以是单元键："
                "--unit \"U137=借款筹资\" "
                "--unit \"其他应收款[借] + 银行存款[贷] :: 预付款张三退回=单位往来\"。"
                "一次把结论交完（别改一个跑一次）。**不要自己写 JSON/PS 改东西**——"
                "程序会把生效的口径修正写到输出目录（看 patch_files）留档，"
                "而且它是用**单元键**存键的，下个月同样的业务模式能直接复用。"
                "单元没找到就看 unknown_units（多半是单元号抄错或该单元不在这个文件里）。"),
        },
        "patch_files": stats.get("patch_files", {}),
        "cashflow_review": {
            "count": len(review),
            "items": review,
            "how_to_fix": (
                "这几条是程序在**现金流**这一步判不下来的。用可重复的 --cashflow "
                "把结论交回来：--cashflow \"502=支付其他与经营活动有关的现金\""
                "（键是上面 items 里的「行号」）。同样不用写文件。"
            ),
        },
        "sheet_rows": stats.get("sheet_rows", {}),
        "output_files": stats["output_files"],
        "column_mapping": stats["column_mapping"],
        "next": ("交付 output_files 里的那一个 Excel。"
                 "如果这次带了 --unit / --cashflow，交付物已经自洽（现金流是按"
                 "改后的桶算的）。如果没带，先跑 `cli.py units` 复核单元清单，"
                 "再把结论交给 run --unit，然后交付重跑后那份。"
                 "复核结论写入 assets/AI_JUDGMENT_NOTES.md"),
    })
    return 0


# ============================================================================
# units —— AI 的复核面（切割 + 打分，不产出任何文件）
# ============================================================================

def cmd_units(args) -> int:
    from .config import SUPPORTED_EXT

    path = Path(args.file)
    if not path.exists():
        _emit({"command": "units", "ok": False, "error": f"文件不存在: {path}"})
        return 2
    if path.suffix.lower() not in SUPPORTED_EXT:
        _emit({"command": "units", "ok": False,
               "error": f"不支持的格式 {path.suffix}",
               "supported": sorted(SUPPORTED_EXT)})
        return 2
    override = _load_json(args.set)
    try:
        res = units_file(path, override=override, chunk_size=args.chunk_size,
                         limit=args.limit, offset=args.offset,
                         bucket=args.bucket_filter, progress=_log)
    except Exception as e:
        _emit({"command": "units", "ok": False, "stage": "运行",
               "error": f"{type(e).__name__}: {e}"})
        return 1
    res["next"] = ("核对 units 里每个单元的「模型桶」；判错了就把结论交给 "
                   "run --unit（可重复，一次交完）。"
                   "整份清单通常只有几百个单元，按概率从低到高看即可。")
    _emit(res)
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
        mapping = (json.loads(Path(args.set).read_text(encoding="utf-8-sig"))
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

    u = sub.add_parser("units",
                       help="★ 把序时账切成单元并打分 —— AI 的复核面（不产出文件）")
    u.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    u.add_argument("--set", help="字段映射覆盖 JSON")
    u.add_argument("--chunk-size", type=int, default=64,
                   help="单次 forward 条数（默认 64，显存紧张可调小）")
    u.add_argument("--limit", type=int, default=0,
                   help="最多输出几个单元（默认 0 = 全部；清单已按最可疑在前排序）")
    u.add_argument("--offset", type=int, default=0, help="跳过前几个（翻页用）")
    u.add_argument("--bucket", dest="bucket_filter",
                   help="只看某个业务桶的单元（排查某个桶时用）")
    u.set_defaults(func=cmd_units)

    r = sub.add_parser("run", help="写回 + 编现金流 + 导出一个 Excel（复核结论作为输入）")
    r.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    r.add_argument("--set", help="字段映射覆盖 JSON")
    r.add_argument("--out", help="输出目录（默认：文件同级「序时账清洗结果」）")
    r.add_argument("--chunk-size", type=int, default=64,
                   help="单次 forward 条数（默认 64，显存紧张可调小）")
    r.add_argument("--unit", action="append", metavar="单元=业务桶",
                   help="★ 复核结论（可重复，一次交完）。键可以是 units 出的「单元号」"
                        "（如 U137），也可以是「单元键」原文"
                        "（如 \"其他应收款[借] + 银行存款[贷] :: 预付款张三退回\"）。"
                        "程序先改单元、再写回凭证、再算现金流。"
                        "例：--unit \"U137=借款筹资\"")
    r.add_argument("--unit-patch",
                   help="口径修正表 JSON 文件（{\"units\": {单元键: 桶}}）——"
                        "给需要长期维护/跨月复用的情况；AI 一般直接用 --unit")
    r.add_argument("--cashflow", action="append", metavar="行号=现金流量项目",
                   help="★ 补现金流待复核条目（可重复）。例："
                        "--cashflow \"502=支付其他与经营活动有关的现金\"")
    r.add_argument("--cashflow-patch",
                   help="现金流补丁 JSON 文件（{\"rows\": {行号: {item}}}）")
    r.set_defaults(func=cmd_run)

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
    _ensure_utf8_stdout()
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
