"""
序时账与银行流水核对工具（银行流水匹配模块）

两种用法：
1. 集成模式：作为「会计分析工具箱」的第四大功能，由根目录 app.py 调用本模块的 show_bank_matcher()
2. 独立模式：直接 `streamlit run app.py` 启动本工具（自动补齐项目根目录到 sys.path）
"""
import re
import sys
import tempfile
import os
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

# 独立运行时，确保项目根目录在 sys.path 上（便于从根目录 import bank_statement_matcher.src.*）
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from bank_statement_matcher.src.core.matcher import ReconciliationEngine, export_results, MATCH_TYPE_MAP
from bank_statement_matcher.src.core.cleaner import clean_journal, split_by_detail_account

# ============== 中英文字段对照 ==============
FIELD_LABELS = {
    "entry_date": "记账日期",
    "voucher_no": "凭证号",
    "abstract": "摘要",
    "level1_account": "一级科目",
    "detail_account": "明细科目",
    "debit": "借方金额",
    "credit": "贷方金额",
    "customer_name": "客商名称（可选）",
    "tx_date": "交易日期",
    "counter_party": "交易方",
    "income": "收入金额",
    "expense": "支出金额",
    "serial_no": "流水号（可选）",
}

# 必填字段
JOURNAL_REQUIRED = ["entry_date", "voucher_no", "abstract", "level1_account",
                    "detail_account", "debit", "credit"]
BANK_REQUIRED = ["tx_date", "counter_party", "income", "expense", "abstract"]

# 可选字段（也展示出来让用户配置）
JOURNAL_OPTIONAL = ["customer_name"]
BANK_OPTIONAL = ["serial_no"]


def safe_df_for_display(df):
    """把 DataFrame 中可能导致 PyArrow 报错的列转为 string"""
    df_safe = df.copy()
    for col in df_safe.columns:
        if df_safe[col].dtype == 'object':
            df_safe[col] = df_safe[col].astype(str).replace('nan', '')
    return df_safe


@st.cache_data
def load_journal_data(journal_bytes):
    return pd.read_excel(BytesIO(journal_bytes))


@st.cache_data
def load_bank_data(file_signature, bank_bytes_list):
    # file_signature 用于确保换文件时 cache 失效
    result = {}
    for name, b in bank_bytes_list:
        result[name] = pd.read_excel(BytesIO(b))
    return result


def _smart_pair(account_name: str, file_names: list) -> str:
    """根据科目名称和银行流水文件名智能配对
    策略：先匹配数字，再匹配中文关键字
    """
    # 提取科目中的数字
    acc_nums = re.findall(r"\d+", account_name)
    # 提取科目中的中文关键字
    acc_keywords = re.findall(r"[\u4e00-\u9fa5]+", account_name)

    best_match = None
    best_score = 0

    for fname in file_names:
        score = 0

        # 1. 数字匹配（高权重）
        file_nums = re.findall(r"\d+", fname)
        if acc_nums and file_nums:
            common = set(acc_nums) & set(file_nums)
            if common:
                score += 100 * len(common)

        # 2. 中文关键字匹配
        file_keywords = re.findall(r"[\u4e00-\u9fa5]+", fname)
        for kw in acc_keywords:
            for fkw in file_keywords:
                if kw in fkw or fkw in kw:
                    score += 50
                # 更宽松的匹配：比如"招行"匹配"招商"
                elif len(kw) >= 2 and len(fkw) >= 2:
                    if kw[:2] == fkw[:2] or kw[-2:] == fkw[-2:]:
                        score += 30

        if score > best_score:
            best_score = score
            best_match = fname

    return best_match


