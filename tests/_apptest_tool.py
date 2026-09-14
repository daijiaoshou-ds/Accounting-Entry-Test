# -*- coding: utf-8 -*-
"""AppTest 驱动：任意小工具详情页 + 预置「上传文件 / 上传文件夹」

由测试脚本通过环境变量控制：
  TOOL_UNDER_TEST = excel_extract | file_batch | xls_convert | pdf_indexer | pdf_merger
  UPLOAD_MODE     = files（默认）| folder（文件名带相对目录，模拟文件夹上传）

页面本身不做任何修改，这里只把 st.file_uploader 换成假实现。
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _fixtures import fixtures_for, install_fake_uploader  # noqa: E402

TOOL = os.environ.get("TOOL_UNDER_TEST", "excel_extract")
MODE = os.environ.get("UPLOAD_MODE", "files")
FOLDER = (MODE == "folder")

install_fake_uploader(fixtures_for(TOOL, folder=FOLDER))

import streamlit as st  # noqa: E402

st.session_state.setdefault("current_page", "tool_detail")
st.session_state.setdefault("current_tool", TOOL)

from other_tools.ui import show_tool_detail  # noqa: E402

show_tool_detail()
