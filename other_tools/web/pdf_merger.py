# -*- coding: utf-8 -*-
"""PDF合并/分拆/转换 —— Streamlit 页面
复用原桌面版 pdf_merger 的 core_merge_process / convert_* 纯逻辑。

输入：上传 PDF（可多选，可混入图片/Excel/Word 一并转换合并）/ 上传文件夹（递归）
输出：合并结果（多个分卷时打包 zip）通过浏览器下载，不写入项目目录。
Word 转 PDF 依赖本机 Microsoft Word（docx2pdf COM），页面会给出能力提示。
"""
import os
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
    work_dir,
    prepare_input,
    pick_source,
    offer_downloads,
    find_cjk_font,
)

_MOD = None

IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp")
XLS_EXTS = (".xlsx", ".xls")
DOC_EXTS = (".docx", ".doc")
UPLOAD_EXTS = ("pdf",) + tuple(e.lstrip(".") for e in IMG_EXTS + XLS_EXTS + DOC_EXTS)


def _mod():
    global _MOD
    if _MOD is None:
        _MOD = load_desktop_module("pdf_merger")
    return _MOD


def _word_available() -> bool:
    """探测 Word 转 PDF 能力（docx2pdf + 本机 Word）"""
    try:
        import docx2pdf  # noqa: F401
        import pythoncom  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_font_asset() -> str:
    """Excel/图片转 PDF 需要 simsun.ttc 资源：缺失时从系统字体复制一份（应用资源，仅本机使用）"""
    rel = os.path.join("assets", "fonts", "simsun.ttc")
    if os.path.exists(rel):
        return os.path.abspath(rel)
    os.makedirs(os.path.join("assets", "fonts"), exist_ok=True)
    sys_font = find_cjk_font()
    if sys_font:
        try:
            shutil.copy2(sys_font, rel)
            return os.path.abspath(rel)
        except OSError:
            return ""
    return ""


def _run_merge(log_cb, stop_event, src_folder, recursive, include_others,
               include_word, keep_converted, split_enabled, split_step):
    return _mod().core_merge_process(
        src_folder, recursive, include_others, include_word, keep_converted,
        split_enabled, split_step, log_cb, stop_event,
    )


def _count_by_kind(files):
    """统计上传文件里图片/Excel/Word 的数量（用于提示「勾选了才会转换」）"""
    n_img = n_xls = n_doc = n_pdf = 0
    for f in files:
        ext = Path(str(f)).suffix.lower()
        if ext == ".pdf":
            n_pdf += 1
        elif ext in IMG_EXTS:
            n_img += 1
        elif ext in XLS_EXTS:
            n_xls += 1
        elif ext in DOC_EXTS:
            n_doc += 1
    return n_pdf, n_img, n_xls, n_doc


def _collect_outputs(in_dir: Path, files, keep_converted: bool):
    """收集产物：合并结果（含分卷）+ 可选保留的转换后单文件 PDF"""
    in_dir = Path(in_dir)
    merged = sorted([p for p in in_dir.rglob("合并结果*.pdf")], key=lambda p: str(p).lower())
    converted = []
    if keep_converted:
        for f in files:
            if Path(str(f)).suffix.lower() in IMG_EXTS + XLS_EXTS + DOC_EXTS:
                cand = Path(str(f)).with_suffix(".pdf")
                if cand.is_file():
                    converted.append(cand)
        converted.sort(key=lambda p: str(p).lower())
    return merged, converted


