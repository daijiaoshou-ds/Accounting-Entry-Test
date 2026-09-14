# -*- coding: utf-8 -*-
"""
小工具模块（Web 集成）

导航层级：首页 > 小工具 > 5 个具体功能（均为可用页面）

5 个功能原为桌面版「基米工具箱」(已废弃) 的一部分，
已移植为 Streamlit 页面（other_tools/web/），复用原桌面版纯逻辑函数。

输入：上传文件 / 上传文件夹（Streamlit 原生目录上传，递归保留目录结构）
输出：一律通过浏览器下载（多个文件打包 zip），不往项目目录、也不往用户原文件夹写产物

接入方式：注册表 TOOLS_REGISTRY 的 page_module 指向 other_tools.web.<id>，
对应页面模块实现 `show_page(tool)` 即可。
"""
import streamlit as st

# ============ 小工具注册表（对应 other_tools/README.md） ============
TOOLS_REGISTRY = [
    {
        "id": "excel_extract",
        "icon": "🔢",
        "name": "Excel数据提取",
        "desc": "批量跨文件提取指定列，表头模糊匹配、自动映射，支持合并单元格与多 Sheet 筛选。",
        "features": [
            "上传文件 / 上传文件夹（递归）",
            "表头模糊匹配与自动映射",
            "合并单元格 / 多 Sheet 过滤",
            "结果 Excel / CSV 浏览器直下",
        ],
        "module_file": "other_tools/modules/column_extractor.py",
        "class_name": "ColumnExtractorModule",
        "page_module": "excel_extract",
    },
    {
        "id": "file_batch",
        "icon": "🗂️",
        "name": "文件批量整理",
        "desc": "生成文件清单 Excel，按清单批量移动、复制、重命名文件，自动避重与非法字符清理。",
        "features": [
            "上传文件夹 → 生成文件清单 Excel",
            "按清单批量移动 / 复制 / 重命名",
            "自动避重与非法字符清理",
            "整理结果打包下载，原文件夹不受影响",
        ],
        "module_file": "other_tools/modules/file_batch_tool.py",
        "class_name": "FileBatchToolModule",
        "page_module": "file_batch",
    },
    {
        "id": "xls_convert",
        "icon": "🔁",
        "name": "Excel格式互转",
        "desc": "xls ↔ xlsx、csv/html → xlsx 批量互转，格式统一，支持原样打包与文件夹递归。",
        "features": [
            "xls ↔ xlsx 双向转换",
            "csv / html → xlsx",
            "上传文件夹批量转换（保留层级）",
            "结果打包下载，不改动原文件",
        ],
        "module_file": "other_tools/modules/xls_to_xlsx.py",
        "class_name": "XLSToXLSXModule",
        "page_module": "xls_convert",
    },
    {
        "id": "pdf_indexer",
        "icon": "🔖",
        "name": "PDF索引号生成",
        "desc": "在 PDF 每页右上角打印红色索引（文件名+页码/总页数），兼容旋转页、裁剪页与窄页。",
        "features": [
            "上传 PDF 文件 / 整个文件夹",
            "索引内容：文件名 + 页码/总页数",
            "兼容旋转/裁剪/窄页",
            "处理后浏览器下载（支持打包）",
        ],
        "module_file": "other_tools/modules/pdf_indexer.py",
        "class_name": "PDFIndexerModule",
        "page_module": "pdf_indexer",
    },
    {
        "id": "pdf_merger",
        "icon": "📑",
        "name": "PDF合并/分拆/转换",
        "desc": "批量合并 PDF（含图像/Word/Excel 转 PDF）、按步长分拆，文件名自动唯一化。",
        "features": [
            "上传 PDF 文件 / 整个文件夹合并",
            "按步长分拆",
            "图像 / Word / Excel 转 PDF",
            "合并结果浏览器下载（分卷自动打包）",
        ],
        "module_file": "other_tools/modules/pdf_merger.py",
        "class_name": "PDFMergerModule",
        "page_module": "pdf_merger",
    },
]

