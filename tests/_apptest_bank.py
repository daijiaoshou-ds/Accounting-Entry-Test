# -*- coding: utf-8 -*-
"""AppTest 驱动脚本：银行流水匹配页（集成模式调用）"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.session_state.setdefault("current_page", "bankmatch")
from bank_statement_matcher.app import show_bank_matcher  # noqa: E402

show_bank_matcher()
