# -*- coding: utf-8 -*-
"""通用编排 API（jobflow）—— 功能清单 + 阶段任务 + 复核结论。

这一层只做三件事：路由、参数校验、把异常映射成 HTTP。业务在 `features/*`
（阶段实现）与 `jobflow`（状态机）里。

    GET    /api/features                  功能清单（前端菜单直接读）
    GET    /api/features/{fid}            单个功能（含阶段链，前端画进度用）
    GET    /api/features/{fid}/fields     上传表单的字段清单
    POST   /api/flow/inspect              上传即读表（列名 + 建议映射 + 预览）
    POST   /api/features/{fid}/jobs       上传建任务（或给 upstream=<job_id> 接着跑）
    POST   /api/features/{fid}/jobs/local 本机路径建任务（免上传）
    GET    /api/features/{fid}/upstream   可"接着跑"的已完成任务
    GET    /api/flow                      最近任务
    GET    /api/flow/{jid}                状态摘要（阶段时间线）
    GET    /api/flow/{jid}/next           下一步该谁做什么（唯一入口）
    GET    /api/flow/{jid}/review         AI 待办（分页 + 列定义 + 已交结论）
    POST   /api/flow/{jid}/decisions      提交结论（同步校验，后台继续跑）
    POST   /api/flow/{jid}/resume         失败/中断后继续
    GET    /api/flow/{jid}/files          产物清单（含现金流量表这类统计事实）
    GET    /api/flow/{jid}/files/{key}    下载
    DELETE /api/flow/{jid}

**为什么用 `/api/flow` 而不是 `/api/jobs`**：5.0 之前 `/api/jobs` 是清洗那套一次性
任务（`JobStore`，没有阶段、没有 decision）。清洗迁进编排层之后那套接口删了，
前缀空了出来——但**这里不改名**：前端、脚本、SKILL 里写的都是 `/api/flow`，
改一次要动四处、没有任何收益。
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from jobflow import JOBS_DIR, Job, read_json
from .errors import ServiceError

router = APIRouter(prefix="/api")

# 任务目录名就是这个正则；顺带挡掉 `../` 之类的路径穿越
JOB_ID_RE = re.compile(r"^[0-9A-Za-z_\-]{4,64}$")

# 单线程：分类阶段吃 GPU，并发跑只会互相抢显存
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="flow-job")
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_running: set = set()
_running_guard = threading.Lock()


# ============================================================================
# 后台执行
# ============================================================================

def _lock(job_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(job_id, threading.Lock())


def is_running(job_id: str) -> bool:
    with _running_guard:
        return job_id in _running


def _schedule(job_id: str, what: str, fn, *args) -> None:
    """把一次 advance/submit 丢给后台线程（同一任务串行）。"""
    with _running_guard:
        _running.add(job_id)

    def _work() -> None:
        try:
            with _lock(job_id):
                fn(job_id, *args)
        except Exception as exc:  # noqa: BLE001 - 状态已写进 state.json
            print(f"[FLOW {job_id}] {what}失败：{type(exc).__name__}: {exc}")
        finally:
            with _running_guard:
                _running.discard(job_id)

    _executor.submit(_work)


def _advance(job_id: str) -> None:
    Job.open(job_id).advance()


# ============================================================================
# 小工具
# ============================================================================

def _job(job_id: str) -> Job:
    if not JOB_ID_RE.match(str(job_id)):
        raise ServiceError("invalid_job_id", f"任务号不合法：{job_id}", 400)
    try:
        return Job.open(job_id)
    except FileNotFoundError:
        raise ServiceError("not_found", f"找不到任务：{job_id}", 404) from None


def _feature(feature_id: str):
    import features
    try:
        return features.get(feature_id)
    except KeyError as exc:
        raise ServiceError("unknown_feature", str(exc), 404) from None


def _feature_names() -> Dict[str, str]:
    """id → 中文名。注册表已缓存，列任务清单时顺手取一份。"""
    try:
        import features
        return {m["id"]: m["name"] for m in features.manifests()}
    except Exception:  # noqa: BLE001 - 名字取不到不该让列表 500
        return {}


def _awaiting_stage(job: Job) -> Optional[str]:
    for st in job.feature.stages:
        if st.actor == "ai" and job.stage_state(st.id).get("status") == "awaiting":
            return st.id
    return None


def _json_or_none(raw: Optional[str], what: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServiceError("invalid_json", f"{what} 不是合法 JSON：{exc}", 400) from None
    if not isinstance(val, dict):
        raise ServiceError("invalid_json", f"{what} 必须是对象", 400)
    return val


def _create(feature_id: str, src: Path,
            mapping: Optional[Dict[str, str]],
            options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    _feature(feature_id)                       # 早点校验功能号
    opts: Dict[str, Any] = dict(options or {})
    if mapping:
        opts["mapping"] = mapping
    try:
        job = Job.create(feature_id, src, options=opts)
    except FileExistsError:
        raise ServiceError("conflict", "任务号冲突，请重试", 409) from None
    _schedule(job.id, "起始阶段", _advance)
    return {"job_id": job.id, "feature": feature_id, "status": "running"}


# ============================================================================
# 功能清单
# ============================================================================

@router.get("/features")
def list_features() -> Dict[str, Any]:
    import features
    return {"items": features.manifests()}


@router.get("/features/{feature_id}")
def get_feature(feature_id: str) -> Dict[str, Any]:
    return _feature(feature_id).manifest()


@router.get("/features/{feature_id}/fields")
def feature_fields(feature_id: str) -> Dict[str, Any]:
    """上传表单该显示哪些字段。

    **用 canonical 名**（voucher_id / subject_l1 …），因为这里的映射会原样进
    `options.mapping`，而 read 阶段用的是 journal_io 的 canoncial 名。
    历史键名（voucher_no…）只留给清洗那套老接口 `/api/fields`。
    """
    from journal_io import FIELD_BY_KEY, requires_of, ui_fields_of

    feat = _feature(feature_id)
    req = tuple(feat.requires) or requires_of(feature_id)
    keys = ui_fields_of(feature_id) or req
    return {
        "feature": feature_id,
        "required": list(req),
        "fields": [{"key": k, "label": FIELD_BY_KEY[k].label, "required": k in req}
                   for k in keys],
    }


# ============================================================================
# 读表（建任务前确认映射）
# ============================================================================

@router.post("/flow/inspect")
async def inspect(file: UploadFile = File(...),
                  feature: str = Form("cashflow")) -> Dict[str, Any]:
    """上传即读表：只认列名，不做业务判断（不加载模型，秒回）。"""
    import journal_io

    name = file.filename or "upload.xlsx"
    data = await file.read()
    if not data:
        raise ServiceError("empty_file", "文件是空的", 400)
    try:
        j = journal_io.read(data, filename=name, requires=())
    except Exception as exc:  # noqa: BLE001 - 解析失败要让用户看到引擎原话
        raise ServiceError("read_failed", f"读表失败：{exc}", 400) from None

    feat = _feature(feature)
    missing = [k for k in feat.requires if not j.schema.mapping.get(k)]
    return {
        "filename": name,
        "feature": feature,
        "rows": int(len(j)),
        "columns": j.columns,
        "engine": j.schema.engine,
        "suggested_mapping": dict(j.schema.mapping),
        "unmapped": list(j.schema.unmapped),
        "required": list(feat.requires),
        "missing": missing,
        "mapping_complete": not missing,
        "preview": json.loads(
            j.typed.head(20).to_json(orient="records", force_ascii=False)),
    }


# ============================================================================
# 建任务
# ============================================================================

def _check_upstream(feature_id: str, upstream: str) -> Job:
    """校验"接着某个任务跑"：功能得声明接受它，上游得已经有分类结果。"""
    feat = _feature(feature_id)
    up = _job(upstream)
    up_feature = up.state.get("feature")
    if up_feature not in feat.accepts_upstream:
        raise ServiceError(
            "bad_upstream",
            f"{feat.name} 不能接着 {up_feature} 的任务跑（可接："
            f"{list(feat.accepts_upstream) or '无'}）", 400)
    if up.stage_state("clean").get("status") not in ("done", "skipped"):
        raise ServiceError("upstream_not_ready",
                           f"上游任务 {upstream} 的业务分类还没跑完（当前 {up.status}）", 409)
    return up


def _create_from_upstream(feature_id: str, upstream: str,
                          options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    up = _check_upstream(feature_id, upstream)
    opts: Dict[str, Any] = dict(options or {})
    opts["upstream"] = upstream
    # 原件直接用上游那份：同一份账、同一个文件名，导出命名也对得上
    return _create(feature_id, up.source, None, opts)


@router.post("/features/{feature_id}/jobs")
async def create_job(
    feature_id: str,
    file: Optional[UploadFile] = File(None),
    mapping: Optional[str] = Form(None, description="JSON 字符串（canonical 名 → 列名）"),
    upstream: Optional[str] = Form(None, description="接着哪个任务的分类结果跑"),
    options: Optional[str] = Form(None, description="JSON 字符串"),
) -> Dict[str, Any]:
    """上传文件（或指定上游任务）→ 建任务 → 后台跑到第一个 AI 阶段。

    两种起跑方式：
    - 给 `file`：自己读表、自己分类（默认）
    - 给 `upstream=<job_id>`：不传文件，直接接上游的分类与改桶结论，
      read/clean/复核桶三段会被跳过（同一份账不必分类两次）
    """
    opts = _json_or_none(options, "options")
    if upstream:
        return _create_from_upstream(feature_id, upstream, opts)
    if file is None:
        raise ServiceError("file_required", "要么上传文件，要么给 upstream=<job_id>", 400)

    name = file.filename or "upload.xlsx"
    data = await file.read()
    if not data:
        raise ServiceError("empty_file", "文件是空的", 400)

    tmp = Path(tempfile.mkdtemp(prefix="flow-in-"))
    try:
        src = tmp / name
        src.write_bytes(data)
        return _create(feature_id, src, _json_or_none(mapping, "mapping"), opts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/features/{feature_id}/jobs/local")
def create_job_local(
    feature_id: str,
    path: Optional[str] = Body(None, embed=True),
    mapping: Optional[Dict[str, str]] = Body(None, embed=True),
    upstream: Optional[str] = Body(None, embed=True),
    options: Optional[Dict[str, Any]] = Body(None, embed=True),
) -> Dict[str, Any]:
    """本机路径建任务（调试/自动化，省去上传几十 MB）。"""
    if upstream:
        return _create_from_upstream(feature_id, upstream, options)
    if not path:
        raise ServiceError("path_required", "要么给 path，要么给 upstream=<job_id>", 400)
    src = Path(path)
    if not src.is_file():
        raise ServiceError("not_found", f"文件不存在：{path}", 404)
    return _create(feature_id, src, mapping, options)


@router.get("/features/{feature_id}/upstream")
def upstream_candidates(feature_id: str) -> Dict[str, Any]:
    """可"接着跑"的已完成任务（前端在建任务页给一个下拉）。"""
    feat = _feature(feature_id)
    allowed = set(feat.accepts_upstream)
    if not allowed:
        return {"items": [], "accepts": []}
    items = [j for j in _scan_jobs()
             if j["feature"] in allowed
             and j["clean_status"] in ("done", "skipped")]
    return {"accepts": list(feat.accepts_upstream), "items": items[:50]}


# ============================================================================
# 任务
# ============================================================================

def _scan_jobs() -> List[Dict[str, Any]]:
    """扫任务目录。只读 state.json —— 为一个中文名把 torch 拉起来不值。"""
    names = _feature_names()
    items: List[Dict[str, Any]] = []
    root = Path(JOBS_DIR)
    if not root.is_dir():
        return items
    for d in root.iterdir():
        sp = d / "state.json"
        if not sp.is_file():
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stages = st.get("stages") or {}
        items.append({
            "job_id": st.get("job_id", d.name),
            "feature": st.get("feature"),
            # 老任务（state 里还没存 feature_name）回退到注册表
            "feature_name": (st.get("feature_name")
                             or names.get(st.get("feature"), st.get("feature"))),
            "filename": (st.get("input") or {}).get("filename", ""),
            "status": st.get("status"),
            "created_at": st.get("created_at"),
            "updated_at": st.get("updated_at"),
            "done_stages": sum(1 for s in stages.values()
                               if s.get("status") in ("done", "skipped")),
            "clean_status": (stages.get("clean") or {}).get("status", ""),
            "vouchers": ((stages.get("clean") or {}).get("facts") or {}).get("vouchers"),
            "awaiting": next((k for k, s in stages.items()
                              if s.get("status") == "awaiting"), None),
        })
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items


@router.get("/flow")
def list_jobs(limit: int = Query(30, ge=1, le=200)) -> Dict[str, Any]:
    """最近的任务。"""
    return {"items": _scan_jobs()[:limit]}


@router.get("/flow/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = _job(job_id)
    return {**job.summary(), "running": is_running(job_id)}


@router.get("/flow/{job_id}/next")
def get_next(job_id: str) -> Dict[str, Any]:
    """下一步该谁做什么。**前端、AI、人读的都是这一个对象**。"""
    job = _job(job_id)
    payload = job.next_action()
    payload["running"] = is_running(job_id)
    stages = payload.get("next", {}).get("stages")
    if stages is None:
        payload["stages"] = job.summary()["stages"]
    return payload


@router.post("/flow/{job_id}/resume")
def resume(job_id: str) -> Dict[str, Any]:
    job = _job(job_id)
    if is_running(job_id):
        raise ServiceError("busy", "任务正在运行，等它跑完再说", 409)
    job.state["status"] = "running"
    job.state["error"] = None
    job.save()
    _schedule(job_id, "续跑", _advance)
    return {"job_id": job_id, "status": "running"}


@router.delete("/flow/{job_id}")
def delete_job(job_id: str) -> Dict[str, Any]:
    job = _job(job_id)
    if is_running(job_id):
        raise ServiceError("busy", "任务正在运行，不能删除", 409)
    shutil.rmtree(job.dir, ignore_errors=True)
    return {"deleted": job_id}


# ============================================================================
# 复核（AI 待办）
# ============================================================================

_NUM_COLS = ("金额", "借方金额", "贷方金额", "校准概率", "组内张数", "行号", "条数")
_TAG_COLS = ("置信度", "业务分类置信度", "现金流方向")
_TEXT_COLS = ("为什么要看", "原因", "help")
# 有就用、没有也不影响判断的列：默认折叠（列太多会把"改为"挤到屏幕外）
_SECONDARY_COLS = ("模型Top3", "校准概率", "组内张数", "对方科目笔数",
                   "业务分类来源", "判定来源")
_LIST_COLS = ("候选项目", "对方科目")


def _kind(key: str, value: Any) -> str:
    if key in _TAG_COLS:
        return "tag"
    if key in _NUM_COLS or isinstance(value, (int, float)):
        return "num"
    if key in _LIST_COLS or isinstance(value, list):
        return "list"
    if key in _TEXT_COLS:
        return "text"
    return "str"


def _columns(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """列定义：按 items 里第一次出现的顺序（阶段实现决定了人类的阅读顺序）。"""
    seen: Dict[str, Dict[str, Any]] = {}
    for it in items:
        for k, v in it.items():
            if k not in seen:
                seen[k] = {"key": k, "label": k, "kind": _kind(k, v),
                           "secondary": k in _SECONDARY_COLS}
    return list(seen.values())


@router.get("/flow/{job_id}/review")
def get_review(job_id: str,
               stage: str = Query("", description="默认当前等待复核的阶段"),
               offset: int = Query(0, ge=0),
               limit: int = Query(200, ge=1, le=2000),
               q: str = Query("", description="关键字过滤（全字段子串）"),
               decided: bool = Query(False, description="只看已交结论的")):
    """AI 待办（分页）。列定义由 items 推出来，前端不需要为每个阶段写表格。"""
    job = _job(job_id)
    sid = stage or _awaiting_stage(job) or ""
    if not sid:
        return {"job_id": job_id, "stage": None, "total": 0, "items": [],
                "columns": [], "value_enum": [], "submitted": {}}

    req_path = job.dir / "reviews" / f"{sid}.request.json"
    if not req_path.exists():
        raise ServiceError("not_found", f"阶段 {sid} 还没生成待办", 404)
    doc = read_json(req_path)

    submitted: Dict[str, Any] = job.decisions(sid)
    items: List[Dict[str, Any]] = doc.get("items") or []
    if decided:
        items = [it for it in items if str(it.get("key")) in submitted]
    if q:
        needle = q.strip().lower()
        items = [it for it in items
                 if any(needle in str(v).lower() for v in it.values())]

    total = len(items)
    page = items[offset:offset + limit]
    return {
        "job_id": job_id,
        "stage": sid,
        "title": job.stage(sid).title,
        "help": doc.get("help", ""),
        "key_name": doc.get("key_name", "key"),
        "value_enum": doc.get("value_enum") or [],
        "total": total,
        "truncated": bool(doc.get("truncated")),
        "offset": offset,
        "limit": limit,
        "columns": _columns(page or (doc.get("items") or [])[:1]),
        "items": page,
        "submitted": submitted,
    }


@router.post("/flow/{job_id}/decisions")
def submit_decisions(job_id: str,
                     doc: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """交一批结论。

    body: `{"stage": "...", "decisions": [{"key","value","note"}], "waive": false}`

    校验（合法取值 / 是否在本轮待办里）**同步**做，几十毫秒；往下跑可能几十秒，
    丢后台。前端拿到 `accepted/rejected` 之后继续轮询 `/next`。
    """
    job = _job(job_id)
    if is_running(job_id):
        raise ServiceError("busy", "任务正在运行，等它跑完再交结论", 409)
    if not doc.get("stage"):
        # 只给一批 decisions 时，默认交给当前等待复核的阶段（前端更省事）
        doc["stage"] = _awaiting_stage(job) or ""
    try:
        res = job.submit(doc, advance=False)
    except (ValueError, KeyError) as exc:
        raise ServiceError("invalid_decision", str(exc), 400) from None
    _schedule(job_id, "继续", _advance)
    return {**res, "job_id": job_id, "status": "running"}


# ============================================================================
# 产物
# ============================================================================

def _file_entry(job: Job, key: str, path: Path, label: str = "") -> Dict[str, Any]:
    return {
        "key": key,
        "label": label or key,
        "filename": path.name,
        "bytes": path.stat().st_size if path.is_file() else 0,
        "url": f"/api/flow/{job.id}/files/{key}",
    }


@router.get("/flow/{job_id}/files")
def list_files(job_id: str) -> Dict[str, Any]:
    """产物清单 + 导出阶段的事实（现金流量表、统计），前端一次拿全。"""
    job = _job(job_id)
    outputs = job.outputs()

    declared: List[Dict[str, Any]] = []
    for spec in job.feature.exports:
        p = outputs.get(spec.get("key", ""))
        if p and Path(p).is_file():
            declared.append(_file_entry(job, spec["key"], Path(p), spec.get("label", "")))

    # 导出阶段的 artifacts 里还有 `sheet:<表名>`（一份 Excel 多张表时每张都在）
    sheets = {k.split(":", 1)[1]: Path(v)
              for k, v in (job.stage_state("export").get("artifacts") or {}).items()
              if k.startswith("sheet:") and Path(v).is_file()}
    extra = [{"sheet": name, "filename": p.name,
              "bytes": p.stat().st_size, "path": str(p)}
             for name, p in sheets.items()]

    facts = job.stage_facts("export")
    return {
        "job_id": job_id,
        "status": job.status,
        "files": declared,
        "sheets": extra,
        # 只挑前端会用的：现金流量表 + 统计 + 导出格式
        "cashflow": facts.get("cashflow") or {},
        "statement": facts.get("cashflow_statement") or [],
        "export": facts.get("export") or {},
        "patch": facts.get("patch") or {},
    }


@router.get("/flow/{job_id}/files/{key}")
def download(job_id: str, key: str):
    job = _job(job_id)
    path = job.outputs().get(key)
    if not path or not Path(path).is_file():
        raise ServiceError("not_found", f"没有产物 {key!r}（任务状态：{job.status}）", 404)
    p = Path(path)
    suffix = p.suffix.lower()
    media = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             ".csv": "text/csv; charset=utf-8",
             ".parquet": "application/octet-stream",
             ".json": "application/json"}.get(suffix, "application/octet-stream")
    return FileResponse(p, media_type=media, filename=p.name)