def show_page(tool: dict):
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)
    tool_header(tool)

    word_ok = _word_available()

    # ---- 合并选项 ----
    c1, c2, c3 = st.columns(3)
    with c1:
        include_others = st.checkbox("转换 Excel/图片 后合并", value=False,
                                     help="把上传的 .xlsx/.xls 与图片转成 PDF 再参与合并")
        if include_others and not os.path.exists(os.path.join("assets", "fonts", "simsun.ttc")):
            st.caption("ℹ️ 首次使用会自动从系统字体复制一份中文字体作为应用资源")
    with c2:
        include_word = st.checkbox("转换 Word 后合并（需本机 Word）", value=False,
                                   disabled=not word_ok, key="pm_word")
        if not word_ok:
            st.caption("ℹ️ 未安装 docx2pdf / 本机 Word，该选项不可用")
    with c3:
        keep_converted = st.checkbox("结果里保留转换后的独立 PDF", value=False,
                                     help="勾选后，图片/Excel/Word 各自转出的 PDF 也会一起给你")

    c1, c2, c3 = st.columns(3)
    with c1:
        split_enabled = st.checkbox("合并后按页数分拆", value=False, key="pm_split")
    with c2:
        split_step = 20
        if split_enabled:
            split_step = st.number_input("每 N 页存为一个文件", min_value=1, value=20,
                                         step=1, key="pm_step")
    with c3:
        st.caption("合并顺序：按文件相对路径排序（子目录里的文件排在后面）")

    # ---- 数据来源：上传文件 / 上传文件夹 ----
    is_folder, uploaded = pick_source(
        UPLOAD_EXTS, key="pm",
        caption="可直接多选 PDF；文件夹上传会递归包含所有子文件夹。"
                "混入的图片/Excel/Word 只有在上面勾选对应「转换」后才会参与合并。",
    )
    if uploaded:
        n_pdf, n_img, n_xls, n_doc = _count_by_kind(uploaded)
        st.caption(f"已选 {len(uploaded)} 个文件：PDF {n_pdf}，图片 {n_img}，Excel {n_xls}，Word {n_doc}")
        if not include_others and (n_img or n_xls):
            st.info(f"检测到 {n_img + n_xls} 个图片/Excel 文件，未勾选「转换 Excel/图片」——它们将被跳过")
        if not include_word and n_doc:
            st.info(f"检测到 {n_doc} 个 Word 文件，未勾选「转换 Word」——它们将被跳过")

    rec = st.session_state.get("task_pdf_merge")
    running = bool(rec and not rec.get("done"))
    c_run, c_stop = st.columns([4, 1])
    with c_run:
        run_clicked = st.button("▶️ 开始合并", type="primary", use_container_width=True,
                                disabled=running or not uploaded, key="pm_btn_run")
    with c_stop:
        if running:
            st.button("⏹️ 停止", use_container_width=True, key="pm_btn_stop",
                      on_click=stop_task, args=("task_pdf_merge",))

    if run_clicked:
        if not uploaded:
            st.error("请先上传 PDF 文件或文件夹")
        elif include_others and not _ensure_font_asset():
            st.error("缺少中文字体（assets/fonts/simsun.ttc 且系统无可用字体），无法转换 Excel/图片")
        else:
            in_dir, files = prepare_input("pdf_merger", uploaded)
            start_task("task_pdf_merge", _run_merge, str(in_dir), True,
                       include_others, include_word, keep_converted,
                       split_enabled, int(split_step))
            st.rerun()

    rec = poll_task("task_pdf_merge")
    if rec is not None and rec.get("done"):
        msg = str(rec["result"] or "")
        is_err = (not msg) or msg.startswith("❌") or "错误" in msg or "未找到" in msg \
            or "已终止" in msg or ("失败" in msg and "成功" not in msg)
        if is_err:
            st.error(msg)
        else:
            st.success(msg)
        show_logs(rec)

        in_dir = work_dir("pdf_merger") / "input"
        uploaded_paths = [str(p) for p in in_dir.rglob("*") if p.is_file()] if in_dir.exists() else []
        merged, converted = _collect_outputs(in_dir, uploaded_paths, keep_converted)
        if merged:
            st.markdown("---")
            st.subheader("📥 结果下载")
            offer_downloads(merged, key_prefix="pm_merged", zip_name="合并结果.zip",
                            base_dir=in_dir,
                            single_label=f"📥 保存文件：{merged[0].name}（浏览器下载）")
        if converted:
            with st.expander(f"转换后的独立 PDF（{len(converted)} 个）"):
                offer_downloads(converted, key_prefix="pm_conv", zip_name="转换后PDF.zip",
                                base_dir=in_dir, single_label="📥 保存转换后的 PDF")
    elif running:
        st.info("⏳ 正在合并中…（日志实时刷新）")
