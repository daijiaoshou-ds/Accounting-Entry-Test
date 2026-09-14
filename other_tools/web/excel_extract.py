# -*- coding: utf-8 -*-
"""Excel数据提取 —— Streamlit 页面
复用原桌面版 column_extractor 的纯逻辑（core_process 统一入口，双引擎）。

输入：上传文件 / 上传文件夹（文件夹递归、保留目录结构）
输出：结果（Excel 或 CSV）通过浏览器下载，不写入项目目录。
"""
from pathlib import Path

import pandas as pd
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
    reset_work_dir,
    save_uploaded,
    pick_source,
    offer_downloads,
    scrub_paths,
)

_MOD = None


def _mod():
    global _MOD
    if _MOD is None:
        _MOD = load_desktop_module("column_extractor")
    return _MOD


def _parse_keywords(raw: str) -> list:
    """解析逗号分隔关键词：兼容全角逗号"""
    if not raw:
        return []
    parts = raw.replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _run(log_cb, stop_event, source_path, is_folder, recursive,
         target_sheets, extract_mode, exact_cols, fuzzy_cols, threshold,
         save_path, use_polars, is_csv, exact_sheet, header_start, raw_merge):
    """后台执行入口（约定签名：log_cb, stop_event, ...）"""
    return _mod().core_process(
        source_path, is_folder, recursive, target_sheets, extract_mode,
        exact_cols, fuzzy_cols, threshold, save_path, log_cb,
        stop_event=stop_event, use_polars=use_polars, is_csv=is_csv,
        exact_sheet_match=exact_sheet, header_start_row=header_start,
        raw_merge_mode=raw_merge,
    )


def prepare_run(is_csv: bool):
    """准备工作目录并返回 (输入目录, 保存路径)。

    与页面共用同一套目录约定，避免「页面建了 output、底层却要写另一处」这类不同步问题；
    也可被测试直接调用以复现页面行为。
    """
    wd = reset_work_dir("excel_extract")
    in_dir = wd / "input"
    in_dir.mkdir(exist_ok=True)
    out_dir = wd / "output"
    out_dir.mkdir(parents=True, exist_ok=True)  # 必须先建目录，否则底层 to_excel/write_csv 报 non-existent directory
    save_path = str(out_dir / ("提取结果.csv" if is_csv else "提取结果.xlsx"))
    return in_dir, save_path


def output_path(is_csv: bool) -> Path:
    """结果文件的约定位置"""
    return work_dir("excel_extract") / "output" / ("提取结果.csv" if is_csv else "提取结果.xlsx")


def _read_output(path: str):
    """按扩展名读回结果 DataFrame"""
    if path.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return pd.read_csv(path, encoding=enc)
            except (UnicodeDecodeError, ValueError):
                continue
        return pd.read_csv(path, encoding="latin1")
    return pd.read_excel(path)


