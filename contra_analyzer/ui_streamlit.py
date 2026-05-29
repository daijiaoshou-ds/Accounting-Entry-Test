"""
对方科目分析 - Streamlit UI界面（重构版）
功能：
1. 左侧上传数据
2. 页面中心字段配置（支持智能检测）
3. 支持压缩模式（精简/极简）
4. 方案计算、导出、导入、生成
"""
import streamlit as st
import pandas as pd
import io
from datetime import datetime
import re

from .core import ContraProcessor
from .algorithm import ExhaustiveSolver
from .memory_web import KnowledgeBase
from .occams_razor import OccamsRazor


# ============ 初始化session state ============
def init_contra_session_state():
    """初始化对方科目分析的session state"""
    keys_defaults = {
        'contra_raw_data': None,          # 原始上传数据
        'contra_processed_data': None,    # 压缩后的数据
        'contra_processor': None,
        'contra_kb': KnowledgeBase(),
        'contra_column_mapping': {},
        'contra_analysis_done': False,
        'contra_filename': None,
        'contra_export_rows': None,
        'contra_final_result': None,
        'contra_compression_mode': 'none',  # none, simple, minimal
        'contra_imported_plan': None,       # 用户导入的方案
    }
    for key, default in keys_defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default


# ============ 智能字段检测 ============
def smart_detect_columns(df):
    """
    智能检测字段映射
    返回: {标准字段名: 检测到的列名, ...}
    """
    mapping = {}
    cols = df.columns.tolist()
    cols_lower = [c.lower() for c in cols]
    
    # 日期字段检测
    date_patterns = [
        ['制单日期', '记账日期', '凭证日期', '日期', 'date', 'time'],
        ['年', '月', '日'],
    ]
    for patterns in date_patterns:
        for col, col_lower in zip(cols, cols_lower):
            if any(p.lower() in col_lower for p in patterns):
                mapping['date'] = col
                break
        if 'date' in mapping:
            break
    
    # 凭证号检测
    voucher_patterns = ['凭证号', '凭证编号', 'voucher', '单号', '记字号', '凭证字号']
    for col, col_lower in zip(cols, cols_lower):
        if any(p.lower() in col_lower for p in voucher_patterns):
            mapping['voucher_id'] = col
            break
    
    # 一级科目检测
    subject_patterns = ['一级科目',  'subject', '会计科目']
    for col, col_lower in zip(cols, cols_lower):
        if any(p.lower() in col_lower for p in subject_patterns):
            mapping['subject'] = col
            break
    
    # 科目明细检测
    detail_subject_patterns = ['科目明细', '明细科目', '科目名称', '明细', '辅助核算']
    for col, col_lower in zip(cols, cols_lower):
        if any(p.lower() in col_lower for p in detail_subject_patterns):
            if col != mapping.get('subject'):  # 避免和一级科目重复
                mapping['detail_subject'] = col
                break
    
    # 借方金额检测
    debit_patterns = ['借方金额', '借方', 'debit', '借', 'debit_amount']
    for col, col_lower in zip(cols, cols_lower):
        if any(p.lower() in col_lower for p in debit_patterns):
            mapping['debit'] = col
            break
    
    # 贷方金额检测
    credit_patterns = ['贷方金额', '贷方', 'credit', '贷', 'credit_amount']
    for col, col_lower in zip(cols, cols_lower):
        if any(p.lower() in col_lower for p in credit_patterns):
            mapping['credit'] = col
            break
    
    # 摘要检测
    summary_patterns = ['摘要', '说明', 'summary', '备注', '附注']
    for col, col_lower in zip(cols, cols_lower):
        if any(p.lower() in col_lower for p in summary_patterns):
            mapping['summary'] = col
            break
    
    return mapping


