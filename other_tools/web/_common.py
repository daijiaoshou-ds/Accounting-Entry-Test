# -*- coding: utf-8 -*-
"""小工具 Web 版公共设施：桌面模块装载、后台任务、临时目录、文件夹上传、打包下载、字体探测

输入约定
--------
两种来源，页面统一用 ``pick_source()`` 渲染：
  - 📁 上传文件：多选文件
  - 🗂️ 上传文件夹：浏览器目录上传（``accept_multiple_files="directory"``），
    递归包含子文件夹，上传文件名形如 ``子目录/文件.xlsx``，由 ``save_uploaded()``
    在临时目录里还原出同样的目录层级。

输出约定
--------
处理产物一律落在**系统临时目录**（``%TEMP%/ka_toolbox_work/<tool_id>``），
不写入项目目录、也不改动用户的原文件夹；结果一律通过 ``offer_downloads()``
以「浏览器下载」的方式交给用户。
"""
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import types
import importlib
import zipfile
from pathlib import Path

import streamlit as st

OTHER_TOOLS_DIR = Path(__file__).resolve().parents[1]  # other_tools/

# 工作目录放在系统临时目录：既避免往项目里写产物，也避免污染用户原文件夹
WORK_ROOT = Path(tempfile.gettempdir()) / "ka_toolbox_work"

# 让原桌面模块里的 `from modules.path_manager import ...` 可解析
if str(OTHER_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(OTHER_TOOLS_DIR))


# ============ 原桌面模块装载（绕过 GUI 依赖） ============
def _install_ctk_stub():
    """原桌面模块顶层有 `import customtkinter as ctk`，但其纯逻辑函数不使用它。
    这里装一个惰性桩模块，避免 Web 环境依赖 customtkinter / tkinter 显示环境。"""
    if "customtkinter" in sys.modules:
        return
    stub = types.ModuleType("customtkinter")

    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Dummy()

    for _name in ("CTk", "CTkFrame", "CTkButton", "CTkLabel", "CTkEntry", "CTkTextbox",
                  "CTkScrollableFrame", "CTkCheckBox", "CTkOptionMenu", "CTkFont",
                  "CTkInputDialog", "CTkProgressBar", "CTkSwitch", "CTkSlider",
                  "CTkComboBox", "CTkSegmentedButton"):
        setattr(stub, _name, _Dummy)
    stub.set_appearance_mode = lambda *a, **k: None
    stub.set_default_color_theme = lambda *a, **k: None
    sys.modules["customtkinter"] = stub


def load_desktop_module(module_name: str):
    """加载 other_tools.modules.<module_name> 并返回模块对象。

    失败时抛 ImportError，由页面自行提示（如未安装依赖库）。
    """
    _install_ctk_stub()
    return importlib.import_module(f"other_tools.modules.{module_name}")


# ============ 临时工作目录（项目外部） ============
def work_dir(tool_id: str) -> Path:
    """返回工具的工作目录（系统临时目录下的稳定路径）"""
    d = WORK_ROOT / tool_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def reset_work_dir(tool_id: str) -> Path:
    """清空并返回工具的工作目录（每次运行前调用）"""
    d = work_dir(tool_id)
    for p in d.iterdir():
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            pass
    return d


def prepare_input(tool_id: str, uploaded_files, subdir: str = "input"):
    """清空工作目录 -> 建输入目录 -> 落实上传文件，返回 (输入目录, 文件路径列表)"""
    wd = reset_work_dir(tool_id)
    in_dir = wd / subdir
    in_dir.mkdir(parents=True, exist_ok=True)
    return in_dir, save_uploaded(uploaded_files, in_dir)


def prepare_output(tool_id: str, subdir: str = "output") -> Path:
    """建输出目录（必须在 prepare_input 之后调用，否则会被清空）"""
    d = work_dir(tool_id) / subdir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_relpath(name: str, index: int) -> Path:
    """把上传文件的 name 转成安全的相对路径。

    文件夹上传时 ``UploadedFile.name`` 是 ``webkitRelativePath``（如 ``子目录/a.xlsx``），
    这里保留层级；同时剥掉盘符与 ``..``，避免越权写入。
    """
    raw = str(name or "").replace("\\", "/")
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if part in ("", ".", ".."):
            continue
        if re.fullmatch(r"[A-Za-z]:", part):  # 盘符（Chrome 在部分场景会带上）
            continue
        parts.append(part)
    if not parts:
        parts = [f"upload_{index}"]
    return Path(*parts)


