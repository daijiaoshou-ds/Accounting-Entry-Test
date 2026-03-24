"""
Streamlit主应用 - 会计分录异常检测系统
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import io
import base64
from datetime import datetime
from collections import defaultdict, Counter

from src.accounting_anomaly import DataProcessor, ClusterEngine, CORE_GROUPS, MLClassifier, AnomalyDetector

# ============ 页面配置 ============
st.set_page_config(
    page_title="会计分录异常检测系统",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============ 自定义样式 ============
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 10px;
        padding: 1rem;
        text-align: center;
    }
    .risk-high {
        color: #ff4b4b;
        font-weight: bold;
    }
    .risk-medium {
        color: #ffa726;
        font-weight: bold;
    }
    .risk-low {
        color: #66bb6a;
        font-weight: bold;
    }
    .config-section {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 10px;
        border: 1px solid #dee2e6;
    }
</style>
""", unsafe_allow_html=True)

# ============ 初始化session state ============
def init_session_state():
    """初始化session state变量"""
    if 'raw_data' not in st.session_state:
        st.session_state.raw_data = None
    if 'processed_data' not in st.session_state:
        st.session_state.processed_data = None
    if 'clustered_data' not in st.session_state:
        st.session_state.clustered_data = None
    if 'anomaly_result' not in st.session_state:
        st.session_state.anomaly_result = None
    if 'distance_matrix' not in st.session_state:
        st.session_state.distance_matrix = None
    if 'column_mapping' not in st.session_state:
        st.session_state.column_mapping = {}
    if 'column_confidence' not in st.session_state:
        st.session_state.column_confidence = {}
    if 'ml_logs' not in st.session_state:
        st.session_state.ml_logs = []

