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

_STORAGE_DIR = Path(__file__).parent / "_storage"
_BACKUP_DIR = _STORAGE_DIR / "backups"
MAX_BACKUPS = 1  # 每个文件保留最近 1 个版本


def _ensure_backup_dir():
    """确保备份目录存在。"""
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _rotate_backups(stem: str):
    """清理旧备份，每个文件只保留最近 MAX_BACKUPS 个常规备份。

    .DELETED 备份不受此限制——它们是手动/程序调用 delete_persisted 时产生的，
    独立计数，不与常规写入备份争抢位置。

    Args:
        stem: 文件名主干（如 "global_counters"），不含扩展名。
    """
    pattern = f"{stem}.*.json"
    all_files = sorted(_BACKUP_DIR.glob(pattern))
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
        backup_path = _BACKUP_DIR / f"{stem}.{ts}.json"
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
            pass  # 最终兜底，不阻塞写入


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
    backup_path = _BACKUP_DIR / f"{stem}.{ts}.DELETED.json"
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
    if not _BACKUP_DIR.exists():
        return {}
    result: Dict[str, list] = {}
    for p in sorted(_BACKUP_DIR.glob("*.json")):
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
    if _BACKUP_DIR.exists():
        for p in _BACKUP_DIR.glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass
