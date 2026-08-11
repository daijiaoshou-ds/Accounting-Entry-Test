# -*- coding: utf-8 -*-
"""
序时账清洗 — Streamlit 界面

基于 PMI 相关性矩阵 + 关键词偏置的业务自动分类
"""

import hashlib
import io
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from .classifier import JournalClassifier
from .config import (
    COLUMN_NAME_PATTERNS,
    BUCKET_CLARITY,
    load_buckets_json,
    load_subject_list,
)
from .memory_learner import TIER1_COUNT_THRESHOLD, DISCARD_SESSION_THRESHOLD
from .persistence import GlobalCounters

# ============================================================================
# Session State 初始化
# ============================================================================

SUMMARY_STATE_DEFAULTS = {
    "summary_raw_data": None,         # 上传的原始 DataFrame
    "summary_column_mapping": {},     # {key: actual_col_name}
    "summary_classified_df": None,    # 分类结果 DataFrame
    "summary_score_detail": None,     # 凭证级分数明细
    "summary_stats": None,            # 分类统计
    "summary_alpha": 0.2,             # 融合权重
    "summary_pmi_matrix": None,       # PMI 矩阵（用于热力图）
    "summary_keyword_hits": None,     # 关键词命中详情
    "summary_classifier": None,       # JournalClassifier 实例
    "summary_bookkeeper_mapping": {}, # 制单人→岗位映射 {"张三": "应收会计", ...}
    "summary_bookkeeper_col": "",     # 制单人列名
}


def _init_state():
    """初始化 session state。"""
    for key, default in SUMMARY_STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ============================================================================
# 主入口
# ============================================================================

def show_summary_cleaner():
    """序时账清洗的主界面入口。由 app.py 调用。"""
    _init_state()

    # ---- 侧边栏 ----
    with st.sidebar:
        st.markdown("## 🧹 序时账清洗")
        st.markdown("*PMI相关性矩阵 · 业务自动分类*")
        st.markdown("---")

        _render_upload_section()
        st.markdown("---")
        _render_controls()

    # ---- 主区域 ----
    st.title("🧹 序时账清洗")
    st.markdown("基于 **PMI科目相关性矩阵** + **关键词偏置** 的序时账业务自动分类系统")

    if st.session_state.summary_raw_data is not None:
        has_result = st.session_state.summary_classified_df is not None
        has_auto_words = st.session_state.get("summary_word_learner") is not None
        tab_names = ["📋 字段配置", "📊 分类概览", "📝 详细结果",
                      "🔗 PMI矩阵", "🔑 关键词命中"]
        if has_result:
            tab_names.append("✏️ 纠错")
        if has_auto_words:
            tab_names.append("🧠 自动词")
        tabs = st.tabs(tab_names)

        with tabs[0]:
            _render_field_config()

        with tabs[1]:
            _render_overview()

        with tabs[2]:
            _render_detailed_results()

        with tabs[3]:
            _render_pmi_matrix()

        with tabs[4]:
            _render_keyword_hits()

        if has_result:
            with tabs[5]:
                _render_correction_page()
        if has_auto_words:
            with tabs[-1]:
                _render_auto_words()
    else:
        st.info("👈 请在左侧上传序时账文件（Excel / CSV）开始分析")


# ============================================================================
# 侧边栏
# ============================================================================

def _compute_fingerprint(df: pd.DataFrame, voucher_col: str) -> str:
    """计算数据指纹：所有凭证ID排序后取 SHA256 前16位。"""
    ids = sorted(df[voucher_col].dropna().astype(str).unique())
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]


def _render_upload_section():
    """渲染文件上传区域。"""
    st.markdown("### 📁 上传数据")

    uploaded = st.file_uploader(
        "序时账文件",
        type=["xlsx", "xls", "csv"],
        key="summary_upload",
        help="支持 Excel (.xlsx/.xls) 和 CSV 格式",
    )

    if uploaded is not None:
        # 检查是否是新文件
        if "summary_last_file" not in st.session_state:
            st.session_state.summary_last_file = None

        if uploaded.name != st.session_state.summary_last_file:
            st.session_state.summary_last_file = uploaded.name
            try:
                if uploaded.name.endswith(".csv"):
                    df = pd.read_csv(uploaded)
                else:
                    df = pd.read_excel(uploaded, engine="openpyxl")
                st.session_state.summary_raw_data = df
                # 自动检测列名
                classifier = _get_classifier()
                st.session_state.summary_column_mapping = classifier.auto_detect_columns(df)
                st.success(f"✅ 已加载：{len(df)} 行 × {len(df.columns)} 列")
            except Exception as e:
                st.error(f"读取文件失败：{e}")
                st.session_state.summary_raw_data = None

    # 显示当前数据信息
    if st.session_state.summary_raw_data is not None:
        df = st.session_state.summary_raw_data
        st.markdown(f"**当前数据**: {len(df)} 行 × {len(df.columns)} 列")

        # 全局计数器状态
        counters = GlobalCounters()
        has_global = counters.load()
        if has_global and counters.N > 0:
            st.markdown(f"🌐 **通用矩阵**: {counters.N} 张历史凭证积累")
        else:
            st.markdown("🌐 **通用矩阵**: 尚无积累数据")


