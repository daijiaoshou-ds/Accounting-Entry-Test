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
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import (ALL_BUCKETS, FIELD_LABELS, HARD_RULE_BUCKETS,
                     MODEL_DIR, RELEASE_NAME, __version__)


# ============================================================================
# 依赖与导入：缺依赖要给"规范出参"，不能甩一坨 traceback
# ============================================================================
#
# 为什么必须改：`jc/cli.py → jc/pipeline.py → jc/model.py → import torch`
# 一路都是**顶层 import**，于是任何命令（连 `status` 这种轻操作）在没装 torch
# 时都是一坨 Python traceback。AI 助手看到 traceback 的第一反应是"代码有 bug"，
# 然后就去改代码了 —— 而真实原因是"环境没装好"。
#
# 所以：内部模块一律**惰性导入**，导入失败抛 EnvError，由 main() 转成
# `{"ok": false, "stage": "env", "missing": ["torch"], "hint": ...}`，
# 一眼就知道该去装依赖。完整 traceback 仍然打到 stderr（真出 bug 时照样能查）。

class EnvError(RuntimeError):
    """依赖没装齐 / 内部模块导入失败 —— 这是环境问题，不是代码 bug。"""

    def __init__(self, detail: str, missing: Optional[List[str]] = None,
                 stage: str = "env"):
        self.detail = detail
        self.missing = list(missing or [])
        self.stage = stage
        super().__init__(detail)


# 第三方依赖（import 名）。列全是为了报错时能说清楚缺哪几个。
REQUIRED_MODULES = ("pandas", "torch", "transformers", "openpyxl")


def missing_modules() -> List[str]:
    """哪些第三方依赖装不上（用 find_spec，不真的 import：快、无副作用）。"""
    import importlib.util
    out: List[str] = []
    for name in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                out.append(name)
        except Exception:
            out.append(name)
    return out


def _import_internal(module: str):
    """导入 jc 内部模块；缺依赖时抛 EnvError，而不是让 traceback 飞出去。"""
    import importlib
    pkg = __package__ or "jc"
    try:
        return importlib.import_module(module, pkg)
    except ImportError as e:
        missing = missing_modules()
        name = str(getattr(e, "name", "") or "")
        # 缺的是我们自己（jc.*）而不是第三方依赖 → 更像文件损坏/版本串味
        internal = name.startswith(("jc", ".")) and not missing
        raise EnvError(f"{type(e).__name__}: {e}", missing=missing,
                       stage="import" if internal else "env") from e


def _pipeline():
    """延迟导入流水线（顶层导入会让 status 也要求 torch 已装）。"""
    return _import_internal(".pipeline")


def _env_hint(e: EnvError) -> str:
    if e.stage == "import":
        return ("jc 内部模块导入失败，而且第三方依赖是齐的 —— 这更像文件损坏或"
                "版本串味，不是缺依赖。请把 stderr 里那段 traceback 交回开发者。")
    mods = "、".join(e.missing) or "（未能识别，请看 stderr）"
    return (f"缺依赖：{mods}。按 reference/SETUP.md 第一节装："
            f"① 建 venv；② 装 **CPU 版** torch（国内走阿里云源，约 106MB；"
            f"不要装 CUDA 版，那是 2.4GB）；③ `pip install -r requirements.txt -i "
            f"https://mirrors.aliyun.com/pypi/simple/`；装完重跑 `cli.py status`。"
            f"**这是环境问题，不是代码 bug —— 不要改代码。**")


# ============================================================================
# 输出约定
# ============================================================================

_OUT_JSON: Optional[Path] = None      # --out-json 指定的落盘路径
SLIM_LIST_LIMIT = 10                  # 超过这么多条的明细数组，在 stdout 里折叠


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


def _slim(value):
    """折叠大数组/大映射 —— 只给 stdout 用；完整内容在 --out-json 那个文件里。

    只折叠"值也是结构"的容器：`bucket_counts`（桶 → 数字）这类要看的统计
    必须原样留着，哪怕它有 20 多个键。
    """
    if isinstance(value, list):
        if value and isinstance(value[0], dict) and len(value) > SLIM_LIST_LIMIT:
            return {"条数": len(value),
                    "内容": f"已折叠（> {SLIM_LIST_LIMIT} 条），完整明细见 out_json 文件"}
        return [_slim(v) for v in value]
    if isinstance(value, dict):
        if (len(value) > SLIM_LIST_LIMIT
                and all(isinstance(v, (dict, list)) for v in value.values())):
            return {"条数": len(value),
                    "内容": f"已折叠（> {SLIM_LIST_LIMIT} 项），完整内容见 out_json 文件"}
        return {k: _slim(v) for k, v in value.items()}
    return value


def _emit(payload: Dict[str, Any]):
    """出参：加了 `--out-json` 就**由程序自己用 UTF-8 落盘**，stdout 只给摘要。

    为什么要有 `--out-json`：Windows PowerShell 的 `1>` 重定向**不是透明的**
    —— 它按控制台编码解码子进程输出，再以 **UTF-16LE**（0xFF 0xFE BOM）写文件，
    调用方按 UTF-8 读必崩。我们管不了 shell，但可以不让调用方依赖 shell：
    让程序自己写。顺带解决 `units` 出参 5MB 单行 JSON 不好取的问题。

    **文件里是纯 JSON（不带 `RESULT ` 前缀）** —— 那是文件不是 stdout 协议，
    直接 `json.loads(Path(p).read_text(encoding="utf-8"))` 就行。
    """
    global _OUT_JSON
    if _OUT_JSON is not None:
        try:
            _OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
            _OUT_JSON.write_text(
                json.dumps({**payload, "out_json": str(_OUT_JSON)},
                           ensure_ascii=False, default=str) + "\n",
                encoding="utf-8")
            summary = _slim(payload)
            summary["out_json"] = str(_OUT_JSON)
            summary["stdout_note"] = (
                f"完整出参已写入 out_json（纯 JSON、UTF-8）。这一行是**摘要**："
                f"超过 {SLIM_LIST_LIMIT} 条的明细数组被折叠成 {{条数, 内容}}。"
                f"要明细就读那个文件。")
            print("RESULT " + json.dumps(summary, ensure_ascii=False, default=str))
            return
        except OSError as e:
            payload = {**payload, "out_json_error": f"{type(e).__name__}: {e}"}
    print("RESULT " + json.dumps(payload, ensure_ascii=False, default=str))


def _load_json(path: Optional[str]) -> Optional[dict]:
    """读一个 JSON 文件。容忍 `RESULT ` 前缀（有人会把 stdout 重定向进文件）。"""
    if not path:
        return None
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if text.startswith("RESULT "):
        text = text[7:].strip()
    return json.loads(text)


# ============================================================================
# status
# ============================================================================

def cmd_status(args) -> int:
    """环境自检：模型是否齐全、torch / GPU 情况、桶清单。

    注意：`.model` 是**惰性导入**的。没装 torch 时这里会抛 EnvError，
    main() 把它转成 `stage=env / missing=[torch]` 的出参 —— 这是有意的：
    "环境没装好"必须一眼能看出来，不能伪装成一坨 traceback 让人去改代码。
    """
    model = _import_internal(".model")

    art = model.artifacts_status()
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

    ready = model.model_ready()
    device = model.pick_device() if torch_ok else "cpu"

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
    res = _pipeline().check_file(args.file, override=_load_json(args.set))
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


def _parse_sim(value) -> float:
    """`--sim`：阈值（0~1）或 off。

    off            → 0（关闭归族，退回"摘要原样文本"去重）
    1              → 1.0（只并归一化后完全相同的，不做模糊相似度）
    0.95（默认）    → 同科目组合内相似度≥0.95 的并成一族
    """
    from .units import SIM_DEFAULT
    if value is None:
        return SIM_DEFAULT
    s = str(value).strip().lower()
    if s in ("off", "none", "no", "0"):
        return 0.0
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"--sim 只认 0~1 的阈值或 off，收到的是：{value}")
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"--sim 应在 0~1 之间（收到 {v}）。1 = 只并完全相同的")
    return v