# ============ 数据压缩 ============
def compress_data_simple(df, mapping):
    """
    精简压缩：保留必要字段并按凭证+科目聚合
    需要的字段：制单日期、凭证编号、一级科目、科目明细、借方金额、贷方金额、摘要
    
    核心逻辑：
    1. 按凭证+科目+摘要分组
    2. 分别累加借方金额和贷方金额
    3. 关键：如果同一科目既有借方又有贷方，拆分成两行
    """
    # 提取需要的列
    required_cols = []
    col_map = {}
    
    for std_col, col_name in mapping.items():
        if col_name in df.columns:
            required_cols.append(col_name)
            col_map[std_col] = col_name
    
    # 如果没有科目明细，使用一级科目作为明细
    if 'detail_subject' not in mapping and 'subject' in mapping:
        col_map['detail_subject'] = mapping['subject']
        if mapping['subject'] not in required_cols:
            required_cols.append(mapping['subject'])
    
    # 选择必要列（去重）
    df_subset = df[list(dict.fromkeys(required_cols))].copy()
    
    # 转换金额为数值（保留正负号）
    df_subset[col_map['debit']] = pd.to_numeric(df_subset[col_map['debit']], errors='coerce').fillna(0)
    df_subset[col_map['credit']] = pd.to_numeric(df_subset[col_map['credit']], errors='coerce').fillna(0)
    
    # 构建分组列
    group_cols = [col_map['date'], col_map['voucher_id']]
    if 'summary' in col_map:
        group_cols.append(col_map['summary'])
    
    group_cols.append(col_map['subject'])
    
    if 'detail_subject' in col_map and col_map['detail_subject'] in df_subset.columns:
        if col_map['detail_subject'] not in group_cols:
            group_cols.append(col_map['detail_subject'])
    
    # 聚合：分别累加借方和贷方
    agg_dict = {
        col_map['debit']: 'sum',
        col_map['credit']: 'sum'
    }
    
    df_compressed = df_subset.groupby(group_cols, as_index=False).agg(agg_dict)
    
    # 关键修复：如果同一行中借方和贷方都有金额，拆分成两行
    # 借方行（贷方设为0）
    df_debit = df_compressed[df_compressed[col_map['debit']] != 0].copy()
    df_debit[col_map['credit']] = 0
    
    # 贷方行（借方设为0）
    df_credit = df_compressed[df_compressed[col_map['credit']] != 0].copy()
    df_credit[col_map['debit']] = 0
    
    # 合并两行
    df_result = pd.concat([df_debit, df_credit], ignore_index=True)
    
    # 保持原始列名，不重命名
    return df_result


