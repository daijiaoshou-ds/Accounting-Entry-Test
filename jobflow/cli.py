# -*- coding: utf-8 -*-
"""`journal` 命令行 —— 程序与 AI 打交道的唯一入口。

    journal features                       列出功能（前端菜单也读这个）
    journal env                            东西在哪：仓库 / 任务目录 / 模型 / 桶表 / 复核口径
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
    journal abort  <job_id>                中止（先杀进程再调它）
    journal export <job_id> [--dest 路径]   取出产物

约定：stdout 只输出**单行 JSON**（前缀 `RESULT `），进度走 stderr。
AI 解析时只认 `RESULT ` 开头那一行；人看的话加 `--pretty`。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
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
    # domains 是纯数据（不 import 功能模块）：前端靠它建分组标题和新建入口，
    # 功能的阶段链/能不能跑仍然在 features 里。
    return {"ok": True, "features": features.manifests(), "domains": features.domains()}


def cmd_env(args) -> Dict[str, Any]:
    """「东西在哪」——给插件与外部调用方的一条命令。

    插件以前自己拼这些路径（`join(repo, 'core/nn/_storage')` 之类），于是每次
    挪目录都要改 JS，改漏了还不报错（桶表读不到只是少一段提示）。路径的真相
    在 Python 这边，就让它从这里问：**目录布局只有一处**。

    只读，不加载模型、不碰任何任务。
    """
    import sys
    from jobflow import config as jobflow_config
    from core.classify.config import get_nn_storage_dir, NN_MODEL_CACHE_DIR
    from core.classify.bucket_registry_v10 import taxonomy_path
    import features

    repo = Path(jobflow_config.REPO_ROOT)
    features_dir = repo / "features"
    # 复核口径跟功能目录一起走：有 `review-guide.md` 的功能才有（现在两个）
    guides = {p.parent.name: str(p)
              for p in sorted(features_dir.glob("*/review-guide.md"))}
    return {
        "ok": True,
        "repo": str(repo),
        "jobs_dir": str(jobflow_config.JOBS_DIR),
        "model_dir": str(get_nn_storage_dir()),
        "model_cache": str(NN_MODEL_CACHE_DIR),
        "taxonomy": str(taxonomy_path()),
        "review_guides": guides,
        "features": features.ids(),
        "python": sys.executable,
        "python_version": sys.version.split()[0],
    }


def cmd_inspect(args) -> Dict[str, Any]:
    """读表并报"列名 ↔ 字段"的识别结果：不建任务、不加载模型。

    "上传 → 配置字段"以前只活 HTTP 那一层（`POST /api/flow/inspect`）。命令行
    补上它，整条链才能不依赖服务端跑完：先 inspect 让人确认映射，再把确认结果
    用 `--mapping` 传回 `run`/`create`。

    **按域而不是按功能看表**（`--domain`）：域决定"这份文件算不算这个域的数据"，
    所以字段空间是一个域的（5–8 个），不是二十个功能并集的那几百个。`--feature`
    仍然可用，只是一个域里挑功能的写法。

    返回给前端的两段是关键：
      · `required`：必需字段，每个带识别到的列 + 样例值 —— 已识别的默认折叠，
        只把没认出来的摆到人面前；
      · `unused`：没参与映射的列，**默认状态就是"不参与"**，不是"待归类"。
        这样配置量才不随列数增长。

    只读 `journal_io`（静态字段表 + 读表引擎），**不 import features**——
    为一个字段清单把 torch 拉起来不值（约 5s）。
    """
    import journal_io
    from journal_io import contracts

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    feature_id = (args.feature or "").strip()
    domain_id = (getattr(args, "domain", "") or "").strip()
    if not domain_id:
        domain_id = contracts.of_feature(feature_id or "cleaning")
    spec = contracts.get(domain_id)
    primary = spec.primary

    required = tuple(primary.fields)
    keys = contracts.ui_fields_of(domain_id)

    j = journal_io.read(path, requires=())
    mapping = dict(j.schema.mapping)
    missing = [k for k in required if not mapping.get(k)]
    typed = j.typed

    def _label(key: str) -> str:
        found = journal_io.FIELD_BY_KEY.get(key)
        return found.label if found else key

    def _samples(column: str, limit: int = 3):
        """这列长什么样 —— 比置信度百分比有用：人看一眼样例就知道对不对。"""
        if not column or column not in typed.columns:
            return []
        out: List[str] = []
        for value in typed[column].dropna().head(limit).tolist():
            text = str(value).strip()
            if text and text.lower() != "nan":
                out.append(text)
        return out

    def _row(key: str) -> Dict[str, Any]:
        column = mapping.get(key, "")
        return {
            "key": key,
            "label": _label(key),
            "column": column,
            "required": key in required,
            # auto = 列名规则认出来的；missing = 没认到，要人看一眼
            "status": "auto" if column else ("missing" if key in required else "empty"),
            "samples": _samples(column),
        }

    required_rows = [_row(k) for k in required]
    optional_rows = [_row(k) for k in keys if k not in required]

    used = set(mapping.values())
    unused = [{"column": str(c), "samples": _samples(str(c))}
              for c in j.columns if str(c) not in used]

    return {
        "ok": True,
        "filename": path.name,
        # feature 只为兼容老调用方；新代码看 domain
        "feature": feature_id or domain_id,
        "domain": domain_id,
        "domain_name": spec.name,
        "input_label": primary.label,
        "input_hint": primary.hint,
        "input_many": primary.many,
        "rows": int(len(j)),
        "columns": list(j.columns),
        "engine": j.schema.engine,
        "suggested_mapping": mapping,
        "unmapped": [item["column"] for item in unused],
        # ── 新结构：配置页只渲染这两段 ──
        "required": required_rows,
        "optional": optional_rows,
        "unused": unused,
        "missing": missing,
        "missing_labels": journal_io.labels(missing),
        "mapping_complete": not missing,
        # 老结构：agent 侧文本渲染和早一版页面还在读它
        "fields": [{"key": r["key"], "label": r["label"], "required": r["required"],
                    "column": r["column"]} for r in required_rows + optional_rows],
        # 前 5 行够看出列长什么样；整表交给前端也没意义（一本账 6 万行）
        "preview": json.loads(
            typed.head(5).to_json(orient="records", force_ascii=False)),
    }


def _subject_report(j, out_dir: Path) -> Dict[str, Any]:
    """配账时该人看一眼的科目：归一化之后仍不是标准一级科目的写法。

    这批就是"别名表收不下"的：客户名当一级科目、银行账户名当一级科目、临时
    过渡科目……它们能照常往下跑（模型按摘要判），但科目信号是偏的——比如一个
    没认出来的账户名不会被当成货币资金。配账是唯一适合和人对一次科目的时机：
    跑起来之后再改，得把已经判好的账整本重算。

    报告落进 `prepared/unknown_subjects.json`，页面据此弹出"对一下科目"。
    """
    from core.classify.subjects import standard_subjects, unmapped

    standard = sorted(standard_subjects())
    try:
        items = unmapped(j.df, j.schema.legacy(), standard=set(standard))
        error = ""
    except Exception as exc:  # noqa: BLE001 - 扫描失败不能挡住配账
        items, error = [], f"{type(exc).__name__}: {exc}"

    if items:
        (out_dir / "unknown_subjects.json").write_text(
            json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "items": items}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    return {"unknown": items, "standard": standard, "scan_error": error}


def cmd_prepare(args) -> Dict[str, Any]:
    """算一张账的科目组合（一级 / 一级+明细），落到指定目录。

    这份结果不属于某个任务，是**账本身**的属性：账套配置时跑一次，之后各功能
    共用。只做原生账上的组合，不做 SS 归一化——归一化后的"模式"是对方科目分析
    自己的逻辑，拿它当公共资产，别的功能看到的就不是账上写的那个样子了。

    一次配账顺带算出「未知科目」清单（别名表收不下的写法）：配账是唯一该和
    人对一次科目的时机——跑起来之后再改，得把已经判好的账整本重算。
    """
    import journal_io
    from .prepare import prepare

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"文件不存在：{path}")

    j = journal_io.read(path, requires=())
    for key, col in (_read_mapping(args.mapping) if getattr(args, "mapping", "") else {}).items():
        if col not in j.df.columns:
            raise ValueError(f"mapping 里 {key}={col!r}：表里没有这一列")
        j.schema.mapping[key] = col
    j.require(("voucher_id", "subject_l1", "debit", "credit"))

    out_dir = Path(args.out)
    summary = prepare(j, out_dir)
    summary.update(_subject_report(j, out_dir))
    return {"ok": True, **summary}


def cmd_alias(args) -> Dict[str, Any]:
    """把「账上写法 → 标准一级科目」写进别名表（全局，所有账共用）。

    这是配账时那个"对一下科目"的落盘动作：页面上选完标准科目，这里校验目标
    科目合法、追加到别名表、清掉内存缓存。**不用重训**，下一次读表即生效。
    """
    from accounting_domain.subject_norm import add_aliases, table_path
    from core.classify.subjects import standard_subjects

    if getattr(args, "standard", False):
        return {"ok": True, "standard": sorted(standard_subjects())}

    pairs: Dict[str, str] = {}
    for item in args.set or []:
        if "=" not in item:
            raise SystemExit(f"--set 需要「账上写法=标准科目」，收到：{item}")
        raw, target = item.split("=", 1)
        pairs[raw.strip()] = target.strip()
    if not pairs:
        raise SystemExit("没有要写入的对应关系（--set 写法=标准科目）")

    standard = standard_subjects()
    bad = {raw: target for raw, target in pairs.items() if target not in standard}
    if bad:
        raise SystemExit(
            "这些目标科目不在标准一级科目表里："
            + "、".join(f"{raw}→{target}" for raw, target in bad.items())
            + f"（可选 {len(standard)} 个，见 `journal alias --standard`）")

    added = add_aliases(pairs)
    return {"ok": True, "applied": {str(k): v for k, v in added.items()},
            "names": sum(len(v) for v in added.values()),
            "table": str(table_path())}


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


def cmd_abort(args) -> Dict[str, Any]:
    """中止一个任务（页面的「■ 中止」/ 命令行）。

    进程由调用方负责杀掉（插件杀自己起的子进程，命令行看情况）。只把状态写成
    aborted 是不够的：还在跑的 python 进程会继续写 state.json，反过来覆盖掉。
    所以正确的顺序是**先杀进程、再 abort**。
    """
    from jobflow import Job

    job = Job.open(args.job)
    return job.abort(reason=(args.reason or "已中止"))


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

    pr = sub.add_parser("prepare", help="算科目组合（一级 / 一级+明细），写到指定目录")
    pr.add_argument("file", help="序时账文件")
    pr.add_argument("--out", required=True, help="输出目录（combo.parquet + combo.json）")
    pr.add_argument("--mapping", default="", help="字段映射：JSON 串 / @文件")

    al = sub.add_parser("alias", help="把「账上写法 → 标准一级科目」写进别名表（全局）")
    al.add_argument("--set", action="append", metavar="写法=科目",
                    help="可重复，如 --set 万和=其他经营收支")
    al.add_argument("--standard", action="store_true", help="只列出标准一级科目表")
    al.add_argument("--pretty", action="store_true")
    f = sub.add_parser("features", help="列出可用功能")
    f.add_argument("--pretty", action="store_true")

    ev = sub.add_parser("env", help="东西在哪：仓库 / 任务目录 / 模型 / 桶表 / 复核口径")
    ev.add_argument("--pretty", action="store_true")

    i = sub.add_parser("inspect", help="读表：列名 + 建议映射 + 缺哪些必需字段")
    i.add_argument("file")
    i.add_argument("--feature", default="cleaning")
    # 域比功能高一层：域决定字段空间（5–8 个）和上传标的，功能只决定能不能跑
    i.add_argument("--domain", default="", help="数据域 id（ledger / cost），优先于 --feature")
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

    ab = sub.add_parser("abort", help="中止任务（先杀掉进程，再调它）")
    ab.add_argument("job")
    ab.add_argument("--reason", default="已中止", help="写进 state 的原因（给人看）")
    ab.add_argument("--pretty", action="store_true")

    e = sub.add_parser("export", help="取出产物")
    e.add_argument("job")
    e.add_argument("--kind", default="result")
    e.add_argument("--dest", help="复制到这里（目录或文件路径）")
    e.add_argument("--pretty", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = {
        "features": cmd_features, "env": cmd_env,
        "inspect": cmd_inspect, "prepare": cmd_prepare, "alias": cmd_alias,
        "create": cmd_create,
        "run": cmd_run, "next": cmd_next,
        "inbox": cmd_inbox, "request": cmd_request, "report": cmd_report,
        "status": cmd_status, "submit": cmd_submit, "resume": cmd_resume,
        "abort": cmd_abort,
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
