# -*- coding: utf-8 -*-
"""Feature：一个业务功能 = 一组阶段 + 字段需求 + 导出项。

前端的功能清单、`journal features` 的输出、API 的路由分组，都从这一个对象来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .stage import Stage


@dataclass
class Feature:
    """业务功能。

    Attributes:
        id: 短标识（目录名 / URL / `--feature`）
        name: 中文名（前端菜单）
        requires: 必需字段（canonical，见 journal_io.fields）
        stages: 阶段链（顺序即执行顺序）
        exports: 可下载的产物 [{"key","label","stage"}]
        accept: 允许上传的后缀
        description: 一句话说明（前端副标题）
        accepts_upstream: 可以"接着哪个功能的任务跑"（功能 id）。前端据此列出
            可选的已完成任务；任务里体现为 `options.upstream=<job_id>`
        domain: 所属数据域（`journal_io/contracts.py`）。决定新建任务时上传什么、
            卡片分在哪一组；空串时按契约表反查，再兜底 ledger。
        available: 是否已接入。占位功能（如成本计算）为 False：卡片照常出现、
            显示「未接入」，但不允许建任务，免得建出一份没人能跑的数据。
    """
    id: str
    name: str
    requires: Tuple[str, ...] = ()
    stages: Tuple[Stage, ...] = ()
    exports: Tuple[Dict[str, str], ...] = ()
    accept: Tuple[str, ...] = (".xlsx", ".xls", ".csv")
    description: str = ""
    accepts_upstream: Tuple[str, ...] = ()
    domain: str = ""
    available: bool = True

    def __post_init__(self) -> None:
        """没写 domain 就按契约表反查 —— 功能不必重复声明自己属于哪一层。"""
        if not self.domain:
            try:
                from journal_io import contracts
                self.domain = contracts.of_feature(self.id)
            except Exception:      # 契约表还没加载/被裁剪：退回老行为
                self.domain = "ledger"

    def stage(self, stage_id: str) -> Stage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise KeyError(f"功能 {self.id} 没有阶段 {stage_id}（有：{[s.id for s in self.stages]}）")

    def stage_ids(self) -> List[str]:
        return [s.id for s in self.stages]

    def manifest(self) -> Dict[str, Any]:
        """给前端 / CLI 的元信息（不含函数对象）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "available": bool(self.available),
            "requires": list(self.requires),
            "accept": list(self.accept),
            "accepts_upstream": list(self.accepts_upstream),
            "exports": [dict(e) for e in self.exports],
            "stages": [
                {"id": s.id, "title": s.title, "actor": s.actor,
                 "depends_on": list(s.depends_on), "help": s.help}
                for s in self.stages
            ],
        }