def compress_data_minimal(df, mapping):
    """
    极简压缩：按月份+分录特征(Pattern)+科目聚合，输出保持原始列名。
    Pattern = 一级科目-科目明细 + 借贷方向，保留完整的科目信息，
    确保最终输出能够生成二级对方科目。

    用于处理50万行以上的超大型序时账。
    """
    df = df.copy()
    debit_col = mapping['debit']
    credit_col = mapping['credit']
    subject_col = mapping['subject']
    date_col = mapping['date']
    voucher_col = mapping['voucher_id']
    has_detail = 'detail_subject' in mapping and mapping['detail_subject'] in df.columns

    df[debit_col] = pd.to_numeric(df[debit_col], errors='coerce').fillna(0)
    df[credit_col] = pd.to_numeric(df[credit_col], errors='coerce').fillna(0)

    # 构建复合科目（一级科目-科目明细），与 _calc_subj 一致
    df['_subj'] = df[subject_col].astype(str).str.strip()
    if has_detail:
        detail = df[mapping['detail_subject']].astype(str).str.strip()
        mask = (detail != '') & (detail != 'nan') & (detail != df['_subj'])
        df.loc[mask, '_subj'] = df['_subj'] + '-' + detail

    # 确定每行的借贷方向
    df['_dir'] = df.apply(
        lambda r: '借方' if r[debit_col] != 0 else '贷方', axis=1
    )

    # Pattern = 科目[方向]、...  按原始凭证聚合
    df['_subj_dir'] = df['_subj'] + '[' + df['_dir'] + ']'
    df['_temp_voucher'] = df[date_col].astype(str) + '_' + df[voucher_col].astype(str)

    voucher_features = df.groupby('_temp_voucher')['_subj_dir'].apply(
        lambda x: '、'.join(sorted(set(x)))
    ).reset_index()
    voucher_features.columns = ['_temp_voucher', '_voucher_feature']
    df = df.merge(voucher_features, on='_temp_voucher', how='left')

    # 提取月份
    df['_month'] = pd.to_datetime(df[date_col], errors='coerce').dt.month

    # 按(月份 + 分录特征 + 复合科目 + 方向)聚合
    group_cols = ['_month', '_voucher_feature', '_subj', '_dir']
    df_compressed = df.groupby(group_cols, as_index=False).agg({
        debit_col: 'sum',
        credit_col: 'sum'
    })

    # 生成虚拟凭证号
    df_compressed['_group_key'] = df_compressed['_month'].astype(str) + '_' + df_compressed['_voucher_feature']
    df_compressed['_pattern_id'] = df_compressed.groupby('_group_key').ngroup() + 1

    # 借方/贷方行拆分
    df_debit = df_compressed[df_compressed[debit_col] != 0].copy()
    df_debit[credit_col] = 0
    df_credit = df_compressed[df_compressed[credit_col] != 0].copy()
    df_credit[debit_col] = 0
    df_result = pd.concat([df_debit, df_credit], ignore_index=True)

    # 构建输出：保持原始列名，拆分复合科目回一级科目和科目明细
    out = pd.DataFrame()
    out[date_col] = df_result['_month'].apply(lambda m: f"2025-{int(m):02d}-01")
    out[voucher_col] = df_result['_pattern_id'].apply(lambda p: f"虚拟_P{p:04d}")

    # 拆分复合科目：一级科目-科目明细 → 一级科目 + 科目明细
    out[subject_col] = df_result['_subj'].apply(lambda s: s.split('-', 1)[0])
    if has_detail:
        out[mapping['detail_subject']] = df_result['_subj'].apply(
            lambda s: s.split('-', 1)[1] if '-' in s else ''
        )

    if 'summary' in mapping:
        out[mapping['summary']] = df_result['_voucher_feature']
    out[debit_col] = df_result[debit_col]
    out[credit_col] = df_result[credit_col]

    return out


# ============ 侧边栏：数据上传 ============
def sidebar_upload():
    """侧边栏：仅数据上传"""
    with st.sidebar:
        st.markdown("## 🔄 对方科目分析")
        st.markdown("---")
        
        # 数据上传
        st.markdown("### 📁 数据上传")
        
        # 压缩模式选择
        compression = st.radio(
            "数据压缩模式",
            ["不压缩", "精简压缩", "极简压缩"],
            index=0,
            help="""
            - 不压缩：保留所有原始数据
            - 精简压缩：按凭证+科目聚合，保留必要字段
            - 极简压缩：按月份+分录特征聚合，生成虚拟凭证（适合50万行以上大数据）
            """
        )
        
        mode_map = {"不压缩": "none", "精简压缩": "simple", "极简压缩": "minimal"}
        st.session_state.contra_compression_mode = mode_map[compression]
        
        st.markdown("---")
        
        uploaded_file = st.file_uploader(
            "上传序时账",
            type=['xlsx', 'xls', 'csv'],
            help="支持Excel(xlsx/xls)或CSV格式"
        )
        
        if uploaded_file is not None:
            current_filename = uploaded_file.name
            previous_filename = st.session_state.get('contra_filename', None)
            
            if current_filename != previous_filename:
                try:
                    if current_filename.endswith('.csv'):
                        df = pd.read_csv(uploaded_file, dtype=str)
                    else:
                        df = pd.read_excel(uploaded_file, dtype=str)
                    
                    st.session_state.contra_raw_data = df
                    st.session_state.contra_filename = current_filename
                    st.session_state.contra_analysis_done = False
                    st.session_state.contra_processor = None
                    st.session_state.contra_export_rows = None
                    st.session_state.contra_final_result = None
                    st.session_state.contra_column_mapping = {}  # 重置字段配置
                    st.session_state.contra_imported_plan = None
                    
                    # 执行智能字段检测
                    detected = smart_detect_columns(df)
                    st.session_state.contra_detected_mapping = detected
                    
                    st.success(f"✅ 已加载 {len(df)} 行数据")
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 读取失败: {str(e)}")


