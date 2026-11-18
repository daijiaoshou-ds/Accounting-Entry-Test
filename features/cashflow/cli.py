# -*- coding: utf-8 -*-
"""现金流的自检入口（只读，不碰序时账）。

    status    环境自检：依赖 / 判定内核 / 模型交付物 / 映射表自洽
    validate  现金流映射表自检（桶覆盖、项目合法性、歧义桶）

**跑账不从这里走。** 一份账怎么读、字段怎么认，统一在任务的配置那一步
（`journal inspect` 认列名 → `journal run --feature cashflow --mapping …`）。
这里曾经还有一个 `check`/`run`，那是"独立跑一份账"的第二套实现：自己读表、
自己认列名、自己判、自己导出——和 jobflow 那条链并行。两套并行的后果是
改一处不认识另一处（列名映射尤其容易），所以删掉了，只留自检。

约定：**stdout 只输出单行 JSON（前缀 `RESULT `）**，进度信息走 stderr。

用法：
    python -m features.cashflow.cli status
    python -m features.cashflow.cli validate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULT_PREFIX = "RESULT "


# ============================================================================
# 依赖
# ============================================================================

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


# ============================================================================
# 输出
# ============================================================================

def _emit(payload: Dict[str, Any], out_json: Optional[str] = None) -> int:
    """输出单行 JSON；进度/人话走 stderr。"""
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if out_json:
        p = Path(out_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                default=str), encoding="utf-8")
    sys.stdout.write(RESULT_PREFIX + text + "\n")
    sys.stdout.flush()
    return 0 if payload.get("ok", True) else 1


# ============================================================================
# 命令
# ============================================================================

def cmd_status(args) -> Dict[str, Any]:
    from .config import CF_ARTIFACTS, PKG_DIR, __version__
    from . import judge

    info: Dict[str, Any] = {
        "ok": True,
        "release": "现金流量表编写",
        "version": __version__,
        "root": str(PKG_DIR),
        "python": sys.version.split()[0],
        "missing_modules": missing_modules(),
    }
    # 判桶与模型在共用的内核里（core/classify + core/nn）
    try:
        import core.classify as classify_pkg
        from core.classify.config import get_nn_storage_dir
        from core.classify.classifier import JournalClassifier
        from core.classify.config import load_subject_list, BUCKET_REGISTRY

        clf = JournalClassifier(subject_list=load_subject_list())
        storage = Path(get_nn_storage_dir())
        artifacts = {
            "fine_tuned": (storage / "fine_tuned").exists(),
            "classifier": (storage / "finance_classifier.pt").exists(),
            "subject_index": (storage / "subject_to_index.json").exists(),
            "bucket_index": (storage / "index_to_bucket.json").exists(),
        }
        info["upstream"] = {
            "package": "core.classify",
            "path": str(Path(classify_pkg.__file__).resolve().parent),
            "buckets": len(BUCKET_REGISTRY),
            "model_artifacts": artifacts,
            "model_ready": all(artifacts.values()),
        }
        info["ok"] = all(artifacts.values()) and not info["missing_modules"]
    except Exception as e:  # noqa: BLE001
        info.update({"ok": False, "upstream": {"error": f"{type(e).__name__}: {e}"}})

    # 映射表
    try:
        cfg = judge.load_cf_config()
        info["cf_maps"] = {
            "dir": str(CF_ARTIFACTS["items"].parent),
            "items": len(cfg["items"]),
            "buckets": len(cfg["bucket_map"]),
            "validate_problems": judge.validate()["problem_count"],
        }
    except Exception as e:  # noqa: BLE001
        info["cf_maps"] = {"error": f"{type(e).__name__}: {e}"}
        info["ok"] = False
    return info


def cmd_validate(args) -> Dict[str, Any]:
    from . import judge
    rep = judge.validate()
    rep["ok"] = rep["problem_count"] == 0
    return rep


# ============================================================================
# main
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="journal cashflow", description="现金流量表编写（桶由 core/classify 判定）")
    p.add_argument("--version", action="version", version="现金流量表编写 1.0.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="环境自检")
    s.add_argument("--out-json")

    v = sub.add_parser("validate", help="现金流映射表自检")
    v.add_argument("--out-json")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {"status": cmd_status, "validate": cmd_validate}[args.cmd]
    try:
        payload = handler(args)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        return _emit({"ok": False, "stage": "error",
                      "error": f"{type(e).__name__}: {e}"},
                     getattr(args, "out_json", None))
    return _emit(payload, getattr(args, "out_json", None))


if __name__ == "__main__":
    sys.exit(main())