def get_download_link(df, filename="result.xlsx"):
    """生成下载链接"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    
    b64 = base64.b64encode(output.getvalue()).decode()
    href = f'<a href="data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,{b64}" download="{filename}" style="text-decoration:none;">📥 下载Excel文件</a>'
    return href

# ============ 字段配置页面 ============
def show_field_configuration():
    """显示字段配置页面"""
    st.markdown("### ⚙️ 字段配置")
    st.info("系统已自动识别字段，请检查并修正（如有需要）")
    
    df = st.session_state.raw_data
    
    # 自动检测
    processor = DataProcessor(df)
    detected, confidence = processor.auto_detect_columns()
    
    # 合并已保存的配置
    if st.session_state.column_mapping:
        detected.update(st.session_state.column_mapping)
    
    st.session_state.column_confidence = confidence
    
    # 显示原始列
    st.markdown("#### 📋 原始数据列")
    st.write("可用列：", ", ".join(df.columns.tolist()))
    
    # 配置表单
    st.markdown("#### 🔧 字段映射配置")
    
    with st.form("column_mapping_form"):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**必需字段**")
            
            # 日期列
            date_options = [''] + df.columns.tolist()
            date_default = detected.get('date', '') if detected.get('date') in df.columns else 0
            if date_default:
                try:
                    date_index = date_options.index(date_default)
                except:
                    date_index = 0
            else:
                date_index = 0
            date_col = st.selectbox("日期列", options=date_options, index=date_index, 
                                   help="凭证日期，用于生成唯一凭证号")
            
            # 凭证号列
            voucher_default = detected.get('voucher_no', '') if detected.get('voucher_no') in df.columns else 0
            voucher_options = [''] + df.columns.tolist()
            if voucher_default:
                try:
                    voucher_index = voucher_options.index(voucher_default)
                except:
                    voucher_index = 0
            else:
                voucher_index = 0
            voucher_col = st.selectbox("凭证号列", options=voucher_options, index=voucher_index,
                                      help="凭证编号，如'记-001'")
            
            # 科目列
            subject_default = detected.get('first_level_subject', '') if detected.get('first_level_subject') in df.columns else 0
            if not subject_default and detected.get('subject_name') in df.columns:
                subject_default = detected.get('subject_name')
            subject_options = [''] + df.columns.tolist()
            if subject_default:
                try:
                    subject_index = subject_options.index(subject_default)
                except:
                    subject_index = 0
            else:
                subject_index = 0
            subject_col = st.selectbox("科目列", options=subject_options, index=subject_index,
                                      help="一级科目或科目名称列")
            
        with col2:
            st.markdown("**金额字段**")
            
            # 借方金额
            debit_default = detected.get('debit', '') if detected.get('debit') in df.columns else 0
            debit_options = [''] + df.columns.tolist()
            if debit_default:
                try:
                    debit_index = debit_options.index(debit_default)
                except:
                    debit_index = 0
            else:
                debit_index = 0
            debit_col = st.selectbox("借方金额列", options=debit_options, index=debit_index)
            
            # 贷方金额
            credit_default = detected.get('credit', '') if detected.get('credit') in df.columns else 0
            credit_options = [''] + df.columns.tolist()
            if credit_default:
                try:
                    credit_index = credit_options.index(credit_default)
                except:
                    credit_index = 0
            else:
                credit_index = 0
            credit_col = st.selectbox("贷方金额列", options=credit_options, index=credit_index)
            
            # 对方科目列（可选）
            counter_default = detected.get('counter_subject', '') if detected.get('counter_subject') in df.columns else 0
            counter_options = ['（自动计算）'] + df.columns.tolist()
            if counter_default:
                try:
                    counter_index = counter_options.index(counter_default)
                except:
                    counter_index = 0
            else:
                counter_index = 0
            counter_col = st.selectbox("对方科目列（可选）", options=counter_options, index=counter_index,
                                      help="如已上传对方科目则选用，否则系统自动计算")
            if counter_col == '（自动计算）':
                counter_col = None
            
            # 摘要列（可选）
            summary_default = detected.get('summary', '') if detected.get('summary') in df.columns else 0
            summary_options = ['（无）'] + df.columns.tolist()
            if summary_default:
                try:
                    summary_index = summary_options.index(summary_default)
                except:
                    summary_index = 0
            else:
                summary_index = 0
            summary_col = st.selectbox("摘要列（可选）", options=summary_options, index=summary_index)
            if summary_col == '（无）':
                summary_col = None
        
        submitted = st.form_submit_button("💾 保存配置", type="primary")
        
        if submitted:
            # 保存配置
            mapping = {}
            if date_col:
                mapping['date'] = date_col
            if voucher_col:
                mapping['voucher_no'] = voucher_col
            if subject_col:
                mapping['first_level_subject'] = subject_col
                mapping['subject_name'] = subject_col
            if debit_col:
                mapping['debit'] = debit_col
            if credit_col:
                mapping['credit'] = credit_col
            if counter_col:
                mapping['counter_subject'] = counter_col
            if summary_col:
                mapping['summary'] = summary_col
            
            st.session_state.column_mapping = mapping
            st.success("✅ 配置已保存！")
            
            # 显示配置摘要
            st.markdown("#### 当前配置")
            config_df = pd.DataFrame([
                {'标准字段': k, '映射列': v} for k, v in mapping.items()
            ])
            st.dataframe(config_df, use_container_width=True, hide_index=True)

# ============ 侧边栏 ============
def sidebar():
    with st.sidebar:
        st.markdown("## 🏦 会计分录异常检测")
        st.markdown("---")
        
        # 数据上传
        st.markdown("### 📁 数据上传")
        uploaded_file = st.file_uploader(
            "上传会计凭证文件",
            type=['xlsx', 'xls', 'csv'],
            help="支持Excel或CSV格式，需包含：日期、凭证编号、科目、借方金额、贷方金额等列"
        )
        
        # 关键修复：只有新上传文件时才清空结果，避免切换Tab时数据丢失
        if uploaded_file is not None:
            # 检查是否是新文件（通过文件名判断）
            current_filename = uploaded_file.name
            previous_filename = st.session_state.get('uploaded_filename', None)
            
            if current_filename != previous_filename:
                # 新文件上传，需要重新读取
                try:
                    if current_filename.endswith('.csv'):
                        df = pd.read_csv(uploaded_file)
                    else:
                        df = pd.read_excel(uploaded_file)
                    
                    st.session_state.raw_data = df
                    st.session_state.uploaded_filename = current_filename
                    st.success(f"✅ 成功加载 {len(df)} 行数据")
                    
                    # 只有新文件上传时才清空之前的分析结果
                    st.session_state.processed_data = None
                    st.session_state.clustered_data = None
                    st.session_state.anomaly_result = None
                    st.session_state.distance_matrix = None
                    
                except Exception as e:
                    st.error(f"❌ 文件读取失败: {str(e)}")
            # 如果不是新文件，不做任何操作（保留现有的 session_state 数据）
        
        st.markdown("---")
        
        # 分析控制
        st.markdown("### ⚙️ 分析控制")
        
        if st.button("🚀 开始分析", type="primary", use_container_width=True):
            if st.session_state.raw_data is not None:
                if not st.session_state.column_mapping:
                    st.warning("⚠️ 请先完成字段配置")
                else:
                    with st.spinner("正在分析中..."):
                        run_analysis()
                    st.success("✅ 分析完成！")
            else:
                st.warning("⚠️ 请先上传数据")
        
        st.markdown("---")
        
        # 关于
        with st.expander("ℹ️ 关于系统"):
            st.markdown("""
            **会计分录异常检测系统**
            
            基于会计科目组合和金额识别异常凭证：
            - 群聚类分析
            - 群距离计算
            - 异常风险评分
            
            **风险等级**
            - 🔴 高风险 (≥80分)
            - 🟠 中风险 (30-80分)
            - 🟡 中低风险 (10-30分)
            - 🟢 正常 (<10分)
            """)

# ============ 分析流程 ============
def run_analysis():
    """运行完整分析流程"""
    df = st.session_state.raw_data
    mapping = st.session_state.column_mapping
    
    # Step 1: 数据预处理
    processor = DataProcessor(df, column_mapping=mapping)
    processed_df = processor.preprocess()
    st.session_state.processed_data = processed_df
    
    # Step 2: 群聚类
    cluster_engine = ClusterEngine()
    clustered_df = cluster_engine.classify_all(processed_df, voucher_col='voucher_unique_id')
    st.session_state.clustered_data = clustered_df
    
    # Step 3: 可选的ML补充分类
    ml_classifier = MLClassifier()
    ml_classifier.fit(clustered_df)
    
    # 显示ML训练日志
    if hasattr(ml_classifier, 'training_log') and ml_classifier.training_log:
        st.session_state.ml_logs = ml_classifier.training_log
        for log in ml_classifier.training_log:
            print(log)  # 输出到控制台
    
    if ml_classifier.is_fitted:
        clustered_df = ml_classifier.batch_predict(clustered_df)
    
    # Step 4: 异常检测
    detector = AnomalyDetector()
    anomaly_result = detector.detect_anomalies(clustered_df)
    st.session_state.anomaly_result = anomaly_result
    st.session_state.distance_matrix = detector.get_distance_matrix_dataframe()
    st.session_state.detector = detector

# ============ 科目连接图 ============
def build_subject_graph(df, group_name):
    """
    构建指定模块内的科目连接图
    
    逻辑：
    1. 只取贷方科目和它们的对方科目
    2. **按凭证号统计连接频次** - 这是核心，使用凭证计数而非行数
    3. 构建有向图（贷方 -> 对方科目）
    
    重要：连接频次 = 有多少个不同的凭证包含这条连接
    """
    # 筛选该群的凭证
    group_df = df[df['primary_group'] == group_name].copy()
    
    if len(group_df) == 0:
        return None, None
    
    # 按凭证去重
    voucher_df = group_df.groupby('voucher_unique_id').agg({
        'voucher_feature': 'first'
    }).reset_index()
    
    # 获取这些凭证的所有行
    voucher_ids = voucher_df['voucher_unique_id'].tolist()
    group_df = df[df['voucher_unique_id'].isin(voucher_ids)].copy()
    
    # 只取贷方行（有贷方金额的行）
    credit_df = group_df[group_df['credit'] > 0].copy()
    
    if len(credit_df) == 0:
        return None, None
    
    # 统计连接频次 - 按凭证计数
    # 使用 {(from, to): set(凭证号)} 来去重
    edge_vouchers = defaultdict(set)
    nodes = set()
    
    for _, row in credit_df.iterrows():
        voucher_id = row['voucher_unique_id']
        from_subject = row['first_level_subject']  # 贷方科目
        to_subjects = row['counter_subject'].split('、') if row['counter_subject'] else []
        
        if not from_subject or not to_subjects:
            continue
        
        nodes.add(from_subject)
        for to_subject in to_subjects:
            if to_subject:
                nodes.add(to_subject)
                edge_vouchers[(from_subject, to_subject)].add(voucher_id)
    
    # 转换为计数 - 每个连接有多少个凭证
    edges = Counter()
    for edge, vouchers in edge_vouchers.items():
        edges[edge] = len(vouchers)
    
    return nodes, edges

def draw_subject_graph(nodes, edges, group_name):
    """
    绘制简洁版科目连接图
    只显示框和线，线上显示计数
    """
    if not nodes or not edges:
        return None
    
    # 节点去重排序
    node_list = sorted(list(set(nodes)))
    node_index = {node: i for i, node in enumerate(node_list)}
    
    # 准备连接数据
    sources = []
    targets = []
    values = []  # 用于显示计数
    
    for (from_node, to_node), count in edges.items():
        if from_node in node_index and to_node in node_index:
            sources.append(node_index[from_node])
            targets.append(node_index[to_node])
            values.append(count)
    
    if not sources:
        return None
    
    # 创建Sankey图 - 极简风格
    fig = go.Figure(data=[go.Sankey(
        arrangement='snap',
        node=dict(
            pad=20,
            thickness=25,
            line=dict(color="black", width=1.5),
            label=node_list,
            color='white',  # 白色节点
            hovertemplate='%{label}<extra></extra>'
        ),
        link=dict(
            source=sources,
            target=targets,
            value=[1] * len(sources),  # 统一线宽为1，不随计数变化
            color='rgba(100,100,100,0.6)',  # 灰色线条
            hovertemplate='%{source.label} → %{target.label}<br>凭证数: %{customdata}<extra></extra>',
            customdata=values  # 实际计数
        )
    )])
    
    fig.update_layout(
        title=dict(
            text=f"{group_name} - 科目资金流向",
            font=dict(size=14)
        ),
        font=dict(size=11),
        height=500,
        width=900,
        margin=dict(l=30, r=30, t=50, b=30),
        paper_bgcolor='white'
    )
    
    return fig

# ============ 主页面 ============
def main_page():
    st.markdown('<div class="main-header">🏦 会计分录异常检测系统</div>', unsafe_allow_html=True)
    
    if st.session_state.raw_data is None:
        # 欢迎页面
        show_welcome()
    else:
        # 显示分析结果
        show_analysis_results()

def show_welcome():
    """显示欢迎页面"""
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.info("""
        ### 👋 欢迎使用！
        
        **使用步骤：**
        1. 在左侧边栏上传您的会计凭证数据（Excel/CSV）
        2. 点击"字段配置"Tab配置字段映射
        3. 点击"开始分析"按钮
        4. 查看分析结果和异常凭证
        
        **数据格式要求：**
        | 必需列 | 说明 |
        |:------|:-----|
        | 制单日期/记账日期 | 凭证日期 |
        | 凭证编号 | 凭证号 |
        | 一级科目/科目名称 | 会计科目 |
        | 借方金额 | 借方发生额 |
        | 贷方金额 | 贷方发生额 |
        """)

def show_analysis_results():
    """显示分析结果"""
    
    # 创建Tab
    tabs = st.tabs([
        "⚙️ 字段配置",
        "📊 数据概览",
        "📦 群聚类结果",
        "🌐 科目连接图",
        "🌡️ 群距离矩阵",
        "⚠️ 异常检测",
        "📈 统计报告"
    ])
    
    # Tab 1: 字段配置
    with tabs[0]:
        show_field_configuration()
    
    # Tab 2: 数据概览
    with tabs[1]:
        show_data_overview()
    
    # Tab 3: 群聚类结果
    with tabs[2]:
        show_cluster_results()
    
    # Tab 4: 科目连接图
    with tabs[3]:
        show_subject_graph()
    
    # Tab 5: 群距离矩阵
    with tabs[4]:
        show_distance_matrix()
    
    # Tab 6: 异常检测
    with tabs[5]:
        show_anomaly_results()
    
    # Tab 7: 统计报告
    with tabs[6]:
        show_statistics()

def show_data_overview():
    """显示数据概览"""
    st.markdown("### 📊 数据概览")
    
    if st.session_state.processed_data is not None:
        df = st.session_state.processed_data
        
        # 统计卡片 - 按凭证计数
        voucher_count = df['voucher_unique_id'].nunique()
        row_count = len(df)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("凭证数量", f"{voucher_count:,}")
        with col2:
            st.metric("数据行数", f"{row_count:,}")
        with col3:
            st.metric("科目种类", f"{df['first_level_subject'].nunique():,}")
        with col4:
            st.metric("分录特征数", f"{df['voucher_feature'].nunique():,}")
        
        # 数据预览
        st.markdown("#### 数据预览（前10行）")
        
        # 选择显示的列
        display_cols = ['voucher_unique_id', 'first_level_subject', 'debit', 'credit', 
                       'direction', 'counter_subject', 'voucher_feature']
        available_cols = [c for c in display_cols if c in df.columns]
        
        st.dataframe(df[available_cols].head(10), use_container_width=True)
        
        # 显示一些示例凭证的完整特征
        st.markdown("#### 示例凭证的会计分录特征（抽查验证）")
        sample_vouchers = df['voucher_unique_id'].unique()[:5]
        for vid in sample_vouchers:
            voucher_data = df[df['voucher_unique_id'] == vid]
            feature = voucher_data['voucher_feature'].iloc[0]
            subjects = voucher_data['first_level_subject'].tolist()
            with st.expander(f"{vid}: {feature}"):
                st.write(f"包含科目: {', '.join(subjects)}")
        
        # 特征分布 - 按凭证计数
        st.markdown("#### 会计分录特征分布（Top 10）- 按凭证计数")
        processor = DataProcessor(st.session_state.raw_data)
        processor.set_column_mapping(st.session_state.column_mapping)
        processor.processed_df = df
        feature_dist = processor.get_feature_distribution().head(10)
        
        fig = px.bar(
            x=feature_dist.index,
            y=feature_dist.values,
            labels={'x': '会计分录特征', 'y': '凭证数量'},
            title='最常见的会计分录特征（按凭证计数）'
        )
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info('请先点击"开始分析"生成结果')

def show_cluster_results():
    """显示群聚类结果"""
    st.markdown("### 📦 群聚类结果")
    
    if st.session_state.clustered_data is not None:
        df = st.session_state.clustered_data
        
        # 显示 ML 训练日志（如果有）
        if 'ml_logs' in st.session_state and st.session_state.ml_logs:
            with st.expander("🤖 机器学习训练日志（点击查看）", expanded=False):
                st.markdown("""
                **📚 ML原理说明**
                
                ML使用**无监督学习（KMeans聚类）**，不是神经网络，没有"学习轮数"概念：
                - **TF-IDF**: 将会计分录特征转换为数字向量
                - **KMeans**: 自动发现相似特征的模式，通过迭代收敛（默认最多300次）
                - **群代表向量**: 每个群的平均向量作为该群的"质心"
                
                **🤔 它是如何帮你划分群的？**
                
                1. **问题**: 规则无法分类的凭证（归入"其他群"）
                2. **解决**: ML通过比较特征相似度，将其归类到最相似的已知群
                3. **原理**: 如果"委托加工物资"的向量与"原材料"（采购群）很相似，就把它分到采购群
                
                **📊 日志解读**
                - `实际迭代次数`: KMeans收敛所需的迭代次数（你的数据: 9次）
                - `惯性`: 聚类的紧密程度（越小越好，你的数据: 2553.98）
                - `群的代表向量`: 已建立6个群的特征向量（用于相似度比较）
                """)
                st.markdown("**训练日志:**")
                for log in st.session_state.ml_logs:
                    st.text(log)
        
        # 群分布统计 - 按凭证计数
        cluster_engine = ClusterEngine()
        distribution = cluster_engine.get_group_distribution(df)
        
        # 跨群统计
        cross_group_count = df[df['is_cross_group'] == True]['voucher_unique_id'].nunique()
        total_voucher_count = df['voucher_unique_id'].nunique()
        
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.markdown("#### 各业务群凭证数量（按凭证计数）")
            st.dataframe(distribution, use_container_width=True)
            st.metric("跨群凭证数", f"{cross_group_count} / {total_voucher_count}")
        
        with col2:
            # 饼图
            fig = px.pie(
                distribution,
                values='凭证数量',
                names='业务群',
                title='业务群分布（按凭证计数）'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # 跨群凭证明细 - 放在下面，上下结构
        st.markdown("---")
        st.markdown(f"#### 🔄 跨群凭证明细（共 {cross_group_count} 个）")
        
        if cross_group_count > 0:
            cross_df = df[df['is_cross_group'] == True].groupby('voucher_unique_id').agg({
                'voucher_feature': 'first',
                'primary_group': 'first',
                'involved_groups': 'first'
            }).reset_index()
            
            # 添加下载按钮
            st.markdown(get_download_link(cross_df, f"跨群凭证_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"), 
                       unsafe_allow_html=True)
            
            # 显示表格
            st.dataframe(cross_df, use_container_width=True, height=400)
        else:
            st.info("未发现跨群凭证")
        
        # 特征明细 - 按凭证计数
        st.markdown("---")
        st.markdown("#### 会计分录特征明细（按凭证计数）")
        
        # 先按凭证去重
        voucher_df = df.groupby('voucher_unique_id').agg({
            'voucher_feature': 'first',
            'primary_group': 'first',
            'is_cross_group': 'first',
            'involved_groups': 'first'
        }).reset_index()
        
        feature_detail = voucher_df.groupby(['voucher_feature', 'primary_group', 'is_cross_group', 'involved_groups']).size().reset_index(name='凭证数量')
        feature_detail = feature_detail.sort_values('凭证数量', ascending=False)
        
        # 标记跨群
        feature_detail['是否跨群'] = feature_detail['is_cross_group'].apply(lambda x: '是' if x else '否')
        feature_detail_display = feature_detail[['voucher_feature', 'primary_group', 'involved_groups', '是否跨群', '凭证数量']]
        feature_detail_display.columns = ['会计分录特征', '归属群', '涉及群', '是否跨群', '凭证数量']
        
        # 添加下载按钮
        st.markdown(get_download_link(feature_detail_display, f"会计分录特征聚类明细_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"), 
                   unsafe_allow_html=True)
        
        # 筛选
        selected_group = st.selectbox(
            "筛选业务群",
            ['全部'] + list(df['primary_group'].unique())
        )
        
        if selected_group != '全部':
            filtered = feature_detail_display[feature_detail_display['归属群'] == selected_group]
        else:
            filtered = feature_detail_display
        
        st.dataframe(filtered.head(100), use_container_width=True)
        
        # 显示统计信息
        st.markdown(f"**共 {len(feature_detail)} 种不同的会计分录特征**")
    else:
        st.info('请先点击"开始分析"生成结果')

def show_subject_graph():
    """显示科目连接详情（修复切换崩溃）"""
    st.markdown("### 🌐 科目连接详情")
    
    # 安全检查：如果数据丢失，尝试从其他session state恢复
    if st.session_state.get('clustered_data') is None:
        st.warning('⚠️ 数据未加载，请返回"群聚类结果"标签页或重新点击"开始分析"')
        return
    
    df = st.session_state.clustered_data
    
    # 调试信息
    st.write(f"调试: 数据框形状 {df.shape}, 群列表: {df['primary_group'].unique()}")
    
    # 选择业务群 - 显示所有有数据的群（包括其他群）
    all_groups = sorted(df['primary_group'].unique())
    groups = [g for g in all_groups if g != '其他群']
    
    if len(groups) == 0:
        st.warning("没有可用的业务群数据")
        return
    
    # 使用session_state保存选择，避免radio切换时重新运行整个页面导致数据丢失
    if 'selected_connection_group' not in st.session_state:
        st.session_state.selected_connection_group = groups[0] if groups else None
    
    # 创建列布局，使用selectbox代替radio（更稳定）
    col1, col2 = st.columns([1, 3])
    
    with col1:
        selected_group = st.selectbox(
            "选择业务群",
            groups,
            index=groups.index(st.session_state.selected_connection_group) if st.session_state.selected_connection_group in groups else 0,
            key='connection_selectbox'
        )
    
    # 同步到session_state（不直接触发重新运行）
    st.session_state.selected_connection_group = selected_group
    
    # 显示连接详情
    st.markdown(f"#### {selected_group} - 科目连接明细")
    
    try:
        nodes, edges = build_subject_graph(df, selected_group)
        
        if not nodes or not edges:
            st.info(f"**{selected_group}** 暂无连接数据（可能没有贷方科目或对方科目为空）")
        else:
            # 显示统计
            st.write(f"**统计**: 科目数 {len(nodes)} | 连接数 {len(edges)} | 总流量 {sum(edges.values())}")
            
            # 显示连接详情表格
            edge_list = [{'贷方科目': e[0], '对方科目': e[1], '凭证数': c} 
                       for e, c in sorted(edges.items(), key=lambda x: -x[1])]
            st.dataframe(pd.DataFrame(edge_list), use_container_width=True, height=350)
    except Exception as e:
        st.error(f"获取连接数据时出错: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
    
    # 显示所有群的统计
    st.markdown("---")
    st.markdown("#### 各群连接汇总（所有业务群）")
    
    all_stats = []
    for group in all_groups:  # 显示所有群，包括其他群
        try:
            nodes, edges = build_subject_graph(df, group)
            all_stats.append({
                '业务群': group,
                '节点数': len(nodes) if nodes else 0,
                '连接数': len(edges) if edges else 0,
                '总流量': sum(edges.values()) if edges else 0
            })
        except Exception as e:
            print(f"统计 {group} 时出错: {e}")
    
    if all_stats:
        st.dataframe(pd.DataFrame(all_stats), use_container_width=True, hide_index=True)

def show_distance_matrix():
    """显示群距离矩阵"""
    st.markdown("### 🌡️ 群距离矩阵")
    
    if st.session_state.distance_matrix is not None:
        matrix = st.session_state.distance_matrix
        
        # 热力图
        fig = px.imshow(
            matrix.values,
            x=matrix.columns,
            y=matrix.index,
            color_continuous_scale='RdYlBu_r',
            title='业务群距离热力图（颜色越红表示距离越远）',
            labels=dict(color="距离D")
        )
        fig.update_layout(height=500)
        st.plotly_chart(fig, use_container_width=True)
        
        # 数据表
        st.markdown("#### 距离数值")
        st.dataframe(matrix, use_container_width=True)
        
        # 最远/最近群对
        st.markdown("#### 群距离分析")
        
        # 提取上三角矩阵的非零值
        pairs = []
        for i, row in enumerate(matrix.index):
            for j, col in enumerate(matrix.columns):
                if i < j and matrix.iloc[i, j] > 0:
                    pairs.append({
                        '群A': row,
                        '群B': col,
                        '距离': matrix.iloc[i, j]
                    })
        
        pairs_df = pd.DataFrame(pairs).sort_values('距离', ascending=False)
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**距离最远的群对（业务差异大）**")
            st.dataframe(pairs_df.head(5), use_container_width=True)
        with col2:
            st.markdown("**距离最近的群对（业务关联密切）**")
            st.dataframe(pairs_df.tail(5).sort_values('距离'), use_container_width=True)
    else:
        st.info('请先点击"开始分析"生成结果')

def show_anomaly_results():
    """显示异常检测结果"""
    st.markdown("### ⚠️ 异常凭证检测")
    
    if st.session_state.anomaly_result is not None:
        result_df = st.session_state.anomaly_result
        detector = st.session_state.get('detector')
        
        # 摘要统计 - 按凭证去重
        summary = detector.get_anomaly_summary(result_df) if detector else {}
        
        cols = st.columns(5)
        with cols[0]:
            st.metric("总凭证数", summary.get('total_vouchers', 0))
        with cols[1]:
            st.metric("高风险", summary.get('high_risk', 0), delta_color="inverse")
        with cols[2]:
            st.metric("中风险", summary.get('medium_risk', 0))
        with cols[3]:
            st.metric("低风险", summary.get('low_risk', 0))
        with cols[4]:
            st.metric("跨模块数", summary.get('cross_group', 0))
        
        # 筛选器
        st.markdown("---")
        col1, col2 = st.columns([1, 3])
        
        with col1:
            risk_filter = st.multiselect(
                "风险等级筛选",
                ['高风险 🔴', '中风险 🟠', '中低风险 🟡', '正常 🟢'],
                default=['高风险 🔴', '中风险 🟠']
            )
            
            if 'anomaly_type' in result_df.columns:
                anomaly_type_filter = st.multiselect(
                    "异常类型筛选",
                    result_df['anomaly_type'].unique(),
                    default=list(result_df['anomaly_type'].unique())
                )
            else:
                anomaly_type_filter = []
        
        with col2:
            # 应用筛选
            if anomaly_type_filter:
                filtered_df = result_df[
                    result_df['risk_level'].isin(risk_filter) &
                    result_df['anomaly_type'].isin(anomaly_type_filter)
                ]
            else:
                filtered_df = result_df[result_df['risk_level'].isin(risk_filter)]
            
            # 显示结果 - 保留原始数据列 + 新增得分列
            st.markdown(f"**显示 {len(filtered_df)} 行数据（来自 {filtered_df['voucher_unique_id'].nunique()} 个凭证）**")
            
            # 确定显示列：原始数据列 + 得分列
            score_cols = ['cross_score', 'inner_score', 'total_score', 'anomaly_type', 'risk_level']
            available_score_cols = [c for c in score_cols if c in filtered_df.columns]
            
            # 优先显示关键原始列
            priority_cols = ['voucher_unique_id', 'first_level_subject', 'debit', 'credit', 'counter_subject', 'voucher_feature']
            available_priority_cols = [c for c in priority_cols if c in filtered_df.columns]
            
            display_cols = available_priority_cols + available_score_cols
            
            # 添加样式
            def highlight_risk(val):
                if '高风险' in str(val):
                    return 'color: red; font-weight: bold;'
                elif '中风险' in str(val):
                    return 'color: darkorange; font-weight: bold;'
                elif '中低风险' in str(val):
                    return 'color: gold; font-weight: bold;'
                return ''
            
            styled_df = filtered_df[display_cols].style.applymap(
                highlight_risk, subset=['risk_level'] if 'risk_level' in display_cols else []
            )
            
            st.dataframe(styled_df, use_container_width=True, height=400)
        
        # 导出按钮
        st.markdown("---")
        if len(filtered_df) > 0:
            st.markdown(get_download_link(filtered_df, f"异常凭证_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"), 
                       unsafe_allow_html=True)
        
        # 高风险凭证详情 - 按凭证展示
        st.markdown("#### 🔴 高风险凭证详情")
        high_risk = result_df[result_df['risk_level'].str.contains('高风险')]
        if len(high_risk) > 0:
            # 按凭证去重展示
            high_risk_vouchers = high_risk.groupby('voucher_unique_id').first()
            for voucher_id, row in high_risk_vouchers.head(10).iterrows():
                with st.expander(f"{voucher_id} - 得分: {row['total_score']:.2f}"):
                    st.markdown(f"""
                    - **会计分录特征**: {row.get('voucher_feature', 'N/A')}
                    - **主要群**: {row.get('primary_group', 'N/A')}
                    - **涉及群**: {row.get('involved_groups', row.get('involved_groups', 'N/A'))}
                    - **异常类型**: {row.get('anomaly_type', 'N/A')}
                    - **跨模块得分**: {row.get('cross_score', 0):.2f}
                    - **内部异常得分**: {row.get('inner_score', 0):.2f}
                    """)
        else:
            st.success("✅ 未发现高风险凭证")
    else:
        st.info('请先点击"开始分析"生成结果')

def show_statistics():
    """显示统计报告"""
    st.markdown("### 📈 统计报告")
    
    if st.session_state.anomaly_result is not None:
        result_df = st.session_state.anomaly_result
        
        # 风险得分分布
        col1, col2 = st.columns(2)
        
        with col1:
            fig = px.histogram(
                result_df,
                x='total_score',
                nbins=50,
                title='风险得分分布',
                labels={'total_score': '综合风险得分', 'count': '凭证数量'}
            )
            fig.add_vline(x=10, line_dash="dash", line_color="yellow", annotation_text="中低风险线")
            fig.add_vline(x=30, line_dash="dash", line_color="orange", annotation_text="中风险线")
            fig.add_vline(x=80, line_dash="dash", line_color="red", annotation_text="高风险线")
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            # 异常类型分布
            anomaly_type_dist = result_df['anomaly_type'].value_counts()
            fig = px.pie(
                values=anomaly_type_dist.values,
                names=anomaly_type_dist.index,
                title='异常类型分布'
            )
            st.plotly_chart(fig, use_container_width=True)
        
        # 各群异常统计
        st.markdown("#### 各业务群异常统计")
        group_stats = result_df.groupby('primary_group').agg({
            'total_score': ['mean', 'max', 'count'],
            'is_cross_group': 'sum'
        }).reset_index()
        group_stats.columns = ['业务群', '平均得分', '最高得分', '凭证数', '跨模块数']
        st.dataframe(group_stats, use_container_width=True)
        
    else:
        st.info('请先点击"开始分析"生成结果')

# ============ 主入口 ============
def main():
    init_session_state()
    sidebar()
    main_page()

if __name__ == "__main__":
    main()