# ============ 主界面：字段配置 ============
def field_config_section():
    """字段配置区域（页面中心）"""
    if st.session_state.contra_raw_data is None:
        st.info("👈 请先在左侧上传数据文件")
        return False
    
    st.markdown("---")
    st.markdown("### ⚙️ 字段配置")
    
    df = st.session_state.contra_raw_data
    cols = df.columns.tolist()
    
    # 显示智能检测结果
    detected = st.session_state.get('contra_detected_mapping', {})
    
    if detected:
        with st.expander("🤖 智能检测结果", expanded=True):
            st.write("系统已自动识别以下字段，如有错误请手动修改：")
            detected_df = pd.DataFrame([
                {'标准字段': k, '检测到的列': v} for k, v in detected.items()
            ])
            st.dataframe(detected_df, hide_index=True, use_container_width=True)
    
    # 字段配置表单
    st.info("请确认或修改字段映射：")
    
    with st.form("contra_mapping_form"):
        col1, col2 = st.columns(2)
        
        # 辅助函数：获取列的默认索引
        def get_default_index(field_name, cols, detected):
            if field_name in detected and detected[field_name] in cols:
                return cols.index(detected[field_name])
            return 0
        
        with col1:
            st.markdown("**基本字段**")
            date_col = st.selectbox("日期列", cols, 
                index=get_default_index('date', cols, detected))
            voucher_col = st.selectbox("凭证号列", cols,
                index=get_default_index('voucher_id', cols, detected))
            subject_col = st.selectbox("一级科目列", cols,
                index=get_default_index('subject', cols, detected))
            detail_subject_col = st.selectbox("科目明细列（必填）", cols, 
                index=get_default_index('detail_subject', cols, detected) if 'detail_subject' in detected else 0)
        
        with col2:
            st.markdown("**金额字段**")
            debit_col = st.selectbox("借方金额列", cols,
                index=get_default_index('debit', cols, detected))
            credit_col = st.selectbox("贷方金额列", cols,
                index=get_default_index('credit', cols, detected))
            summary_col = st.selectbox("摘要列（可选）", ['（无）'] + cols, 
                index=get_default_index('summary', ['（无）'] + cols, detected) if 'summary' in detected else 0)
        
        submitted = st.form_submit_button("💾 保存配置并开始分析", type="primary", use_container_width=True)
        
        if submitted:
            mapping = {
                'date': date_col,
                'voucher_id': voucher_col,
                'subject': subject_col,
                'debit': debit_col,
                'credit': credit_col,
            }
            mapping['detail_subject'] = detail_subject_col
            if summary_col != '（无）':
                mapping['summary'] = summary_col
            
            st.session_state.contra_column_mapping = mapping
            
            # 根据压缩模式处理数据
            compression_mode = st.session_state.contra_compression_mode
            try:
                if compression_mode == 'simple':
                    df_processed = compress_data_simple(df, mapping)
                    st.success(f"✅ 精简压缩完成：从 {len(df)} 行 → {len(df_processed)} 行")
                elif compression_mode == 'minimal':
                    df_processed = compress_data_minimal(df, mapping)
                    st.success(f"✅ 极简压缩完成：从 {len(df)} 行 → {len(df_processed)} 行")
                else:
                    df_processed = df
                    st.success("✅ 配置已保存（未压缩）")
                
                st.session_state.contra_processed_data = df_processed

                # 直接执行分析
                with st.spinner("正在分层分析..."):
                    stats = _run_contra_analysis()
                st.success(f"✅ 完成！处理 {stats['processed']} 个凭证，复杂模式 {stats['complex_groups']} 个")
                st.rerun()
                return True

            except Exception as e:
                st.error(f"❌ 数据处理失败: {str(e)}")
                import traceback
                st.code(traceback.format_exc())
                return False
    
    return bool(st.session_state.contra_column_mapping)


