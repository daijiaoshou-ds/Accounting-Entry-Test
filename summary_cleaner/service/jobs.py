# -*- coding: utf-8 -*-
"""作业（job）存储：一次「清洗」就是磁盘上的一个目录。

目录结构（`<root>/<job_id>/`）::

    source.xlsx        用户上传的原文件（保留原样，重跑/复现都靠它）
    meta.json          文件名、行列数、字段映射、创建时间、参数
    status.json        状态机 + 进度（供前端轮询）
    result.parquet     行级结果（原表 + 业务分类/置信度/模型概率…）
    detail.parquet     凭证级分数明细（235 列）
    vouchers.parquet   凭证级汇总（复核页用，按校准概率排序）
    unreviewed.json    未审训练数据（如果本次产出）

为什么要落盘而不是放内存：
1. 前端是浏览器，刷新页面之后还得能继续看结果；
2. 明细 235 列 × 上万行，放 session 内存会让 API 进程越来越胖；
3. 出问题能直接进目录看原件，比问「当时传了什么」快。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .errors import not_found

_JOBS_ROOT_ENV = "SUMMARY_CLEANER_JOBS_DIR"
_DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "_storage_jobs"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

_JOB_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6}$")


def jobs_root() -> Path:
    root = Path(os.environ.get(_JOBS_ROOT_ENV) or _DEFAULT_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """把混合类型的 object 列规整成 parquet 能接受的类型。

    分类结果里有「模型概率」这种列：命中的是 float，未命中（硬规则预分配）
    写的是空串 ""。pandas 会把它留成 object，pyarrow 于是报
    "Could not convert '' with type str: tried to convert to double"。
    这里按列判断：去掉空串后能整体转数字的 → 存数字（排序/聚合还能用），
    否则 → 存字符串（空值变 null）。
    """
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != object:
            continue
        s = out[col].replace("", None)
        as_num = pd.to_numeric(s, errors="coerce")
        nonnull = int(s.notna().sum())
        if nonnull and int(as_num.notna().sum()) == nonnull:
            out[col] = as_num
        else:
            out[col] = s.astype("string")
    return out


class Job:
    """一个作业目录的句柄。"""

    def __init__(self, job_id: str, path: Path):
        self.id = job_id
        self.path = path

    # -- 文件位置 --------------------------------------------------------
    def file(self, name: str) -> Path:
        return self.path / name

    def exists(self) -> bool:
        return self.path.is_dir()

    # -- meta / status ---------------------------------------------------
    @property
    def meta(self) -> Dict[str, Any]:
        return _read_json(self.file("meta.json"), {}) or {}

    def update_meta(self, **patch: Any) -> Dict[str, Any]:
        meta = self.meta
        meta.update(patch)
        _atomic_write_json(self.file("meta.json"), meta)
        return meta

    @property
    def status(self) -> Dict[str, Any]:
        data = _read_json(self.file("status.json"), {}) or {}
        data.setdefault("state", STATUS_QUEUED)
        data.setdefault("progress", 0)
        data.setdefault("message", "")
        return data

    def set_status(self, state: str, progress: int = None,
                   message: str = "", **extra: Any) -> Dict[str, Any]:
        data = self.status
        data["state"] = state
        if progress is not None:
            data["progress"] = max(0, min(100, int(progress)))
        if message:
            data["message"] = message
        data.update(extra)
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _atomic_write_json(self.file("status.json"), data)
        return data

    # -- 数据帧 ----------------------------------------------------------
    def save_frame(self, name: str, df: pd.DataFrame) -> None:
        tmp = self.file(name + ".tmp")
        _parquet_safe(df).to_parquet(tmp, index=False)
        os.replace(tmp, self.file(name))

    def load_frame(self, name: str) -> pd.DataFrame:
        path = self.file(name)
        if not path.exists():
            raise not_found("结果数据", f"{self.id}/{name}")
        return pd.read_parquet(path)

    def public(self) -> Dict[str, Any]:
        meta = self.meta
        return {
            "job_id": self.id,
            "filename": meta.get("filename", ""),
            "rows": meta.get("rows", 0),
            "vouchers": meta.get("vouchers", 0),
            "created_at": meta.get("created_at", ""),
            "status": self.status,
        }


class JobStore:
    """作业目录的创建 / 查找 / 列举。"""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else jobs_root()

    # -- 创建 ------------------------------------------------------------
    def create(self, filename: str, mapping: Dict[str, str],
               options: Dict[str, Any]) -> Job:
        job_id = f"{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(3)}"
        path = self.root / job_id
        path.mkdir(parents=True, exist_ok=False)
        job = Job(job_id, path)
        _atomic_write_json(job.file("meta.json"), {
            "job_id": job_id,
            "filename": filename,
            "stored_name": "",
            "mapping": mapping,
            "options": options,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        job.set_status(STATUS_QUEUED, 0, "排队中")
        return job

    def save_source(self, job: Job, data: bytes, filename: str) -> Path:
        suffix = Path(filename).suffix.lower() or ".xlsx"
        target = job.file("source" + suffix)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        job.update_meta(stored_name=target.name)
        return target

    def source_path(self, job: Job) -> Path:
        name = job.meta.get("stored_name") or ""
        if not name:
            raise not_found("原始文件", job.id)
        path = job.file(name)
        if not path.exists():
            raise not_found("原始文件", job.id)
        return path

    # -- 查找 ------------------------------------------------------------
    def get(self, job_id: str) -> Job:
        if not _JOB_ID_RE.match(str(job_id or "")):
            raise not_found("作业", job_id)
        path = self.root / job_id
        if not path.is_dir():
            raise not_found("作业", job_id)
        return Job(job_id, path)

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        dirs = [p for p in self.root.iterdir() if p.is_dir()
                and _JOB_ID_RE.match(p.name)]
        dirs.sort(key=lambda p: p.name, reverse=True)
        return [Job(p.name, p).public() for p in dirs[:max(1, limit)]]

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        shutil.rmtree(job.path, ignore_errors=True)