def _render_bookkeeper_role_mapping():
    """在字段配置下方渲染制单人→会计岗位映射（选填项）。"""
    df = st.session_state.summary_raw_data
    mapping = st.session_state.summary_column_mapping
    if df is None:
        return

    bookkeeper_col = mapping.get("bookkeeper", "")
    if not bookkeeper_col or bookkeeper_col not in df.columns:
        return

    # 提取唯一制单人
    all_bookkeepers = sorted(
        set(df[bookkeeper_col].dropna().astype(str).str.strip())
    )
    all_bookkeepers = [b for b in all_bookkeepers if b and b not in ("nan", "None", "NaT")]
    if not all_bookkeepers:
        return

    st.markdown("---")
    st.markdown("### 👤 制单人岗位配置")
    st.caption("模块会计 → 对应业务桶偏好加分（+0.5 偏好桶 / -0.1 其他模块桶）")

    # 计算指纹 → 加载历史映射
    v_col = mapping.get("voucher_no", "")
    fingerprint = ""
    existing_mapping = {}
    if v_col and v_col in df.columns:
        fingerprint = _compute_fingerprint(df, v_col)
        if fingerprint:
            existing_mapping = GlobalCounters().load_bookkeeper_mapping(fingerprint)

    # 首次加载（指纹变化）→ 用历史映射覆盖 session state
    bk_state_key = "summary_bk_fingerprint"
    if bk_state_key not in st.session_state:
        st.session_state[bk_state_key] = ""
    if fingerprint and fingerprint != st.session_state[bk_state_key]:
        st.session_state.summary_bookkeeper_mapping = dict(existing_mapping)
        st.session_state[bk_state_key] = fingerprint

    roles = ["无", "应收会计", "应付会计", "资产会计", "工资会计", "生产会计"]
    current_mapping = st.session_state.summary_bookkeeper_mapping

    # 紧凑排列：每行3个下拉框
    cols = st.columns(3)
    for i, bk in enumerate(all_bookkeepers):
        with cols[i % 3]:
            default_role = current_mapping.get(bk, "无")
            default_idx = roles.index(default_role) if default_role in roles else 0
            selected = st.selectbox(
                bk,
                roles,
                index=default_idx,
                key=f"summary_bk_role_{bk}",
            )
            if selected == "无":
                current_mapping.pop(bk, None)
            else:
                current_mapping[bk] = selected


def _render_controls():
    """渲染参数控件和操作按钮。"""
    st.markdown("### ⚙️ 参数设置")

    alpha = st.slider(
        "融合权重 α (专属R占比)",
        min_value=0.0,
        max_value=1.0,
        value=st.session_state.summary_alpha,
        step=0.05,
        help="α=0: 仅用通用矩阵 | α=1: 仅用公司矩阵 | 默认0.2",
        key="summary_alpha_slider",
    )
    st.session_state.summary_alpha = alpha

    st.markdown("---")

    # 分类按钮
    btn_disabled = st.session_state.summary_raw_data is None
    if st.button("🚀 开始分类", type="primary", use_container_width=True,
                 disabled=btn_disabled, key="summary_classify_btn"):
        _run_classification()

    # 全局计数器管理
    st.markdown("---")
    with st.expander("🗄️ 全局计数器管理"):
        counters = GlobalCounters()
        counters.load()
        if counters.N > 0:
            st.markdown(f"已积累 **{counters.N}** 张凭证")
            st.markdown(f"涵盖 **{counters.get_stats()['unique_subjects']}** 个科目")
        else:
            st.markdown("尚无积累数据")

        if st.button("🗑️ 重置全局计数器", use_container_width=True,
                     key="summary_reset_counters"):
            counters.delete_persisted()
            st.success("已重置全局计数器")
            st.rerun()


# ============================================================================
# Tab 1: 字段配置
# ============================================================================

