# -*- coding: utf-8 -*-
"""
共享存储工具 — 安全写入 + 自动备份

每次覆盖写入前，先将现文件备份到 _storage/backups/，
保留最近 MAX_BACKUPS 个版本，防止数据意外丢失。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict

from .config import get_storage_dir

MAX_BACKUPS = 3  # 每个文件保留最近 3 个版本


def _get_backup_dir() -> Path:
    """返回当前环境的备份目录。"""
    return get_storage_dir() / "backups"


def _ensure_backup_dir():
    """确保备份目录存在。"""
    _get_backup_dir().mkdir(parents=True, exist_ok=True)


def _rotate_backups(stem: str):
    """清理旧备份，每个文件只保留最近 MAX_BACKUPS 个常规备份。

    .DELETED 备份不受此限制——它们是手动/程序调用 delete_persisted 时产生的，
    独立计数，不与常规写入备份争抢位置。

    Args:
        stem: 文件名主干（如 "global_counters"），不含扩展名。
    """
    pattern = f"{stem}.*.json"
    all_files = sorted(_get_backup_dir().glob(pattern))
    # 分开处理：常规备份 + .DELETED 备份
    regular = [f for f in all_files if ".DELETED." not in f.name]
    deleted = [f for f in all_files if ".DELETED." in f.name]
    # 常规备份保留最近 MAX_BACKUPS 个
    while len(regular) > MAX_BACKUPS:
        oldest = regular.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass
    # .DELETED 备份也限制数量（防止无限堆积）
    while len(deleted) > MAX_BACKUPS:
        oldest = deleted.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


def safe_write_json(filepath: Path, data: dict):
    """安全写入 JSON 文件：先备份 → 再写 .tmp → 原子替换。

    Args:
        filepath: 目标文件路径（如 _storage/global_counters.json）
        data: 要写入的数据
    """
    _ensure_backup_dir()

    # Step 1: 如果目标文件存在，先备份
    if filepath.exists():
        stem = filepath.stem  # e.g., "global_counters"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = _get_backup_dir() / f"{stem}.{ts}.json"
        try:
            shutil.copy2(filepath, backup_path)
        except OSError:
            pass  # 备份失败不阻塞写入
        # 清理旧备份
        _rotate_backups(stem)

    # Step 2: 写临时文件
    tmp_path = filepath.with_suffix(".tmp")
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path.write_text(json_str, encoding="utf-8")

    # Step 3: 原子替换（Windows 兼容）
    try:
        tmp_path.replace(filepath)
    except OSError:
        # Windows: 目标存在时 replace 可能报拒绝访问
        # 先删目标文件，再重命名
        try:
            if filepath.exists():
                filepath.unlink()
            tmp_path.replace(filepath)
        except OSError:
            # 兜底失败: 数据仍完整保留在 .tmp 中，打告警便于人工恢复
            # （旧实现静默 pass，删掉目标后第二次 rename 失败 = 数据无声丢失）
            print(f"[WARN] safe_write_json 写入失败: {filepath}"
                  f"（数据完整保留在 {tmp_path}，请人工处理）")


def safe_delete_json(filepath: Path):
    """安全删除 JSON 文件：先备份再删除。

    Args:
        filepath: 要删除的文件路径
    """
    if not filepath.exists():
        return

    _ensure_backup_dir()
    stem = filepath.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = _get_backup_dir() / f"{stem}.{ts}.DELETED.json"
    try:
        shutil.copy2(filepath, backup_path)
    except OSError:
        pass

    try:
        filepath.unlink()
    except OSError:
        pass


def list_backups() -> Dict[str, list]:
    """列出所有备份文件，按原始文件名分组。"""
    if not _get_backup_dir().exists():
        return {}
    result: Dict[str, list] = {}
    for p in sorted(_get_backup_dir().glob("*.json")):
        # 文件名格式: {stem}.{timestamp}.json 或 {stem}.{timestamp}.DELETED.json
        name = p.name
        # 提取 stem
        parts = name.split(".")
        if len(parts) >= 3:
            stem = parts[0]
        else:
            stem = name
        if stem not in result:
            result[stem] = []
        result[stem].append(str(p))
    return result


def cleanup_all_backups():
    """清除所有备份文件。"""
    if _get_backup_dir().exists():
        for p in _get_backup_dir().glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass
