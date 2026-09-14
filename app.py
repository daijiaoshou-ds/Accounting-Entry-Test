"""
一站式会计分析工具 - 统一入口
集成：会计分录测试 + 对方科目分析 + 序时账清洗 + 银行流水匹配 + 小工具
"""
import streamlit as st

# ============ 页面配置 ============
# 根据当前页面动态调整布局
if 'current_page' not in st.session_state:
    st.session_state.current_page = "home"

# 统一使用宽布局（首页原为居中布局导致内容缩在中间、四周留白过多）
layout_mode = "wide"

st.set_page_config(
    page_title="会计分录分析工具箱",
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

        /* 左侧导航标题 */
        .home-nav-title {
            font-size: 0.8rem;
            color: #94a3b8;
            letter-spacing: 0.12em;
            margin-bottom: 0.6rem;
            padding-left: 0.25rem;
        }

        /* 右侧详情面板 */
        .home-panel {
            background: linear-gradient(135deg, #f8fafc 0%, #eef2f7 100%);
            border: 1px solid #e2e8f0;
            border-radius: 22px;
            padding: 2.2rem 2.6rem;
            box-shadow: 0 8px 24px -8px rgba(15, 23, 42, 0.08);
        }

        .home-panel-icon {
            font-size: 3rem;
            line-height: 1;
            margin-bottom: 1rem;
        }

        .home-panel-title {
            font-size: 1.8rem;
            font-weight: 700;
            color: #1e293b;
            margin-bottom: 0.8rem;
        }

        .home-panel-desc {
            color: #475569;
            font-size: 1.02rem;
            line-height: 1.8;
            margin-bottom: 1.5rem;
        }

        .home-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
        }

        .home-chip {
            background: #dbeafe;
            color: #1d4ed8;
            border-radius: 999px;
            padding: 0.32rem 0.9rem;
            font-size: 0.85rem;
            font-weight: 500;
        }

        /* 首页按钮圆角统一 */
        [data-testid="stHorizontalBlock"] [data-testid="stColumn"] .stButton button {
            border-radius: 12px;
            font-weight: 600;
        }
    </style>
    """, unsafe_allow_html=True)

# ============ Session State ============
def init_session():
    """初始化session state"""
    if 'current_page' not in st.session_state:
        st.session_state.current_page = "home"
    if 'current_tool' not in st.session_state:
        st.session_state.current_tool = None
    if 'selected_module' not in st.session_state:
        st.session_state.selected_module = "anomaly"

def go_to_page(page_name):
    """跳转到指定页面"""
    st.session_state.current_page = page_name
    st.rerun()

def go_home():
    """返回首页"""
    st.session_state.current_page = "home"
    st.rerun()

# ============ 一级功能模块注册表 ============
HOME_MODULES = [
    {
        "page": "anomaly",
        "icon": "📊",
        "title": "会计分录测试",
        "desc": "基于业务群聚类和距离计算的异常分录检测系统，智能识别跨模块异常凭证。",
        "chips": ["8大业务群自动分类", "跨群距离矩阵计算", "异常风险评分", "科目资金流向可视化"],
    },
    {
        "page": "contra",
        "icon": "🔄",
        "title": "对方科目分析",
        "desc": "基于穷举算法的多借多贷分录对方科目解析工具，自动计算最优组合方案。",
        "chips": ["5类分录结构识别", "多借多贷穷举计算", "奥卡姆得分排序", "方案预览与导出"],
    },
    {
        "page": "summary",
        "icon": "🧹",
        "title": "序时账清洗",
        "desc": "基于PMI相关性矩阵和关键词偏置的凭证业务自动分类系统，智能归集到14个标准业务桶。",
        "chips": ["PMI科目相关性矩阵", "金额权重+清晰度加权", "AC自动机关键词匹配", "14大业务桶自动分类"],
    },
    {
        "page": "bankmatch",
        "icon": "💱",
        "title": "银行流水匹配",
        "desc": "基于明细科目配对与多算法匹配引擎的序时账-银行流水核对工具，定位未达账项与月度差异。",
        "chips": ["字段自动识别与映射", "科目-流水智能配对", "GL/Bank多级匹配引擎", "月度差异与结果导出"],
    },
    {
        "page": "tools",
        "icon": "🧰",
        "title": "小工具",
        "desc": "日常办公效率工具集：Excel 数据提取、文件批量整理、格式互转、PDF 索引与合并拆分。",
        "chips": ["Excel数据提取", "文件批量整理", "Excel格式互转", "PDF索引/合并/分拆/转换"],
    },
]

# ============ 首页 ============
def show_home():
    """显示首页：双栏仪表盘（左侧功能导航 + 右侧详情面板）"""
    st.markdown('<div class="main-header">🏦 会计分析工具箱</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">一站式会计数据分析平台 · 分录测试 | 对方科目 | 序时账清洗 | 银行流水匹配 | 小工具</div>', unsafe_allow_html=True)

    selected = st.session_state.get("selected_module", "anomaly")

    nav_col, detail_col = st.columns([1, 2.3], gap="large")

    # ---- 左侧：功能导航 ----
    with nav_col:
        st.markdown('<div class="home-nav-title">功能导航</div>', unsafe_allow_html=True)
        for mod in HOME_MODULES:
            is_active = (mod["page"] == selected)
            label = f"{mod['icon']}  {mod['title']}"
            if st.button(label,
                         use_container_width=True,
                         type="primary" if is_active else "secondary",
                         key=f"nav_{mod['page']}"):
                st.session_state.selected_module = mod["page"]
                st.rerun()

    # ---- 右侧：选中模块的详情面板 ----
    with detail_col:
        mod = next(m for m in HOME_MODULES if m["page"] == selected)
        chips_html = "".join(f'<span class="home-chip">{c}</span>' for c in mod["chips"])
        st.markdown(f"""
        <div class="home-panel">
            <div class="home-panel-icon">{mod['icon']}</div>
            <div class="home-panel-title">{mod['title']}</div>
            <div class="home-panel-desc">{mod['desc']}</div>
            <div class="home-chips">{chips_html}</div>
        </div>
        """, unsafe_allow_html=True)

        if st.button(f"🚀 进入{mod['title']} →", type="primary", use_container_width=True,
                     key=f"enter_{mod['page']}"):
            go_to_page(mod["page"])

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

# ============ 摘要清洗2.0模块 ============
def show_summary_cleaner():
    """显示摘要清洗2.0模块"""
    show_back_button_in_sidebar()
    from summary_cleaner.v2.ui import show_summary_cleaner as _show_summary
    _show_summary()

# ============ 银行流水匹配模块 ============
def show_bank_matcher():
    """显示银行流水匹配模块"""
    show_back_button_in_sidebar()
    from bank_statement_matcher.app import show_bank_matcher as _show_bank
    _show_bank()

# ============ 小工具模块 ============
def show_tools_home():
    """显示小工具二级导航页"""
    from other_tools.ui import show_tools_home as _show_tools
    _show_tools()

def show_tool_detail():
    """显示小工具详情页"""
    from other_tools.ui import show_tool_detail as _show_tool_detail
    _show_tool_detail()

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
    elif page == "summary":
        show_summary_cleaner()
    elif page == "bankmatch":
        show_bank_matcher()
    elif page == "tools":
        show_tools_home()
    elif page == "tool_detail":
        show_tool_detail()

if __name__ == "__main__":
    main()