def _render_field_config():
    """字段映射配置。"""
    st.markdown("### 字段映射配置")
    st.markdown("自动检测列名，如有错误可手动调整。标记 * 的为必填项。")

    mapping = st.session_state.summary_column_mapping
    df = st.session_state.summary_raw_data
    columns = list(df.columns)

    col1, col2, col3 = st.columns(3)

    with col1:
        _field_selector("voucher_no", "凭证号 *", columns, mapping,
                        ["凭证号", "凭证编号", "voucher"])
        _field_selector("subject_name", "科目名称", columns, mapping,
                        ["科目名称", "明细科目", "二级科目"])
        _field_selector("date", "制单日期", columns, mapping,
                        ["制单日期", "日期", "date"])

    with col2:
        _field_selector("summary", "摘要", columns, mapping,
                        ["摘要", "说明"])
        _field_selector("debit", "借方金额 *", columns, mapping,
                        ["借方金额", "借方", "debit"])
        # 制单人是选填项，默认"（无）"，用户需要时手动选择
        bk_options = ["（无）"] + columns
        bk_current = mapping.get("bookkeeper", "")
        bk_idx = bk_options.index(bk_current) if bk_current in bk_options else 0
        bk_selected = st.selectbox(
            "制单人", bk_options, index=bk_idx,
            key="summary_field_bookkeeper",
        )
        mapping["bookkeeper"] = "" if bk_selected == "（无）" else bk_selected

    with col3:
        _field_selector("subject", "一级科目 *", columns, mapping,
                        ["一级科目", "科目"])
        _field_selector("credit", "贷方金额 *", columns, mapping,
                        ["贷方金额", "贷方", "credit"])



    # 制单人岗位配置（选填项，配置了制单人列后出现）
    _render_bookkeeper_role_mapping()

    # 数据预览
    st.markdown("---")
    st.markdown("#### 数据预览")
    st.dataframe(df.head(10), use_container_width=True, hide_index=True)


def _field_selector(key: str, label: str, columns: list, mapping: dict,
                     patterns: list):
    """单个字段选择器。"""
    # 查找当前值
    current = mapping.get(key, "")
    if not current:
        # 按模式匹配
        for col in columns:
            col_str = str(col).strip()
            for pat in patterns:
                if pat in col_str:
                    current = col
                    break
            if current:
                break

    idx = columns.index(current) if current in columns else 0
    selected = st.selectbox(
        label, columns, index=idx,
        key=f"summary_field_{key}",
    )
    mapping[key] = selected


# ============================================================================
# Tab 2: 分类概览
# ============================================================================

def _render_overview():
    """分类结果概览。"""
    if st.session_state.summary_stats is None:
        st.info("请先在侧边栏点击「开始分类」")
        return

    stats = st.session_state.summary_stats

    # 指标卡片
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总凭证数", stats.get("total_vouchers", 0))
    with col2:
        st.metric("已分类", stats.get("classified_count", 0))
    with col3:
        st.metric("未分类", stats.get("unclassified_count", 0))
    with col4:
        st.metric("覆盖率", f"{stats.get('coverage', 0):.1%}")

    st.markdown("---")

    # 桶分布图
    bucket_counts = stats.get("bucket_counts", {})
    if bucket_counts:
        import plotly.express as px

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 各桶凭证数量")
            counts_df = pd.DataFrame({
                "业务桶": list(bucket_counts.keys()),
                "凭证数": list(bucket_counts.values()),
            }).sort_values("凭证数", ascending=True)
            fig = px.bar(counts_df, x="凭证数", y="业务桶", orientation="h",
                         title="凭证分布 by 业务桶",
                         color="凭证数", color_continuous_scale="Blues")
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.markdown("#### 各桶金额分布")
            amounts = stats.get("amount_by_bucket", {})
            if amounts:
                amt_df = pd.DataFrame({
                    "业务桶": list(amounts.keys()),
                    "金额": list(amounts.values()),
                }).sort_values("金额", ascending=True)
                fig = px.bar(amt_df, x="金额", y="业务桶", orientation="h",
                             title="金额分布 by 业务桶",
                             color="金额", color_continuous_scale="Greens")
                fig.update_layout(height=400)
                st.plotly_chart(fig, use_container_width=True)

    # 全局计数器状态
    st.markdown("---")
    st.markdown(f"🌐 全局计数器：已积累 **{stats.get('global_N', 0)}** 张凭证")
    st.markdown(f"🔗 公司R形状：{stats.get('company_R_shape', 'N/A')}  |  "
                f"融合R形状：{stats.get('final_R_shape', 'N/A')}")


# ============================================================================
# Tab 3: 详细结果
# ============================================================================

def _render_detailed_results():
    """详细分类结果表格。"""
    if st.session_state.summary_classified_df is None:
        st.info("请先在侧边栏点击「开始分类」")
        return

    df = st.session_state.summary_classified_df
    score_detail = st.session_state.summary_score_detail

    st.markdown("### 分类结果明细")

    # 筛选
    buckets_in_data = sorted(df["业务分类"].dropna().unique())
    selected_buckets = st.multiselect(
        "按业务桶筛选", buckets_in_data,
        default=buckets_in_data[:5] if len(buckets_in_data) > 5 else buckets_in_data,
        key="summary_filter_buckets",
    )

    # 搜索
    search_term = st.text_input("🔍 搜索摘要/凭证号", key="summary_search",
                                placeholder="输入关键词筛选...")

    # 过滤
    filtered = df.copy()
    if selected_buckets:
        filtered = filtered[filtered["业务分类"].isin(selected_buckets)]
    if search_term:
        mask = pd.Series(False, index=filtered.index)
        for col in filtered.columns:
            if filtered[col].dtype == "object":
                mask |= filtered[col].astype(str).str.contains(search_term, na=False)
        filtered = filtered[mask]

    st.markdown(f"显示 {len(filtered)} / {len(df)} 行")
    st.dataframe(filtered, use_container_width=True, hide_index=True)

    # 下载按钮
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col1:
        _download_excel(df, "分类结果", "分类结果.xlsx")
    with col2:
        if score_detail is not None and not score_detail.empty:
            _download_excel(score_detail, "分数明细", "得分明细.xlsx")

    # 凭证级分数明细
    if score_detail is not None and not score_detail.empty:
        st.markdown("---")
        st.markdown("### 凭证级分数明细")
        st.markdown("*每张凭证对各桶的最终得分（Score = λ_struct × v·w' + max(b,c) + s + d + e）*")
        st.dataframe(score_detail, use_container_width=True, hide_index=True)


