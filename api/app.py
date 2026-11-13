# -*- coding: utf-8 -*-
"""会计分析工具箱 API（编排层 + 前端静态文件）。

这一层只做四件事：建 app、挂路由、把异常映射成 HTTP、托管前端构建产物。
业务全在 `features/*`（阶段实现）与 `jobflow`（状态机）里。

**它是"独立网页版"的壳，不是主入口。** 主入口是 DSH 插件
（`dsh_plugin/journal-workbench`）：插件不经过这一层，它直接 spawn
`journal.py`，页面也画在 DSH 的会话视图里。这里保留下来的理由只有一个——
不装 DSH 时还能用浏览器点。所以：

  · 改业务 → 改 `features/*` / `jobflow`，两条壳都跟着变
  · 改这一层 → 只影响独立网页版（插件看不到）
  · `web/`（React）同此，和插件里的 `client.js` 是两套页面，别改错地方

启动：
    venv\\Scripts\\python.exe -m api.run_api           # 127.0.0.1:8502
    venv\\Scripts\\python.exe -m uvicorn api.app:app --port 8502

路由分布：
    /api/health            健康检查（前端顶栏的模型状态灯）
    /api/features/**       功能清单与建任务          → api/flow_api.py
    /api/flow/**           任务状态、复核、产物      → api/flow_api.py
    /                      web/dist（构建产物）

历史：以前这里还有一整套 `/api/jobs/*`（清洗的一次性任务，`JobStore` 存
standalone 目录、没有阶段、没有 decision）。编排层的 `cleaning` 功能接管之后
已删除——同一份账不会再出现"两个任务系统"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .errors import ServiceError

APP_VERSION = "2.0.0"
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

app = FastAPI(title="会计分析工具箱 API",
              version=APP_VERSION,
              description="序时账相关功能的唯一入口：功能清单、阶段任务、复核、产物。"
                          "前端（React）与 AI 助手共用同一套接口。")

# 前端开发服务器（Vite 5173）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:4173", "http://127.0.0.1:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ============================================================================
# 基础
# ============================================================================

@app.get("/api/health")
def health() -> Dict[str, Any]:
    """模型是否就绪（前端顶栏那个点）+ 版本。不加载模型，只看交付物在不在。"""
    from summary_cleaner.v2.classifier import get_classifier
    from summary_cleaner.v2.config import get_nn_storage_dir

    clf = get_classifier()
    nn = getattr(clf, "_nn", None)
    configured = False
    try:
        configured = (Path(get_nn_storage_dir()) / "finance_classifier.pt").exists()
    except OSError:
        pass
    import time
    return {
        "status": "ok",
        "version": APP_VERSION,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "buckets": len(clf._all_bucket_names),
        "subjects": len(clf.subject_list),
        "model": {
            # configured: 交付物在盘上（≠ 已加载）；loaded 才代表这次会话真的加载过
            "configured": configured,
            "available": bool(getattr(clf, "_nn_available", False)),
            "loaded": bool(getattr(nn, "is_loaded", False)),
            "device": getattr(nn, "device", None),
        },
    }


# ============================================================================
# 编排层路由（功能清单 / 任务 / 复核 / 产物）
# ============================================================================

from .flow_api import router as flow_router  # noqa: E402

app.include_router(flow_router)


# ============================================================================
# 静态前端（构建产物存在时挂上，开发阶段走 Vite 5173）
# ============================================================================

if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
