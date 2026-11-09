# -*- coding: utf-8 -*-
"""序时账清洗 —— Streamlit 界面（4.0 单一流程）。

页面只做四件事：上传 → 确认字段 → 运行 → 看结果/复核。
没有 PMI 矩阵、关键词命中、自动词、Excel 纠错页——这些在 4.0 已整体删除
（原因见 `summary_cleaner/pipeline.py` 顶部）。同样的能力在 React 前端里
（`web/`，走 `api/app.py`），两边共用 `summary_cleaner/pipeline`。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from .classifier import JournalClassifier
from .config import load_subject_list
from .io_utils import EXCEL_ROW_LIMIT, read_table, serialize_table, too_large_hint

# ============================================================================
# Session State
# ============================================================================

SUMMARY_STATE_DEFAULTS = {
    "summary_raw_data": None,          # 上传的原始 DataFrame
    "summary_column_mapping": {},      # {key: 实际列名}
    "summary_classified_df": None,     # 行级结果
    "summary_detail": None,            # 凭证明细
    "summary_stats": None,             # 统计
    "summary_classifier": None,        # JournalClassifier（单例，模型只加载一次）
    "summary_last_sig": None,          # 文件内容签名（同名重传也能识别）
    "summary_last_file": "",
}


def _init_state():
    for key, default in SUMMARY_STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


def _get_classifier() -> JournalClassifier:
    if st.session_state.summary_classifier is None:
        st.session_state.summary_classifier = JournalClassifier(
            subject_list=load_subject_list())
    return st.session_state.summary_classifier


# ============================================================================
# 入口
# ============================================================================

def show_summary_cleaner():
    """序时账清洗主界面。由 app.py 调用。"""
    _init_state()

    with st.sidebar:
        st.markdown("## 序时账清洗")
        st.caption("科目归一化 → 模型识别 → 机械凭证硬约束")
        st.markdown("---")
        _render_upload_section()
        st.markdown("---")
        _render_run_controls()

    st.title("序时账清洗")
    st.caption("业务桶由模型判定；程序只负责读表、科目归一化、机械凭证与写回。")
    _render_model_status()

    if st.session_state.summary_raw_data is None:
        st.info("请在左侧上传序时账文件（Excel / CSV）开始。")
        return

    has_result = st.session_state.summary_classified_df is not None
    tabs_defs = [("字段配置", _render_field_config)]
    if has_result:
        tabs_defs += [
            ("分类概览", _render_overview),
            ("详细结果", _render_detailed_results),
            ("复核", _render_review_view),
        ]
    tabs = st.tabs([name for name, _ in tabs_defs])
    for tab, (_name, render) in zip(tabs, tabs_defs):
        with tab:
            render()


# ============================================================================
# 侧边栏
# ============================================================================

def _render_upload_section():
    st.markdown("### 上传数据")
    uploaded = st.file_uploader(
        "序时账文件", type=["xlsx", "xls", "xlsm", "xlsb", "csv", "txt", "tsv"],
        key="summary_upload", help="Excel 走 calamine（Rust），CSV 自动回退编码",
    )
    if uploaded is not None:
        # 内容签名判新文件：同名同大小但内容变了也能重新加载
        sig = hashlib.md5(uploaded.getvalue()).hexdigest()
        if sig != st.session_state.summary_last_sig:
            st.session_state.summary_last_sig = sig
            st.session_state.summary_last_file = uploaded.name
            try:
                df = read_table(uploaded)
                st.session_state.summary_raw_data = df
                st.session_state.summary_classified_df = None
                st.session_state.summary_detail = None
                st.session_state.summary_stats = None
                st.session_state.summary_column_mapping = \
                    _get_classifier().auto_detect_columns(df)
                st.success(f"已加载：{len(df):,} 行 × {len(df.columns)} 列")
            except Exception as e:  # noqa: BLE001
                st.error(f"读取文件失败：{e}")
                st.session_state.summary_raw_data = None

    df = st.session_state.summary_raw_data
    if df is not None:
        st.caption(f"当前：{st.session_state.summary_last_file} ｜ "
                   f"{len(df):,} 行 × {len(df.columns)} 列")


def _render_run_controls():
    st.markdown("### 运行")
    df = st.session_state.summary_raw_data
    mapping = st.session_state.summary_column_mapping or {}
    missing = [k for k in ("voucher_no", "subject") if not mapping.get(k)]
    if df is not None and missing:
        st.caption("必填字段未配置：" + "、".join(missing))

    if st.button("开始分类", type="primary", use_container_width=True,
                 disabled=(df is None or bool(missing)), key="summary_run_btn"):
        _run_classification()


def _render_model_status():
    """模型交付物是否齐全（只查文件，不加载权重）。"""
    from .config import get_nn_storage_dir

    base = Path(get_nn_storage_dir())
    pt = base / "finance_classifier.pt"
    fine = base / "fine_tuned"
    try:
        import torch  # noqa: F401
        has_torch = True
    except ImportError:
        has_torch = False

    if not has_torch or not pt.exists() or not fine.is_dir():
        st.error(
            "模型未就绪：4.0 没有纯程序兜底，缺模型就无法分类。\n\n"
            f"检查项 — torch: {has_torch} ｜ finance_classifier.pt: {pt.exists()} ｜ "
            f"fine_tuned/: {fine.is_dir()}"
        )
        return

    detail = ""
    try:
        import torch
        ck = torch.load(pt, map_location="cpu", weights_only=True)
        if ck.get("best_val_acc") is not None:
            detail = f"（验证集 {ck['best_val_acc']:.1%}"
            if ck.get("best_epoch"):
                detail += f"，epoch {ck['best_epoch']}"
            detail += "）"
    except Exception:  # noqa: BLE001
        pass
    st.caption(f"模型已就绪{detail}")


# ============================================================================
# 字段配置
# ============================================================================

def _render_field_config():
    st.markdown("#### 字段映射")
    st.caption("自动识别列名，可手动调整。带 * 为必填。")
    mapping = st.session_state.summary_column_mapping
    df = st.session_state.summary_raw_data
    columns = list(df.columns)

    col1, col2, col3 = st.columns(3)
    with col1:
        _field_selector("voucher_no", "凭证号 *", columns, mapping,
                        ["凭证号", "凭证编号", "voucher"])
        _field_selector("subject", "一级科目 *", columns, mapping,
                        ["一级科目", "subject"])
    with col2:
        _field_selector("summary", "摘要", columns, mapping, ["摘要", "summary"])
        _field_selector("subject_name", "科目名称/明细", columns, mapping,
                        ["科目名称", "明细科目", "二级科目", "subject_name"])
    with col3:
        _field_selector("debit", "借方金额", columns, mapping,
                        ["借方金额", "借方", "debit"])
        _field_selector("credit", "贷方金额", columns, mapping,
                        ["贷方金额", "贷方", "credit"])

    st.markdown("---")
    st.markdown("#### 数据预览")
    st.dataframe(df.head(10), use_container_width=True, hide_index=True)


def _field_selector(key: str, label: str, columns: list, mapping: dict,
                    patterns: list):
    current = mapping.get(key, "")
    if not current:
        for col in columns:
            if any(p in str(col) for p in patterns):
                current = col
                break
    idx = columns.index(current) if current in columns else 0
    mapping[key] = st.selectbox(label, columns, index=idx,
                                key=f"summary_field_{key}")


# ============================================================================
# 运行
# ============================================================================

def _run_classification():
    df = st.session_state.summary_raw_data
    mapping = dict(st.session_state.summary_column_mapping or {})
    mapping = {k: v for k, v in mapping.items() if v}

    bar = st.progress(0, text="准备中…")

    def on_progress(pct: int, msg: str) -> None:
        bar.progress(min(100, max(0, pct)), text=msg)

    try:
        clf = _get_classifier()
        out, detail, stats = clf.classify(df, mapping, progress=on_progress)
        st.session_state.summary_classified_df = out
        st.session_state.summary_detail = detail
        st.session_state.summary_stats = stats
        bar.progress(100, text="完成")
        engine = stats.get("engine") or {}
        st.success(
            f"完成：{stats['total_vouchers']:,} 张凭证 ｜ "
            f"模型判定 {engine.get('model_calls', 0):,} ｜ "
            f"硬约束 {engine.get('hard_rule_vouchers', 0):,}"
        )
        conf = stats.get("confidence_dist") or {}
        st.caption(f"置信度：高 {conf.get('高', 0):,} ｜ 中 {conf.get('中', 0):,} "
                   f"｜ 低 {conf.get('低', 0):,}")
        bar.empty()
    except Exception as e:  # noqa: BLE001
        bar.empty()
        st.error(f"分类失败：{e}")


# ============================================================================
# 概览 / 明细 / 复核
# ============================================================================

def _render_overview():
    stats = st.session_state.summary_stats or {}
    detail = st.session_state.summary_detail
    if detail is None or detail.empty:
        st.info("没有结果")
        return

    conf = stats.get("confidence_dist") or {}
    engine = stats.get("engine") or {}
    total = max(1, sum(conf.values()))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("凭证数", f"{stats.get('total_vouchers', 0):,}")
    c2.metric("高置信", f"{conf.get('高', 0):,}", f"{conf.get('高', 0) / total:.1%}")
    c3.metric("中置信", f"{conf.get('中', 0):,}", f"{conf.get('中', 0) / total:.1%}")
    c4.metric("低置信", f"{conf.get('低', 0):,}", f"{conf.get('低', 0) / total:.1%}")

    st.caption(
        f"引擎：{engine.get('mode', '')} ｜ 模型调用 {engine.get('model_calls', 0):,} ｜ "
        f"硬约束 {engine.get('hard_rule_vouchers', 0):,} ｜ "
        f"父级回退 {engine.get('fallback_to_parent', 0):,}"
    )

    unk = stats.get("unknown_subjects") or {}
    if unk.get("vouchers"):
        names = "、".join(f"{x['name']}({x['vouchers']})"
                         for x in (unk.get("subjects") or [])[:10])
        st.warning(
            f"有 **{unk['vouchers']}** 张凭证含模型没见过的科目：{names}"
            "\n\n这些凭证仍由模型按摘要判定，但科目信号缺位。"
            "多数情况是别名表没维护——把上面的科目补进 `subject_aliases.json` 后重跑。"
        )

    counts = stats.get("bucket_counts") or {}
    if counts:
        st.markdown("#### 桶分布")
        series = pd.Series(counts).sort_values(ascending=False)
        st.dataframe(series.rename("凭证数").to_frame(), use_container_width=True)

    st.markdown("---")
    _render_downloads()


def _render_downloads():
    out = st.session_state.summary_classified_df
    detail = st.session_state.summary_detail
    if out is None:
        return
    c1, c2 = st.columns(2)
    with c1:
        data, name, mime, hint = serialize_table(out, "分类结果.xlsx", sheet_name="分类结果")
        if hint:
            st.warning(hint)
        st.download_button("下载分类结果", data=data, file_name=name, mime=mime,
                           key="dl_result")
    with c2:
        if detail is not None and not detail.empty:
            data, name, mime, hint = serialize_table(detail, "凭证汇总.xlsx",
                                                     sheet_name="凭证汇总")
            if hint:
                st.warning(hint)
            st.download_button("下载凭证汇总", data=data, file_name=name, mime=mime,
                               key="dl_vouchers")


def _render_detailed_results():
    detail = st.session_state.summary_detail
    if detail is None or detail.empty:
        st.info("没有结果")
        return

    level = st.radio("置信度", ["全部", "高", "中", "低"], horizontal=True,
                     key="summary_detail_level")
    view = detail if level == "全部" else detail[detail["置信度"] == level]

    st.caption(f"{len(view):,} 张凭证")
    if len(view) > EXCEL_ROW_LIMIT:
        st.info("结果行数较多，建议用上方导出后查看。")
    st.dataframe(view.head(500), use_container_width=True, hide_index=True)


def _render_review_view():
    stats = st.session_state.summary_stats or {}
    detail = st.session_state.summary_detail
    if detail is None or detail.empty:
        st.info("没有结果")
        return

    st.caption(
        "按校准概率升序：最不确定的排最前。低置信基本等于「模型犹豫」或「科目没见过」，"
        "需要人工确认；确认后的结论走 AI 复核流程进金标准，不再喂给程序学习。"
    )
    level = st.radio("范围", ["中+低置信", "仅低置信", "全部"], horizontal=True,
                     key="summary_review_level")
    keep = {"中+低置信": ["中", "低"], "仅低置信": ["低"]}.get(level)
    view = detail if keep is None else detail[detail["置信度"].isin(keep)]

    if view.empty:
        st.success("没有需要复核的凭证。")
        return

    cols = [c for c in ["凭证号", "业务分类", "置信度", "校准概率", "模型概率",
                        "模型Top3", "判定来源", "后处理", "未知科目", "摘要", "科目"]
            if c in view.columns]
    st.caption(f"{len(view):,} 张凭证待复核")
    st.dataframe(view[cols], use_container_width=True, hide_index=True)

    nn_path = stats.get("nn_training_path")
    if nn_path and Path(nn_path).exists():
        st.markdown("#### 未审训练数据")
        st.code(nn_path, language="text")
        st.download_button("下载未审训练数据 (JSON)",
                           data=Path(nn_path).read_bytes(),
                           file_name=Path(nn_path).name,
                           mime="application/json", key="dl_unreviewed")
