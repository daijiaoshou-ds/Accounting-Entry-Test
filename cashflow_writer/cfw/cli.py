# -*- coding: utf-8 -*-
"""对外唯一命令行入口（AI 用它，不碰内部模块）。

    status    环境自检：依赖 / 上游 summary_cleaner / 模型交付物 / 映射表自洽
    check     读文件 + 认列名 + 数货币资金凭证（不加载模型、不写任何文件）
    run       业务分类（上游）→ 编现金流量表 → 导出一个 Excel
    validate  现金流映射表自检（桶覆盖、项目合法性、歧义桶）

约定：**stdout 只输出单行 JSON（前缀 `RESULT `）**，进度信息走 stderr。
AI 解析时只认 `RESULT ` 开头那一行。

用法：
    python -m cfw.cli status
    python -m cfw.cli check "序时账.xlsx"
    python -m cfw.cli run   "序时账.xlsx" --out "输出目录"
    python -m cfw.cli run   "序时账.xlsx" --cf "37=支付其他与经营活动有关的现金"
    python -m cfw.cli validate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULT_PREFIX = "RESULT "


# ============================================================================
# 依赖与导入：缺依赖要给"规范出参"，不能甩一坨 traceback
# ============================================================================
#
# 为什么必须这样：任何命令（连 status 这种轻操作）在没装 torch 时都是一坨
# Python traceback。AI 看到 traceback 的第一反应是"代码有 bug"，然后去改代码
# —— 而真实原因是"环境没装好"。
#
# 所以内部模块一律**惰性导入**，失败抛 EnvError，由 main() 转成
# {"ok": false, "stage": "env", "missing": [...]}，一眼就知道该装什么。

class EnvError(RuntimeError):
    """依赖没装齐 / 上游包不可用 —— 环境问题，不是代码 bug。"""

    def __init__(self, detail: str, missing: Optional[List[str]] = None,
                 stage: str = "env"):
        self.detail = detail
        self.missing = list(missing or [])
        self.stage = stage
        super().__init__(detail)


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


def _load(module: str):
    """导入 cfw 内部模块；缺依赖/缺上游时抛 EnvError。"""
    import importlib
    pkg = __package__ or "cfw"
    try:
        return importlib.import_module(module, pkg)
    except ImportError as e:
        from .config import UpstreamMissing
        if isinstance(e, UpstreamMissing):
            raise EnvError(str(e), stage="upstream") from e
        missing = missing_modules()
        raise EnvError(f"{type(e).__name__}: {e}", missing=missing) from e


def _hint(e: EnvError) -> str:
    if e.stage == "upstream":
        return e.detail
    mods = "、".join(e.missing) or "（未能识别，请看 stderr）"
    return (f"缺依赖：{mods}。装法：pip install -r requirements.txt"
            f"（torch 单独装，见 reference/SETUP.md）。"
            f"**这是环境问题，不是代码 bug —— 不要改代码。**")


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


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _parse_pairs(items: Optional[List[str]], left: str, right: str) -> Dict[str, str]:
    """把 ["a=b", ...] 解析成 {a: b}，格式不对直接报错（别静默吞）。"""
    out: Dict[str, str] = {}
    for raw in items or []:
        if "=" not in raw:
            raise SystemExit(f"参数格式错：{raw!r}，应形如 {left}={right}")
        k, v = raw.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# ============================================================================
# 命令
# ============================================================================

def cmd_status(args) -> Dict[str, Any]:
    from .config import (CF_ARTIFACTS, ROOT_DIR, __version__, ensure_upstream,
                         UpstreamMissing)
    from . import judge

    info: Dict[str, Any] = {
        "ok": True,
        "release": "现金流量表编写",
        "version": __version__,
        "root": str(ROOT_DIR),
        "python": sys.version.split()[0],
        "missing_modules": missing_modules(),
    }
    # 上游
    try:
        ensure_upstream()
        import summary_cleaner
        from summary_cleaner.v2.config import get_nn_storage_dir
        from summary_cleaner.v2.classifier import JournalClassifier
        from summary_cleaner.v2.config import load_subject_list, BUCKET_REGISTRY

        clf = JournalClassifier(subject_list=load_subject_list())
        storage = Path(get_nn_storage_dir())
        artifacts = {
            "fine_tuned": (storage / "fine_tuned").exists(),
            "classifier": (storage / "finance_classifier.pt").exists(),
            "subject_index": (storage / "subject_to_index.json").exists(),
            "bucket_index": (storage / "index_to_bucket.json").exists(),
        }
        info["upstream"] = {
            "package": getattr(summary_cleaner, "__name__", "summary_cleaner"),
            "path": str(Path(summary_cleaner.__file__).resolve().parent),
            "buckets": len(BUCKET_REGISTRY),
            "model_artifacts": artifacts,
            "model_ready": all(artifacts.values()),
        }
        info["ok"] = all(artifacts.values()) and not info["missing_modules"]
    except UpstreamMissing as e:
        info.update({"ok": False, "upstream": {"error": str(e)}})
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


def cmd_check(args) -> Dict[str, Any]:
    pipe = _load(".pipeline")
    try:
        out = pipe.check_file(args.file, _parse_pairs(args.set, "字段", "列名"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "check",
                "error": f"{type(e).__name__}: {e}"}
    return out


def cmd_validate(args) -> Dict[str, Any]:
    from . import judge
    rep = judge.validate()
    rep["ok"] = rep["problem_count"] == 0
    return rep


def cmd_run(args) -> Dict[str, Any]:
    from . import judge
    pipe = _load(".pipeline")

    cf_patch = None
    if args.cf_patch:
        cf_patch = judge.load_patch(args.cf_patch)
    inline = _parse_pairs(args.cf, "行号", "项目")
    if inline:
        cf_patch = cf_patch or {"rows": {}}
        cf_patch.setdefault("rows", {})
        for row, item in inline.items():
            cf_patch["rows"][row] = {"item": item, "note": "命令行指定"}

    bucket_patch = None
    if args.bucket_patch:
        doc = json.loads(Path(args.bucket_patch).read_text(encoding="utf-8-sig"))
        bucket_patch = doc
    inline_b = _parse_pairs(args.bucket, "凭证号", "业务桶")
    if inline_b:
        bucket_patch = bucket_patch or {"vouchers": {}}
        bucket_patch.setdefault("vouchers", {})
        bucket_patch["vouchers"].update(inline_b)

    return pipe.run_file(
        args.file,
        out_dir=args.out,
        override=_parse_pairs(args.set, "字段", "列名"),
        cf_patch=cf_patch,
        bucket_patch=bucket_patch,
        progress=None if args.quiet else _log,
    )


# ============================================================================
# main
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cfw", description="现金流量表编写（依赖 summary_cleaner 判业务桶）")
    p.add_argument("--version", action="version", version="现金流量表编写 1.0.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="环境自检")
    s.add_argument("--out-json")

    c = sub.add_parser("check", help="读文件 + 认列名 + 数货币资金凭证（只读）")
    c.add_argument("file")
    c.add_argument("--set", action="append", metavar="字段=列名",
                   help="手工指定列名映射，可重复")
    c.add_argument("--out-json")

    r = sub.add_parser("run", help="业务分类 → 编现金流量表 → 导出")
    r.add_argument("file")
    r.add_argument("--out", help="输出目录（默认：序时账同级的「现金流量表编写结果」）")
    r.add_argument("--set", action="append", metavar="字段=列名")
    r.add_argument("--cf", action="append", metavar="行号=现金流量项目",
                   help="给待复核条目直接下结论，可重复")
    r.add_argument("--cf-patch", help="现金流补丁 JSON 文件")
    r.add_argument("--bucket", action="append", metavar="凭证号=业务桶",
                   help="应急：覆盖某张凭证的业务桶，可重复")
    r.add_argument("--bucket-patch", help="业务桶覆盖 JSON 文件")
    r.add_argument("--quiet", action="store_true", help="不输出进度")
    r.add_argument("--out-json")

    v = sub.add_parser("validate", help="现金流映射表自检")
    v.add_argument("--out-json")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {"status": cmd_status, "check": cmd_check,
               "run": cmd_run, "validate": cmd_validate}[args.cmd]
    try:
        payload = handler(args)
    except EnvError as e:
        _log(f"[env] {e.detail}")
        return _emit({"ok": False, "stage": e.stage, "missing": e.missing,
                      "error": e.detail, "hint": _hint(e)},
                     getattr(args, "out_json", None))
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        return _emit({"ok": False, "stage": "error",
                      "error": f"{type(e).__name__}: {e}"},
                     getattr(args, "out_json", None))
    return _emit(payload, getattr(args, "out_json", None))


if __name__ == "__main__":
    sys.exit(main())
