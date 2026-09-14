# -*- coding: utf-8 -*-
"""AppTest 驱动脚本：小工具详情页（由测试设置 session_state['current_tool']）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.session_state.setdefault("current_page", "tool_detail")
from other_tools.ui import show_tool_detail  # noqa: E402

show_tool_detail()
