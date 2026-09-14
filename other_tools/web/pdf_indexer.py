# -*- coding: utf-8 -*-
"""PDF索引号生成 —— Streamlit 页面
复用原桌面版 pdf_indexer 的 add_index_to_pdf 纯逻辑。
Web 版处理的是上传副本，绝不改动用户原始文件；结果经浏览器下载。

输入：上传 PDF 文件 / 上传 PDF 文件夹（文件夹递归、保留目录结构）
输出：带索引的 PDF（多个时打包 zip）浏览器下载。
"""
import os
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
    work_dir,
    prepare_input,
    prepare_output,
    save_uploaded,
    pick_source,
    offer_downloads,
    list_outputs,
    rel_to,
    find_cjk_font,
)

_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        _MOD = load_desktop_module("pdf_indexer")
    return _MOD


def _resolve_font(font_choice: str, uploaded_font) -> str:
    """返回字体文件路径（自动检测 / 系统字体 / 用户上传）"""
    if font_choice == "📤 上传字体文件":
        if uploaded_font is None:
            return ""
        wd = work_dir("pdf_indexer") / "fonts"
        wd.mkdir(exist_ok=True)
        p = wd / uploaded_font.name
        p.write_bytes(uploaded_font.getbuffer())
        return str(p)
    if font_choice != "🔍 自动检测":
        # 形如 C:\Windows\Fonts\simsun.ttc
        return font_choice
    return find_cjk_font()


def _run(log_cb, stop_event, files, font_path, out_dir, base_dir=None):
    """逐个给 PDF 打索引（处理副本，输出到 out_dir，保留上传时的目录层级）

    入参规范化：files 可能是 str（save_uploaded 返回）或 Path；
    底层 add_index_to_pdf 按 str 路径写（内部有 file_path + ".tmp"），
    所以对外一律传 str，本函数内部用 Path 处理目录拼接。
    """
    m = _mod()
    if not font_path or not os.path.exists(font_path):
        log_cb("❌ 未找到可用的中文字体！请在下方选择或上传字体文件。")
        return True, "缺少字体，任务未执行。"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ok_count = fail_count = 0
    for i, src in enumerate(files):
        if stop_event and stop_event.is_set():
            log_cb(">>> 用户停止任务")
            break
        src = str(src)
        log_cb(f"[{i + 1}/{len(files)}] 处理: {os.path.basename(src)}")
        ok, msg = m.add_index_to_pdf(src, font_path, log_cb, stop_event)
        if ok:
            ok_count += 1
            # 处理成功的文件移动到输出目录（保留子目录层级）
            dst = out_dir / (rel_to(src, base_dir) if base_dir else Path(src).name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            os.replace(src, str(dst))
            log_cb(f"  ✅ {rel_to(dst, out_dir)}")
        else:
            fail_count += 1
            log_cb(f"  ❌ {msg}")
    return True, f"处理完成。成功: {ok_count}, 失败: {fail_count}"


def _render_preview(pdf_path, max_pages=2):
    """渲染 PDF 前几页为图片预览（带红色索引的效果展示）"""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        for i in range(min(max_pages, doc.page_count)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
            st.image(pix.tobytes("png"), caption=f"第 {i + 1} 页", width=360)
        doc.close()
    except Exception as e:  # noqa: BLE001
        st.caption(f"（预览失败：{e}）")


def show_page(tool: dict):
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)
    tool_header(tool)

    # ---- 数据来源：上传文件 / 上传文件夹 ----
    is_folder, uploaded = pick_source(
        ("pdf",), key="idx",
        caption="只处理 .pdf；文件夹上传会递归包含子文件夹，结果里保留同样的目录层级。",
    )

    # ---- 字体 ----
    c1, c2 = st.columns([1, 2])
    with c1:
        font_choice = st.radio("索引字体", ["🔍 自动检测", "📤 上传字体文件"], key="idx_font_choice")
    with c2:
        if font_choice == "🔍 自动检测":
            auto_font = find_cjk_font()
            if auto_font:
                st.caption(f"✅ 已检测到系统中文字体：`{auto_font}`")
            else:
                st.caption("⚠️ 未检测到系统中文字体，请切换到「上传字体文件」")
        else:
            st.file_uploader("上传字体（.ttc/.ttf，需支持中文）", type=["ttc", "ttf", "otf"],
                             key="idx_font_upload")

    # 系统字体快捷选择
    sys_fonts = []
    for c in ("C:\\Windows\\Fonts\\simsun.ttc", "C:\\Windows\\Fonts\\msyh.ttc",
              "C:\\Windows\\Fonts\\simhei.ttf"):
        if os.path.exists(c):
            sys_fonts.append(c)
    extra_font = ""
    if sys_fonts:
        extra_font = st.selectbox("或直接指定系统字体", ["（自动检测）"] + sys_fonts,
                                  key="idx_sys_font")
        if extra_font == "（自动检测）":
            extra_font = ""

    rec = st.session_state.get("task_pdf_indexer")
    running = bool(rec and not rec.get("done"))
    c_run, c_stop = st.columns([4, 1])
    with c_run:
        run_clicked = st.button("▶️ 添加索引", type="primary", use_container_width=True,
                                disabled=running or not uploaded, key="idx_btn_run")
    with c_stop:
        if running:
            st.button("⏹️ 停止", use_container_width=True, key="idx_btn_stop",
                      on_click=stop_task, args=("task_pdf_indexer",))

    if run_clicked:
        if not uploaded:
            st.error("请先上传 PDF 文件或文件夹")
        else:
            in_dir, files = prepare_input("pdf_indexer", uploaded)
            out_dir = prepare_output("pdf_indexer")
            font_path = extra_font or _resolve_font(font_choice,
                            st.session_state.get("idx_font_upload"))
            start_task("task_pdf_indexer", _run, files, font_path, str(out_dir), str(in_dir))
            st.rerun()

    rec = poll_task("task_pdf_indexer")
    if rec is not None and rec.get("done"):
        _, msg = split_result(rec["result"])
        if "成功" in msg:
            st.success(msg)
        else:
            st.error(msg)
        show_logs(rec)

        out_dir = work_dir("pdf_indexer") / "output"
        outputs = list_outputs(out_dir)
        if outputs:
            st.markdown("---")
            st.subheader("👀 效果预览")
            _render_preview(str(outputs[0]))
            st.subheader("📥 结果下载")
            offer_downloads(outputs, key_prefix="idx_out", zip_name="带索引PDF.zip",
                            base_dir=out_dir,
                            single_label="📥 保存文件：带索引的 PDF（浏览器下载）")
    elif running:
        st.info("⏳ 正在处理中…（日志实时刷新）")