def save_uploaded(uploaded_files, target_dir: Path, prefix: str = "",
                  overwrite: bool = False) -> list:
    """把 st.file_uploader 的文件对象落盘，返回路径列表。

    - 文件夹上传会**保留相对目录结构**（如 ``子目录/a.xlsx`` -> ``<target>/子目录/a.xlsx``）；
    - overwrite=False 时同名文件加序号避让；True 时直接覆盖（「重新上传同一批文件」的场景）；
    - 注意：这里**必须返回 str**而不是 Path —— 原桌面模块（如 pdf_indexer.add_index_to_pdf
      会做 `file_path + ".tmp"`）是按 str 路径写的，传 Path 会抛
      `TypeError: unsupported operand type(s) for +: 'WindowsPath' and 'str'`。
    """
    saved = []
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for i, uf in enumerate(uploaded_files):
        rel = _safe_relpath(getattr(uf, "name", ""), i)
        if prefix:
            rel = rel.with_name(f"{prefix}{rel.name}")
        p = target_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and not overwrite:  # 同名（异常情况）避免覆盖
            p = p.with_name(f"{p.stem}_{i}{p.suffix}")
        p.write_bytes(uf.getbuffer())
        saved.append(str(p))
    return saved


def rel_to(path, base) -> Path:
    """取相对路径（用于把结果按上传时的目录层级归档；取不到则退回文件名）"""
    try:
        return Path(path).resolve().relative_to(Path(base).resolve())
    except (ValueError, OSError):
        return Path(path).name


def list_outputs(out_dir, patterns=("*",), base_dir=None, recursive: bool = True) -> list:
    """收集输出目录里的产物（按相对路径排序，返回 Path 列表）"""
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return []
    it = out_dir.rglob("*") if recursive else out_dir.glob("*")
    files = [p for p in it if p.is_file() and any(p.match(pat) for pat in patterns)]
    files.sort(key=lambda p: str(rel_to(p, base_dir or out_dir)).lower())
    return files


# ============ 来源选择（上传文件 / 上传文件夹） ============
SRC_FILES = "📁 上传文件"
SRC_FOLDER = "🗂️ 上传文件夹"


def pick_source(types, key: str, caption: str = "", file_label: str = "",
                help_text: str = "", folder_all_types: bool = False):
    """渲染「上传文件 / 上传文件夹」二选一，返回 (是否文件夹, 上传文件列表)。

    文件夹模式用 Streamlit 原生目录上传：一次选中整个文件夹（含子文件夹），
    并在临时目录里还原相对层级。

    types            文件模式限定的扩展名（也用于文件夹模式）
    folder_all_types 文件夹模式不限扩展名（整包上传，页面自行筛选），
                     供「原样打包其他格式」这类功能使用
    """
    mode = st.radio("数据来源", [SRC_FILES, SRC_FOLDER], horizontal=True, key=f"{key}__mode")
    is_folder = (mode == SRC_FOLDER)
    if caption:
        st.caption(caption)
    eff_types = None if (is_folder and folder_all_types) else types
    uploaded = st.file_uploader(
        file_label or ("选择文件夹（含子文件夹，保留目录结构）" if is_folder
                       else "选择文件（可多选，可多次追加）"),
        type=list(eff_types) if eff_types else None,
        accept_multiple_files="directory" if is_folder else True,
        key=f"{key}__upload",
        help=help_text or None,
    )
    return is_folder, uploaded


# ============ 打包 / 下载 ============
_MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".txt": "text/plain",
    ".html": "text/html",
}


def mime_of(path) -> str:
    """按扩展名给出下载 MIME"""
    return _MIME.get(Path(path).suffix.lower(), "application/octet-stream")


