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
import math

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
    
    # 重要性水平预览（放在字段配置下方）
    st.markdown("---")
    st.markdown("### 💰 重要性水平配置")
    
    # 帕累托百分比数字输入
    col1, col2 = st.columns([1, 3])
    with col1:
        pareto_input = st.number_input(
            "帕累托累计百分比 (%)",
            min_value=30,
            max_value=99,
            value=int(st.session_state.get('pareto_percent', 0.95) * 100),
            step=1,
            help="输入30-99之间的整数"
        )
        pareto_percent = pareto_input / 100
        st.session_state.pareto_percent = pareto_percent
    with col2:
        st.caption(f"当前设置: {pareto_percent*100:.0f}%")
        st.info("提示：数值越小，剔除的凭证越多。95%表示只关注金额最大的前95%凭证。")
    
    # 显示重要性水平预览
    if st.session_state.column_mapping:
        show_materiality_preview_main(pareto_percent)

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
        st.markdown("### 🚀 分析控制")
        
        if st.button("开始分析", type="primary", use_container_width=True):
            if st.session_state.raw_data is not None:
                if not st.session_state.column_mapping:
                    st.warning("⚠️ 请先完成字段配置")
                else:
                    with st.spinner("正在分析中..."):
                        # 从 session_state 获取帕累托百分比
                        pareto_value = st.session_state.get('pareto_percent', 0.95)
                        run_analysis(pareto_value)
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

# ============ 重要性水平预览 ============
def get_materiality_preview_data(pareto_percent):
    """获取重要性水平预览数据"""
    if st.session_state.processed_data is None:
        return None
    
    # 如果还没有群聚类数据，先进行聚类
    if st.session_state.clustered_data is None:
        with st.spinner("正在计算群聚类..."):
            cluster_engine = ClusterEngine()
            clustered_df = cluster_engine.classify_all(
                st.session_state.processed_data, 
                voucher_col='voucher_unique_id'
            )
            st.session_state.clustered_data = clustered_df
    
    df = st.session_state.clustered_data
    
    # 计算重要性水平预览
    detector = AnomalyDetector()
    preview = detector.get_materiality_preview(
        df,
        group_col='primary_group',
        voucher_col='voucher_unique_id',
        amount_col='voucher_abs_amount',
        pareto_percent=pareto_percent
    )
    
    return preview

def show_materiality_preview_main(pareto_percent):
    """在主页面显示重要性水平预览"""
    preview = get_materiality_preview_data(pareto_percent)
    
    if preview is None:
        st.warning("请先完成字段配置")
        return
    
    # 显示总体统计
    st.markdown("#### 📊 总体统计")
    
    # 添加合计行
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("总凭证数", f"{preview['总凭证数']:,}")
    with col2:
        st.metric("剔除凭证数", f"{preview['总剔除凭证数']:,}")
    with col3:
        st.metric("总金额", f"{preview['总金额']:,.2f}")
    with col4:
        st.metric("剔除金额", f"{preview['总剔除金额']:,.2f}")
    
    # 显示占比
    col1, col2 = st.columns(2)
    with col1:
        st.metric("剔除凭证占比", f"{preview['总剔除占比']*100:.1f}%")
    with col2:
        st.metric("剔除金额占比", f"{preview['总剔除金额占比']*100:.1f}%")
    
    # 显示各群详情
    st.markdown("#### 📋 各群重要性水平详情")
    
    group_df = pd.DataFrame(preview['各群详情'])
    
    # 添加合计行
    total_row = {
        '业务群': '**合计**',
        '重要性水平': '-',
        '总凭证数': group_df['总凭证数'].sum(),
        '总金额': group_df['总金额'].sum(),
        '剔除凭证数': group_df['剔除凭证数'].sum(),
        '剔除占比': preview['总剔除占比'],
        '剔除金额': group_df['剔除金额'].sum(),
        '剔除金额占比': preview['总剔除金额占比'],
        '剔除原始金额': group_df['剔除原始金额'].sum(),
    }
    
    # 格式化数据
    group_df['重要性水平'] = group_df['重要性水平'].apply(lambda x: f"{x:,.2f}")
    group_df['总金额'] = group_df['总金额'].apply(lambda x: f"{x:,.2f}")
    group_df['剔除金额'] = group_df['剔除金额'].apply(lambda x: f"{x:,.2f}")
    group_df['剔除占比'] = group_df['剔除占比'].apply(lambda x: f"{x*100:.1f}%")
    group_df['剔除金额占比'] = group_df['剔除金额占比'].apply(lambda x: f"{x*100:.1f}%")
    
    st.dataframe(group_df, use_container_width=True, hide_index=True)
    
    # 显示合计信息
    st.markdown(f"""
    **合计**: 共 {preview['总凭证数']:,} 张凭证，剔除 {preview['总剔除凭证数']:,} 张 ({preview['总剔除占比']*100:.1f}%)，
    总金额 {preview['总金额']:,.2f}，剔除金额 {preview['总剔除金额']:,.2f} ({preview['总剔除金额占比']*100:.1f}%)
    """)
    
    # 说明
    st.info(f"""
    **说明**：
    - 帕累托累计百分比: {preview['帕累托百分比']*100:.0f}%
    - 重要性水平：该群前 {preview['帕累托百分比']*100:.0f}% 金额的凭证中，最后一张的金额
    - 剔除凭证：金额低于重要性水平的凭证，其α系数将小于1，降低异常得分
    """)

