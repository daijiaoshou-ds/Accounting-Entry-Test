# -*- coding: utf-8 -*-
"""文件批量整理 —— Streamlit 页面（单页呈现：上为清单生成，下为清单执行）

输入：上传文件夹 / 上传文件（会作为副本放进临时目录，不动你本机的原文件）
输出：清单 Excel、整理后的文件夹（打包 zip）均通过浏览器下载。
"""
import os
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
    prepare_input,
    save_uploaded,
    pick_source,
    offer_downloads,
    rel_to,
    scrub_paths,
)

_MOD = None

MANIFEST_NAME = "文件清单.xlsx"


def _mod():
    global _MOD
    if _MOD is None:
        _MOD = load_desktop_module("file_batch_tool")
    return _MOD


def _input_dir() -> Path:
    """本次任务的输入副本目录（上传件落盘处，也是「整理」的根目录）"""
    d = work_dir("file_batch") / "input"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    return work_dir("file_batch") / MANIFEST_NAME


def _relativize_manifest(excel_path, root_folder) -> int:
    """把清单里的「文件路径」改成相对根目录的路径（下载给别人/换机器也能看懂）"""
    df = pd.read_excel(excel_path, dtype=str).fillna("")
    if "文件路径" not in df.columns:
        return 0
    root = Path(root_folder)
    vals, n = [], 0
    for v in df["文件路径"].tolist():
        v = str(v).strip()
        if v:
            try:
                v = str(Path(v).resolve().relative_to(root.resolve()))
                n += 1
            except (ValueError, OSError):
                pass
        vals.append(v)
    df["文件路径"] = vals
    df.to_excel(excel_path, index=False)
    return n


def _resolve_manifest(excel_path, root_folder):
    """把清单里的相对「文件路径」解析成绝对路径，返回给底层用的清单路径"""
    df = pd.read_excel(excel_path, dtype=str).fillna("")
    if "文件路径" not in df.columns:
        return excel_path
    root = Path(root_folder)
    vals, changed = [], False
    for v in df["文件路径"].tolist():
        v = str(v).strip()
        if v and not os.path.isabs(v):
            v = str(root / v)
            changed = True
        vals.append(v)
    if not changed:
        return excel_path
    df["文件路径"] = vals
    resolved = work_dir("file_batch") / "_清单_resolved.xlsx"
    df.to_excel(resolved, index=False)
    return str(resolved)


def _manifest_self_contained(excel_path) -> bool:
    """清单里的「文件路径」是否都是绝对路径（= 自带文件位置，不需要上传副本）"""
    try:
        df = pd.read_excel(excel_path, dtype=str).fillna("")
    except Exception:  # noqa: BLE001
        return False
    vals = [str(v).strip() for v in df.get("文件路径", [])]
    vals = [v for v in vals if v]
    return bool(vals) and all(os.path.isabs(v) for v in vals)


def _run_generate(log_cb, stop_event, root_folder, save_path):
    ok, msg = _mod().generate_excel_template(root_folder, save_path, log_cb, stop_event)
    if ok:
        try:
            n = _relativize_manifest(save_path, root_folder)
            log_cb(f"清单路径已转为相对路径（{n} 行），换机器/改完再传回来都能用")
        except Exception as e:  # noqa: BLE001
            log_cb(f"（清单路径相对化失败，不影响执行：{e}）")
    return ok, msg


def _run_process(log_cb, stop_event, excel_path, root_folder, is_copy, is_replace):
    resolved = _resolve_manifest(excel_path, root_folder)
    return _mod().process_files_from_excel(
        resolved, root_folder, log_cb,
        is_copy_mode=is_copy, is_replace_mode=is_replace, stop_event=stop_event,
    )