# ============ 页面样式（与首页卡片风格保持一致） ============
_TOOL_CSS = """
<style>
    [data-testid="stHorizontalBlock"] {
        align-items: stretch !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        display: flex !important;
        flex-direction: column !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
        flex-grow: 1 !important;
        display: flex !important;
        flex-direction: column !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]
    > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:first-child {
        flex-grow: 1 !important;
        display: flex !important;
        flex-direction: column !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]
    > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:first-child > div,
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]
    > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:first-child > div > div,
    [data-testid="stMarkdownContainer"] {
        flex-grow: 1 !important;
        display: flex !important;
        flex-direction: column !important;
    }

    .subtitle {
        text-align: center;
        color: #666;
        font-size: 1.05rem;
        margin-bottom: 2rem;
    }

    .tool-card {
        background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
        border-radius: 18px;
        padding: 1.5rem;
        border: 1px solid #cbd5e1;
        display: flex;
        flex-direction: column;
        flex-grow: 1;
        transition: all 0.25s ease;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
    }
    .tool-card:hover {
        transform: translateY(-6px);
        box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
        border-color: #94a3b8;
    }
    .tool-card-soon {
        background: #ffffff;
        border-radius: 18px;
        padding: 1.5rem;
        border: 2px dashed #cbd5e1;
        display: flex;
        flex-direction: column;
        flex-grow: 1;
        justify-content: center;
        align-items: center;
        text-align: center;
        color: #94a3b8;
    }
    .tool-icon {
        font-size: 2.2rem;
        margin-bottom: 0.8rem;
        line-height: 1;
    }
    .tool-title {
        font-size: 1.25rem;
        font-weight: 700;
        color: #1e293b;
        margin-bottom: 0.6rem;
    }
    .tool-desc {
        color: #475569;
        font-size: 0.9rem;
        line-height: 1.6;
        margin-bottom: 1rem;
        flex-grow: 1;
    }
    .tool-features {
        list-style: none;
        padding: 0;
        margin: 0 0 1rem 0;
    }
    .tool-features li {
        padding: 0.3rem 0;
        color: #334155;
        font-size: 0.85rem;
        line-height: 1.5;
        display: flex;
        align-items: flex-start;
    }
    .tool-features li:before {
        content: "✓";
        color: #10b981;
        font-weight: bold;
        margin-right: 0.6rem;
        flex-shrink: 0;
    }

    [data-testid="stHorizontalBlock"] [data-testid="stColumn"] .stButton {
        width: 100% !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"] .stButton button {
        width: 100% !important;
        background: linear-gradient(135deg, #4a9eff 0%, #2f7de1 100%) !important;
        color: white !important;
        border: none !important;
        border-radius: 12px !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        padding: 0.6rem 1.1rem !important;
        box-shadow: 0 4px 14px rgba(47, 125, 225, 0.3) !important;
        transition: all 0.2s ease !important;
    }
    [data-testid="stHorizontalBlock"] [data-testid="stColumn"] .stButton button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(47, 125, 225, 0.4) !important;
        filter: brightness(1.05) !important;
    }

    .breadcrumb {
        color: #64748b;
        font-size: 0.85rem;
        margin-bottom: 0.5rem;
    }
    .tool-detail-banner {
        background: linear-gradient(135deg, #eef4ff 0%, #e2e8f0 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        text-align: center;
        border: 1px solid #cbd5e1;
        margin-bottom: 1.5rem;
    }
    .tool-detail-icon {
        font-size: 3.2rem;
        line-height: 1;
        margin-bottom: 0.8rem;
    }
    .tool-detail-title {
        font-size: 1.8rem;
        font-weight: 700;
        color: #1e293b;
        margin-bottom: 0.6rem;
    }
    .tool-detail-desc {
        color: #475569;
        font-size: 1rem;
        line-height: 1.7;
        max-width: 720px;
        margin: 0 auto;
    }
    .wip-badge {
        display: inline-block;
        background: #ffedd5;
        color: #c2410c;
        border: 1px solid #fdba74;
        border-radius: 999px;
        padding: 0.3rem 1.1rem;
        font-size: 0.85rem;
        font-weight: 600;
        margin-top: 1rem;
    }
</style>
"""


# ============ 导航辅助 ============
def _find_tool(tool_id):
    """按 id 查找工具，未找到返回 None"""
    for tool in TOOLS_REGISTRY:
        if tool["id"] == tool_id:
            return tool
    return None


def go_tools():
    """返回小工具二级目录页"""
    st.session_state.current_page = "tools"
    st.rerun()


