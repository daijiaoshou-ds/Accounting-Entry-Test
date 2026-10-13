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
    python -m jc.cli sample "输出\\序时账_结果.xlsx" --confidence 高 --n 5
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

from .config import (ALL_BUCKETS, FIELD_LABELS, HARD_RULE_BUCKETS, LOWEST_CONF_N,
                     MODEL_DIR, RELEASE_NAME, __version__)
from .pipeline import check_file, load_bucket_patch, run_file


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

def cmd_run(args) -> int:
    override = _load_json(args.set)
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
    try:
        bucket_patch = (load_bucket_patch(args.bucket_patch)
                        if args.bucket_patch else None)
    except Exception as e:
        _emit({"command": "run", "ok": False, "stage": "读业务分类补丁",
               "error": f"{type(e).__name__}: {e}",
               "hint": "补丁形如 {\"vouchers\": {\"记-001\": {\"bucket\": "
                       "\"单位往来\", \"note\": \"依据\"}}}"})
        return 2
    try:
        stats = run_file(
            args.file,
            out_dir=args.out,
            override=override,
            chunk_size=args.chunk_size,
            cf_patch=cf_patch,
            bucket_patch=bucket_patch,
            lowest_n=args.lowest,
            progress=_log,
        )
    except Exception as e:
        _emit({"command": "run", "ok": False, "stage": "运行",
               "error": f"{type(e).__name__}: {e}"})
        return 1

    cf = stats.get("cashflow", {})
    review = stats.get("cashflow_review", [])
    bp = stats.get("bucket_patch", {})
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
        "cashflow": cf,
        "bucket_patch": {
            "provided": bp.get("provided", 0),
            "applied": bp.get("applied", 0),
            "rejected": bp.get("rejected", []),
            "unknown_vouchers": bp.get("unknown_vouchers", []),
            "how_to_fix": (
                "业务分类补丁形如 {\"vouchers\": {\"记-001\": {\"bucket\": "
                "\"单位往来\", \"note\": \"依据（可选）\"}}}，键是**凭证号**、"
                "桶必须是 20 个业务桶之一。它在**算现金流之前**套用，"
                "所以改判后现金流会跟着重算——改完记得再看一眼 "
                "cashflow_review（可能出现新的待复核）。"),
        },
        "cashflow_review": {
            "count": len(review),
            "items": review,
            "how_to_fix": (
                "把每条的结论写成补丁 JSON（键是上面 items 里的「行号」），"
                "再跑一次 run --cashflow-patch <补丁.json>，程序会重出结果文件。"
                "补丁形如 {\"rows\": {\"123\": {\"item\": \"吸收投资收到的现金\","
                " \"note\": \"对方科目为实收资本\"}}}。"
            ),
        },
        "sheet_rows": stats.get("sheet_rows", {}),
        "lowest_confidence": stats.get("lowest_confidence", {}),
        "output_files": stats["output_files"],
        "column_mapping": stats["column_mapping"],
        "next": ("这一份 Excel 是**底稿**，若有改判/补丁要带补丁重跑，交付重跑后那份。"
                 "复核顺序：① 看 lowest_confidence（最可疑的几张，附完整分录）——"
                 "判错了写**业务分类补丁**（--bucket-patch），"
                 "程序会在算现金流之前套用、按改好的桶重算现金流；"
                 "② 再看 cashflow_review（有则写现金流补丁 --cashflow-patch）；"
                 "③ 带着补丁重跑；④ 用 sample 抽查高置信度。"
                 "复核结论写入 assets/AI_JUDGMENT_NOTES.md"),
    })
    return 0


# ============================================================================
# sample
# ============================================================================

# 抽样要给**整张凭证**，不是单行 —— 判断业务桶靠的是"科目组合 + 摘要"。
# 只回一行会误导：实测 800 张凭证 100% 是多行，货币资金那行常常不在第一行，
# 单行出参会让 AI 把非货币资金行的"贷方有数"误读成付现。
SAMPLE_COLUMNS = ("凭证号", "业务分类", "置信度", "制单人")

