# -*- coding: utf-8 -*-
"""`journal` 命令行 —— 程序与 AI 打交道的唯一入口。

    journal features                       列出功能（前端菜单也读这个）
    journal run  "序时账.xlsx" --feature cashflow
    journal next <job_id>                  下一步该谁做什么（机器可读）
    journal submit <job_id> --file decisions.json
    journal status <job_id>
    journal resume <job_id>                失败修好后继续
    journal export <job_id> [--dest 路径]   取出产物

约定：stdout 只输出**单行 JSON**（前缀 `RESULT `），进度走 stderr。
AI 解析时只认 `RESULT ` 开头那一行；人看的话加 `--pretty`。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

RESULT_PREFIX = "RESULT "


# ============================================================================
# 输出
# ============================================================================

def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _emit(payload: Dict[str, Any], pretty: bool = False) -> int:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if pretty:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2,
                                    default=str) + "\n")
    else:
        sys.stdout.write(RESULT_PREFIX + text + "\n")
    sys.stdout.flush()
    ok = payload.get("ok", True)
    status = payload.get("status")
    return 0 if ok and status != "failed" else 1


# ============================================================================
# 命令
# ============================================================================

def cmd_features(args) -> Dict[str, Any]:
    import features
    return {"ok": True, "features": features.manifests()}


def cmd_run(args) -> Dict[str, Any]:
    from jobflow import Job

    options: Dict[str, Any] = {}
    for raw in args.option or []:
        if "=" not in raw:
            raise SystemExit(f"--option 格式错：{raw!r}，应形如 key=value")
        k, v = raw.split("=", 1)
        options[k.strip()] = v.strip()
    if args.max_review:
        options["max_review"] = int(args.max_review)

    job = Job.create(args.feature, args.file, options=options, root=args.root)
    _log(f"任务 {job.id}（{job.feature.name}）")
    payload = job.advance(log=_log if not args.quiet else None)
    payload["dir"] = str(job.dir)
    return payload


def cmd_next(args) -> Dict[str, Any]:
    from jobflow import Job
    return Job.open(args.job).next_action()


def cmd_status(args) -> Dict[str, Any]:
    from jobflow import Job
    job = Job.open(args.job)
    return {"ok": True, **job.summary()}


def cmd_resume(args) -> Dict[str, Any]:
    from jobflow import Job
    job = Job.open(args.job)
    job.state["status"] = "running"
    job.state["error"] = None
    job.save()
    return job.advance(log=_log if not args.quiet else None)


def cmd_submit(args) -> Dict[str, Any]:
    from jobflow import Job

    if args.empty:
        doc = {"stage": args.stage, "decisions": [], "waive": True}
        if not doc["stage"]:
            raise SystemExit("--empty 需要同时给 --stage <阶段 id>")
    else:
        if not args.file:
            raise SystemExit("要么 --file decisions.json，要么 --empty --stage <id>")
        p = Path(args.file)
        if not p.exists():
            raise SystemExit(f"decision 文件不存在：{p}")
        doc = json.loads(p.read_text(encoding="utf-8-sig"))

    job = Job.open(args.job)
    payload = job.submit(doc, log=_log if not args.quiet else None)
    payload["dir"] = str(job.dir)
    return payload


def cmd_export(args) -> Dict[str, Any]:
    from jobflow import Job

    job = Job.open(args.job)
    outputs = job.outputs()
    key = args.kind or "result"
    if key not in outputs:
        done = [s for s in job.feature.stages if s.id == "export"]
        return {"ok": False, "status": job.status,
                "error": f"没有产物 {key!r}（任务状态：{job.status}）"
                         + ("，先跑完 export" if done else ""),
                "available": list(outputs)}
    src = Path(outputs[key])
    if args.dest:
        dest = Path(args.dest)
        # 目录（存在，或没写后缀）→ 放到里面；否则当成目标文件名
        if dest.is_dir() or (not dest.exists() and not dest.suffix):
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return {"ok": True, "exported": str(dest), "from": str(src)}
    return {"ok": True, "path": str(src)}


# ============================================================================
# main
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="journal",
        description="序时账相关的多阶段任务编排（程序阶段 + AI 复核阶段）")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("features", help="列出可用功能")
    f.add_argument("--pretty", action="store_true")

    r = sub.add_parser("run", help="建任务并跑到第一个 AI 阶段")
    r.add_argument("file")
    r.add_argument("--feature", default="cashflow")
    r.add_argument("--root", help="任务目录（默认 _storage_jobs/）")
    r.add_argument("--option", action="append", metavar="k=v",
                   help="透传给阶段的选项，可重复")
    r.add_argument("--max-review", type=int, help="每个 AI 阶段最多给多少条待办")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--pretty", action="store_true")

    n = sub.add_parser("next", help="下一步做什么")
    n.add_argument("job")
    n.add_argument("--pretty", action="store_true")

    s = sub.add_parser("status", help="任务状态")
    s.add_argument("job")
    s.add_argument("--pretty", action="store_true")

    b = sub.add_parser("submit", help="提交 AI 结论并继续")
    b.add_argument("job")
    b.add_argument("--file", help="decision 文件")
    b.add_argument("--empty", action="store_true", help="这批没有要改的（空结论）")
    b.add_argument("--stage", help="--empty 时指定阶段")
    b.add_argument("--quiet", action="store_true")
    b.add_argument("--pretty", action="store_true")

    m = sub.add_parser("resume", help="失败/中断后继续")
    m.add_argument("job")
    m.add_argument("--quiet", action="store_true")
    m.add_argument("--pretty", action="store_true")

    e = sub.add_parser("export", help="取出产物")
    e.add_argument("job")
    e.add_argument("--kind", default="result")
    e.add_argument("--dest", help="复制到这里（目录或文件路径）")
    e.add_argument("--pretty", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "features": cmd_features, "run": cmd_run, "next": cmd_next,
        "status": cmd_status, "submit": cmd_submit, "resume": cmd_resume,
        "export": cmd_export,
    }[args.cmd]
    try:
        payload = handler(args)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.stderr)
        return _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                     getattr(args, "pretty", False))
    return _emit(payload, getattr(args, "pretty", False))


if __name__ == "__main__":
    sys.exit(main())
