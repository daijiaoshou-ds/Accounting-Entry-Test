"""
一站式会计分析工具 - 统一入口
集成：会计分录测试 + 对方科目分析
"""
import streamlit as st

# ============ 页面配置 ============
# 根据当前页面动态调整布局
if 'current_page' not in st.session_state:
    st.session_state.current_page = "home"

# 首页使用居中布局，功能页使用宽布局
layout_mode = "centered" if st.session_state.current_page == "home" else "wide"

st.set_page_config(
    page_title="会计分析工具箱",
    page_icon="🏦",
    layout=layout_mode,
    initial_sidebar_state="collapsed" if st.session_state.current_page == "home" else "expanded"
)

# ============ 自定义样式 ============
# 只在首页应用隐藏侧边栏的样式
if st.session_state.current_page == "home":
    st.markdown("""
    <style>
        /* 首页：完全隐藏侧边栏 */
        [data-testid="stSidebar"] {
            display: none !important;
        }
        [data-testid="stAppViewContainer"] {
            margin-left: 0 !important;
        }
        
        .main-header {
            font-size: 2.2rem;
            font-weight: bold;
            color: #1f77b4;
            text-align: center;
            margin-bottom: 1rem;
        }
        
        .subtitle {
            text-align: center;
            color: #666;
            font-size: 1.1rem;
            margin-bottom: 2rem;
        }
        
        .module-card {
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e7f1 100%);
            border-radius: 16px;
            padding: 2rem;
            border: 1px solid #d1d5db;
            height: 100%;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        
        .module-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 24px rgba(0,0,0,0.1);
        }
        
        .module-icon {
            font-size: 2.5rem;
            margin-bottom: 1rem;
        }
        
        .module-title {
            font-size: 1.4rem;
            font-weight: bold;
            color: #1f2937;
            margin-bottom: 0.75rem;
        }
        
        .module-desc {
            color: #4b5563;
            font-size: 0.95rem;
            line-height: 1.6;
            margin-bottom: 1.5rem;
        }
        
        .feature-list {
            list-style: none;
            padding: 0;
            margin: 0 0 1.5rem 0;
        }
        
        .feature-list li {
            padding: 0.35rem 0;
            color: #374151;
            font-size: 0.9rem;
        }
        
        .feature-list li:before {
            content: "✓ ";
            color: #10b981;
            font-weight: bold;
            margin-right: 0.5rem;
        }
    </style>
    """, unsafe_allow_html=True)

# ============ Session State ============
def init_session():
    """初始化session state"""
    if 'current_page' not in st.session_state:
        st.session_state.current_page = "home"

def go_to_page(page_name):
    """跳转到指定页面"""
    st.session_state.current_page = page_name
    st.rerun()

def go_home():
    """返回首页"""
    st.session_state.current_page = "home"
    st.rerun()

# ============ 首页 ============
def show_home():
    """显示首页导航"""
    st.markdown('<div class="main-header">🏦 会计分析工具箱</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">一站式会计数据分析平台 · 集成分录异常检测与对方科目分析</div>', unsafe_allow_html=True)
    
    # 功能模块卡片
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("""
        <div class="module-card">
            <div class="module-icon">📊</div>
            <div class="module-title">会计分录测试</div>
            <div class="module-desc">
                基于业务群聚类和距离计算的异常分录检测系统，智能识别跨模块异常凭证。
            </div>
            <ul class="feature-list">
                <li>8大业务群自动分类</li>
                <li>跨群距离矩阵计算</li>
                <li>异常风险评分</li>
                <li>科目资金流向可视化</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("进入会计分录测试 →", type="primary", use_container_width=True, key="btn_anomaly"):
            go_to_page("anomaly")
    
    with col2:
        st.markdown("""
        <div class="module-card">
            <div class="module-icon">🔄</div>
            <div class="module-title">对方科目分析</div>
            <div class="module-desc">
                基于穷举算法的多借多贷分录对方科目解析工具，自动计算最优组合方案。
            </div>
            <ul class="feature-list">
                <li>5类分录结构识别</li>
                <li>多借多贷穷举计算</li>
                <li>奥卡姆得分排序</li>
                <li>方案预览与导出</li>
            </ul>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("进入对方科目分析 →", type="primary", use_container_width=True, key="btn_contra"):
            go_to_page("contra")

# ============ 返回按钮（在侧边栏顶部） ============
def show_back_button_in_sidebar():
    """在侧边栏显示返回首页按钮"""
    with st.sidebar:
        if st.button("← 返回首页", use_container_width=True):
            go_home()
        st.markdown("---")

# ============ 会计分录测试模块 ============
def show_anomaly_test():
    """显示会计分录测试模块"""
    show_back_button_in_sidebar()
    from pages.anomaly_test import show_anomaly_test as _show_anomaly
    _show_anomaly()

# ============ 对方科目分析模块 ============
def show_contra_analyzer():
    """显示对方科目分析模块"""
    show_back_button_in_sidebar()
    from contra_analyzer.ui_streamlit import show_contra_analyzer as _show_contra
    _show_contra()

# ============ 主入口 ============
def main():
    """主函数"""
    init_session()
    
    # 根据当前页面显示内容
    page = st.session_state.current_page
    
    if page == "home":
        show_home()
    elif page == "anomaly":
        show_anomaly_test()
    elif page == "contra":
        show_contra_analyzer()

if __name__ == "__main__":
    main()