# 科目列名**不写死**：结果 sheet 用程序口径「一级科目」，但用户原始列名也可能是
# 「科目」「会计科目」……挨个试，避免改名后这里静默丢列、AI 拿不到科目。
SUBJECT_ALIASES = ("一级科目", "科目", "会计科目", "总账科目", "科目编码")
DETAIL_ALIASES = ("科目明细", "科目名称", "明细科目", "二级科目", "会计科目名称")
SUMMARY_ALIASES = ("摘要", "说明", "备注", "业务描述")
DEBIT_ALIASES = ("借方金额", "借方", "借方发生额", "借")
CREDIT_ALIASES = ("贷方金额", "贷方", "贷方发生额", "贷")
CF_COLUMN_ORDER = ("现金流量项目", "现金流方向", "现金流判定", "现金流复核")


def _pick_col(columns, aliases) -> Optional[str]:
    """按别名顺序挑第一个存在的列（找不到返回 None）。"""
    for a in aliases:
        if a in columns:
            return a
    return None


def _cell_text(v) -> Optional[str]:
    return None if v is None or pd.isna(v) else str(v)


def _to_amount(v) -> float:
    from . import etl
    return 0.0 if v is None or pd.isna(v) else float(etl.to_number(v))


def voucher_entries(df: pd.DataFrame, vno, v_col: str, s_col: str,
                    d_col: str, c_col: str,
                    det_col: Optional[str] = None) -> List[dict]:
    """抽出一张凭证的**全部分录** —— AI 复核业务分类的核心依据。"""
    blk = df[df[v_col].astype(str) == str(vno)]
    rows: List[dict] = []
    for _, r in blk.iterrows():
        item = {"一级科目": "" if pd.isna(r[s_col]) else str(r[s_col]).strip()}
        if det_col:
            item["明细"] = _cell_text(r.get(det_col))
        item["借方金额"] = _to_amount(r[d_col])
        item["贷方金额"] = _to_amount(r[c_col])
        rows.append(item)
    return rows


def voucher_cashflow(df: pd.DataFrame, vno, v_col: str,
                     cf_cols: List[str]) -> List[dict]:
    """这张凭证里被程序判了现金流的分录（通常只有货币资金那一行）。"""
    if not cf_cols:
        return []
    blk = df[df[v_col].astype(str) == str(vno)]
    out: List[dict] = []
    for _, r in blk.iterrows():
        head = r.get(cf_cols[0])
        if not isinstance(head, str) or not head.strip():
            continue
        rec = {"现金流量项目": head.strip()}
        for c in cf_cols[1:]:
            v = r.get(c)
            if isinstance(v, str) and v.strip():
                rec[c] = v.strip()
        out.append(rec)
    return out