def show_materiality_preview(pareto_percent):
    """在侧边栏显示重要性水平预览（简化版）"""
    preview = get_materiality_preview_data(pareto_percent)
    
    if preview is None:
        st.warning("请先完成字段配置")
        return
    
    st.markdown("**预览摘要**")
    st.write(f"总凭证: {preview['总凭证数']:,}")
    st.write(f"剔除: {preview['总剔除凭证数']:,} ({preview['总剔除占比']*100:.1f}%)")
    st.write(f"详细预览请在「字段配置」页面查看")
    
    # 说明
    st.info(f"""
    **说明**：
    - 帕累托累计百分比: {preview['帕累托百分比']*100:.0f}%
    - 重要性水平：该群前 {preview['帕累托百分比']*100:.0f}% 金额的凭证中，最后一张的金额
    - 剔除凭证：金额低于重要性水平的凭证，其α系数将小于1，降低异常得分
    """)

# ============ 分析流程 ============
def run_analysis(pareto_percent=0.95):
    """运行完整分析流程（带重要性水平）"""
    df = st.session_state.raw_data
    mapping = st.session_state.column_mapping
    
    # Step 1: 数据预处理
    processor = DataProcessor(df, column_mapping=mapping)
    processed_df = processor.preprocess()
    st.session_state.processed_data = processed_df
    
    # Step 2: 群聚类（纯规则分类，不使用ML）
    cluster_engine = ClusterEngine()
    clustered_df = cluster_engine.classify_all(processed_df, voucher_col='voucher_unique_id')
    st.session_state.clustered_data = clustered_df
    
    # Step 3: 异常检测（使用新的算法，带重要性水平）
    detector = AnomalyDetector()
    
    # 获取原始列名，避免重复添加
    original_cols = list(mapping.values()) if mapping else []
    
    # 调用检测方法，传入重要性水平参数
    anomaly_result = detector.detect_anomalies(
        clustered_df,
        group_col='primary_group',
        feature_col='voucher_feature',
        voucher_col='voucher_unique_id',
        subject_col='first_level_subject',
        counter_col='counter_subject',
        credit_col='credit',
        involved_col='involved_groups',
        amount_col='voucher_abs_amount',
        pareto_percent=pareto_percent,
        original_cols=original_cols
    )
    
    st.session_state.anomaly_result = anomaly_result
    st.session_state.distance_matrix = detector.get_distance_matrix_dataframe()
    st.session_state.detector = detector
    st.session_state.pareto_percent = pareto_percent

# ============ 科目连接图 ============
import networkx as nx

