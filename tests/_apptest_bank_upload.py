# -*- coding: utf-8 -*-
"""AppTest 驱动脚本：银行流水匹配 —— 用假 file_uploader 预置上传文件。

仅测试用：把 st.file_uploader 换成返回内存文件的假实现，让 AppTest 能走完
「上传 -> 字段映射 -> 配对 -> 核对 -> 结果展示 -> 导出」全流程。
被测代码（bank_statement_matcher/app.py）不做任何修改。
"""
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

_DATA = HERE / "_work" / "bank"

_JOURNAL = "序时账.xlsx"
_BANKS = ["工行流水.xlsx", "建行流水.xlsx"]


class FakeUploadedFile:
    """最小可用的 st.file_uploader 返回值替身"""

    def __init__(self, path: Path):
        self.name = path.name
        self.type = ("application/vnd.openxmlformats-officedocument"
                     ".spreadsheetml.sheet")
        self._bytes = path.read_bytes()
        self.size = len(self._bytes)
        self.file_id = f"fake-{path.name}"
        self._fp = BytesIO(self._bytes)

    def getvalue(self):
        return self._bytes

    def getbuffer(self):
        return memoryview(self._bytes)

    def read(self, *a):
        return self._fp.read(*a)

    def __len__(self):
        return self.size


_FAKE_JOURNAL = FakeUploadedFile(_DATA / _JOURNAL)
_FAKE_BANKS = [FakeUploadedFile(_DATA / b) for b in _BANKS]

_real_file_uploader = st.file_uploader
_calls = {"n": 0}


def _fake_file_uploader(label, *args, **kwargs):
    _calls["n"] += 1
    if kwargs.get("accept_multiple_files"):
        return list(_FAKE_BANKS)
    return _FAKE_JOURNAL


st.file_uploader = _fake_file_uploader

st.session_state.setdefault("current_page", "bankmatch")
from bank_statement_matcher.app import show_bank_matcher  # noqa: E402

show_bank_matcher()