def cmd_sample(args) -> int:
    """随机抽 N 条指定置信度的凭证，供 AI 抽样复核。"""
    from .config import SUPPORTED_EXT
    from . import etl

    path = Path(args.file)
    if not path.exists():
        _emit({"command": "sample", "ok": False, "error": f"文件不存在: {path}"})
        return 2
    # 结果文件是一个 Excel 多个 sheet，「业务分类结果」那张才是要抽的
    sheet = None
    if path.suffix.lower() in SUPPORTED_EXT and path.suffix.lower() != ".csv":
        try:
            names = pd.ExcelFile(path).sheet_names
        except Exception as e:
            _emit({"command": "sample", "ok": False,
                   "error": f"读不了这个 Excel：{type(e).__name__}: {e}"})
            return 2
        if "业务分类结果" in names:
            sheet = "业务分类结果"
    try:
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path, dtype=object)
        elif path.suffix.lower() in SUPPORTED_EXT:
            df = pd.read_excel(path, dtype=object, sheet_name=sheet or 0)
        else:
            _emit({"command": "sample", "ok": False,
                   "error": f"不支持的格式 {path.suffix}"})
            return 2
    except Exception as e:
        _emit({"command": "sample", "ok": False,
               "error": f"{type(e).__name__}: {e}"})
        return 2

    if "置信度" not in df.columns or "业务分类" not in df.columns:
        _emit({"command": "sample", "ok": False,
               "error": "这不是分类结果（缺「业务分类」或「置信度」列）",
               "hint": "传 run 导出的 `<文件名>_结果.xlsx`",
               "sheet_read": sheet or "(第一个 sheet)",
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

    all_cols = [str(c) for c in df.columns]
    v_col = _pick_col(all_cols, ("凭证号", "凭证编号", "凭证字号", "单号"))
    s_col = _pick_col(all_cols, SUBJECT_ALIASES)
    d_col = _pick_col(all_cols, DEBIT_ALIASES)
    c_col = _pick_col(all_cols, CREDIT_ALIASES)
    det_col = _pick_col(all_cols, DETAIL_ALIASES)
    sum_col = _pick_col(all_cols, SUMMARY_ALIASES)
    cf_cols = [c for c in CF_COLUMN_ORDER if c in all_cols]

    if not all((v_col, s_col, d_col, c_col)):
        _emit({"command": "sample", "ok": False,
               "error": "结果文件缺列（凭证号/一级科目/借方金额/贷方金额），"
                        "没法展开整张凭证",
               "found_columns": all_cols})
        return 2

    cols = [c for c in SAMPLE_COLUMNS if c in df.columns]
    samples = []
    for _, row in picked.iterrows():
        vno = row[v_col]
        item: Dict[str, Any] = {str(c): _cell_text(row[c]) for c in cols}
        if sum_col:
            item["摘要"] = _cell_text(row.get(sum_col))
        item["分录"] = voucher_entries(df, vno, v_col, s_col, d_col, c_col, det_col)
        cf = voucher_cashflow(df, vno, v_col, cf_cols)
        if cf:
            item["现金流"] = cf
        samples.append(item)

    dist = (df.drop_duplicates(subset=[v_col]).groupby("置信度").size().to_dict()
            if v_col in df.columns else df["置信度"].value_counts().to_dict())

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
            "每条给的是**整张凭证**（`分录` 是全部借贷行）。核对「业务分类」要看"
            "科目组合 + 摘要，别只看一行。依据优先级："
            "① assets/AI_JUDGMENT_NOTES.md 的已有案例与规则；"
            "② reference/AI_REVIEW_GUIDE.md 的 A 表（模式 → 桶）；"
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

    r = sub.add_parser("run", help="完整清洗并导出结果（一个 Excel，多 sheet）")
    r.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    r.add_argument("--set", help="字段映射覆盖 JSON")
    r.add_argument("--out", help="输出目录（默认：文件同级「序时账清洗结果」）")
    r.add_argument("--chunk-size", type=int, default=64,
                   help="单次 forward 条数（默认 64，显存紧张可调小）")
    r.add_argument("--cashflow-patch",
                   help="现金流补丁 JSON：人工/AI 对「待复核」条目的结论，"
                        "程序据此重出结果文件")
    r.add_argument("--bucket-patch",
                   help="业务分类补丁 JSON：AI 复核改判的桶（凭证号 → bucket）。"
                        "在算现金流**之前**套用，所以现金流会按改好的桶重算")
    r.add_argument("--lowest", type=int, default=LOWEST_CONF_N,
                   help=f"出参里附几张「最可疑」凭证（按概率升序，默认 "
                        f"{LOWEST_CONF_N}；0 = 不出）")
    r.set_defaults(func=cmd_run)

    sp = sub.add_parser("sample", help="抽样复核：抽 N 条指定置信度的凭证")
    sp.add_argument("file", help="run 导出的「<文件名>_结果.xlsx」")
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
