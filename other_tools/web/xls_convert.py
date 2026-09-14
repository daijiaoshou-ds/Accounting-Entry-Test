# -*- coding: utf-8 -*-
"""Excel格式互转 —— Streamlit 页面
复用原桌面版 xls_to_xlsx 的纯逻辑函数（5 个转换函数）。

输入：上传文件 / 上传文件夹（文件夹递归，结果保留同样的目录层级）
输出：转换结果（多个时打包 zip）通过浏览器下载，不写入项目目录、不改动原文件。
"""
import shutil
from pathlib import Path

import streamlit as st

from other_tools.ui import _TOOL_CSS
from other_tools.web._common import (
    load_desktop_module,
    tool_header,
    start_task,
    poll_task,
    stop_task,
    show_logs,
    split_result,
    prepare_input,
    prepare_output,
    pick_source,
    offer_downloads,
    list_outputs,
    rel_to,
)

_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        _MOD = load_desktop_module("xls_to_xlsx")
    return _MOD


MODES = [
    ("旧版转新版 (.xls → .xlsx)", ".xls", ".xlsx", "xls2xlsx"),
    ("新版转旧版 (.xlsx → .xls)", ".xlsx", ".xls", "xlsx2xls"),
    ("启用宏格式 (.xlsx → .xlsm)", ".xlsx", ".xlsm", "fmt"),
    ("移除宏格式 (.xlsm → .xlsx)", ".xlsm", ".xlsx", "fmt"),
    ("CSV转Excel (.csv → .xlsx)", ".csv", ".xlsx", "csv2xlsx"),
]

MODE_UPLOAD_EXTS = [("xls", "html"), ("xlsx",), ("xlsx",), ("xlsm",), ("csv",)]


def _convert_one(log_cb, src: str, dst: str, mode_index: int):
    """按模式转换单个文件，返回 (ok, msg)"""
    m = _mod()
    if mode_index == 0:
        return m.convert_xls_to_xlsx_logic(src, dst)
    if mode_index == 1:
        return m.convert_xlsx_to_xls_logic(src, dst)
    if mode_index in (2, 3):
        return m.convert_format_change_logic(src, dst)
    if mode_index == 4:
        return m.convert_csv_to_xlsx_logic(src, dst)
    return False, f"未知模式 {mode_index}"


def _accept_exts(mode_index: int) -> tuple:
    """当前模式可转换的源扩展名（小写，带点）"""
    return tuple(f".{e}" for e in MODE_UPLOAD_EXTS[mode_index])


def _unique_path(path: Path) -> Path:
    """目标重名时自动加序号，避免覆盖"""
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def _run_upload(log_cb, stop_event, files, mode_index, out_dir, base_dir=None,
                copy_others=False):
    """逐个转换，输出到 out_dir（base_dir 给定时保留上传时的目录层级）。

    copy_others=True 时，把不参与转换的其他文件也复制到输出目录（结果 zip 能还原整个文件夹）。
    """
    m = _mod()
    _, _, dst_ext, _ = MODES[mode_index]
    accept = _accept_exts(mode_index)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ok_count = fail_count = copy_count = skip_count = 0

    for i, src in enumerate(files):
        if stop_event and stop_event.is_set():
            log_cb(">>> 用户停止任务")
            break
        src = str(src)
        name = Path(src).name
        rel = rel_to(src, base_dir) if base_dir else Path(name)

        if Path(name).suffix.lower() not in accept:
            if copy_others:
                dst = _unique_path(out_dir / rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copy_count += 1
                log_cb(f"[{i + 1}/{len(files)}] {rel} → 非目标格式，原样打包")
            else:
                skip_count += 1
            continue

        log_cb(f"[{i + 1}/{len(files)}] {rel}")
        dst = _unique_path(out_dir / rel.with_suffix(dst_ext))
        dst.parent.mkdir(parents=True, exist_ok=True)
        ok, msg = _convert_one(log_cb, src, str(dst), mode_index)
        if ok:
            ok_count += 1
            log_cb(f"  ✅ {rel_to(dst, out_dir)}")
        else:
            fail_count += 1
            log_cb(f"  ❌ {msg}")

    parts = [f"转换完成。成功: {ok_count}", f"失败: {fail_count}"]
    if copy_count:
        parts.append(f"原样打包: {copy_count}")
    if skip_count:
        parts.append(f"跳过（非目标格式）: {skip_count}")
    return True, "，".join(parts)


def show_page(tool: dict):
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)
    tool_header(tool)

    mode_index = st.selectbox("转换类型（模式）", range(len(MODES)),
                              format_func=lambda i: MODES[i][0], key="xc_mode_idx")
    if mode_index == 3:
        st.caption("⚠️ 移除宏格式：.xlsm → .xlsx 会静默丢弃 VBA 宏，请确认后再转换")

    exts = MODE_UPLOAD_EXTS[mode_index]
    is_folder, uploaded = pick_source(
        exts, key="xc", folder_all_types=True,
        caption=f"当前模式只转换 {' / '.join('.' + e for e in exts)} 文件；"
                "「上传文件夹」会递归包含子文件夹（结果里保留同样层级），"
                "文件夹模式整包上传，其余格式的文件可通过下方「原样打包」一并放进结果 zip。",
    )
    copy_others = False
    if is_folder:
        copy_others = st.checkbox("原样打包：把文件夹里其他格式的文件也放进结果 zip", value=False,
                                  key="xc_copy_others")

    rec = st.session_state.get("task_xls_convert")
    running = bool(rec and not rec.get("done"))
    c_run, c_stop = st.columns([4, 1])
    with c_run:
        run_clicked = st.button("▶️ 开始转换", type="primary", use_container_width=True,
                                disabled=running or not uploaded, key="xc_btn_run")
    with c_stop:
        if running:
            st.button("⏹️ 停止", use_container_width=True, key="xc_btn_stop",
                      on_click=stop_task, args=("task_xls_convert",))

    if run_clicked:
        if not uploaded:
            st.error("请先上传文件或文件夹")
        else:
            in_dir, files = prepare_input("xls_convert", uploaded)
            out_dir = prepare_output("xls_convert")
            start_task("task_xls_convert", _run_upload, files, mode_index,
                       str(out_dir), str(in_dir), copy_others)
            st.rerun()

    rec = poll_task("task_xls_convert")
    if rec is not None and rec.get("done"):
        ok, msg = split_result(rec["result"])
        # 只要「成功: N」里 N>0 就算成功（历史上曾把成功当失败展示成红色 error）
        has_success = ok and "成功" in str(msg) and "成功: 0" not in str(msg)
        if has_success:
            st.success(msg)
        else:
            st.error(msg)
        show_logs(rec)

        out_dir = prepare_output("xls_convert")
        outputs = list_outputs(out_dir)
        if outputs:
            st.markdown("---")
            st.subheader("📥 结果下载")
            offer_downloads(outputs, key_prefix="xc_out", zip_name="转换结果.zip",
                            base_dir=out_dir,
                            single_label="📥 保存文件：转换结果（浏览器下载）")
    elif running:
        st.info("⏳ 正在转换中…（日志实时刷新）")