def make_zip_bytes(files, zip_name: str = "outputs.zip", base_dir=None) -> bytes:
    """把一组文件打包成 zip 字节。

    base_dir 给定时压缩包内保留相对层级（文件夹上传的目录结构）。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            f = Path(f)
            if not f.is_file():
                continue
            arc = str(rel_to(f, base_dir)) if base_dir else f.name
            zf.write(str(f), arc)
    return buf.getvalue()


def _cached_zip(files, key: str, base_dir=None) -> bytes:
    """带缓存的打包：同一批产物在多次 rerun 之间不重复压缩"""
    files = [Path(p) for p in files]
    try:
        sig = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    except OSError:
        sig = None
    cache = st.session_state.setdefault("_zip_cache", {})
    rec = cache.get(key)
    if sig is None or rec is None or rec.get("sig") != sig:
        rec = {"sig": sig, "data": make_zip_bytes(files, base_dir=base_dir)}
        cache[key] = rec
    return rec["data"]


def offer_downloads(paths, key_prefix: str, zip_name: str = "结果.zip",
                    single_label: str = "", zip_label: str = "", base_dir=None,
                    empty_hint: str = ""):
    """把产物渲染成下载按钮：1 个文件直接下载，多个文件打包下载 + 逐个下载。

    与「序时账清洗」的输出方式一致：用户在浏览器里点保存，文件不落到项目目录。
    """
    files = [Path(p) for p in paths if Path(p).is_file()]
    if not files:
        if empty_hint:
            st.warning(empty_hint)
        return
    if len(files) == 1:
        p = files[0]
        st.download_button(single_label or f"📥 保存文件：{p.name}",
                           data=p.read_bytes(), file_name=p.name, mime=mime_of(p),
                           use_container_width=True, key=f"{key_prefix}__dl_single")
        return
    total_mb = sum(p.stat().st_size for p in files) / 1024 / 1024
    st.download_button(zip_label or f"📥 打包保存全部结果（{len(files)} 个文件 · {total_mb:.1f} MB）",
                       data=_cached_zip(files, key_prefix, base_dir=base_dir),
                       file_name=zip_name, mime="application/zip",
                       use_container_width=True, key=f"{key_prefix}__dl_zip")
    with st.expander(f"或逐个保存（{len(files)} 个文件）"):
        for i, p in enumerate(files[:100]):
            st.download_button(f"📥 {rel_to(p, base_dir) if base_dir else p.name}",
                               data=p.read_bytes(), file_name=p.name, mime=mime_of(p),
                               key=f"{key_prefix}__dl_{i}")


# ============ 后台任务（实时日志） ============
def start_task(key: str, fn, *args, **kwargs):
    """启动后台任务。

    fn 约定签名：fn(log_callback, stop_event, *args, **kwargs)
    进度日志经 log_callback(msg) 实时收集，结果经返回值。
    """
    st.session_state[key] = {
        "logs": [],
        "stop": threading.Event(),
        "thread": None,
        "result": None,
        "done": False,
    }
    rec = st.session_state[key]

    def _worker():
        try:
            rec["result"] = fn(rec["logs"].append, rec["stop"], *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            rec["result"] = f"❌ 任务异常: {e}\n{traceback.format_exc()}"
        rec["done"] = True

    rec["thread"] = threading.Thread(target=_worker, name=f"tool-{key}", daemon=True)
    rec["thread"].start()


def poll_task(key: str):
    """轮询后台任务：未完成则短暂等待后 rerun（实现日志实时刷新）；完成后返回任务记录"""
    rec = st.session_state.get(key)
    if not rec or rec.get("thread") is None:
        return None
    if not rec.get("done"):
        time.sleep(0.4)
        st.rerun()
    return rec


def stop_task(key: str):
    """请求停止后台任务（仅影响支持 stop_event 的流程）"""
    rec = st.session_state.get(key)
    if rec:
        rec["stop"].set()


def show_logs(rec, height: int = 240):
    """在折叠框里展示任务日志"""
    if not rec:
        return
    st.markdown("---")
    with st.expander(f"📜 运行日志（{len(rec['logs'])} 行）", expanded=True):
        st.code("\n".join(rec["logs"][-300:]) or "（无日志）")


def split_result(result):
    """把后台任务返回值规整成 (ok, msg)"""
    return result if isinstance(result, tuple) else (False, str(result))


def scrub_paths(text, *bases) -> str:
    """把提示信息里的临时目录路径抹掉（用户只需要看到文件名，不需要看 %TEMP% 长路径）"""
    s = str(text or "")
    for b in bases:
        if not b:
            continue
        for form in (str(b), str(b).replace("\\", "/"), str(b).replace("/", "\\")):
            s = s.replace(form + os.sep, "").replace(form + "/", "").replace(form + "\\", "")
            s = s.replace(form, "")
    return s


# ============ 字体探测 ============
FONT_CANDIDATES = [
    "C:\\Windows\\Fonts\\simsun.ttc",
    "C:\\Windows\\Fonts\\msyh.ttc",
    "C:\\Windows\\Fonts\\simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def find_cjk_font() -> str:
    """查找系统中可用的中文字体路径，找不到返回空串"""
    for c in FONT_CANDIDATES:
        if os.path.exists(c):
            return c
    return ""


# ============ 页面头部通用块 ============
def tool_header(tool: dict):
    """渲染工具页面包屑 + 横幅"""
    st.markdown(f'<p class="breadcrumb">🏠 首页 / 🧰 小工具 / {tool["name"]}</p>',
                unsafe_allow_html=True)
    st.markdown(f"""
    <div class="tool-detail-banner">
        <div class="tool-detail-icon">{tool['icon']}</div>
        <div class="tool-detail-title">{tool['name']}</div>
        <div class="tool-detail-desc">{tool['desc']}</div>
    </div>
    """, unsafe_allow_html=True)
