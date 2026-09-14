# -*- coding: utf-8 -*-
"""测试夹具：样例文件生成 + 假的上传文件对象（供 AppTest 驱动脚本使用）

夹具统一放在 tests/_work/fixtures（gitignore），按需生成，测试之间自给自足。
"""
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # 项目根
WORK = HERE / "_work"                    # 测试临时目录（不进 git）
FIXTURES = WORK / "fixtures"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeUploadedFile:
    """模拟 st.file_uploader 返回的 UploadedFile。

    name 可以是带相对目录的路径（如 ``上传文件夹/子目录/a.xlsx``），
    用来模拟浏览器的「文件夹上传」（Streamlit 用 webkitRelativePath 当文件名）。
    """

    def __init__(self, path: Path, name: str = ""):
        self.name = name or path.name
        self.type = "application/octet-stream"
        self._bytes = Path(path).read_bytes()
        self.size = len(self._bytes)
        self.file_id = f"fake-{self.name}"
        self._fp = io.BytesIO(self._bytes)

    def getvalue(self):
        return self._bytes

    def getbuffer(self):
        return memoryview(self._bytes)

    def read(self, *a):
        return self._fp.read(*a)

    def __len__(self):
        return self.size


# ============ 样例文件 ============
def make_xlsx(path: Path, sheet="Sheet1"):
    """两行样例表：姓名 / 金额"""
    import pandas as pd
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"姓名": ["张三", "李四"], "金额": [1.0, 2.0]}).to_excel(
        path, index=False, sheet_name=sheet)
    return path


def make_pdf(path: Path, pages=2, text="TEST"):
    """小体积样例 PDF"""
    import fitz
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"{text}{i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def make_txt(path: Path, content=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else path.name, encoding="utf-8")
    return path


# ============ 各工具的上传夹具 ============
def fixtures_for(tool_id: str, folder: bool = False) -> list:
    """按工具返回样例文件列表。

    folder=True 时把文件名改成 ``上传文件夹/...`` 形式，模拟「上传文件夹」
    （含一个子目录，用来验证目录层级是否被保留）。
    """
    FIXTURES.mkdir(parents=True, exist_ok=True)
    paths = []
    if tool_id == "excel_extract":
        paths = [make_xlsx(FIXTURES / "excel" / "src.xlsx")]
    elif tool_id == "file_batch":
        base = FIXTURES / "filebatch"
        paths = [make_txt(base / "1.txt"), make_txt(base / "2.txt"),
                 make_txt(base / "3.txt")]
        if folder:
            paths.append(make_txt(base / "子目录" / "4.txt"))
    elif tool_id == "xls_convert":
        paths = [make_xlsx(FIXTURES / "xlsconv" / "src.xlsx")]
        if folder:
            # 文件夹模式整包上传：混一个非目标格式，用来验证「原样打包」
            paths.append(make_txt(FIXTURES / "xlsconv" / "说明.txt", "非目标格式"))
    elif tool_id == "pdf_indexer":
        paths = [make_pdf(FIXTURES / "pdfidx" / "doc.pdf", 2)]
    elif tool_id == "pdf_merger":
        paths = [make_pdf(FIXTURES / "pdfmerge" / "a.pdf", 2, "A"),
                 make_pdf(FIXTURES / "pdfmerge" / "b.pdf", 1, "B")]
    else:
        raise ValueError(f"未知工具: {tool_id}")

    if not folder:
        return [FakeUploadedFile(p) for p in paths]

    out = []
    for p in paths:
        rel = p.relative_to(FIXTURES).as_posix()      # excel/src.xlsx
        out.append(FakeUploadedFile(p, name=f"上传文件夹/{rel}"))
    return out


def install_fake_uploader(files):
    """把 st.file_uploader 换成返回固定文件的假实现（AppTest 驱动脚本用）。

    行为对齐真实 Streamlit：accept_multiple_files 为 True / "directory" 时返回列表，
    否则返回单个文件对象。
    """
    import streamlit as st

    def _fake(label=None, type=None, accept_multiple_files=False, key=None, **kw):
        if accept_multiple_files:
            return list(files)
        return files[0] if files else None

    st.file_uploader = _fake


# ============ AppTest 小工具页面（交互测试共用） ============
def start_tool_app(tool: str, mode: str = "files"):
    """打开小工具详情页的 AppTest 会话（mode: files / folder）"""
    import os

    from streamlit.testing.v1 import AppTest

    os.environ["TOOL_UNDER_TEST"] = tool
    os.environ["UPLOAD_MODE"] = mode
    at = AppTest.from_file(str(HERE / "_apptest_tool.py"), default_timeout=300)
    at.run()
    return at


def drive(at, run_label: str, max_rounds: int = 80):
    """点一次按钮，然后反复 rerun 等后台任务结束。

    必须只点一次：每轮都点会让 Streamlit 认为按钮每次 rerun 都被按下，
    导致 start_task 重复起线程（多个线程抢同一个 .tmp 文件）。
    """
    import time

    btns = [b for b in at.button if run_label in b.label]
    if not btns:
        return at, f"找不到按钮「{run_label}」，当前按钮: {[b.label for b in at.button]}"
    btns[0].click()
    at.run()
    for _ in range(max_rounds):
        if at.exception:
            return at, f"抛异常: {str(at.exception[0].value)[:400]}"
        if at.get("download_button") or at.error:
            return at, None
        time.sleep(0.3)
        at.run()
    return at, f"等待任务完成超时（{max_rounds} 轮）"
