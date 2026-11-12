# -*- coding: utf-8 -*-
"""对方科目分析模块。

拆两层：

    core / algorithm / occams_razor / ss_normalizer / memory_web   纯算法，不依赖界面
    ui_streamlit                                                   Streamlit 页面

`show_contra_analyzer` 用 **惰性导入**（PEP 562）导出：命令行 `journal run --feature contra`
走的是纯算法，不该为了拿一个类把 streamlit 拉起来（多 0.7s，而且干净环境里没装
streamlit 就是 ModuleNotFoundError，整个功能直接不可用）。
"""

from .algorithm import ExhaustiveSolver
from .core import ContraProcessor
from .memory_web import KnowledgeBase
from .occams_razor import OccamsRazor

__all__ = [
    'ContraProcessor',
    'ExhaustiveSolver',
    'OccamsRazor',
    'KnowledgeBase',
    'show_contra_analyzer',
]


def __getattr__(name):
    if name == 'show_contra_analyzer':
        from .ui_streamlit import show_contra_analyzer
        return show_contra_analyzer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
