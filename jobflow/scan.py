# -*- coding: utf-8 -*-
"""扫任务目录：列出所有 job 的轻量状态。

只读 `state.json`，**不 import 任何功能模块**——为一个中文名把 torch 拉起来
（约 6s / 1.3GB）不值。功能名在创建时已存进 state（`feature_name`），老任务
回退到注册表查。

两个消费者：
    `journal inbox`    agent 一进来先看"有哪些任务在等我"
    `GET /api/flow`    首页的最近任务列表
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import JOBS_DIR


def _feature_names() -> Dict[str, str]:
    """id → 中文名。走注册表的 `names()`（静态表），不 import 功能模块。"""
    try:
        import features
        return features.names()
    except Exception:               # noqa: BLE001 - 列清单不该因为某个功能坏掉而失败
        return {}


def scan_jobs(root: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """所有任务，按创建时间倒序。

    `awaiting` 只可能来自 AI 阶段——`advance()` 里只在 `actor="ai"` 的分支
    会把状态置成 awaiting（程序阶段要么跑完、要么抛错）。
    """
    names = _feature_names()
    items: List[Dict[str, Any]] = []
    base = Path(root or JOBS_DIR)
    if not base.is_dir():
        return items

    for d in base.iterdir():
        sp = d / "state.json"
        if not sp.is_file():
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue           # 正在写（有原子写兜底，理论上不会）或已损坏
        stages = st.get("stages") or {}
        awaiting = next((k for k, v in stages.items()
                         if (v or {}).get("status") == "awaiting"), None)
        items.append({
            "job_id": st.get("job_id", d.name),
            "feature": st.get("feature"),
            # 老任务（state 里还没存 feature_name）回退到注册表
            "feature_name": (st.get("feature_name")
                             or names.get(st.get("feature"), st.get("feature"))),
            "filename": (st.get("input") or {}).get("filename", ""),
            # 归属哪个任务（账套）：建任务时用 --option task=… 记下，老任务没有
            "task": (st.get("options") or {}).get("task"),
            "status": st.get("status"),
            "created_at": st.get("created_at"),
            "updated_at": st.get("updated_at"),
            "done_stages": sum(1 for s in stages.values()
                               if (s or {}).get("status") in ("done", "skipped")),
            "total_stages": len(stages) or None,
            "clean_status": (stages.get("clean") or {}).get("status", ""),
            "vouchers": ((stages.get("clean") or {}).get("facts") or {}).get("vouchers"),
            "awaiting": awaiting,
            "awaiting_actor": "ai" if awaiting else None,
            "awaiting_count": ((stages.get(awaiting) or {}).get("facts") or {}
                               ).get("count") if awaiting else None,
        })
    items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items