def _render_correction_page():
    """纠错页 — Excel 导出 → 修改 → 上传回传。"""
    df = st.session_state.summary_classified_df
    if df is None:
        st.info("请先完成分类")
        return

    from .correction import CorrectionManager
    from .config import BUCKET_REGISTRY
    all_buckets = list(BUCKET_REGISTRY.keys())

    st.markdown("### ✏️ Excel 纠错")
    st.markdown("下载纠错表 → 在 Excel 中修改「纠错分类」列 → 上传回传 → 系统自动学习")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 📥 1. 下载纠错表")
        _export_correction_sheet(df, all_buckets)

    with col2:
        st.markdown("#### 📤 2. 上传纠错表")
        uploaded = st.file_uploader(
            "上传修改后的纠错表", type=["xlsx", "xls"],
            key="corr_upload",
            help="只比对「纠错分类」与「当前分类」不同的行"
        )
        if uploaded is not None:
            try:
                corrections = _import_correction_sheet(uploaded, df, all_buckets)
                if corrections:
                    mgr = CorrectionManager()
                    mgr.load()
                    mgr.record_corrections_batch(corrections)
                    st.success(f"✅ 已学习 {len(corrections)} 条纠错，涉及 {len(set(c['vid'] for c in corrections))} 张凭证")
                else:
                    st.info("未发现新的纠错（纠错列与当前分类一致）")
            except Exception as e:
                st.error(f"导入失败：{e}")

    _render_correction_history()


def _export_correction_sheet(df, all_buckets):
    """导出纠错工作表（精简列 + 纠错下拉验证）。"""
    import io

    # 优先用字段配置的映射，fallback 到 _find_col 猜测
    mapping = st.session_state.get("summary_column_mapping", {})
    v_col = mapping.get("voucher_no") or _find_col(df, ["凭证号", "凭证编号", "voucher_no", "vid"], "凭证")
    s_col = mapping.get("subject") or _find_col(df, ["一级科目", "subject"], "科目")
    sum_col = mapping.get("summary") or _find_col(df, ["摘要", "summary"], "摘要")
    d_col = mapping.get("debit") or _find_col(df, ["借方金额", "debit", "借方(本币)"], "", numeric_only=True)

    if not v_col or v_col not in df.columns:
        st.error(f"未找到凭证号列，请先在「字段配置」中设置。当前映射: {mapping}")
        return

    rows = []
    for vid, group in df.groupby(v_col):
        bucket = group["业务分类"].iloc[0] if "业务分类" in group.columns else ""
        summary = str(group[sum_col].iloc[0])[:100] if sum_col and sum_col in group.columns else ""
        subjects = "、".join(group[s_col].dropna().astype(str).unique()) if s_col and s_col in group.columns else ""
        amount = 0.0
        if d_col and d_col in group.columns:
            amt_series = group[d_col]
            if amt_series.dtype.kind in 'iuf':
                amount = float(amt_series.dropna().apply(abs).sum())
        rows.append({
            "凭证号": str(vid), "摘要": summary, "科目": subjects,
            "金额": round(amount, 2), "当前分类": bucket, "纠错分类": bucket,
        })

    export_df = pd.DataFrame(rows)
    st.markdown(f"共 **{len(export_df)}** 张凭证")

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="纠错表")
        ws = writer.sheets["纠错表"]
        from openpyxl.worksheet.datavalidation import DataValidation
        col_letter = chr(ord('A') + len(export_df.columns) - 1)
        dv = DataValidation(type="list", formula1='"' + ','.join(all_buckets) + '"', allow_blank=True)
        dv.error = "请选择有效的业务桶"
        dv_range = f"{col_letter}2:{col_letter}{len(export_df)+1}"
        ws.add_data_validation(dv)
        dv.add(dv_range)

    buffer.seek(0)
    st.download_button(
        label="📥 下载纠错表 (Excel)", data=buffer,
        file_name="纠错表.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="corr_download",
    )
    st.caption("💡 打开 Excel → 筛选出分类错误的凭证 → 修改「纠错分类」列 → 保存 → 上传回这里")