def build_subject_graph(df, group_name):
    """
    构建指定模块内的科目连接图（有向图）
    
    逻辑：
    1. 只取贷方科目和它们的对方科目
    2. **按凭证号统计连接频次** - 使用凭证计数
    3. 构建有向图（贷方 -> 对方科目）
    4. 支持识别资金流向路径
    
    返回:
        G: NetworkX有向图
        edge_stats: 边统计信息
    """
    # 筛选该群的凭证
    group_df = df[df['primary_group'] == group_name].copy()
    
    if len(group_df) == 0:
        return None, None
    
    # 获取该群的所有凭证ID
    voucher_ids = group_df['voucher_unique_id'].unique()
    
    # 获取这些凭证的所有行
    group_df = df[df['voucher_unique_id'].isin(voucher_ids)].copy()
    
    # 只取贷方行（有贷方金额的行）
    credit_df = group_df[group_df['credit'] > 0].copy()
    
    if len(credit_df) == 0:
        return None, None
    
    # 创建有向图
    G = nx.DiGraph()
    
    # 统计连接频次 - 按凭证计数
    edge_vouchers = defaultdict(set)
    
    for _, row in credit_df.iterrows():
        voucher_id = row['voucher_unique_id']
        from_subject = row['first_level_subject']  # 贷方科目
        to_subjects = str(row['counter_subject']).split('、') if pd.notna(row['counter_subject']) else []
        
        if not from_subject or not to_subjects:
            continue
        
        for to_subject in to_subjects:
            if to_subject and to_subject.strip():
                to_subject = to_subject.strip()
                edge_vouchers[(from_subject, to_subject)].add(voucher_id)
                
                # 添加节点和边到图
                if not G.has_node(from_subject):
                    G.add_node(from_subject)
                if not G.has_node(to_subject):
                    G.add_node(to_subject)
                
                if G.has_edge(from_subject, to_subject):
                    G[from_subject][to_subject]['weight'] += 1
                    G[from_subject][to_subject]['vouchers'].add(voucher_id)
                else:
                    G.add_edge(from_subject, to_subject, weight=1, vouchers={voucher_id})
    
    # 边的统计信息
    edge_stats = {}
    for edge, vouchers in edge_vouchers.items():
        edge_stats[edge] = len(vouchers)
    
    return G, edge_stats


# ── 配色表（与前端 D3 版保持一致） ─────────────────────────────────────────
PALETTE = {
    "asset":     {"bg": "#E1F5EE", "border": "#1D9E75", "text": "#085041", "edge": "#1D9E75"},
    "liability": {"bg": "#FAECE7", "border": "#D85A30", "text": "#4A1B0C", "edge": "#D85A30"},
    "expense":   {"bg": "#FAEEDA", "border": "#BA7517", "text": "#412402", "edge": "#BA7517"},
    "cost":      {"bg": "#EEEDFE", "border": "#7F77DD", "text": "#26215C", "edge": "#7F77DD"},
    "revenue":   {"bg": "#D4EDDA", "border": "#28A745", "text": "#155724", "edge": "#28A745"},
    "default":   {"bg": "#F1EFE8", "border": "#888780", "text": "#2C2C2A", "edge": "#888780"},
}

