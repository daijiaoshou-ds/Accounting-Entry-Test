# -*- coding: utf-8 -*-
"""v2 包内的领域异常。"""

from __future__ import annotations


class EngineNotReady(RuntimeError):
    """模型不可用 —— 4.0 起没有纯程序兜底，必须让调用方看到明确错误。"""