# ============ 分析执行（共享函数） ============
def _run_contra_analysis():
    """执行对方科目分层分析，保存结果到session state"""
    processor = ContraProcessor()

    if st.session_state.contra_processed_data is not None:
        df = st.session_state.contra_processed_data.copy()
    else:
        df = st.session_state.contra_raw_data.copy()

    mapping = st.session_state.contra_column_mapping
    processor.mapping = mapping
    processor.df = df

    date_col = mapping['date']
    voucher_col = mapping['voucher_id']
    summ_col = mapping.get('summary', date_col)

    processor.df['_uid'] = (processor.df[date_col].astype(str).str.replace(r'\s+00:00:00', '', regex=True)
                              + "_" + processor.df[voucher_col].astype(str))

    for uid, group in processor.df.groupby('_uid'):
        first_row = group.iloc[0]
        unique_summs = group[summ_col].dropna().unique() if summ_col in group.columns else []
        combined_summ = " | ".join([str(s) for s in unique_summs if str(s).strip()])
        processor.meta_cache[uid] = {
            'date': first_row[date_col] if date_col in first_row else '',
            'voucher_id': first_row[voucher_col] if voucher_col in first_row else uid,
            'summary': combined_summ
        }

    processor.df['_calc_debit'] = pd.to_numeric(processor.df[mapping['debit']], errors='coerce').fillna(0).round(2)
    processor.df['_calc_credit'] = pd.to_numeric(processor.df[mapping['credit']], errors='coerce').fillna(0).round(2)

    subj = processor.df[mapping['subject']].astype(str).str.strip()
    processor.df['_calc_subj'] = subj
    if 'detail_subject' in mapping and mapping['detail_subject'] in processor.df.columns:
        detail = processor.df[mapping['detail_subject']].astype(str).str.strip()
        mask = (detail != '') & (detail != 'nan') & (detail != subj)
        processor.df.loc[mask, '_calc_subj'] = subj + '-' + detail

    stats = processor.process_all()
    st.session_state.contra_processor = processor
    st.session_state.contra_analysis_done = True
    return stats


# ============ 主界面：分析控制 ============
def analysis_control():
    """分析控制区域"""
    if not st.session_state.contra_column_mapping:
        return False

    st.markdown("---")
    st.markdown("### 🔍 分层分析")

    if st.session_state.contra_analysis_done:
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("🔄 重新分析", use_container_width=True):
                st.session_state.contra_analysis_done = False
                st.session_state.contra_processor = None
                st.session_state.contra_export_rows = None
                st.rerun()
        with col2:
            st.success("✅ 分析已完成")
        return True

    return False


# ============ 主界面：结果概览 ============
def results_overview():
    """显示分析结果概览"""
    processor = st.session_state.contra_processor
    if not processor:
        return
    
    st.markdown("---")
    st.markdown("### 📊 分析结果")
    
    total = len(processor.df.groupby('_uid').size())
    complex_count = len(processor.complex_clusters)
    simple = total - complex_count
    
    col1, col2, col3 = st.columns(3)
    col1.metric("总凭证数", f"{total:,}")
    col2.metric("自动匹配", f"{simple:,}", help="1借1贷、1借多贷、多借1贷")
    col3.metric("复杂模式", f"{complex_count:,}", help="多借多贷需要穷举")
    
    if complex_count > 0:
        with st.expander(f"📋 查看复杂模式详情（{complex_count}种）"):
            sorted_samples = sorted(
                processor.cluster_samples.items(),
                key=lambda x: x[1]['count'],
                reverse=True
            )
            
            data = []
            for k, sample in sorted_samples:
                data.append({
                    '出现次数': sample['count'],
                    '科目组合': sample['name'][:60] + ('...' if len(sample['name']) > 60 else '')
                })
            
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)


