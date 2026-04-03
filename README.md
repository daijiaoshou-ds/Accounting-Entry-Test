# 🏦 会计分析工具箱

一站式会计数据分析平台，集成**会计分录异常检测**与**对方科目分析**两大核心功能，帮助财务人员快速识别异常凭证、解析复杂分录结构。

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-red.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

---

## ✨ 功能特性

### 📊 会计分录异常检测
基于业务群聚类和距离计算的异常分录检测系统

| 特性 | 描述 |
|------|------|
| **9大业务群自动分类** | 采购、销售、薪酬、生产、研发、资产、资金、税务、其他 |
| **跨群距离矩阵计算** | 量化业务群之间的关联程度，识别跨模块异常 |
| **异常风险评分** | 综合跨模块得分、模块内得分和重要性水平 |
| **科目资金流向可视化** | 交互式网络图展示科目间的资金流向 |
| **重要性水平配置** | 帕累托分析剔除小额凭证，聚焦重点 |

### 🔄 对方科目分析
基于穷举算法的多借多贷分录对方科目解析工具

| 特性 | 描述 |
|------|------|
| **5类分录结构识别** | 1借1贷、1借m贷、多借多贷、全借全贷、特殊分录 |
| **多借多贷穷举计算** | 智能剪枝算法，高效计算所有可行组合 |
| **奥卡姆得分排序** | 基于简单性原则选择最优方案 |
| **在线方案预览** | Web端直接查看和选择方案 |
| **批量导出** | 支持Excel格式结果下载 |

---

## 🚀 快速开始

### 环境要求
- Python 3.10+
- 推荐使用虚拟环境

### 安装依赖

```bash
# 克隆项目
git clone <repository-url>
cd 会计分录测试

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 启动应用

```bash
streamlit run app.py
```

或双击 `start.bat`（Windows）

应用将在浏览器中自动打开，默认地址：`http://localhost:8501`

---

## 📖 使用指南

### 首页导航
启动后进入首页，点击对应模块卡片进入功能页面。

### 会计分录异常检测

1. **数据上传**：在左侧边栏上传Excel/CSV格式的会计凭证数据
2. **字段配置**：映射数据列到系统字段（日期、凭证号、科目、金额等）
3. **重要性水平**：设置帕累托百分比，剔除不重要的凭证
4. **执行分析**：点击"开始分析"按钮
5. **查看结果**：
   - 📊 数据概览：查看凭证分布和统计
   - 📦 群聚类结果：查看8大业务群分类
   - 🌐 科目连接图：可视化科目资金流向
   - 🌡️ 群距离矩阵：查看业务群关联度
   - ⚠️ 异常检测：查看高风险凭证列表
   - 📈 统计报告：导出分析报告

### 对方科目分析

1. **数据上传**：上传包含借方、贷方金额的凭证数据
2. **字段配置**：设置科目列、金额列等映射
3. **执行分析**：系统自动识别分录结构
4. **方案选择**：对于多借多贷分录，查看穷举计算结果
5. **导出结果**：下载包含对方科目的完整数据

---

## 📁 项目结构

```
├── app.py                      # 主入口：统一导航框架
├── README.md                   # 项目简介
├── PROJECT_STRUCTURE.md        # 详细项目结构说明
├── requirements.txt            # 依赖包列表
├── start.bat                   # Windows启动脚本
│
├── pages/                      # Streamlit页面模块
│   ├── __init__.py
│   └── anomaly_test.py         # 会计分录异常检测页面
│
├── src/                        # 会计分录异常检测核心代码
│   └── accounting_anomaly/
│       ├── __init__.py
│       ├── data_processor.py   # 数据预处理
│       ├── cluster_engine.py   # 群聚类引擎
│       ├── anomaly_detector.py # 异常检测器
│       ├── ml_classifier.py    # ML分类器
│       ├── utils.py            # 工具函数
│       └── theory.md           # 算法理论文档
│
├── contra_analyzer/            # 对方科目分析模块
│   ├── __init__.py
│   ├── core.py                 # 核心处理器
│   ├── algorithm.py            # 穷举算法
│   ├── occams_razor.py         # 奥卡姆得分
│   ├── memory_web.py           # Web版简化记忆
│   ├── ui_streamlit.py         # Streamlit界面
│   └── theory.md               # 算法理论文档
│
├── data/                       # 数据文件目录
├── docs/                       # 项目文档
└── venv/                       # Python虚拟环境
```

详细结构参见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| **Web框架** | Streamlit |
| **数据处理** | Pandas, NumPy |
| **可视化** | Plotly, NetworkX |
| **机器学习** | scikit-learn (KMeans) |
| **图算法** | NetworkX |
| **算法** | 穷举搜索 + 启发式剪枝 |

---

## 📝 数据格式要求

### 会计分录异常检测

| 必需列 | 说明 | 示例 |
|--------|------|------|
| 制单日期/记账日期 | 凭证日期 | 2024-01-15 |
| 凭证编号 | 凭证号 | 记-001 |
| 一级科目/科目名称 | 会计科目 | 银行存款 |
| 借方金额 | 借方发生额 | 10000.00 |
| 贷方金额 | 贷方发生额 | 0.00 |

### 对方科目分析

| 必需列 | 说明 |
|--------|------|
| 科目列 | 会计科目名称 |
| 借方金额 | 借方发生额 |
| 贷方金额 | 贷方发生额 |

---

## 📚 文档

- [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) - 项目结构详细说明
- [src/accounting_anomaly/theory.md](src/accounting_anomaly/theory.md) - 异常检测算法理论
- [contra_analyzer/theory.md](contra_analyzer/theory.md) - 对方科目分析算法理论

---

## 📄 许可证

MIT License

---