def show_bank_matcher():
    """银行流水匹配：序时账与银行流水的自动核对页面"""
    st.title("🏦 序时账与银行流水核对工具")
    st.markdown("---")

    # ============== 侧边栏 ==============
    with st.sidebar:
        st.header("📁 数据上传")

        journal_file = st.file_uploader("上传序时账 (Excel)", type=["xlsx", "xls"])
        bank_files = st.file_uploader(
            "上传银行流水 (Excel，支持多份)",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
        )

        st.markdown("---")
        st.header("⚙️ 核对参数")

        tol = st.number_input(
            "金额容差（元）",
            min_value=0.0,
            max_value=10.0,
            value=0.001,
            step=0.001,
            help="浮点数精度容差，建议 0.001",
        )

        date_window = st.number_input(
            "日期容差天数",
            min_value=1,
            max_value=365,
            value=31,
            step=1,
            help="GL 记账日期与 Bank 交易日期的最大允许差距（默认 31 天/一个月）。超出此范围的流水不会互相匹配。",
        )

        dynamic_greedy = st.checkbox(
            "动态贪心排序",
            value=True,
            help="勾选时：每轮按候选池从小到大排序处理（简单GL优先，快但有抢占风险）。\n"
                 "取消时：按GL原始顺序处理（慢但更稳定，避免简单GL抢占Bank导致复杂GL无解）。",
        )

        st.markdown("---")
        st.info("💡 提示：先上传文件，确认字段映射无误后再运行核对")

    # ============== 主界面 ==============
    if journal_file is None or not bank_files:
        st.info("👈 请在左侧上传序时账和至少一份银行流水")
        st.stop()

    # 读取数据
    bank_bytes_list = [(f.name, f.getvalue()) for f in bank_files]
    bank_sig = "_".join(f"{name}_{len(b)}" for name, b in bank_bytes_list)

    df_journal = load_journal_data(journal_file.getvalue())
    bank_dfs = load_bank_data(bank_sig, bank_bytes_list)

    st.subheader("📖 序时账预览")
    st.dataframe(safe_df_for_display(df_journal.head(10)), use_container_width=True)

    st.subheader("🏦 银行流水预览")
    for name, df in bank_dfs.items():
        with st.expander(f"**{name}** — {len(df)} 行"):
            st.dataframe(safe_df_for_display(df.head(10)), use_container_width=True)

    # ============== 字段智能识别（软识别）==============
    file_signature = f"{journal_file.name}_{bank_sig}_{len(df_journal)}"

    if "bank_engine_sig" not in st.session_state or st.session_state.bank_engine_sig != file_signature:
        engine = ReconciliationEngine(tol=tol, date_window_days=date_window,
                                      dynamic_greedy=dynamic_greedy)
        journal_missing = engine.load_journal(df_journal)

        # 为每份银行流水单独做字段识别
        bank_missing = {}
        for name, df in bank_dfs.items():
            missing = engine.load_bank(name, df)
            if missing:
                bank_missing[name] = missing

        st.session_state.bank_engine = engine
        st.session_state.bank_engine_sig = file_signature
        st.session_state.bank_journal_missing = journal_missing
        st.session_state.bank_missing = bank_missing
    else:
        engine = st.session_state.bank_engine
        journal_missing = st.session_state.bank_journal_missing
        bank_missing = st.session_state.bank_missing
        engine.tol = tol
        engine.date_window_days = date_window
        engine.dynamic_greedy = dynamic_greedy

    # ============== 字段映射确认 ==============
    st.markdown("---")
    st.subheader("🔍 字段映射确认")

    if journal_missing:
        st.warning(f"⚠️ 序时账以下字段未自动识别，请手动选择：{[FIELD_LABELS.get(f, f) for f in journal_missing]}")
    if bank_missing:
        for fname, missing in bank_missing.items():
            st.warning(f"⚠️ 银行流水 [{fname}] 以下字段未自动识别，请手动选择：{[FIELD_LABELS.get(f, f) for f in missing]}")

    # --- 序时账字段 ---
    st.markdown("**📖 序时账字段**")
    journal_cols = list(df_journal.columns)
    for field in JOURNAL_REQUIRED:
        current_col = engine.field_mapper.get_journal_col(field)
        options = ["（未选择）"] + journal_cols
        default_index = options.index(current_col) if current_col and current_col in journal_cols else 0

        label = f"{FIELD_LABELS.get(field, field)} {'🔴' if field in journal_missing else '✅'}"
        selected = st.selectbox(label, options=options, index=default_index, key=f"bank_journal_{field}")
        if selected and selected != "（未选择）":
            engine.field_mapper.set_journal_field(field, selected)
        elif selected == "（未选择）":
            if field in engine.field_mapper.journal_map:
                del engine.field_mapper.journal_map[field]

    for field in JOURNAL_OPTIONAL:
        current_col = engine.field_mapper.get_journal_col(field)
        options = ["（未选择）"] + journal_cols
        default_index = options.index(current_col) if current_col and current_col in journal_cols else 0
        label = f"{FIELD_LABELS.get(field, field)} {'⚪' if not current_col else '✅'}"
        selected = st.selectbox(label, options=options, index=default_index, key=f"bank_journal_opt_{field}")
        if selected and selected != "（未选择）":
            engine.field_mapper.set_journal_field(field, selected)
        elif selected == "（未选择）":
            if field in engine.field_mapper.journal_map:
                del engine.field_mapper.journal_map[field]

    st.markdown("---")

    # --- 银行流水字段（表格式配置，一行一个文件）---
    st.markdown("**🏦 银行流水字段配置**")

    # 表头
    _header_cols = st.columns([2.5, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3])
    with _header_cols[0]:
        st.caption("文件名")
    with _header_cols[1]:
        st.caption("交易日期")
    with _header_cols[2]:
        st.caption("交易方")
    with _header_cols[3]:
        st.caption("收入金额")
    with _header_cols[4]:
        st.caption("支出金额")
    with _header_cols[5]:
        st.caption("摘要")
    with _header_cols[6]:
        st.caption("流水号")

    BANK_FIELDS_ORDER = ["tx_date", "counter_party", "income", "expense", "abstract", "serial_no"]

    for fname, df in bank_dfs.items():
        bank_cols = list(df.columns)
        has_missing = fname in bank_missing and bool(bank_missing[fname])

        row_cols = st.columns([2.5, 1.3, 1.3, 1.3, 1.3, 1.3, 1.3])

        with row_cols[0]:
            st.markdown(f"{'🔴 ' if has_missing else ''}`{fname}`")

        for idx, field in enumerate(BANK_FIELDS_ORDER):
            with row_cols[idx + 1]:
                current_col = engine.field_mapper.get_bank_col(fname, field)
                options = ["—"] + bank_cols
                default_index = options.index(current_col) if current_col and current_col in bank_cols else 0

                sel = st.selectbox(
                    f"bank_{fname}_{field}",
                    options=options,
                    index=default_index,
                    label_visibility="collapsed",
                )
                if sel and sel != "—":
                    engine.field_mapper.set_bank_field(fname, field, sel)
                elif sel == "—":
                    if field in engine.field_mapper.bank_maps.get(fname, {}):
                        del engine.field_mapper.bank_maps[fname][field]

    live_journal_missing = engine.field_mapper.validate_journal()
    live_bank_missing_all = engine.field_mapper.validate_all_banks()

    if live_journal_missing or live_bank_missing_all:
        st.info("👆 请补全上方标 🔴 的字段，补全后即可开始核对")
        st.stop()
    else:
        st.success("🎉 所有必填字段已配置完毕，可以开始核对了！")

    # ============== 科目明细-银行流水配对表 ==============
    st.markdown("---")
    st.subheader("🔗 科目明细-银行流水配对")

    # 提前清洗序时账，获取明细科目列表
    _temp_journal, _ = clean_journal(df_journal, engine.field_mapper.journal_map)
    _temp_by_account = split_by_detail_account(_temp_journal)
    all_accounts = sorted(list(_temp_by_account.keys()))

    if len(all_accounts) == 0:
        st.warning("序时账中没有找到银行存款明细科目，请检查「一级科目」字段映射是否正确")
        st.stop()

    st.info(f"检测到 {len(all_accounts)} 个银行存款明细科目，已上传 {len(bank_files)} 份银行流水")

    # 配对表：每个科目配对一个银行流水文件
    bank_file_names = list(bank_dfs.keys())
    pairings = {}

    col_header1, col_header2 = st.columns([3, 3])
    with col_header1:
        st.markdown("**序时账明细科目**")
    with col_header2:
        st.markdown("**对应的银行流水**")

    for acc in all_accounts:
        c1, c2 = st.columns([3, 3])
        with c1:
            st.text(acc)
        with c2:
            # 智能匹配默认值
            smart_default = _smart_pair(acc, bank_file_names)
            options = ["（未选择）"] + bank_file_names
            default_index = options.index(smart_default) if smart_default in options else 0

            selected = st.selectbox(
                f"配对_{acc}",
                options=options,
                index=default_index,
                label_visibility="collapsed",
            )
            if selected and selected != "（未选择）":
                pairings[acc] = [selected]

    if not pairings:
        st.warning("👆 请至少配对一个明细科目与银行流水")
        st.stop()

    st.success(f"已配对 {len(pairings)} 个科目：{', '.join(pairings.keys())}")

    # ============== 运行核对 ==============
    st.markdown("---")
    st.subheader("🚀 运行核对")

    run_btn = st.button("▶️ 开始核对", type="primary", use_container_width=True)

    if run_btn:
        ready_errors = engine.check_ready(list(bank_dfs.keys()))
        if ready_errors:
            for err in ready_errors:
                st.error(f"❌ {err}")
            st.info("👆 请在上方字段映射区域补全缺失字段")
            st.stop()

        progress_bar = st.progress(0, text="正在核对，请稍候...")

        all_results = {}
        all_monthly_summaries = []
        all_diagnostics = {
            "journal_total": 0,
            "journal_errors": 0,
            "bank_total": 0,
            "bank_errors": 0,
            "journal_by_account": {},
            "bank_monthly": [],
            "matching_process": [],
        }

        try:
            for idx, (acc, bank_file_list) in enumerate(pairings.items()):
                progress_pct = int(10 + 80 * idx / len(pairings))
                progress_bar.progress(progress_pct, text=f"正在核对：{acc}...")

                # 临时设置只使用该科目配对的银行文件
                engine.bank_dfs = {f: bank_dfs[f] for f in bank_file_list}

                results, monthly_summary, diagnostics = engine.run_with_summary(detail_account=acc)
                all_results.update(results)
                if not monthly_summary.empty:
                    all_monthly_summaries.append(monthly_summary)
                # 合并 diagnostics
                for k in ["journal_total", "journal_errors", "bank_total", "bank_errors"]:
                    all_diagnostics[k] = max(all_diagnostics[k], diagnostics[k])
                all_diagnostics["journal_by_account"].update(diagnostics["journal_by_account"])
                all_diagnostics["bank_monthly"].extend(diagnostics["bank_monthly"])
                all_diagnostics["matching_process"].extend(diagnostics["matching_process"])

            progress_bar.progress(100, text="核对完成！")

            # 合并月度汇总
            if all_monthly_summaries:
                combined_monthly = pd.concat(all_monthly_summaries, ignore_index=True)
            else:
                combined_monthly = pd.DataFrame()

            st.session_state["bank_results"] = all_results
            st.session_state["bank_monthly_summary"] = combined_monthly
            st.session_state["bank_diagnostics"] = all_diagnostics
            st.success(f"✅ 核对完成！已核对 {len(pairings)} 个科目")
        except Exception as e:
            progress_bar.empty()
            st.error(f"❌ 核对失败: {e}")
            import traceback
            st.code(traceback.format_exc())
            st.stop()

    # ============== 诊断面板 ==============
    if "bank_diagnostics" in st.session_state:
        diag = st.session_state["bank_diagnostics"]

        st.markdown("---")
        st.subheader("🔬 数据诊断")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("序时账清洗成功", diag["journal_total"])
        c2.metric("序时账清洗失败", diag["journal_errors"])
        c3.metric("银行流水清洗成功", diag["bank_total"])
        c4.metric("银行流水清洗失败", diag["bank_errors"])

        # 显示对方科目分析结果
        with st.expander("📋 对方科目分析结果"):
            cpa = diag.get("counterparty_analysis", {})
            if cpa:
                st.markdown(f"对方科目分析统计（按凭证号分组）：")
                col_a1, col_a2, col_a3, col_a4 = st.columns(4)
                col_a1.metric("简单拆分", cpa.get("simple_count", 0),
                             help="非银行存款只有一个科目 → 按金额拆分，一一对应客商")
                col_a2.metric("多银行账户", cpa.get("multi_bank_count", 0),
                             help="有多个银行明细账户 → 金额对应或聚合")
                col_a3.metric("复杂聚合", cpa.get("complex_count", 0),
                             help="非银行存款有多个科目 → 客商全部聚合")
                col_a4.metric("无对方科目", cpa.get("no_counterparty_count", 0),
                             help="凭证只有银行存款行，无对方科目")
            else:
                st.info("无对方科目分析数据（可能在清洗前未执行分析）")

        # 显示检测到的明细科目列表
        with st.expander("📋 检测到的明细科目列表"):
            all_detected_accounts = list(diag["journal_by_account"].keys())
            if all_detected_accounts:
                st.write(f"共检测到 {len(all_detected_accounts)} 个明细科目：")
                for acc in all_detected_accounts:
                    st.markdown(f"- `{acc}`")
            else:
                st.warning("未检测到任何明细科目，请检查「明细科目」字段映射是否正确")

        # 显示序时账按科目的月度分布
        with st.expander("📋 序时账各明细科目月度分布"):
            for acc, month_data in diag["journal_by_account"].items():
                st.markdown(f"**{acc}**")
                if month_data:
                    df_disp = pd.DataFrame(month_data)
                    df_disp["年月"] = df_disp.apply(lambda r: f"{r['year']}-{r['month']:02d}", axis=1)
                    df_disp["收支"] = df_disp["direction"].map({"income": "收入", "expense": "支出"})
                    df_disp = df_disp[["年月", "收支", "count", "sum"]].rename(columns={"count": "笔数", "sum": "金额合计"})
                    st.dataframe(df_disp, use_container_width=True)
                else:
                    st.info("该科目无月度数据（可能所有记录的金额均为0或方向无法识别）")

        # 显示银行流水月度分布
        with st.expander("📋 银行流水月度分布"):
            if diag["bank_monthly"]:
                df_bank = pd.DataFrame(diag["bank_monthly"])
                df_bank["年月"] = df_bank.apply(lambda r: f"{r['year']}-{r['month']:02d}", axis=1)
                df_bank["收支"] = df_bank["direction"].map({"income": "收入", "expense": "支出"})
                df_bank = df_bank[["年月", "收支", "count", "sum"]].rename(columns={"count": "笔数", "sum": "金额合计"})
                st.dataframe(df_bank, use_container_width=True)
            else:
                st.info("无银行流水数据")

        # 显示核对过程详情
        with st.expander("📋 核对过程详情（按科目/月份）"):
            for proc in diag["matching_process"]:
                acc = proc["account"]
                st.markdown(f"**{acc}**")
                if proc["months"]:
                    df_months = pd.DataFrame(proc["months"])
                    df_months["年月"] = df_months.apply(lambda r: f"{r['year']}-{r['month']:02d}", axis=1)
                    df_months["收支"] = df_months["direction"].map({"income": "收入", "expense": "支出"})
                    df_months = df_months[["年月", "收支", "gl_count", "bank_count", "gl_sum", "bank_sum", "matches"]]
                    df_months = df_months.rename(columns={
                        "gl_count": "GL笔数", "bank_count": "Bank笔数",
                        "gl_sum": "GL金额", "bank_sum": "Bank金额",
                        "matches": "匹配数",
                    })
                    st.dataframe(df_months, use_container_width=True)

                else:
                    st.info("无数据")

    # ============== 月度总体差异 ==============
    if "bank_monthly_summary" in st.session_state:
        st.markdown("---")
        st.subheader("📊 月度总体差异")

        df_summary = st.session_state["bank_monthly_summary"]
        if not df_summary.empty:
            st.dataframe(df_summary, use_container_width=True)

            # 按科目分组展示（折叠）
            with st.expander("📋 按科目展开查看收入/支出明细"):
                for acc in df_summary["明细科目"].unique():
                    sub = df_summary[df_summary["明细科目"] == acc]
                    st.markdown(f"**{acc}**")

                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("*收入*")
                        income_df = sub[sub["收支"] == "收入"][["年份", "月份", "序时账金额", "银行流水金额", "差异"]]
                        if not income_df.empty:
                            st.dataframe(income_df, use_container_width=True)
                        else:
                            st.info("无收入数据")

                    with col2:
                        st.markdown("*支出*")
                        expense_df = sub[sub["收支"] == "支出"][["年份", "月份", "序时账金额", "银行流水金额", "差异"]]
                        if not expense_df.empty:
                            st.dataframe(expense_df, use_container_width=True)
                        else:
                            st.info("无支出数据")
        else:
            st.info("暂无月度汇总数据")

    # ============== 结果展示 ==============
    if "bank_results" in st.session_state:
        results = st.session_state["bank_results"]

        st.markdown("---")
        st.subheader("📊 核对结果")

        total_matches = sum(r.summary["total_matches"] for r in results.values())
        total_unmatched_gl = sum(r.summary["total_unmatched_gl"] for r in results.values())
        total_unmatched_bank = sum(r.summary["total_unmatched_bank"] for r in results.values())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("明细科目数", len(results))
        c2.metric("✅ 匹配成功", total_matches)
        c3.metric("❌ 未匹配序时账", total_unmatched_gl)
        c4.metric("❌ 未匹配银行流水", total_unmatched_bank)

        account_options = list(results.keys())
        selected_account = st.selectbox("选择明细科目查看", ["全部"] + account_options)

        all_matches = []
        all_unmatched_gl = []
        all_unmatched_bank = []

        for acc, res in results.items():
            if selected_account != "全部" and acc != selected_account:
                continue

            shown_bank_ids = set()
            for gl, banks, mtype in res.matches:
                mtype_cn = MATCH_TYPE_MAP.get(mtype, mtype)
                if len(banks) == 1:
                    b = banks[0]
                    bid = id(b)
                    show_bank_amount = bid not in shown_bank_ids
                    shown_bank_ids.add(bid)
                    # GL客商格式化
                    gl_parties = getattr(gl, 'counterparties', None)
                    gl_parties_str = "，".join(gl_parties) if gl_parties else getattr(gl, 'customer_name', '')
                    all_matches.append({
                        "明细科目": acc,
                        "匹配方式": mtype_cn,
                        "GL日期": getattr(gl, 'entry_date', None),
                        "GL凭证号": getattr(gl, 'voucher_no', ''),
                        "GL摘要": getattr(gl, 'abstract', ''),
                        "GL金额": gl.amount,
                        "GL客商": gl_parties_str,
                        "Bank日期": getattr(b, 'tx_date', ''),
                        "Bank交易方": getattr(b, 'counter_party', ''),
                        "Bank金额": b.amount if show_bank_amount else None,
                    })
                else:
                    for i, b in enumerate(banks):
                        bid = id(b)
                        show_bank_amount = bid not in shown_bank_ids
                        shown_bank_ids.add(bid)
                        gl_parties = getattr(gl, 'counterparties', None)
                        gl_parties_str = "，".join(gl_parties) if gl_parties else getattr(gl, 'customer_name', '')
                        all_matches.append({
                            "明细科目": acc,
                            "匹配方式": mtype_cn,
                            "GL日期": getattr(gl, 'entry_date', None),
                            "GL凭证号": getattr(gl, 'voucher_no', ''),
                            "GL摘要": getattr(gl, 'abstract', ''),
                            "GL金额": gl.amount if i == 0 else None,
                            "GL客商": gl_parties_str if i == 0 else "",
                            "Bank日期": getattr(b, 'tx_date', ''),
                            "Bank交易方": getattr(b, 'counter_party', ''),
                            "Bank金额": b.amount if show_bank_amount else None,
                        })

            for gl in res.unmatched_gl:
                gl_parties = getattr(gl, 'counterparties', None)
                gl_parties_str = "，".join(gl_parties) if gl_parties else getattr(gl, 'customer_name', '')
                all_unmatched_gl.append({
                    "明细科目": acc,
                    "GL日期": getattr(gl, 'entry_date', None),
                    "GL凭证号": getattr(gl, 'voucher_no', ''),
                    "GL摘要": getattr(gl, 'abstract', ''),
                    "GL金额": gl.amount,
                    "GL客商": gl_parties_str,
                })

            for b in res.unmatched_bank:
                all_unmatched_bank.append({
                    "明细科目": acc,
                    "Bank日期": getattr(b, 'tx_date', ''),
                    "Bank交易方": getattr(b, 'counter_party', ''),
                    "Bank摘要": getattr(b, 'abstract', ''),
                    "Bank流水号": getattr(b, 'serial_no', ''),
                    "Bank金额": b.amount,
                })

        tab1, tab2, tab3 = st.tabs(["✅ 匹配结果", "❌ 未匹配序时账", "❌ 未匹配银行流水"])

        with tab1:
            if all_matches:
                df_matches = pd.DataFrame(all_matches)
                st.dataframe(df_matches, use_container_width=True)
                st.markdown("**匹配方式分布**")
                match_type_counts = df_matches["匹配方式"].value_counts().reset_index()
                match_type_counts.columns = ["匹配方式", "数量"]
                st.bar_chart(match_type_counts.set_index("匹配方式"))
            else:
                st.info("暂无匹配结果")

        with tab2:
            if all_unmatched_gl:
                st.dataframe(pd.DataFrame(all_unmatched_gl), use_container_width=True)
            else:
                st.info("序时账全部匹配成功！")

        with tab3:
            if all_unmatched_bank:
                st.dataframe(pd.DataFrame(all_unmatched_bank), use_container_width=True)
            else:
                st.info("银行流水全部匹配成功！")

        # 下载结果
        st.markdown("---")
        st.subheader("💾 导出结果")

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            export_results(results, tmp_path)
            with open(tmp_path, "rb") as f:
                excel_data = f.read()

            st.download_button(
                label="📥 下载核对结果 (Excel)",
                data=excel_data,
                file_name="核对结果.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


# ============== 独立模式入口 ==============
if __name__ == "__main__":
    st.set_page_config(
        page_title="序时账与银行流水核对工具",
        page_icon="🏦",
        layout="wide",
    )
    show_bank_matcher()