# -*- coding: utf-8 -*-
"""序时账清洗 API（v1）。

这一层只做三件事：路由、参数校验、把 ServiceError 映射成 HTTP。
业务逻辑全在 `summary_cleaner.service` 里，与前端无关。

启动：
    ./venv/Scripts/python.exe -m api.run_api          # 127.0.0.1:8502
    ./venv/Scripts/python.exe -m uvicorn api.app:app --port 8502

契约文档：docs/design/cleaner/api_contract_v1.md
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import Body, FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from summary_cleaner.service import cleaner
from summary_cleaner.service.errors import ServiceError
from summary_cleaner.service.jobs import JobStore

APP_VERSION = "1.0.0"
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

app = FastAPI(title="会计分析工具箱 · 序时账清洗 API",
              version=APP_VERSION,
              description="清洗/复核的唯一业务入口，前端（React/Streamlit）共用。")

# 前端开发服务器（Vite 5173）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:4173", "http://127.0.0.1:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = JobStore()
# 单线程执行器：分类任务吃 GPU，并发跑只会互相抢显存；排队反而更可预期
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clean-job")


# ============================================================================
# 统一错误体
# ============================================================================

@app.exception_handler(ServiceError)
async def _service_error_handler(_request, exc: ServiceError):
    return JSONResponse(status_code=exc.status, content=exc.to_dict())


@app.exception_handler(ValueError)
async def _value_error_handler(_request, exc: ValueError):
    return JSONResponse(status_code=400, content={
        "error": {"code": "invalid_input", "message": str(exc), "detail": {}},
    })


def _download_headers(filename: str, hint: str = "") -> Dict[str, str]:
    """附件头：中文名走 RFC 5987（HTTP 头只能是 latin-1，直接放中文会 500）。"""
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode().strip() or "download.xlsx"
    headers = {
        "Content-Disposition":
            f"attachment; filename=\"{ascii_name}\"; "
            f"filename*=UTF-8''{quote(filename)}",
    }
    if hint:
        # 提示文案同样可能含中文 → 百分号编码成纯 ASCII
        headers["X-Export-Hint"] = quote(hint)
    return headers


# ============================================================================
# 基础
# ============================================================================

@app.get("/api/health")
def health() -> Dict[str, Any]:
    payload = cleaner.health()
    payload["version"] = APP_VERSION
    return payload


@app.get("/api/buckets")
def get_buckets() -> Dict[str, Any]:
    return cleaner.buckets()


@app.get("/api/fields")
def get_fields(feature: str = Query("cleaning")) -> Dict[str, Any]:
    """字段配置项（前端渲染映射表单用）。

    清单来自 `journal_io.fields`（全项目唯一一份字段字典），这里只把 canonical 名
    换回历史键名——前端与 service 层还在用 voucher_no / subject / subject_name。
    """
    from journal_io import FIELD_BY_KEY, legacy_key, requires_of, ui_fields_of

    req = requires_of(feature)
    return {
        "feature": feature,
        "fields": [
            {"key": legacy_key(k), "label": FIELD_BY_KEY[k].label,
             "required": k in req}
            for k in ui_fields_of(feature)
        ],
    }


# ============================================================================
# 读表 / 建任务
# ============================================================================

@app.post("/api/inspect")
async def inspect(file: UploadFile = File(...)) -> Dict[str, Any]:
    """上传即读表：返回列名、建议映射、前 20 行预览（不落盘）。"""
    data = await file.read()
    return cleaner.inspect(data, filename=file.filename or "")


@app.post("/api/jobs")
async def create_job(
    file: UploadFile = File(...),
    mapping: Optional[str] = Form(None, description="JSON 字符串"),
    options: Optional[str] = Form(None, description="JSON 字符串"),
) -> Dict[str, Any]:
    """上传文件 + 字段映射 → 建任务并排队执行。"""
    data = await file.read()
    mapping_dict = json.loads(mapping) if mapping else None
    options_dict = json.loads(options) if options else None
    job = cleaner.create_job(store, data, file.filename or "",
                             mapping_dict, options_dict)
    _submit(job)
    return {"job_id": job.id, "status": job.status}


@app.post("/api/jobs/local")
def create_job_local(
    path: str = Body(..., embed=True),
    mapping: Optional[Dict[str, str]] = Body(None, embed=True),
    options: Optional[Dict[str, Any]] = Body(None, embed=True),
) -> Dict[str, Any]:
    """本机路径建任务（CLI / 调试 / 自动化用，避免重复上传大文件）。"""
    src = Path(path)
    if not src.exists():
        raise ServiceError("not_found", f"文件不存在：{path}", 404)
    job = cleaner.create_job(store, src.read_bytes(), src.name, mapping, options)
    _submit(job)
    return {"job_id": job.id, "status": job.status}


def _submit(job) -> None:
    def _work():
        try:
            cleaner.run_job(job)
        except Exception as exc:  # noqa: BLE001 - 状态已写进 job，这里只兜底日志
            print(f"[JOB {job.id}] 失败：{type(exc).__name__}: {exc}")

    _executor.submit(_work)


# ============================================================================
# 任务状态 / 结果
# ============================================================================

@app.get("/api/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=200)) -> Dict[str, Any]:
    return {"items": store.list(limit)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    return cleaner.job_summary(store.get(job_id))


@app.get("/api/jobs/{job_id}/rows")
def get_rows(job_id: str,
             level: str = Query("mid_low", pattern="^(low|mid_low|all)$"),
             offset: int = Query(0, ge=0),
             limit: int = Query(50, ge=1, le=1000),
             bucket: str = Query("")) -> Dict[str, Any]:
    """复核清单分页（凭证级，按校准概率升序）。"""
    return cleaner.review_rows(store.get(job_id), level=level, offset=offset,
                               limit=limit, bucket=bucket)


@app.get("/api/jobs/{job_id}/unknown-subjects")
def get_unknown_subjects(job_id: str):
    """下载本次的未知科目清单（JSON，给别名表维护用）。"""
    job = store.get(job_id)
    path = job.file("unknown_subjects.json")
    if not path.exists():
        raise ServiceError("not_found", "本次没有生成未知科目清单", 404)
    return Response(content=path.read_bytes(), media_type="application/json",
                    headers=_download_headers(f"{job.id}_未知科目.json"))


@app.get("/api/jobs/{job_id}/unreviewed")
def get_unreviewed(job_id: str) -> Dict[str, Any]:
    return cleaner.unreviewed(store.get(job_id))


@app.get("/api/jobs/{job_id}/export")
def export(job_id: str,
           kind: str = Query("result", pattern="^(result|detail|vouchers)$")):
    data, filename, mime, hint = cleaner.export(store.get(job_id), kind)
    return Response(content=data, media_type=mime,
                    headers=_download_headers(filename, hint))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> Dict[str, Any]:
    store.delete(job_id)
    return {"deleted": job_id}


# ============================================================================
# 编排层（jobflow）：功能清单 + 阶段任务 + 复核结论
#
# 与上面那套 `/api/jobs/*`（清洗的一次性任务）并存，理由见 api/flow_api.py 的
# 模块注释：两套任务的存储与语义都不同，等清洗迁进 jobflow 后再统一。
# ============================================================================

from .flow_api import router as flow_router  # noqa: E402  (放在业务路由之后)

app.include_router(flow_router)


# ============================================================================
# 静态前端（构建产物存在时挂上，开发阶段走 Vite 5173）
# ============================================================================

if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