SUBJECT_CATEGORY = {
    # 资产类 (asset)
    "银行存款": "asset", "库存现金": "asset", "其他货币资金": "asset",
    "原材料": "asset", "半成品": "asset", "产成品": "asset", "库存商品": "asset",
    "周转材料": "asset", "委托加工物资": "asset", "发出商品": "asset",
    "材料采购过渡": "asset", "在途物资": "asset", "材料成本差异": "asset",
    "交易性金融资产": "asset", "应收票据": "asset", "应收账款": "asset",
    "预付账款": "asset", "其他应收款": "asset", "应收利息": "asset", "应收股利": "asset",
    "固定资产": "asset", "累计折旧": "asset", "固定资产清理": "asset",
    "在建工程": "asset", "工程物资": "asset", "无形资产": "asset",
    "累计摊销": "asset", "长期待摊费用": "asset", "待处理财产损溢": "asset",
    "存货跌价准备": "asset", "固定资产减值准备": "asset", "无形资产减值准备": "asset",
    "长期股权投资": "asset", "投资性房地产": "asset", "使用权资产": "asset",
    "使用权资产累计折旧": "asset", "长期应收款": "asset",
    # 负债类 (liability)
    "应付账款": "liability", "应付票据": "liability", "其他应付款": "liability",
    "应付账款暂估": "liability", "暂估应付账款": "liability",
    "预收账款": "liability", "合同负债": "liability",
    "应付职工薪酬": "liability", "应付工资": "liability", "应付福利费": "liability",
    "应付社会保险费": "liability", "应付住房公积金": "liability",
    "应交税费": "liability", "应交增值税": "liability", "应交消费税": "liability",
    "应交所得税": "liability", "应交城市维护建设税": "liability",
    "应交教育费附加": "liability", "应交地方教育费附加": "liability",
    "未交增值税": "liability", "待抵扣进项税额": "liability", "待认证进项税额": "liability",
    "应付利息": "liability", "应付股利": "liability",
    "短期借款": "liability", "长期借款": "liability", "长期应付款": "liability",
    "预计负债": "liability", "递延收益": "liability", "租赁负债": "liability",
    # 费用类 (expense)
    "管理费用": "expense", "销售费用": "expense", "财务费用": "expense",
    "研发费用": "expense", "税金及附加": "expense",
    "业务招待费": "expense", "差旅费": "expense", "办公费": "expense",
    "折旧费": "expense", "摊销费": "expense", "维修费": "expense",
    "水电费": "expense", "通讯费": "expense", "培训费": "expense",
    "广告费": "expense", "运输费": "expense", "装卸费": "expense",
    "包装费": "expense", "展览费": "expense", "租赁费": "expense",
    "利息支出": "expense", "手续费": "expense", "汇兑损益": "expense",
    "资产减值损失": "expense", "信用减值损失": "expense",
    # 成本类 (cost)
    "主营业务成本": "cost", "其他业务成本": "cost", "其他业务支出": "cost",
    "生产成本": "cost", "制造费用": "cost", "劳务成本": "cost",
    "基本生产成本": "cost", "辅助生产成本": "cost", "废品损失": "cost", "停工损失": "cost",
    "开发支出": "cost",
    # 收入类 (revenue)
    "主营业务收入": "revenue", "其他业务收入": "revenue",
    "投资收益": "revenue", "公允价值变动损益": "revenue",
    "营业外收入": "revenue", "其他收益": "revenue",
    "补贴收入": "revenue", "租赁收入": "revenue",
}
 
 
def _cat(subject: str) -> dict:
    return PALETTE[SUBJECT_CATEGORY.get(subject, "default")]
 
 
def _node_width(text: str) -> int:
    """按中英文字符估算节点宽度"""
    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return chinese * 15 + (len(text) - chinese) * 8 + 36
 
 
def _bezier(x0, y0, x1, y1, n=60):
    """三次贝塞尔曲线，控制点向两侧偏移让曲线柔和"""
    dx, dy = x1 - x0, y1 - y0
    cx1, cy1 = x0 + dx * 0.4, y0 + dy * 0.15 + (20 if dy > 0 else -20)
    cx2, cy2 = x0 + dx * 0.6, y1 - dy * 0.15 - (20 if dy > 0 else -20)
    t = np.linspace(0, 1, n)
    xc = (1-t)**3*x0 + 3*(1-t)**2*t*cx1 + 3*(1-t)*t**2*cx2 + t**3*x1
    yc = (1-t)**3*y0 + 3*(1-t)**2*t*cy1 + 3*(1-t)*t**2*cy2 + t**3*y1
    return xc, yc
 
 