def _import_correction_sheet(uploaded_file, original_df, all_buckets):
    """解析纠错表，返回需要记录的新纠错列表。"""
    corr_df = pd.read_excel(uploaded_file, engine="openpyxl")
    for col in ["凭证号", "当前分类", "纠错分类"]:
        if col not in corr_df.columns:
            raise ValueError(f"缺少必要列：「{col}」")

    mapping = st.session_state.get("summary_column_mapping", {})
    orig_v_col = mapping.get("voucher_no") or _find_col(original_df, ["凭证号", "凭证编号", "voucher_no", "vid"], "凭证")
    orig_s_col = mapping.get("subject") or _find_col(original_df, ["一级科目", "subject"], "科目")
    orig_sum_col = mapping.get("summary") or _find_col(original_df, ["摘要", "summary"], "摘要")
    orig_sn_col = mapping.get("subject_name") or _find_col(original_df, ["科目名称", "科目明细", "subject_name"], "")
    orig_d_col = mapping.get("debit") or _find_col(original_df, ["借方金额", "debit", "借方(本币)"], "", numeric_only=True)

    if not orig_v_col or orig_v_col not in original_df.columns:
        raise ValueError(f"未找到凭证号列，请先在「字段配置」中设置。当前映射: {mapping}")

    corrections = []
    for _, row in corr_df.iterrows():
        vid = str(row["凭证号"])
        current = str(row["当前分类"]).strip()
        corrected = str(row["纠错分类"]).strip()
        if corrected == current or corrected not in all_buckets:
            continue

        voucher_rows = original_df[original_df[orig_v_col].astype(str) == vid] if orig_v_col else pd.DataFrame()
        summary = str(voucher_rows[orig_sum_col].iloc[0])[:120] if orig_sum_col and len(voucher_rows) > 0 else ""
        subjects = voucher_rows[orig_s_col].dropna().astype(str).tolist() if orig_s_col and len(voucher_rows) > 0 else []
        sub_details = voucher_rows[orig_sn_col].dropna().astype(str).tolist() if orig_sn_col and len(voucher_rows) > 0 else []
        amount = 0.0
        if orig_d_col and len(voucher_rows) > 0:
            s = voucher_rows[orig_d_col]
            if s.dtype.kind in 'iuf':
                amount = float(s.dropna().apply(abs).sum())

        corrections.append({
            "vid": vid, "original_bucket": current, "correct_bucket": corrected,
            "amount": amount, "summary": summary, "subjects": subjects, "subject_details": sub_details,
        })
    return corrections


def _find_col(df, candidates, fallback_hint, numeric_only=False):
    """在 DataFrame 中找列名。"""
    for c in candidates:
        if c in df.columns:
            if numeric_only and df[c].dtype.kind not in 'iuf':
                continue
            return c
    for c in df.columns:
        if fallback_hint and fallback_hint in str(c):
            if numeric_only and df[c].dtype.kind not in 'iuf':
                continue
            return c
    return ""


def _render_correction_history():
    """显示纠错历史。"""
    from .correction import CorrectionManager
    mgr = CorrectionManager()
    if mgr.load() and mgr.corrections:
        with st.expander(f"📜 纠错历史 ({len(mgr.corrections)} 条)", expanded=False):
            hist = pd.DataFrame(mgr.corrections)
            st.dataframe(hist, use_container_width=True, hide_index=True)


def _download_excel(df: pd.DataFrame, label: str, filename: str):
    """生成 Excel 下载按钮。"""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    buffer.seek(0)
    st.download_button(
        label=f"📥 下载{label} (Excel)",
        data=buffer, file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"summary_dl_{label}",
    )


# ============================================================================
# Tab 4: PMI 矩阵
# ============================================================================

