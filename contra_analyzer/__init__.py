"""
对方科目分析模块

原tkinter版本已适配为Streamlit Web版本
"""

# 核心算法（保持原有功能）
from .core import ContraProcessor
from .algorithm import ExhaustiveSolver
from .occams_razor import OccamsRazor

# Web版记忆模块（无本地存储）
from .memory_web import KnowledgeBase

# Streamlit UI
from .ui_streamlit import show_contra_analyzer

__all__ = [
    'ContraProcessor',
    'ExhaustiveSolver',
    'OccamsRazor',
    'KnowledgeBase',
    'show_contra_analyzer',
]