def draw_subject_graph(G, edge_stats, group_name):
    """
    绘制科目资金流向图（Plotly，适配 Streamlit）
 
    视觉特性
    --------
    - 节点颜色按科目类型分四色（资产/负债/费用/成本）
    - 边线宽度按凭证数对数缩放，直观反映流量大小
    - 凭证数 ≥ 5 的边显示数字徽章
    - 贝塞尔曲线边，带颜色匹配箭头
    - 自动分层布局（贷方 → 借方，从左到右）
    """
    if G is None or len(G.nodes()) == 0:
        return None
 
    NODE_H      = 38        # 节点高度
    NODE_R      = 8         # 圆角半径
    LEVEL_W     = 280       # 层间距
    V_SPACING   = 88        # 同层节点垂直间距
    LABEL_MIN_W = 12        # 显示数字徽章的最小凭证数
 
    # ── 1. 分层布局 ──────────────────────────────────────────────────────────
    levels: dict[str, int] = {}
    queue = [n for n in G.nodes() if G.in_degree(n) == 0]
    for n in queue:
        levels[n] = 0
    visited = set(queue)
    while queue:
        cur = queue.pop(0)
        for suc in G.successors(cur):
            if suc not in visited:
                levels[suc] = levels[cur] + 1
                visited.add(suc)
                queue.append(suc)
    for n in G.nodes():
        if n not in levels:
            levels[n] = max(levels.values(), default=0) + 1
 
    level_nodes: dict[int, list] = defaultdict(list)
    for n, lv in levels.items():
        level_nodes[lv].append(n)
 
    # ── 2. 节点坐标 ──────────────────────────────────────────────────────────
    pos: dict[str, tuple[float, float]] = {}
    for lv, nodes in level_nodes.items():
        nodes_sorted = sorted(nodes)
        total_h = (len(nodes_sorted) - 1) * V_SPACING
        x_center = lv * LEVEL_W + 150
        for i, node in enumerate(nodes_sorted):
            y = total_h / 2 - i * V_SPACING
            pos[node] = (x_center, y)
 
    all_x = [p[0] for p in pos.values()]
    all_y = [p[1] for p in pos.values()]
    x_min, x_max = min(all_x), max(all_x)
    y_min, y_max = min(all_y), max(all_y)
 
    # ── 3. 边的粗细：对数映射到 [1.5, 8] ───────────────────────────────────
    weights = [d.get("weight", 1) for _, _, d in G.edges(data=True)]
    w_max   = max(weights) if weights else 1
 
    def stroke_width(w):
        return 1.5 + (math.log1p(w) / math.log1p(w_max)) * 6.5
 
    # ── 4. 绘制 ──────────────────────────────────────────────────────────────
    edge_traces = []
    shapes      = []
    annotations = []
 
    # 边
    for u, v, d in G.edges(data=True):
        w    = d.get("weight", 1)
        c    = _cat(u)
        nw_u = _node_width(u)
        nw_v = _node_width(v)
        x0, y0 = pos[u]
        x1, y1 = pos[v]
 
        # 从节点右/左边缘出发
        sx = x0 + nw_u / 2 + 4  if x1 >= x0 else x0 - nw_u / 2 - 4
        ex = x1 - nw_v / 2 - 10 if x1 >= x0 else x1 + nw_v / 2 + 10
 
        xc, yc = _bezier(sx, y0, ex, y1)
 
        # 透明度：流量大的线更不透明
        alpha = 0.28 + 0.55 * (math.log1p(w) / math.log1p(w_max))
 
        edge_traces.append(go.Scatter(
            x=xc, y=yc, mode="lines",
            line=dict(color=c["edge"], width=stroke_width(w)),
            opacity=alpha,
            hoverinfo="text",
            text=f"<b>{u} → {v}</b><br>凭证数：{w}",
            showlegend=False,
        ))
 
        # 箭头注释（用曲线末段方向对齐）
        annotations.append(dict(
            ax=xc[-6], ay=yc[-6],
            x=xc[-2],  y=yc[-2],
            xref="x", yref="y", axref="x", ayref="y",
            showarrow=True,
            arrowhead=2, arrowsize=1.5, arrowwidth=2,
            arrowcolor=c["edge"],
        ))
 
        # 数字徽章（仅大流量边显示）
        if w >= LABEL_MIN_W:
            mid = len(xc) // 2
            annotations.append(dict(
                x=xc[mid], y=yc[mid] - 8,
                text=str(w),
                showarrow=False,
                font=dict(size=10, color="white", family="Arial Black"),
                bgcolor=c["edge"],
                borderpad=3,
                bordercolor="rgba(255,255,255,0.6)",
                borderwidth=1,
            ))
 
    # 节点（圆角矩形 path）
    for node in G.nodes():
        c  = _cat(node)
        nw = max(_node_width(node), 80)
        cx, cy = pos[node]
        x0, y0_r = cx - nw / 2,     cy - NODE_H / 2
        x1, y1_r = cx + nw / 2,     cy + NODE_H / 2
        r = NODE_R
 
        # 投影（浅色偏移矩形模拟阴影）
        shapes.append(dict(
            type="path",
            path=(f"M {x0+r+2} {y0_r+2} L {x1-r+2} {y0_r+2} "
                  f"Q {x1+2} {y0_r+2} {x1+2} {y0_r+r+2} "
                  f"L {x1+2} {y1_r-r+2} Q {x1+2} {y1_r+2} {x1-r+2} {y1_r+2} "
                  f"L {x0+r+2} {y1_r+2} Q {x0+2} {y1_r+2} {x0+2} {y1_r-r+2} "
                  f"L {x0+2} {y0_r+r+2} Q {x0+2} {y0_r+2} {x0+r+2} {y0_r+2} Z"),
            fillcolor=c["border"],
            line=dict(width=0),
            opacity=0.12,
        ))
 
        # 节点主体
        shapes.append(dict(
            type="path",
            path=(f"M {x0+r} {y0_r} L {x1-r} {y0_r} "
                  f"Q {x1} {y0_r} {x1} {y0_r+r} "
                  f"L {x1} {y1_r-r} Q {x1} {y1_r} {x1-r} {y1_r} "
                  f"L {x0+r} {y1_r} Q {x0} {y1_r} {x0} {y1_r-r} "
                  f"L {x0} {y0_r+r} Q {x0} {y0_r} {x0+r} {y0_r} Z"),
            fillcolor=c["bg"],
            line=dict(color=c["border"], width=1.5),
            opacity=0.97,
        ))
 
        # 文字
        annotations.append(dict(
            x=cx, y=cy,
            text=f"<b>{node}</b>",
            showarrow=False,
            font=dict(size=12, color=c["text"], family="Arial"),
            xanchor="center",
            yanchor="middle",
        ))
 
    # ── 5. 图例（右上角） ────────────────────────────────────────────────────
    LEGEND = [
        ("资产类",   "asset"),
        ("负债/应付类", "liability"),
        ("费用类",   "expense"),
        ("成本类",   "cost"),
    ]
    leg_x = x_max + 60
    leg_y = y_max
    for i, (label, cat_key) in enumerate(LEGEND):
        c  = PALETTE[cat_key]
        ly = leg_y - i * 26
        shapes.append(dict(
            type="rect",
            x0=leg_x, y0=ly - 9, x1=leg_x + 16, y1=ly + 9,
            fillcolor=c["bg"], line=dict(color=c["border"], width=1.5),
            xref="x", yref="y",
        ))
        annotations.append(dict(
            x=leg_x + 22, y=ly,
            text=label, showarrow=False,
            font=dict(size=11, color="#555"),
            xref="x", yref="y", xanchor="left", yanchor="middle",
        ))
 
    # ── 6. 组装 Figure ───────────────────────────────────────────────────────
    PAD_X, PAD_Y = 80, 70
    canvas_w = max(900, int(x_max - x_min) + 300)
    canvas_h = max(480, int(y_max - y_min) + 220)
 
    fig = go.Figure(data=edge_traces)
    fig.update_layout(
        title=dict(
            text=f"<b>{group_name}</b>  <sup>科目资金流向图 · {len(G.nodes())} 个科目，{len(G.edges())} 条连接</sup>",
            font=dict(size=15, color="#2C2C2A"),
            x=0.04, xanchor="left",
        ),
        showlegend=False,
        hovermode="closest",
        margin=dict(l=30, r=30, t=55, b=30),
        plot_bgcolor="white",
        paper_bgcolor="white",
        shapes=shapes,
        annotations=annotations,
        height=canvas_h,
        width=canvas_w,
        xaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            range=[x_min - PAD_X, x_max + PAD_X + 120],  # 右侧留图例空间
            fixedrange=False,
        ),
        yaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            range=[y_min - PAD_Y, y_max + PAD_Y],
            fixedrange=False,
        ),
        dragmode="pan",
        modebar=dict(
            orientation="h",
            bgcolor="rgba(255,255,255,0.85)",
            color="rgba(0,0,0,0.5)",
            activecolor="rgba(0,0,0,0.9)",
        ),
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
    """显示科目连接详情和可视化图"""
    st.markdown("### 🌐 科目连接详情")
    
    # 安全检查
    if st.session_state.get('clustered_data') is None:
        st.warning('⚠️ 数据未加载，请返回"群聚类结果"标签页或重新点击"开始分析"')
        return
    
    df = st.session_state.clustered_data
    
    # 选择业务群（包含所有群，包括其他群）
    all_groups = sorted(df['primary_group'].unique())
    groups = all_groups  # 不过滤其他群
    
    if len(groups) == 0:
        st.warning("没有可用的业务群数据")
        return
    
    # 使用session_state保存选择
    if 'selected_connection_group' not in st.session_state:
        st.session_state.selected_connection_group = groups[0] if groups else None
    
    # 创建列布局
    col1, col2 = st.columns([1, 3])
    
    with col1:
        selected_group = st.selectbox(
            "选择业务群",
            groups,
            index=groups.index(st.session_state.selected_connection_group) if st.session_state.selected_connection_group in groups else 0,
            key='connection_selectbox'
        )
    
    st.session_state.selected_connection_group = selected_group
    
    # 显示连接详情
    st.markdown(f"#### {selected_group} - 科目资金流向图")
    
    try:
        G, edge_stats = build_subject_graph(df, selected_group)
        
        if G is None or len(G.nodes()) == 0:
            st.info(f"**{selected_group}** 暂无连接数据（可能没有贷方科目或对方科目为空）")
        else:
            # 绘制连接图
            fig = draw_subject_graph(G, edge_stats, selected_group)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
            
            # 显示连接详情表格
            st.markdown(f"**统计**: 科目数 {len(G.nodes())} | 连接数 {len(G.edges())} | 总流量 {sum(edge_stats.values())}")
            
            edge_list = [{'贷方科目': e[0], '对方科目': e[1], '凭证数': c} 
                       for e, c in sorted(edge_stats.items(), key=lambda x: -x[1])]
            st.dataframe(pd.DataFrame(edge_list), use_container_width=True, height=300)
    except Exception as e:
        st.error(f"获取连接数据时出错: {str(e)}")
        import traceback
        st.code(traceback.format_exc())
    
    # 显示所有群的统计
    st.markdown("---")
    st.markdown("#### 各群连接汇总（所有业务群）")
    
    all_stats = []
    for group in all_groups:
        try:
            G, edge_stats = build_subject_graph(df, group)
            all_stats.append({
                '业务群': group,
                '节点数': len(G.nodes()) if G else 0,
                '连接数': len(G.edges()) if G else 0,
                '总流量': sum(edge_stats.values()) if edge_stats else 0
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
    """显示异常检测结果（优化版：显示全部凭证，中文列名）"""
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
        
        # 获取用户原始列名（上传的数据列）
        mapping = st.session_state.get('column_mapping', {})
        original_cols = list(mapping.values()) if mapping else []
        
        # 列名映射
        col_name_mapping = {
            'voucher_unique_id': '凭证唯一ID',
            'primary_group': '主要业务群',
            'involved_groups': '涉及业务群',
            'voucher_feature': '会计分录特征',
            '跨模块得分': '跨模块得分',
            '模块内得分': '模块内得分',
            'α系数': 'α系数',
            '综合得分': '综合得分',
            '重要性水平': '重要性水平',
            '凭证金额': '凭证金额',
            '异常类型': '异常类型',
            '风险等级': '风险等级'
        }
        
        # 风险等级筛选
        risk_filter = st.multiselect(
            "风险等级筛选",
            ['高风险 🔴', '中风险 🟠', '中低风险 🟡', '正常 🟢'],
            default=['高风险 🔴', '中风险 🟠', '中低风险 🟡', '正常 🟢']
        )
        
        # 应用筛选
        st.markdown("---")
        filtered_df = result_df[result_df['风险等级'].isin(risk_filter)]
        
        # 显示结果统计
        st.markdown(f"**显示 {filtered_df['voucher_unique_id'].nunique()} 个凭证 ({len(filtered_df)} 行数据)**")
        
        # 构建显示列：用户原始列 + 系统列
        all_available_cols = result_df.columns.tolist()
        display_cols = []
        
        # 1. 用户原始列
        for col in original_cols:
            if col in all_available_cols and col not in display_cols:
                display_cols.append(col)
        
        # 2. 系统生成的列
        system_cols = ['voucher_unique_id', 'voucher_feature', 'primary_group', 'involved_groups', 
                       '跨模块得分', '模块内得分', 'α系数', '综合得分', '重要性水平', '凭证金额',
                       '异常类型', '风险等级']
        for col in system_cols:
            if col in all_available_cols and col not in display_cols:
                display_cols.append(col)
        
        # 准备显示的数据框
        display_df = filtered_df[display_cols].copy()
        
        # 重命名列为中文
        rename_dict = {}
        for col in display_df.columns:
            if col in col_name_mapping:
                rename_dict[col] = col_name_mapping[col]
        
        if rename_dict:
            display_df = display_df.rename(columns=rename_dict)
        
        # 直接使用 st.dataframe 显示（不使用样式避免报错）
        st.dataframe(display_df, use_container_width=True, height=500)
        
        # 导出按钮
        st.markdown("---")
        if len(filtered_df) > 0:
            # 导出时使用中文列名
            export_df = display_df.copy()
            st.markdown(get_download_link(export_df, f"凭证异常分析_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"), 
                       unsafe_allow_html=True)
        
        # 高风险凭证详情
        st.markdown("#### 🔴 高风险凭证详情")
        high_risk = result_df[result_df['风险等级'].str.contains('高风险')]
        if len(high_risk) > 0:
            high_risk_vouchers = high_risk.groupby('voucher_unique_id').first()
            for voucher_id, row in high_risk_vouchers.head(10).iterrows():
                with st.expander(f"{voucher_id} - 综合得分: {row['综合得分']:.2f}"):
                    st.markdown(f"""
                    - **会计分录特征**: {row.get('voucher_feature', row.get('会计分录特征', 'N/A'))}
                    - **主要业务群**: {row.get('primary_group', row.get('主要业务群', 'N/A'))}
                    - **涉及业务群**: {row.get('involved_groups', row.get('涉及业务群', 'N/A'))}
                    - **异常类型**: {row.get('异常类型', 'N/A')}
                    - **跨模块得分**: {row.get('跨模块得分', 0):.2f}
                    - **模块内得分**: {row.get('模块内得分', 0):.2f}
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
            # 按凭证去重
            voucher_scores = result_df.groupby('voucher_unique_id')['综合得分'].first()
            fig = px.histogram(
                x=voucher_scores.values,
                nbins=50,
                title='风险得分分布（按凭证）',
                labels={'x': '综合风险得分', 'y': '凭证数量'}
            )
            fig.add_vline(x=10, line_dash="dash", line_color="yellow", annotation_text="中低风险线")
            fig.add_vline(x=30, line_dash="dash", line_color="orange", annotation_text="中风险线")
            fig.add_vline(x=80, line_dash="dash", line_color="red", annotation_text="高风险线")
            st.plotly_chart(fig, use_container_width=True)
        
        with col2:
            # 异常类型分布
            if '异常类型' in result_df.columns:
                anomaly_type_dist = result_df.groupby('voucher_unique_id')['异常类型'].first().value_counts()
                fig = px.pie(
                    values=anomaly_type_dist.values,
                    names=anomaly_type_dist.index,
                    title='异常类型分布（按凭证）'
                )
                st.plotly_chart(fig, use_container_width=True)
        
        # 各群异常统计
        st.markdown("#### 各业务群异常统计")
        
        # 按凭证去重统计
        voucher_df = result_df.groupby('voucher_unique_id').agg({
            'primary_group': 'first',
            '综合得分': 'first',
            'is_cross_group': 'first'
        }).reset_index()
        
        group_stats = voucher_df.groupby('primary_group').agg({
            '综合得分': ['mean', 'max', 'count'],
            'is_cross_group': 'sum'
        }).reset_index()
        group_stats.columns = ['业务群', '平均得分', '最高得分', '凭证数', '跨模块数']
        group_stats = group_stats.sort_values('凭证数', ascending=False)
        
        st.dataframe(group_stats, use_container_width=True)
        
        # 显示距离矩阵热力图（如果存在）
        if st.session_state.distance_matrix is not None:
            st.markdown("#### 群距离矩阵")
            matrix = st.session_state.distance_matrix
            fig = px.imshow(
                matrix.values,
                x=matrix.columns,
                y=matrix.index,
                color_continuous_scale='RdYlBu_r',
                title='业务群距离热力图（颜色越红表示距离越远）',
                labels=dict(color="距离D")
            )
            fig.update_layout(height=400)
            st.plotly_chart(fig, use_container_width=True)
        
    else:
        st.info('请先点击"开始分析"生成结果')

# ============ 主入口 ============
def main():
    init_session_state()
    sidebar()
    main_page()

if __name__ == "__main__":
    main()