def _render_pmi_matrix():
    """PMI 相关性矩阵可视化。"""
    st.markdown("### PMI 科目相关性矩阵")

    if st.session_state.summary_classified_df is None:
        st.info("请先在侧边栏点击「开始分类」以生成 PMI 矩阵")
        return

    from .persistence import GlobalCounters
    import plotly.express as px

    # 优先使用分类器缓存的融合矩阵，避免重复计算
    classifier = _get_classifier()
    display_R = classifier.get_final_R()

    if display_R is None or display_R.empty:
        st.warning("PMI 矩阵为空（凭证数据不足）")
        return

    # 显示矩阵来源说明
    counters = GlobalCounters()
    counters.load()
    alpha = st.session_state.summary_alpha
    df = st.session_state.summary_raw_data
    mapping = st.session_state.get("summary_column_mapping", {})
    v_col = mapping.get("voucher_no", "")
    if counters.N > 0:
        # 公司端显示凭证数，与通用的 N_global 量纲一致
        company_vouchers = df[v_col].nunique() if v_col and v_col in df.columns else len(df)
        st.markdown(f"显示 **融合矩阵** (α={alpha})：通用({counters.N}凭证) + 公司({company_vouchers}凭证)")
    else:
        st.markdown("显示 **公司专属矩阵**（尚无通用矩阵积累）")

    st.markdown(f"矩阵大小：{display_R.shape[0]} × {display_R.shape[1]} 科目")

    # 热力图
    if display_R.shape[0] > 1:
        # 过滤掉全为0的行和列（减小矩阵大小以提升可读性）
        non_zero_mask = (display_R.sum(axis=1) > 0.01)
        display_R_filtered = display_R.loc[non_zero_mask, non_zero_mask]

        if display_R_filtered.shape[0] > 1:
            fig = px.imshow(
                display_R_filtered,
                text_auto=".2f" if display_R_filtered.shape[0] <= 15 else False,
                color_continuous_scale="RdBu_r",
                title="PMI 相关性矩阵（值越大 = 科目绑定越强）",
                aspect="auto",
            )
            fig.update_layout(height=max(500, display_R_filtered.shape[0] * 30))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("没有显著相关的科目对")
    else:
        st.info("科目数量不足，无法绘制矩阵")

    # 显示显著相关的科目对
    st.markdown("---")
    st.markdown("#### 最强相关科目对 (Top 20)")
    pairs = []
    for i, si in enumerate(display_R.index):
        for j, sj in enumerate(display_R.columns):
            if i < j:
                val = display_R.at[si, sj]
                if val > 0:
                    pairs.append((si, sj, val))
    pairs.sort(key=lambda x: -x[2])
    if pairs:
        pairs_df = pd.DataFrame(pairs[:20], columns=["科目A", "科目B", "PMI值"])
        pairs_df["PMI值"] = pairs_df["PMI值"].round(4)
        st.dataframe(pairs_df, use_container_width=True, hide_index=True)
    else:
        st.info("没有显著相关的科目对")


# ============================================================================
# Tab 5: 关键词命中
# ============================================================================

def _render_keyword_hits():
    """关键词命中统计。"""
    st.markdown("### 关键词命中统计")

    if st.session_state.summary_classified_df is None:
        st.info("请先在侧边栏点击「开始分类」查看关键词命中情况")
        return

    df = st.session_state.summary_raw_data
    mapping = st.session_state.summary_column_mapping

    # 获取分类器并扫描所有凭证
    classifier = _get_classifier()
    matcher = classifier.keyword_matcher

    # 扫描每张凭证的关键词命中
    v_col = mapping.get("voucher_no", "")
    s_col = mapping.get("subject", "")
    sn_col = mapping.get("subject_name", "")
    sum_col = mapping.get("summary", "")

    if not v_col:
        st.warning("请先配置字段映射")
        return

    all_hits = {}
    bucket_hit_counts = {b: 0 for b in matcher.bucket_names}

    for vid, group in df.groupby(v_col):
        summary = str(group[sum_col].iloc[0]) if sum_col and sum_col in group.columns else ""
        subjects = group[s_col].dropna().astype(str).tolist() if s_col in group.columns else []
        sub_details = group[sn_col].dropna().astype(str).tolist() if sn_col and sn_col in group.columns else []

        detail = matcher.match_voucher_detail(summary, subjects, sub_details)
        if detail:
            all_hits[str(vid)] = detail
            for bucket_name in detail:
                if bucket_name in bucket_hit_counts:
                    bucket_hit_counts[bucket_name] += 1

    # 关键词分布图
    if bucket_hit_counts:
        import plotly.express as px

        hits_df = pd.DataFrame({
            "业务桶": list(bucket_hit_counts.keys()),
            "命中凭证数": list(bucket_hit_counts.values()),
        }).sort_values("命中凭证数", ascending=True)

        fig = px.bar(hits_df, x="命中凭证数", y="业务桶", orientation="h",
                     title="各桶关键词命中凭证数",
                     color="命中凭证数", color_continuous_scale="Oranges")
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

    # 关键词详细命中
    st.markdown("---")
    st.markdown("#### 关键词命中明细")
    st.markdown(f"共 {len(all_hits)} 张凭证有至少一个关键词命中")

    # 每个桶显示其被命中的关键词及其频次
    all_keyword_freq = {}
    for vid, buckets_hits in all_hits.items():
        for bucket, keywords in buckets_hits.items():
            if bucket not in all_keyword_freq:
                all_keyword_freq[bucket] = {}
            for kw in keywords:
                all_keyword_freq[bucket][kw] = all_keyword_freq[bucket].get(kw, 0) + 1

    for bucket_name in matcher.bucket_names:
        if bucket_name in all_keyword_freq:
            with st.expander(f"📌 {bucket_name} — {len(all_keyword_freq[bucket_name])} 个关键词"):
                kw_freq = all_keyword_freq[bucket_name]
                kw_sorted = sorted(kw_freq.items(), key=lambda x: -x[1])
                st.markdown("、".join(
                    f"`{kw}`({cnt})" for kw, cnt in kw_sorted[:30]
                ))
                if len(kw_sorted) > 30:
                    st.markdown(f"... 及其他 {len(kw_sorted) - 30} 个")