# ============ 主界面：方案计算与表格预览 ============
def solution_table_preview():
    """方案计算、表格预览、导入、生成"""
    processor = st.session_state.contra_processor
    if not processor or not processor.complex_clusters:
        return
    
    st.markdown("---")
    st.markdown("### 🔍 方案计算与选择")
    
    # 计算方案
    if st.session_state.contra_export_rows is None:
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("🧮 计算所有方案", type="primary"):
                with st.spinner("正在穷举计算..."):
                    try:
                        solver = ExhaustiveSolver()
                        kb = st.session_state.contra_kb
                        all_rows = []

                        sorted_samples = sorted(
                            processor.cluster_samples.items(),
                            key=lambda x: x[1]['count'],
                            reverse=True
                        )

                        progress_bar = st.progress(0)

                        for pattern_idx, (key_hash, sample) in enumerate(sorted_samples, 1):
                            pattern_name = sample['name']
                            node_map = sample.get('node_map', {})  # v2.0节点映射

                            solutions, is_timeout = solver.calculate_combinations(
                                sample['debits'], sample['credits'],
                                max_solutions=200, timeout=2.0, node_map=node_map
                            )

                            if not solutions:
                                continue

                            # 排序并标注 (v2.0传入node_map用于硬骨头检测)
                            annotated = []
                            for sol in solutions:
                                r = OccamsRazor.score_solution(sol, node_map)
                                m = kb.get_memory_score(pattern_name, sol)
                                tot = kb.calculate_total_score(r, m)
                                annotated.append({
                                    "sol": sol, "razor": r, "mem": m, "total": tot
                                })
                            annotated.sort(key=lambda x: x['total'], reverse=True)

                            # 预缓存：将最优解的科目级连接关系缓存到processor，方案执行阶段直接复用
                            if annotated:
                                processor.cache_pattern_solution(
                                    key_hash, annotated[0]['sol'], node_map
                                )

                            # 生成表格行
                            sample_uid = sample.get('sample_uid', '')
                            for sol_idx, item in enumerate(annotated, 1):
                                sol = item['sol']
                                option_id = f"{pattern_idx}-{sol_idx}"
                                is_selected = sol_idx == 1
                                check_mark = "x" if is_selected else ""
                                desc = f"O:{item['razor']:.1f} | M:{item['mem']:.4f}"

                                # 方案标题行
                                all_rows.append({
                                    "模式特征": pattern_name,
                                    "方案ID": option_id,
                                    "请在此列打x": check_mark,
                                    "凭证号": sample_uid,
                                    "会计科目": f"=== 方案 {option_id} ===",
                                    "借方金额": None,
                                    "对方科目": None,
                                    "拆分金额": None,
                                    "说明": desc
                                })

                                # 明细行 (v2.0: 使用node_map将节点ID映射为带方向的科目名)
                                for d_node_id, c_map in sol.items():
                                    # 从node_map获取带方向的科目名
                                    if d_node_id in node_map:
                                        d_node = node_map[d_node_id]
                                        d_name = f"{d_node['subject']}[{'借方' if d_node['original_side']=='debit' else '贷方'}]"
                                    else:
                                        d_name = d_node_id.split('__')[0]  # v1.x兼容

                                    for c_node_id, amt in c_map.items():
                                        if abs(amt) > 0.001:
                                            if c_node_id in node_map:
                                                c_node = node_map[c_node_id]
                                                c_name = f"{c_node['subject']}[{'借方' if c_node['original_side']=='debit' else '贷方'}]"
                                            else:
                                                c_name = c_node_id.split('__')[0]  # v1.x兼容

                                            all_rows.append({
                                                "模式特征": pattern_name,
                                                "方案ID": option_id,
                                                "请在此列打x": check_mark,
                                                "凭证号": sample_uid,
                                                "会计科目": d_name,
                                                "借方金额": amt,
                                                "对方科目": c_name,
                                                "拆分金额": amt,
                                                "说明": ""
                                            })
                            
                            progress_bar.progress(pattern_idx / len(sorted_samples))
                        
                        st.session_state.contra_export_rows = all_rows
                        progress_bar.empty()
                        st.success(f"✅ 方案计算完成！共 {len(sorted_samples)} 个模式")
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"❌ 计算失败: {str(e)}")
                        import traceback
                        st.code(traceback.format_exc())
    
    # 显示方案表格
    else:
        rows = st.session_state.contra_export_rows
        df_display = pd.DataFrame(rows)
        
        st.info(f"共 {df_display['模式特征'].nunique()} 种复杂模式，下面以表格形式展示所有方案")
        
        # 显示完整表格
        st.dataframe(df_display, use_container_width=True, height=600)
        
        # 方案导入和导出
        st.markdown("---")
        st.markdown("#### 📤 方案操作")
        
        # 第一行：导入、导出、生成按钮
        col1, col2, col3, col4 = st.columns([1.2, 1, 1, 1])
        
        with col1:
            # 方案导入
            uploaded_plan = st.file_uploader(
                "📂 导入调整后的方案",
                type=['xlsx'],
                key="contra_plan_import",
                label_visibility="visible"
            )
            
            if uploaded_plan is not None:
                try:
                    df_imported = pd.read_excel(uploaded_plan)
                    st.session_state.contra_imported_plan = df_imported
                    st.success("✅ 方案已导入")
                except Exception as e:
                    st.error(f"❌ 导入失败: {str(e)}")
        
        with col2:
            # 导出当前方案
            st.markdown("<br>", unsafe_allow_html=True)  # 对齐
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                df_display.to_excel(writer, index=False, sheet_name='方案选择')
            
            st.download_button(
                label="📥 导出方案",
                data=excel_buffer.getvalue(),
                file_name="方案选择.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        
        with col3:
            # 使用默认方案
            st.markdown("<br>", unsafe_allow_html=True)  # 对齐
            if st.button("✨ 按默认方案", use_container_width=True):
                generate_final_result(use_imported=False)
        
        with col4:
            # 使用导入方案
            st.markdown("<br>", unsafe_allow_html=True)  # 对齐
            if st.session_state.contra_imported_plan is not None:
                if st.button("✨ 按导入方案", type="primary", use_container_width=True):
                    generate_final_result(use_imported=True)
            else:
                st.button("✨ 按导入方案", disabled=True, use_container_width=True, 
                         help="请先上传调整后的方案文件")
        
        # 第二行：下载结果（全宽）
        if st.session_state.contra_final_result:
            st.markdown("---")
            st.success("✅ 对方科目分析表已生成")
            st.download_button(
                label="⬇️ 下载对方科目分析表",
                data=st.session_state.contra_final_result,
                file_name=f"对方科目分析_{datetime.now().strftime('%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary"
            )


def _parse_subject_side(name):
    """解析 '科目名称[借方/贷方]' 格式，返回 (科目, 方向)"""
    match = re.match(r'^(.+)\[(借方|贷方)\]$', name)
    if match:
        return match.group(1), match.group(2)
    return name, None



def generate_final_result(use_imported=False):
    """生成最终结果"""
    with st.spinner("正在生成..."):
        try:
            kb = st.session_state.contra_kb
            processor = st.session_state.contra_processor

            # 解析用户导入的方案选择
            user_selections = None
            custom_solutions = None
            if use_imported and st.session_state.contra_imported_plan is not None:
                df_plan = st.session_state.contra_imported_plan
                user_selections = {}
                custom_solutions = {}

                # 查找包含 "x" 的行（用户选择的方案）
                selected_rows = df_plan[df_plan['请在此列打x'].astype(str).str.strip().str.lower() == 'x']

                # 按模式特征分组，统计用户实际改动
                custom_count = 0
                modified_count = 0
                for _, row in selected_rows.iterrows():
                    pattern_name = str(row.get('模式特征', '')).strip()
                    option_id = str(row.get('方案ID', '')).strip()
                    if pattern_name and option_id:
                        if 'X' in option_id.upper():
                            sol = _build_custom_from_plan(df_plan, pattern_name, option_id, processor)
                            if sol:
                                custom_solutions[pattern_name] = sol
                                user_selections[pattern_name] = option_id
                                custom_count += 1
                        else:
                            user_selections[pattern_name] = option_id
                            if not option_id.endswith('-1'):
                                modified_count += 1

                if user_selections:
                    parts = []
                    if custom_count:
                        parts.append(f"{custom_count} 个自设计")
                    if modified_count:
                        parts.append(f"{modified_count} 个改选方案")
                    if parts:
                        st.info(f"检测到用户修改：{'、'.join(parts)}（共 {len(user_selections)} 个模式）")
                    else:
                        st.info(f"已导入 {len(user_selections)} 个模式的默认方案选择")

            def log_cb(msg):
                pass

            final_df = processor.finalize_report(kb, log_cb, user_selections, custom_solutions)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                final_df.to_excel(writer, index=False, sheet_name='对方科目分析')

            st.session_state.contra_final_result = output.getvalue()
            st.success("生成完成！")
            st.rerun()

        except Exception as e:
            st.error(f"生成失败: {str(e)}")
            import traceback
            st.code(traceback.format_exc())


def _build_custom_from_plan(df_plan, pattern_name, option_id, processor):
    """从用户导入的Excel中提取科目级连接关系（拓扑，不含金额）"""
    from collections import defaultdict

    # 提取该pattern+方案的明细行（会计科目非空且非标题行）
    detail_rows = df_plan[
        (df_plan['模式特征'].astype(str).str.strip() == pattern_name) &
        (df_plan['方案ID'].astype(str).str.strip() == option_id) &
        (df_plan['会计科目'].notna()) &
        (df_plan['会计科目'].astype(str).str.strip() != '') &
        (~df_plan['会计科目'].astype(str).str.startswith('=== '))
    ]

    if detail_rows.empty:
        return None

    side_map = {'借方': 'debit', '贷方': 'credit'}
    driver_keys = set()
    bucket_keys = set()
    edges = defaultdict(list)

    for _, row in detail_rows.iterrows():
        d_name = str(row['会计科目']).strip()
        c_name = str(row['对方科目']).strip()

        d_subj, d_side = _parse_subject_side(d_name)
        c_subj, c_side = _parse_subject_side(c_name)

        if not d_subj or not c_subj:
            continue

        d_key = (d_subj, side_map.get(d_side, 'debit'))
        c_key = (c_subj, side_map.get(c_side, 'credit'))

        driver_keys.add(d_key)
        bucket_keys.add(c_key)
        if c_key not in edges[d_key]:
            edges[d_key].append(c_key)

    if not edges:
        return None

    return {
        'driver_keys': driver_keys,
        'bucket_keys': bucket_keys,
        'edges': dict(edges)
    }


# ============ 主函数 ============
def show_contra_analyzer():
    """对方科目分析主函数"""
    st.markdown("## 🔄 对方科目分析")
    
    init_contra_session_state()
    
    # 左侧上传
    sidebar_upload()
    
    # 主界面流程
    config_ok = field_config_section()
    
    if config_ok:
        if analysis_control():
            results_overview()
            solution_table_preview()