def cmd_run(args) -> int:
    pipeline = _pipeline()
    override = _load_json(args.set)

    # ── 复用上一次的打分（跳过模型推理）──
    # 真实流程是**回环**的：run 之后 cashflow_review 会暴露"桶判错"，
    # 回去改单元就要**再跑一遍全量推理**。真账上一轮就是这么白花 4.5 分钟的。
    reuse = None
    reuse_file = None
    if args.reuse_units:
        reuse_file = str(Path(args.reuse_units).resolve())
        try:
            reuse = _load_json(args.reuse_units)
        except Exception as e:
            _emit({"command": "run", "ok": False, "stage": "读复用清单",
                   "error": f"{type(e).__name__}: {e}",
                   "hint": "--reuse-units 要传 `units --out-json 文件` 产出的那份 JSON"})
            return 2
        if not isinstance(reuse, dict) or not (reuse.get("units")
                                              or reuse.get("reuse_labels")):
            _emit({"command": "run", "ok": False, "stage": "读复用清单",
                   "error": "文件里既没有 units 数组、也没有 reuse_labels，"
                            "不像是 `units` 命令的出参",
                   "hint": "先跑 `cli.py units 序时账.xlsx --out-json 单元.json`"
                           "（--summary / --families 加 --out-json 也可以），"
                           "再把同一个文件传给 --reuse-units"})
            return 2

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
        unit_patch = (pipeline.load_unit_patch(args.unit_patch)
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
        stats = pipeline.run_file(
            args.file,
            out_dir=args.out,
            override=override,
            chunk_size=args.chunk_size,
            cf_patch=cf_patch,
            unit_patch=unit_patch,
            progress=_log,
            sim=_parse_sim(args.sim),
            reuse_units=reuse,
            reuse_file=reuse_file,
        )
    except EnvError:
        raise                      # 环境问题原样上抛（别伪装成"运行失败"）
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
        "reuse": stats.get("reuse"),
        "hard_rule_hits": stats["hard_rule_hits"],
        "confidence_dist": stats["confidence_dist"],
        "bucket_counts": stats["bucket_counts"],
        "amount_by_bucket": stats["amount_by_bucket"],
        "dedup": stats.get("dedup"),
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
                 "改后的桶算的）。如果没带，先跑 `cli.py units` 复核单元清单"
                 "（`units --summary` 看全局、`units --families` 看同族离群），"
                 "再把结论交给 run --unit，然后交付重跑后那份。"
                 "⚠️ 迭代时把上次的 `units --out-json 单元.json` 用 "
                 "`--reuse-units 单元.json` 带上，可以跳过全量推理。"
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
    if args.summary and args.families:
        _emit({"command": "units", "ok": False,
               "error": "--summary 与 --families 只能选一个",
               "hint": "先 --summary 看全局，再 --families 按科目组合找少数派"})
        return 2
    view = "families" if args.families else ("summary" if args.summary else "units")
    try:
        res = _pipeline().units_file(
            path, override=override, chunk_size=args.chunk_size,
            limit=args.limit, offset=args.offset,
            bucket=args.bucket_filter, progress=_log,
            sim=_parse_sim(args.sim), view=view,
            min_family=args.min_family, outlier_share=args.outlier_share)
    except EnvError:
        raise                      # 环境问题原样上抛（别伪装成"运行失败"）
    except Exception as e:
        _emit({"command": "units", "ok": False, "stage": "运行",
               "error": f"{type(e).__name__}: {e}"})
        return 1
    if view == "units":
        res["next"] = (
            "核对 units 里每个单元的「模型桶」（同族只看代表，「同族摘要」是"
            "抄它作业的其它变体）；判错了就把结论交给 run --unit（可重复，一次交完）。"
            "清单长度≠凭证数：同科目组合内相似的摘要已经并成一族，去看 dedup 字段。"
            "⚠️ 清单可能几千条、按概率排序**几乎没用**（概率饱和到 1.0）："
            "先 `units --summary` 看全局，再 `units --families` 看同族离群。"
            "要落盘+摘要，就加 `--out-json 单元.json`（那份 JSON 还能用 "
            "`run --reuse-units` 复用，迭代时省掉全量推理）。")
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

OUT_JSON_HELP = ("把**完整** RESULT 用 UTF-8 写入该文件（程序自己写，不经过 shell）；"
                 "stdout 只留摘要。为什么要它：Windows PowerShell 的 `1>` 重定向"
                 "拿到的是 UTF-16LE，按 UTF-8 读必崩；而 `units` 出参又有 5MB。"
                 "写了它，就可以直接读文件、也可以给 `run --reuse-units` 复用。")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m jc.cli",
        description=f"{RELEASE_NAME} — 命令行入口（供 AI 助手调用）")
    # 顶层也认（写在子命令前面也行）；default=SUPPRESS 保证子命令那份不覆盖它
    p.add_argument("--out-json", metavar="PATH", default=argparse.SUPPRESS,
                   help=OUT_JSON_HELP)
    sub = p.add_subparsers(dest="command", required=True)

    def add_out_json(parser):
        parser.add_argument("--out-json", metavar="PATH",
                            default=argparse.SUPPRESS, help=OUT_JSON_HELP)

    s = sub.add_parser("status", help="环境自检：模型 / torch / GPU / 桶数")
    add_out_json(s)
    s.set_defaults(func=cmd_status)

    c = sub.add_parser("check", help="读文件 + 认列名 + 校验字段")
    c.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    c.add_argument("--set", help="字段映射覆盖 JSON")
    add_out_json(c)
    c.set_defaults(func=cmd_check)

    u = sub.add_parser("units",
                       help="★ 把序时账切成单元并打分 —— AI 的复核面（不产出文件）")
    u.add_argument("file", help="序时账文件（xlsx/xls/xlsm/csv）")
    u.add_argument("--set", help="字段映射覆盖 JSON")
    u.add_argument("--chunk-size", type=int, default=64,
                   help="单次 forward 条数（默认 64，显存紧张可调小）")
    u.add_argument("--limit", type=int, default=0,
                   help="最多输出几个单元（默认 0 = 全部；只对默认视图有效）")
    u.add_argument("--offset", type=int, default=0, help="跳过前几个（翻页用）")
    u.add_argument("--bucket", dest="bucket_filter",
                   help="只看某个业务桶的单元（排查某个桶时用）")
    u.add_argument("--summary", action="store_true",
                   help="★ 只给**全局画像**（桶分布、概率分布、族数、少数派规模、"
                        "最可疑的 20 个单元），不出 units 数组 —— 复核第一步看这个")
    u.add_argument("--families", action="store_true",
                   help="★ 按**科目组合**聚合成族，只列**有少数派**的族"
                        "（族内单元数 ≥ --min-family、某个桶占比 ≤ --outlier-share）"
                        "—— 这是真账上最管用的找错办法（概率饱和后排序几乎没用）")
    u.add_argument("--min-family", type=int, default=5, metavar="N",
                   help="--families：族至少这么大才找少数派（默认 5）")
    u.add_argument("--outlier-share", type=float, default=0.25, metavar="P",
                   help="--families：占比不超过 P 的桶算少数派（默认 0.25）")
    u.add_argument("--sim", default=None, metavar="阈值|off",
                   help="摘要相似度阈值（默认 0.95）：同一科目组合内，"
                        "归一化（忽略数字）后相似度≥阈值的并成一族，模型只看代表。"
                        "1 = 只并完全相同的，off = 关闭归族（退回原样文本去重）")
    add_out_json(u)
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
    r.add_argument("--reuse-units", metavar="PATH",
                   help="★ 复用 `units --out-json 单元.json` 那份打分结果，"
                        "跳过全量模型推理（迭代改判时最省时间）。"
                        "默认视图、`--summary`、`--families` 的出参都能用；"
                        "命中的单元直接用上次的桶与概率，没命中的才重新推理。")
    r.add_argument("--unit-patch",
                   help="口径修正表 JSON 文件（{\"units\": {单元键: 桶}}）——"
                        "给需要长期维护/跨月复用的情况；AI 一般直接用 --unit")
    r.add_argument("--cashflow", action="append", metavar="行号=现金流量项目",
                   help="★ 补现金流待复核条目（可重复）。例："
                        "--cashflow \"502=支付其他与经营活动有关的现金\"")
    r.add_argument("--cashflow-patch",
                   help="现金流补丁 JSON 文件（{\"rows\": {行号: {item}}}）")
    r.add_argument("--sim", default=None, metavar="阈值|off",
                   help="摘要相似度阈值（默认 0.95）：同一科目组合内相似的摘要"
                        "并成一族、模型只判代表，结论写回整族。"
                        "1 = 只并完全相同的，off = 关闭归族。"
                        "⚠️ 要和 `units` 用同一个值，否则单元号会对不上"
                        "（单元键不受影响，仍可跨月复用）")
    add_out_json(r)
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
    global _OUT_JSON
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    raw = getattr(args, "out_json", "") or ""
    _OUT_JSON = Path(raw) if raw else None
    try:
        return args.func(args)
    except EnvError as e:
        # 环境问题要**一眼看出来是环境问题**：规范出参 + 完整 traceback 走 stderr
        import traceback
        traceback.print_exc(file=sys.stderr)
        _emit({"command": args.command, "ok": False, "stage": e.stage,
               "missing": e.missing, "error": e.detail,
               "hint": _env_hint(e)})
        return 4
    except Exception as e:
        _emit({"command": args.command, "ok": False,
               "error": f"{type(e).__name__}: {e}",
               "error_type": type(e).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
