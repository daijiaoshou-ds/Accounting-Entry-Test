# -*- coding: utf-8 -*-
"""`journal` 命令行 —— 程序与 AI 打交道的唯一入口。

    journal features                       列出功能（前端菜单也读这个）
    journal inspect "序时账.xlsx"            读表：列名 + 建议映射 + 缺哪些必需字段
    journal create "序时账.xlsx" --feature cleaning
                                           只建任务（后台起任务时先拿 job_id）
    journal run  "序时账.xlsx" --feature cashflow
    journal inbox                          有哪些任务在等 AI（agent 一进来先看这个）
    journal next <job_id>                  下一步 + 待办条目（内联，最多 30 条）
    journal request <job_id>               分页取待办（条数多时用）
    journal submit <job_id> --file decisions.json [--by ai|human]
    journal report <job_id>                结果摘要（跑完之后给用户看）
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
# 选项与输入
# ============================================================================

def _read_mapping(raw: str) -> Dict[str, str]:
    """字段映射的三种写法：JSON 串 / @文件 / 已存在的文件路径。

    值可以是空串（表示"这个字段不映射"），会被丢掉——空列名传给 `run_read`
    只会在校验那里报一句"表里没有这一列"，不如在这里就不传。
    """
    text = raw.strip()
    if text.startswith("@"):
        text = Path(text[1:].strip()).read_text(encoding="utf-8-sig")
    elif not text.startswith("{") and Path(text).is_file():
        text = Path(text).read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--mapping 不是合法 JSON：{exc}") from None
    if not isinstance(value, dict):
        raise SystemExit(f"--mapping 必须是对象：{{\"voucher_id\": \"凭证号\", …}}，收到 {type(value).__name__}")
    return {str(k): str(v).strip() for k, v in value.items()
            if v is not None and str(v).strip()}


def _parse_options(args) -> Dict[str, Any]:
    """`--option` / `--mapping` / `--max-review` → `options` 字典。

    `--mapping` 是"配置字段"那一步的入口。以前只有 HTTP 的 Form 能传它：
    命令行只能给扁平的 `--option k=v`，而 `run_read` 拿到的 `options["mapping"]`
    必须是 dict——传字符串进去会直接崩在 `.items()` 上。
    """
    options: Dict[str, Any] = {}
    for raw in args.option or []:
        if "=" not in raw:
            raise SystemExit(f"--option 格式错：{raw!r}，应形如 key=value")
        k, v = raw.split("=", 1)
        options[k.strip()] = v.strip()
    if getattr(args, "max_review", None):
        options["max_review"] = int(args.max_review)
    if getattr(args, "mapping", None):
        options["mapping"] = _read_mapping(args.mapping)
    return options


def _source_or_upstream(file, options: Dict[str, Any]):
    """有文件就用文件；只给了 `upstream=<job_id>` 就用上游那份原件。

    接上游时必须用**同一份账**：文件名与 sha 都要对得上，`link` 阶段才搬得进去。
    这里直接拿上游的原件建任务，比让调用方再传一次更不容易错。
    """
    if file:
        return file
    jid = options.get("upstream")
    if not jid:
        raise SystemExit("要么给序时账文件，要么 --option upstream=<job_id>")
    from jobflow import Job

    up = Job.open(jid)
    src = up.dir / str((up.state.get("input") or {}).get("file") or "")
    if not src.is_file():
        raise SystemExit(f"上游任务 {jid} 的原件找不到了：{src}")
    return src


# ============================================================================
# 命令
# ============================================================================

def cmd_features(args) -> Dict[str, Any]:
    import features
    return {"ok": True, "features": features.manifests()}


def cmd_inspect(args) -> Dict[str, Any]:
    """读表并报"列名 ↔ 字段"的识别结果：不建任务、不加载模型。

    "上传 → 配置字段"以前只活 HTTP 那一层（`POST /api/flow/inspect`）。命令行
    补上它，整条链才能不依赖服务端跑完：先 inspect 让人确认映射，再把确认结果
    用 `--mapping` 传回 `run`/`create`。

    只读 `journal_io`（静态字段表 + 读表引擎），**不 import features**——
    为一个字段清单把 torch 拉起来不值（约 5s）。
    """
    import journal_io

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    feature = (args.feature or "cleaning").strip()
    required = journal_io.requires_of(feature)
    keys = journal_io.ui_fields_of(feature) or required

    j = journal_io.read(path, requires=())
    mapping = dict(j.schema.mapping)
    missing = [k for k in required if not mapping.get(k)]

    def _label(key: str) -> str:
        spec = journal_io.FIELD_BY_KEY.get(key)
        return spec.label if spec else key

    return {
        "ok": True,
        "filename": path.name,
        "feature": feature,
        "rows": int(len(j)),
        "columns": list(j.columns),
        "engine": j.schema.engine,
        "suggested_mapping": mapping,
        "unmapped": list(j.schema.unmapped),
        # 表单就是这张表：key/label 给人看，column 是自动认到的列（可改写）
        "fields": [{"key": k, "label": _label(k), "required": k in required,
                    "column": mapping.get(k, "")} for k in keys],
        "missing": missing,
        "missing_labels": journal_io.labels(missing),
        "mapping_complete": not missing,
        # 前 5 行够看出列长什么样；整表交给前端也没意义（一本账 6 万行）
        "preview": json.loads(
            j.typed.head(5).to_json(orient="records", force_ascii=False)),
    }


def cmd_create(args) -> Dict[str, Any]:
    """只建任务，不往下跑（跑用 `journal resume <id>`）。

    给"后台起任务"的调用方用（插件、脚本）：先拿到 job_id，才能在它跑的同时
    查状态、看进度。`run` 会一直阻塞到第一个 AI 阶段，拿不到中间的 id。
    """
    from jobflow import Job

    options = _parse_options(args)
    source = _source_or_upstream(args.file, options)
    job = Job.create(args.feature, source, options=options, root=args.root)
    return {"ok": True, "job_id": job.id, "dir": str(job.dir),
            "feature": job.state["feature"],
            "feature_name": job.state.get("feature_name"),
            "status": job.status, "input": job.state.get("input")}


def cmd_run(args) -> Dict[str, Any]:
    from jobflow import Job

    options = _parse_options(args)
    source = _source_or_upstream(args.file, options)
    job = Job.create(args.feature, source, options=options, root=args.root)
    _log(f"任务 {job.id}（{job.feature.name}）")
    payload = job.advance(log=_log if not args.quiet else None)
    payload["dir"] = str(job.dir)
    return payload


def cmd_next(args) -> Dict[str, Any]:
    """下一步 + 待办条目。

    条目**内联**在返回值里，是因为这个命令的消费者就是 AI：让它为了看 35 条待办
    再跑一次 `cat reviews/*.json`，等于每次复核多一次工具调用、还容易读错路径。
    """
    from jobflow import Job
    return Job.open(args.job).next_action(
        with_items=not args.no_items, item_limit=args.limit)


def cmd_inbox(args) -> Dict[str, Any]:
    """列任务。默认只列"在等 AI/失败"的——已经跑完的不占注意力。"""
    from jobflow import scan_jobs
    rows = scan_jobs()
    if not args.all:
        rows = [r for r in rows if r["status"] in ("awaiting_review", "failed")]
    return {"ok": True, "count": len(rows), "jobs": rows[:args.limit]}


def cmd_request(args) -> Dict[str, Any]:
    """分页取某个 AI 阶段的待办（大账一次上千条，`next` 只给前 30 条）。"""
    from jobflow import Job
    job = Job.open(args.job)
    sid = args.stage
    if not sid:
        nxt = job.next_action(with_items=False).get("next") or {}
        sid = nxt.get("stage") or ""
    if not sid:
        return {"ok": False, "status": job.status,
                "error": "当前没有等待复核的阶段（用 journal status 看阶段状态）"}

    doc = job.request_doc(sid)
    if not doc:
        return {"ok": False, "status": job.status,
                "error": f"阶段 {sid} 还没有待办（没跑到，或已提交完）"}
    items = doc.get("items") or []
    lo = max(0, args.offset)
    return {
        "ok": True, "job_id": job.id, "stage": sid,
        "title": job.stage(sid).title,
        "help": doc.get("help", ""),
        "key_name": doc.get("key_name", "key"),
        "value_enum": doc.get("value_enum") or [],
        "total": len(items), "offset": lo, "limit": args.limit,
        "items": items[lo:lo + args.limit],
        "already_decided": job.decisions(sid),
        "decision_format": (doc.get("how_to_submit") or {}).get("format"),
        "submit": f"journal submit {job.id} --file decisions.json",
    }


def cmd_report(args) -> Dict[str, Any]:
    """任务结果摘要：给 agent 转述给用户用的那一份。

    和 `status` 的区别：status 讲阶段，report 讲**结果**（规模、桶分布、
    置信度、待复核、产物路径），且不带待办条目。
    """
    from jobflow import Job
    job = Job.open(args.job)
    s = job.summary()

    # 结果事实：取最后一个跑完的程序阶段（通常是 export），去掉内部文件清单
    result: Dict[str, Any] = {}
    for st in reversed(job.feature.stages):
        if st.actor != "program":
            continue
        if job.stage_state(st.id).get("status") != "done":
            continue
        f = dict(job.stage_facts(st.id))
        f.pop("files", None)
        if f:
            result = f
            break

    nxt = job.next_action(with_items=False).get("next")
    return {"ok": True, **s, "result": result, "next": nxt}


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

    # 谁交的写进 decision（幂等键之外的审计信息）。agent 交的与人在网页上改的
    # 结论混在一起时，事后要能分开看。
    doc.setdefault("by", args.by)
    for d in doc.get("decisions") or []:
        if isinstance(d, dict):
            d.setdefault("by", args.by)

    job = Job.open(args.job)
    # `--no-advance`：只落盘结论，不往下跑。给"交完结论就得立刻回话"的调用方用
    # （插件/HTTP 要同步返回采纳了几条，而下游可能是几分钟的分类）。
    payload = job.submit(doc, log=_log if not args.quiet else None,
                         advance=not args.no_advance)
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

    i = sub.add_parser("inspect", help="读表：列名 + 建议映射 + 缺哪些必需字段")
    i.add_argument("file")
    i.add_argument("--feature", default="cleaning")
    i.add_argument("--pretty", action="store_true")

    c = sub.add_parser("create", help="只建任务，不跑（后台起任务时先拿 job_id）")
    c.add_argument("file", nargs="?",
                   help="序时账文件；用 --option upstream=<job_id> 接着跑时可省略")
    c.add_argument("--feature", default="cleaning")
    c.add_argument("--root", help="任务目录（默认 _storage_jobs/）")
    c.add_argument("--option", action="append", metavar="k=v",
                   help="透传给阶段的选项，可重复")
    c.add_argument("--mapping", metavar="JSON|@FILE",
                   help='字段映射（canonical 名 → 列名），如 {"voucher_id": "凭证号"}')
    c.add_argument("--max-review", type=int, help="每个 AI 阶段最多给多少条待办")
    c.add_argument("--pretty", action="store_true")

    r = sub.add_parser("run", help="建任务并跑到第一个 AI 阶段")
    r.add_argument("file", nargs="?",
                   help="序时账文件；用 --option upstream=<job_id> 接着跑时可省略")
    r.add_argument("--feature", default="cashflow")
    r.add_argument("--root", help="任务目录（默认 _storage_jobs/）")
    r.add_argument("--option", action="append", metavar="k=v",
                   help="透传给阶段的选项，可重复")
    r.add_argument("--mapping", metavar="JSON|@FILE",
                   help='字段映射（canonical 名 → 列名），如 {"voucher_id": "凭证号"}')
    r.add_argument("--max-review", type=int, help="每个 AI 阶段最多给多少条待办")
    r.add_argument("--quiet", action="store_true")
    r.add_argument("--pretty", action="store_true")

    n = sub.add_parser("next", help="下一步做什么（含待办条目）")
    n.add_argument("job")
    n.add_argument("--no-items", action="store_true", help="只要动作，不要条目")
    n.add_argument("--limit", type=int, default=30, help="内联多少条待办（默认 30）")
    n.add_argument("--pretty", action="store_true")

    ib = sub.add_parser("inbox", help="有哪些任务在等 AI")
    ib.add_argument("--all", action="store_true", help="连跑完的也列")
    ib.add_argument("--limit", type=int, default=50)
    ib.add_argument("--pretty", action="store_true")

    rq = sub.add_parser("request", help="分页取待办")
    rq.add_argument("job")
    rq.add_argument("--stage", help="默认当前等待复核的阶段")
    rq.add_argument("--offset", type=int, default=0)
    rq.add_argument("--limit", type=int, default=50)
    rq.add_argument("--pretty", action="store_true")

    rp = sub.add_parser("report", help="结果摘要")
    rp.add_argument("job")
    rp.add_argument("--pretty", action="store_true")

    s = sub.add_parser("status", help="任务状态")
    s.add_argument("job")
    s.add_argument("--pretty", action="store_true")

    b = sub.add_parser("submit", help="提交 AI 结论并继续")
    b.add_argument("job")
    b.add_argument("--file", help="decision 文件")
    b.add_argument("--empty", action="store_true", help="这批没有要改的（空结论）")
    b.add_argument("--stage", help="--empty 时指定阶段")
    b.add_argument("--by", default="ai", choices=("ai", "human"),
                   help="谁交的结论（记进 decision，便于事后区分）")
    b.add_argument("--no-advance", action="store_true",
                   help="只落盘结论、不往下跑（调用方自己再 `resume`）")
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
        "features": cmd_features, "inspect": cmd_inspect, "create": cmd_create,
        "run": cmd_run, "next": cmd_next,
        "inbox": cmd_inbox, "request": cmd_request, "report": cmd_report,
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