def go_tool(tool_id):
    """进入某个小工具详情页"""
    st.session_state.current_tool = tool_id
    st.session_state.current_page = "tool_detail"
    st.rerun()


def _tool_sidebar():
    """小工具系列页面的侧边栏：返回首页 / 返回小工具目录"""
    with st.sidebar:
        if st.button("← 返回首页", use_container_width=True):
            st.session_state.current_page = "home"
            st.rerun()
        if st.button("← 返回小工具", use_container_width=True):
            go_tools()
        st.markdown("---")


# ============ 二级目录：小工具列表 ============
def show_tools_home():
    """小工具二级导航页：展示 5 个已集成的功能卡片"""
    _tool_sidebar()
    st.title("🧰 小工具")
    st.markdown(
        '<div class="subtitle">日常办公效率工具集 · 原「基米工具箱」桌面版功能，正在逐个移植为 Web 页面</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)

    # 5 个功能卡片（上 3 下 2）+ 1 个「后续打磨中」占位卡片
    row1 = st.columns(3)
    row2 = st.columns(3)
    placed = 0

    for tool in TOOLS_REGISTRY:
        col = row1[placed] if placed < 3 else row2[placed - 3]
        placed += 1
        with col:
            features_html = "".join(f"<li>{f}</li>" for f in tool["features"])
            st.markdown(f"""
            <div class="tool-card">
                <div class="tool-icon">{tool['icon']}</div>
                <div class="tool-title">{tool['name']}</div>
                <div class="tool-desc">{tool['desc']}</div>
                <ul class="tool-features">{features_html}</ul>
            </div>
            """, unsafe_allow_html=True)

            if st.button(f"打开{tool['name']} →", type="primary", use_container_width=True,
                         key=f"btn_tool_{tool['id']}"):
                go_tool(tool["id"])

    with row2[2]:
        st.markdown("""
        <div class="tool-card-soon">
            <div style="font-size:2.2rem;margin-bottom:0.6rem;">🚧</div>
            <div style="font-size:1.1rem;font-weight:600;color:#64748b;">更多工具打磨中…</div>
            <div style="font-size:0.85rem;margin-top:0.4rem;color:#94a3b8;">
                其余桌面版功能将在此陆续上线
            </div>
        </div>
        """, unsafe_allow_html=True)


# ============ 工具详情页（分发到真实 Web 功能页） ============
def show_tool_detail():
    """工具详情页：分发到 other_tools.web.<page_module>.show_page(tool)"""
    _tool_sidebar()

    tool_id = st.session_state.get("current_tool")
    tool = _find_tool(tool_id)
    if tool is None:
        st.markdown(_TOOL_CSS, unsafe_allow_html=True)
        st.warning("未找到对应的小工具，已为你返回小工具列表")
        go_tools()
        return

    page_module = tool.get("page_module")
    if page_module:
        try:
            import importlib
            page_mod = importlib.import_module(f"other_tools.web.{page_module}")
            page_mod.show_page(tool)
            return
        except Exception as e:  # noqa: BLE001
            st.markdown(_TOOL_CSS, unsafe_allow_html=True)
            st.error(f"「{tool['name']}」页面加载失败：{e}")
            st.caption("可能是缺少依赖库。请检查控制台日志，或联系开发者。")

    # ---- 兜底占位（未接入页面的工具） ----
    st.markdown(_TOOL_CSS, unsafe_allow_html=True)
    st.markdown(f'<p class="breadcrumb">🏠 首页 / 🧰 小工具 / {tool["name"]}</p>',
                unsafe_allow_html=True)

    features_html = "".join(f"<li>{f}</li>" for f in tool["features"])
    st.markdown(f"""
    <div class="tool-detail-banner">
        <div class="tool-detail-icon">{tool['icon']}</div>
        <div class="tool-detail-title">{tool['name']}</div>
        <div class="tool-detail-desc">{tool['desc']}</div>
        <div><span class="wip-badge">🚧 功能打磨中</span></div>
    </div>
    """, unsafe_allow_html=True)

    st.info("该功能正在移植为 Web 页面，将在后续版本开放。")

    st.markdown("**📋 功能预览**")
    st.markdown(
        "<ul style='column-count:2;column-gap:2rem;line-height:2;color:#334155;'>"
        + features_html + "</ul>",
        unsafe_allow_html=True,
    )