# ============================================================================
# 分类执行
# ============================================================================

def _run_classification():
    """执行分类流程。"""
    df = st.session_state.summary_raw_data
    mapping = st.session_state.summary_column_mapping

    # 验证
    classifier = _get_classifier()
    ok, missing = classifier.validate_column_mapping(mapping)
    if not ok:
        st.error(f"缺少必填字段映射：{', '.join(missing)}")
        return

    alpha = st.session_state.summary_alpha

    progress_bar = st.progress(0, text="准备中...")
    status = st.empty()

    try:
        status.info("Step 1/5: 构建公司 PMI 矩阵...")
        progress_bar.progress(10)

        status.info("Step 2/5: 加载通用矩阵并融合...")
        progress_bar.progress(25)

        status.info("Step 3/5: 相关性传播 w' = w × R ...")
        progress_bar.progress(45)

        status.info("Step 4/5: 逐凭证分类 (向量化 + 关键词匹配 + 评分) ...")
        progress_bar.progress(65)

        # 制单人映射：写入 storage（同哈希下次自动回填）
        bookkeeper_col = mapping.get("bookkeeper", "")
        bookkeeper_mapping = st.session_state.get("summary_bookkeeper_mapping", {})
        if bookkeeper_col and bookkeeper_mapping:
            # 计算指纹并保存
            v_col = mapping.get("voucher_no", "")
            if v_col and v_col in df.columns:
                fingerprint = _compute_fingerprint(df, v_col)
                if fingerprint:
                    GlobalCounters().save_bookkeeper_mapping(
                        fingerprint, bookkeeper_mapping, bookkeeper_col
                    )

        classified_df, score_detail, stats = classifier.classify(
            df, mapping, alpha,
            bookkeeper_col=bookkeeper_col if bookkeeper_col and bookkeeper_col in df.columns else None,
            bookkeeper_mapping=bookkeeper_mapping if bookkeeper_mapping else None,
        )

        progress_bar.progress(90)
        status.info("Step 5/5: 更新全局计数器...")

        # 存储结果
        st.session_state.summary_classified_df = classified_df
        st.session_state.summary_score_detail = score_detail
        st.session_state.summary_stats = stats
        # 存储 word_learner 供自动词 Tab 使用
        if classifier.word_learner is not None:
            st.session_state.summary_word_learner = classifier.word_learner

        progress_bar.progress(100, text="✅ 分类完成！")
        time.sleep(0.5)
        progress_bar.empty()
        status.empty()

        # 显示摘要
        st.success(
            f"✅ 分类完成！"
            f"共 {stats['total_vouchers']} 张凭证，"
            f"命中 {stats['classified_count']} 张，"
            f"覆盖率 {stats['coverage']:.1%}"
        )
        # NN 融合模式提示（左脚踩右脚）
        fusion = stats.get("nn_fusion", {})
        if fusion:
            detail = ""
            if fusion.get("fused") is not None:
                detail = (f" ｜ 模型融合 {fusion['fused']} 张, "
                          f"未知科目退避 {fusion['backed_off']} 张")
            st.caption(f"🧠 分类模式: {fusion['mode']}{detail}")

    except Exception as e:
        progress_bar.empty()
        status.empty()
        st.error(f"分类失败：{e}")
        import traceback
        st.code(traceback.format_exc())


def _get_classifier() -> JournalClassifier:
    """获取或创建 JournalClassifier 实例。"""
    if st.session_state.summary_classifier is None:
        subjects = load_subject_list()
        st.session_state.summary_classifier = JournalClassifier(
            subject_list=subjects,
        )
    return st.session_state.summary_classifier


# ============================================================================
# 自动词 Tab
# ============================================================================

@st.fragment
def _render_auto_words():
    """渲染自动词三层存储 Tab — 桶切换 + 紧凑表格布局。

    用 @st.fragment 隔离：multiselect 勾选和删除按钮只重跑本区域，
    不会触发整个页面（PMI 矩阵等重型计算）重新执行。
    """
    wl = st.session_state.get("summary_word_learner")
    if wl is None:
        st.info("暂无自动词数据。请先运行分类。")
        return

    tier1 = wl.get_tier1_words()
    tier2 = wl.get_tier2_words()
    trash = wl.get_trash_bin()
    deleted = wl.get_deleted_words()

    st.markdown("### 🧠 自动词特征")
    st.caption("程序从摘要中自动发现的强特征词，按词频 + PMI 排序")

    # ── 统计卡片 ──
    t1_total = sum(len(words) for words in tier1.values())
    t2_total = sum(len(words) for words in tier2.values())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("⭐ Tier 1 高频词", t1_total)
    c2.metric("🌱 Tier 2 低频词", t2_total)
    c3.metric("🗑️ Tier 3 垃圾桶", len(trash))
    c4.metric("🚫 已删除", len(deleted))

    st.markdown("---")

    # ── 桶选择器 ──
    all_buckets = sorted(set(list(tier1.keys()) + list(tier2.keys())))
    if not all_buckets:
        st.info("暂无自动词。Tier 1 和 Tier 2 都为空。")
        _render_tier3_section(trash, deleted)
        return

    selected_bucket = st.selectbox(
        "选择业务桶查看自动词",
        all_buckets,
        format_func=lambda x: f"📌 {x}",
        key="auto_words_bucket_selector",
    )

    # ── Tier 1 表格 ──
    t1_words = tier1.get(selected_bucket, {})
    t2_words = tier2.get(selected_bucket, {})

    st.markdown(f"#### ⭐ Tier 1 高频词 — {selected_bucket}")
    if t1_words:
        _render_word_table(wl, selected_bucket, t1_words, "t1")
    else:
        st.caption("该桶暂无 Tier 1 高频词（需 count ≥ 5）")

    # ── Tier 2 表格 ──
    if t2_words:
        st.markdown(f"#### 🌱 Tier 2 低频词 — {selected_bucket}")
        st.caption("累积中，需更多数据验证")
        _render_word_table(wl, selected_bucket, t2_words, "t2")

    # ── 全局视角（折叠）──
    st.markdown("---")
    _render_tier3_section(trash, deleted)


def _render_word_table(wl, bucket: str, words: dict, tier_label: str):
    """渲染单桶自动词表格 + 多选删除。"""
    sorted_words = sorted(words.items(), key=lambda x: -x[1].get("auto_score", 0))

    # 构建 DataFrame
    rows = []
    for word, info in sorted_words:
        rows.append({
            "词": word,
            "Score": f"{info['auto_score']:.3f}",
            "PMI": f"{info['pmi']:.2f}",
            "次数": info["count"],
            "Session": len(info.get("sessions", [])),
        })

    df = pd.DataFrame(rows)

    # 紧凑表格
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=min(len(rows) * 36 + 40, 400),
        column_config={
            "词": st.column_config.TextColumn("词", width="medium"),
            "Score": st.column_config.TextColumn("Score", width="small"),
            "PMI": st.column_config.TextColumn("PMI", width="small"),
            "次数": st.column_config.NumberColumn("次数", width="small"),
            "Session": st.column_config.NumberColumn("Session", width="small"),
        },
    )

    # 多选删除
    word_opts = [f"{w}  (score={info['auto_score']:.2f}, x{info['count']})"
                 for w, info in sorted_words]
    word_keys = [w for w, _ in sorted_words]
    opt_to_key = dict(zip(word_opts, word_keys))

    selected = st.multiselect(
        f"勾选要删除的词（{tier_label}）",
        word_opts,
        key=f"del_sel_{tier_label}_{bucket}",
        placeholder="勾选后点击下方按钮删除...",
    )

    if selected:
        if st.button(f"🗑️ 删除选中的 {len(selected)} 个词", key=f"del_btn_{tier_label}_{bucket}"):
            for opt in selected:
                w = opt_to_key[opt]
                wl.delete_word(w, bucket)
            _persist_word_learner(wl)
            st.rerun()


def _render_tier3_section(trash: list, deleted: list):
    """渲染垃圾桶 + 已删除（底部折叠区）。"""
    c1, c2 = st.columns(2)

    with c1:
        with st.expander(f"🗑️ Tier 3 垃圾桶（{len(trash)} 条，仅日志）"):
            st.caption(f"跨 ≥{DISCARD_SESSION_THRESHOLD} session 仍低频，自动丢弃")
            if trash:
                trash_rows = [{
                    "词": t["word"], "桶": t["bucket"],
                    "次数": t["count"], "sessions": t.get("sessions", "?"),
                    "丢弃时间": t.get("discarded_at", ""),
                } for t in trash]
                st.dataframe(trash_rows, use_container_width=True, hide_index=True, height=min(len(trash_rows) * 36 + 40, 300))
            else:
                st.caption("暂无")

    with c2:
        with st.expander(f"🚫 已删除词（{len(deleted)} 条）"):
            if deleted:
                for item in deleted:
                    st.markdown(f"- `{item}`")
            else:
                st.caption("暂无")


def _persist_word_learner(wl):
    """将 word_learner 的删除/分层状态持久化。

    注意：不调用 load() —— 直接用内存中的 global_counters 状态，
    避免 load() 覆盖掉 classify() 刚写入的新格式数据。
    """
    classifier = _get_classifier()
    gc = classifier.global_counters
    gc.auto_scores_tier1 = wl._auto_scores_tier1
    gc.auto_scores_tier2 = wl._auto_scores_tier2
    gc.auto_scores_tier3 = wl._trash_bin
    gc.auto_scores_deleted = sorted(wl._deleted_words)
    gc.save()
