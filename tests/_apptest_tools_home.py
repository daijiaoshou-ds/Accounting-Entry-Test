# -*- coding: utf-8 -*-
"""AppTest 驱动脚本：小工具二级目录页"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.session_state.current_page = "tools"
from other_tools.ui import show_tools_home  # noqa: E402

show_tools_home()