def show_page(tool: dict):
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)
    tool_header(tool)

    st.caption("清单 Excel 固定 5 列：`原文件夹名称` / `原文件名` / `文件路径` / `新文件夹名称` / `新文件名`"
               "（文件路径留空 = 新建文件；新文件夹/新文件名留空 = 保持原样）")

    # ============ ① 生成文件清单 ============
    st.markdown("#### ① 生成文件清单")
    is_folder, uploaded = pick_source(
        None, key="fb_gen",
        caption="上传要整理的那个文件夹（或一批文件）；文件夹会递归扫描所有子目录。",
    )

    gen_rec = st.session_state.get("task_fb_generate")
    gen_running = bool(gen_rec and not gen_rec.get("done"))
    c1, c2 = st.columns([4, 1])
    with c1:
        gen_click = st.button("📄 生成文件清单 Excel", type="primary", use_container_width=True,
                              disabled=gen_running, key="fb_btn_gen")
    with c2:
        if gen_running:
            st.button("⏹️ 停止", use_container_width=True, key="fb_btn_stop_gen",
                      on_click=stop_task, args=("task_fb_generate",))
    if gen_click:
        if not uploaded:
            st.error("请先上传文件夹或文件")
        else:
            in_dir, files = prepare_input("file_batch", uploaded)   # 每次生成都重来一份干净副本
            start_task("task_fb_generate", _run_generate, str(in_dir), str(_manifest_path()))
            st.rerun()

    gen_rec = poll_task("task_fb_generate")
    if gen_rec is not None and gen_rec.get("done"):
        ok, msg = split_result(gen_rec["result"])
        if ok:
            st.success(scrub_paths(msg, work_dir("file_batch")))
            manifest = _manifest_path()
            if manifest.exists():
                try:
                    st.dataframe(pd.read_excel(manifest).head(200), use_container_width=True)
                except Exception as e:  # noqa: BLE001
                    st.caption(f"（清单预览失败：{e}）")
                st.download_button("📥 下载文件清单（用 Excel 编辑后到下方②上传回传）",
                                   data=manifest.read_bytes(), file_name=MANIFEST_NAME,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   use_container_width=True, key="fb_btn_download_list")
                st.info("💡 清单已记录：可直接到下方②执行（也可先下载编辑再上传）")
        else:
            st.error(msg)
        show_logs(gen_rec, height=180)

    st.markdown("---")

    # ============ ② 按清单执行整理 ============
    st.markdown("#### ② 按清单执行整理")
    had_generated = _manifest_path().exists()
    excel_src = st.radio("清单来源", ["上传已编辑好的清单 Excel", "使用刚刚生成的清单"],
                         index=1 if had_generated else 0,
                         horizontal=True, key="fb_excel_src")
    if not had_generated and excel_src == "使用刚刚生成的清单":
        st.info("当前还没有生成过清单，请先在①生成（或选择上传模式）")

    excel_path = ""
    if excel_src == "上传已编辑好的清单 Excel":
        up_manifest = st.file_uploader("上传清单 Excel（.xlsx）", type=["xlsx"], key="fb_upload_excel")
        if up_manifest:
            excel_path = str(work_dir("file_batch") / "上传清单.xlsx")
            Path(excel_path).write_bytes(up_manifest.getbuffer())
    else:
        excel_path = str(_manifest_path()) if had_generated else ""

    # 输入副本：可沿用①上传的，也可在这里重新上传一份（同名覆盖）
    in_dir = _input_dir()
    existing = [p for p in in_dir.rglob("*") if p.is_file()]
    if existing:
        st.caption(f"当前输入副本：{len(existing)} 个文件（来自①上传，可直接执行）")
    st.markdown("**（可选）重新上传**：覆盖上面的副本，清单里的相对路径按新副本解析")
    _, up_more = pick_source(None, key="fb_more")

    c1, c2 = st.columns(2)
    with c1:
        is_copy = st.checkbox("📋 复制模式（保留原文件）", value=False,
                              help="默认关闭 = 移动文件", key="fb_copy")
    with c2:
        is_replace = st.checkbox("⚠️ 覆盖模式（强制替换同名文件）", value=False,
                                 help="默认关闭 = 自动重命名避重；开启后目标位置已有同名文件时直接替换",
                                 key="fb_replace")
    if is_replace:
        st.caption("已开启覆盖模式：同名目标直接替换（只影响本次上传的副本，你本机的原文件不受影响）")

    proc_rec = st.session_state.get("task_fb_process")
    proc_running = bool(proc_rec and not proc_rec.get("done"))
    c1, c2 = st.columns([4, 1])
    with c1:
        proc_click = st.button("▶️ 执行整理", type="primary", use_container_width=True,
                               disabled=proc_running or not excel_path, key="fb_btn_exec")
    with c2:
        if proc_running:
            st.button("⏹️ 停止", use_container_width=True, key="fb_btn_stop_exec",
                      on_click=stop_task, args=("task_fb_process",))

    if proc_click:
        if not excel_path or not Path(excel_path).exists():
            st.error("清单文件不存在，请上传或先生成")
        elif up_more:
            save_uploaded(up_more, in_dir, overwrite=True)   # 同名覆盖，保留①副本的其余文件
            start_task("task_fb_process", _run_process, excel_path, str(in_dir), is_copy, is_replace)
            st.rerun()
        elif not existing and not _manifest_self_contained(excel_path):
            st.error("输入副本为空：请先在上方上传文件夹，或回到①重新上传生成清单")
        else:
            start_task("task_fb_process", _run_process, excel_path, str(in_dir), is_copy, is_replace)
            st.rerun()

    proc_rec = poll_task("task_fb_process")
    if proc_rec is not None and proc_rec.get("done"):
        ok, msg = split_result(proc_rec["result"])
        msg = scrub_paths(msg, work_dir("file_batch"))
        if ok:
            st.success(msg)
        else:
            st.error(msg)
        show_logs(proc_rec, height=260)

        # 产物 = 整理后的整个根目录（保留目录结构打包）
        results = sorted([p for p in in_dir.rglob("*") if p.is_file()],
                         key=lambda p: str(rel_to(p, in_dir)).lower())
        if ok and results:
            st.markdown("---")
            st.subheader("📥 整理结果下载")
            st.caption("以下内容为**副本**整理后的结果，点保存即下载；你本机的原文件夹不受影响。")
            if len(results) <= 60:
                st.dataframe(
                    pd.DataFrame({"整理后路径": [str(rel_to(p, in_dir)) for p in results]}),
                    use_container_width=True, height=min(400, 40 + 26 * len(results)),
                )
            offer_downloads(results, key_prefix="fb_out", zip_name="整理结果.zip", base_dir=in_dir,
                            single_label="📥 保存整理结果")