def show_page(tool: dict):
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)
    tool_header(tool)

    # ---- 提取参数 ----
    c1, c2, c3 = st.columns(3)
    with c1:
        extract_mode = st.radio("提取列模式", ["提取全部列", "提取指定列"], horizontal=True)
    with c2:
        engine_choice = st.radio("处理引擎", ["Pandas（稳定兼容）", "Polars+fastexcel（极速）"], horizontal=True)
    with c3:
        out_fmt = st.radio("结果格式", ["Excel (.xlsx)", "CSV (.csv)"], horizontal=True)

    raw_merge = st.checkbox("原封不动直接合并（堆叠模式，保留所有原始列）", value=False,
                            help="勾选后忽略下方「提取指定列」规则，全部列原样堆叠")

    specific = (extract_mode == "提取指定列") and not raw_merge
    exact_cols, fuzzy_cols, threshold = [], [], 0.6
    if specific:
        c1, c2, c3 = st.columns(3)
        with c1:
            exact_cols = _parse_keywords(st.text_input("精确匹配列（列名完全一致，逗号分隔）"))
        with c2:
            fuzzy_cols = _parse_keywords(st.text_input("模糊匹配列（包含关键词即提取，逗号分隔）"))
        with c3:
            threshold = st.slider("匹配严格度（Polars 引擎生效）", 0.1, 1.0, 0.3, 0.01)
        if not exact_cols and not fuzzy_cols:
            st.warning("请至少填写一类匹配列，或改用「提取全部列」")

    c1, c2, c3 = st.columns(3)
    with c1:
        target_sheets = _parse_keywords(st.text_input("指定工作表（留空=全部，逗号分隔）"))
    with c2:
        exact_sheet = st.checkbox("工作表名称精确匹配", value=False)
    with c3:
        header_start = st.number_input("表头起始行", min_value=1, value=1, step=1)

    use_polars = engine_choice.startswith("Polars")
    is_csv = out_fmt.startswith("CSV")

    # ---- 数据来源：上传文件 / 上传文件夹 ----
    is_folder, uploaded = pick_source(
        ("xlsx", "xlsm"), key="ex",
        caption="只处理 .xlsx / .xlsm；文件夹上传会递归包含子文件夹（子目录里的表格一并提取）。",
    )
    if uploaded:
        _preview_first(uploaded[0], is_folder)

    # ---- 运行区 ----
    rec = st.session_state.get("task_excel_extract")
    running = bool(rec and not rec.get("done"))

    c_run, c_stop = st.columns([4, 1])
    with c_run:
        run_clicked = st.button("▶️ 开始提取", type="primary", use_container_width=True,
                                disabled=running, key="ex_btn_run")
    with c_stop:
        if running:
            st.button("⏹️ 停止", use_container_width=True, key="ex_btn_stop",
                      on_click=stop_task, args=("task_excel_extract",))

    if run_clicked:
        if not uploaded:
            st.error("请先上传 Excel 文件或文件夹")
        else:
            in_dir, save_path = prepare_run(is_csv)   # 先清工作目录并建好输出目录
            save_uploaded(uploaded, in_dir)           # 再落盘（文件夹上传保留目录结构）
            start_task("task_excel_extract", _run, str(in_dir), True, True,
                       target_sheets, "specific" if specific else "all",
                       exact_cols, fuzzy_cols, threshold, save_path,
                       use_polars, is_csv, exact_sheet, int(header_start), raw_merge)
            st.rerun()

    rec = poll_task("task_excel_extract")
    if rec is not None and rec.get("done"):
        ok, msg = split_result(rec["result"])
        if ok:
            st.success(scrub_paths(msg, work_dir("excel_extract")))
        else:
            st.error(scrub_paths(msg, work_dir("excel_extract")))
        show_logs(rec)

        out_path = output_path(is_csv)
        if out_path.exists():
            st.markdown("---")
            st.subheader("📥 结果预览与下载")
            try:
                df = _read_output(str(out_path))
                st.dataframe(df.head(200), use_container_width=True)
                st.caption(f"共 {len(df)} 行 × {df.shape[1]} 列（预览前 200 行）")
            except Exception as e:  # noqa: BLE001
                st.warning(f"结果预览失败：{e}")
            offer_downloads([out_path], key_prefix="ex_out",
                            single_label=f"📥 保存文件：{out_path.name}（浏览器下载）")
    elif running:
        st.info("⏳ 正在提取中…（日志实时刷新）")


def _preview_first(uploaded_file, is_folder: bool = False):
    """预览第一个文件的工作表与前几行（帮助用户确认表头）"""
    try:
        with st.expander(f"📖 预览「{uploaded_file.name}」", expanded=False):
            buf = uploaded_file.getvalue()
            import io
            sheets = pd.read_excel(io.BytesIO(buf), sheet_name=None, nrows=6)
            for name, df in sheets.items():
                st.markdown(f"**工作表：{name}**（前 {len(df)} 行）")
                st.dataframe(df.head(6), use_container_width=True)
    except Exception as e:  # noqa: BLE001
        st.caption(f"（预览失败：{e}）")
