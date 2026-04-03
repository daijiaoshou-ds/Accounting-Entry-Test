# 项目结构说明

## 目录结构

```
project_root/
│
├── app.py                      # 主入口：统一导航框架
├── README.md                   # 项目简介
├── PROJECT_STRUCTURE.md        # 本文件：项目结构说明
├── requirements.txt            # 依赖包列表
├── start.bat                   # Windows启动脚本
│
├── pages/                      # Streamlit页面模块
│   ├── __init__.py
│   └── anomaly_test.py         # 会计分录测试页面（原app.py内容）
│
├── src/                        # 会计分录测试核心代码
│   └── accounting_anomaly/
│       ├── __init__.py
│       ├── data_processor.py   # 数据预处理
│       ├── cluster_engine.py   # 群聚类引擎
│       ├── anomaly_detector.py # 异常检测器
│       ├── ml_classifier.py    # ML分类器
│       └── utils.py            # 工具函数
│
├── contra_analyzer/            # 对方科目分析模块
│   ├── __init__.py
│   ├── core.py                 # 核心处理器（ContraProcessor）
│   ├── algorithm.py            # 穷举算法（ExhaustiveSolver）
│   ├── occams_razor.py         # 奥卡姆得分（OccamsRazor）
│   ├── memory_web.py           # Web版简化记忆（无本地存储）
│   ├── ui_streamlit.py         # Streamlit界面
│   └── theory.md               # 算法理论文档
│
├── tests/                      # 测试代码
│
├── docs/                       # 项目文档
│
└── venv/                       # Python虚拟环境
```

## 模块说明

### 1. 会计分录测试 (Anomaly Detection)

**功能：** 基于业务群聚类和距离计算的异常分录检测

**核心流程：**
1. 数据上传与字段配置
2. 群聚类分析（8大业务群）
3. 群距离矩阵计算
4. 异常风险评分
5. 科目资金流向可视化

**主要文件：**
- `pages/anomaly_test.py` - Streamlit页面
- `src/accounting_anomaly/` - 核心算法

### 2. 对方科目分析 (Contra Analysis)

**功能：** 基于穷举算法的多借多贷分录对方科目解析

**核心流程：**
1. 数据上传与字段配置
2. 漏斗分流（5类分录结构）
3. 多借多贷穷举计算
4. 奥卡姆得分排序
5. 方案预览与选择
6. 结果导出

**主要文件：**
- `contra_analyzer/ui_streamlit.py` - Streamlit界面
- `contra_analyzer/core.py` - 核心处理
- `contra_analyzer/algorithm.py` - 穷举算法

## 开发指南

### 添加新功能模块

1. 在 `pages/` 下创建新的页面文件
2. 在 `src/` 或根目录下创建对应的核心模块目录
3. 在 `app.py` 的 `show_home()` 中添加导航卡片
4. 在 `main()` 中添加路由

### 导入注意事项

- 主入口使用绝对导入：`from pages.xxx import ...`
- 模块内部使用相对导入：`from .xxx import ...`
- Streamlit页面使用session_state保持状态

## 运行方式

```bash
# 启动应用
streamlit run app.py

# 或直接双击
start.bat
```
