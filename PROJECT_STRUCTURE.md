# 项目结构说明

本文档详细说明项目的目录结构、模块职责和开发规范。

---

## 📁 目录结构

```
project_root/
│
├── app.py                      # 主入口：统一导航框架
├── README.md                   # 项目简介与使用说明
├── PROJECT_STRUCTURE.md        # 本文件：项目结构详细说明
├── requirements.txt            # Python依赖包列表
├── start.bat                   # Windows启动脚本
├── .gitignore                  # Git忽略配置
│
├── pages/                      # Streamlit页面模块
│   ├── __init__.py
│   └── anomaly_test.py         # 会计分录异常检测页面
│                               # - 包含完整的Streamlit UI代码
│                               # - 数据上传、字段配置、结果展示
│                               # - 可视化图表渲染
│
├── src/                        # 会计分录异常检测核心代码
│   └── accounting_anomaly/     # 异常检测模块包
│       ├── __init__.py         # 包初始化，导出主要类
│       ├── data_processor.py   # 数据预处理
│       │                       # - 字段自动识别
│       │                       # - 数据清洗和标准化
│       │                       # - 凭证唯一标识生成
│       ├── cluster_engine.py   # 群聚类引擎
│       │                       # - 8大业务群规则分类
│       │                       # - 特征向量生成
│       ├── anomaly_detector.py # 异常检测器
│       │                       # - 群距离矩阵计算
│       │                       # - 异常风险评分
│       │                       # - 重要性水平处理
│       ├── ml_classifier.py    # ML分类器
│       │                       # - KMeans聚类（备用）
│       │                       # - 主成分分析
│       ├── utils.py            # 工具函数
│       │                       # - 通用数据处理函数
│       │                       # - 辅助方法
│       └── theory.md           # 异常检测算法理论文档
│
├── contra_analyzer/            # 对方科目分析模块
│   ├── __init__.py             # 包初始化
│   ├── core.py                 # 核心处理器 (ContraProcessor)
│   │                           # - 数据加载和预处理
│   │                           # - 分录结构识别
│   │                           # - 结果组装
│   ├── algorithm.py            # 穷举算法 (ExhaustiveSolver)
   │                           # - 多借多贷组合生成
│   │                           # - 智能剪枝策略
│   │                           # - 约束求解
│   ├── occams_razor.py         # 奥卡姆评分 (OccamsRazor)
│   │                           # - 简单性评分
│   │                           # - 方案排序
│   ├── memory_web.py           # Web版简化记忆
│   │                           # - 无本地存储的状态管理
│   ├── ui_streamlit.py         # Streamlit界面
│   │                           # - 页面布局和组件
│   │                           # - 用户交互处理
│   └── theory.md               # 对方科目分析算法理论文档
│
├── data/                       # 数据文件目录（用户数据）
│                               # - 不上传到版本控制
│                               # - 运行时生成
│
├── docs/                       # 项目文档
│                               # - 使用说明
│                               # - API文档
│                               # - 设计文档
│
├── streamlit/                  # Streamlit配置
│                               # - 主题配置
│                               # - 自定义组件
│
├── venv/                       # Python虚拟环境
│                               # - 不上传到版本控制
│
└── __pycache__/                # Python缓存文件
                                # - 不上传到版本控制
```

---

## 🔧 模块详细说明

### 1. 会计分录异常检测 (Anomaly Detection)

**文件位置**：`pages/anomaly_test.py`, `src/accounting_anomaly/`

**功能描述**：
基于业务群聚类和距离计算的异常分录检测系统，通过分析凭证的科目组合和金额特征，识别偏离正常业务模式的异常凭证。

**核心算法**：
1. **业务群分类**：基于会计科目规则将凭证分为8大业务群
2. **特征提取**：生成凭证的业务群特征向量
3. **距离计算**：计算凭证与所属群的偏离程度（模块内距离）
4. **跨群分析**：计算凭证涉及业务群之间的关联度（跨群距离）
5. **风险评分**：综合各项得分计算异常风险分数

**主要类**：
- `DataProcessor`：数据预处理和字段映射
- `ClusterEngine`：业务群分类引擎
- `AnomalyDetector`：异常检测核心
- `MLClassifier`：机器学习分类器（备用）

**8大业务群**：
| 业务群 | 核心科目 | 描述 |
|--------|----------|------|
| 采购 | 应付账款、原材料 | 采购付款、材料入库 |
| 销售 | 应收账款、主营业务收入 | 销售收款、收入确认 |
| 薪酬 | 应付职工薪酬 | 工资计提与发放 |
| 生产 | 生产成本、制造费用 | 生产耗用、成本结转 |
| 研发 | 研发支出 | 研发费用处理 |
| 资产 | 固定资产、累计折旧 | 资产购置、折旧计提 |
| 资金 | 银行存款、库存现金 | 资金收付、银行转账 |
| 税务 | 应交税费 | 税金计提与缴纳 |

---

### 2. 对方科目分析 (Contra Analysis)

**文件位置**：`contra_analyzer/`

**功能描述**：
基于穷举算法的多借多贷分录对方科目解析工具。对于复杂的多借多贷凭证，系统会穷举所有可能的对方科目组合，并按奥卡姆剃刀原则（简单性）排序，帮助用户选择最合理的方案。

**核心算法**：
1. **分录分类**：按借贷方数量和金额将凭证分为5类
2. **穷举求解**：对多借多贷分录生成所有可能的对方科目组合
3. **剪枝优化**：通过约束条件减少无效组合
4. **奥卡姆评分**：按科目数量、金额匹配度等计算简单性得分
5. **方案排序**：按得分排序供用户选择

**主要类**：
- `ContraProcessor`：核心处理器，协调各组件
- `ExhaustiveSolver`：穷举求解器
- `OccamsRazor`：奥卡姆评分器

**5类分录结构**：
| 类型 | 特征 | 处理方式 |
|------|------|----------|
| 1借1贷 | 单借单贷 | 直接匹配 |
| 1借m贷 | 一借多贷 | 多贷方配对 |
| 多借1贷 | 多借一贷 | 多借方配对 |
| 多借多贷 | 复杂分录 | 穷举求解 |
| 全借全贷 | 红字冲销 | 特殊处理 |

---

## 🏗️ 架构说明

### 应用架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Streamlit Web UI                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  首页导航    │  │ 异常检测页面 │  │ 对方科目分析页面     │ │
│  │  app.py     │  │ anomaly_test│  │  ui_streamlit       │ │
│  └─────────────┘  └──────┬──────┘  └──────────┬──────────┘ │
└──────────────────────────┼────────────────────┼────────────┘
                           │                    │
                           ▼                    ▼
              ┌────────────────────┐  ┌────────────────────┐
              │ accounting_anomaly │  │ contra_analyzer    │
              │  ┌──────────────┐  │  │  ┌──────────────┐  │
              │  │DataProcessor │  │  │  │Core Processor│  │
              │  ├──────────────┤  │  │  ├──────────────┤  │
              │  │ClusterEngine │  │  │  │Algorithm     │  │
              │  ├──────────────┤  │  │  ├──────────────┤  │
              │  │AnomalyDetect │  │  │  │OccamsRazor   │  │
              │  └──────────────┘  │  │  └──────────────┘  │
              └────────────────────┘  └────────────────────┘
```

### 数据流

**会计分录异常检测**：
```
用户上传 → 字段配置 → 数据预处理 → 群聚类 → 距离计算 → 异常评分 → 结果展示
   CSV      列映射      清洗标准化     分类      矩阵生成     风险等级     可视化
```

**对方科目分析**：
```
用户上传 → 字段配置 → 结构识别 → 算法选择 → 计算结果 → 方案排序 → 结果导出
   CSV      列映射     分录分类    穷举/直接   对方科目    奥卡姆得分    Excel
```

---

## 💻 开发指南

### 添加新功能模块

1. **创建页面文件**
   ```bash
   touch pages/new_feature.py
   ```

2. **创建核心模块**
   ```bash
   mkdir -p src/new_feature
   touch src/new_feature/__init__.py
   touch src/new_feature/core.py
   ```

3. **在 app.py 中添加导航**
   ```python
   def show_home():
       # ... 在 show_home 中添加新卡片
       with col3:
           st.markdown("...新模块卡片...")
           if st.button("进入新模块 →"):
               go_to_page("new_feature")
   
   def show_new_feature():
       """显示新功能模块"""
       show_back_button_in_sidebar()
       from pages.new_feature import show_new_feature as _show
       _show()
   
   def main():
       # ... 添加路由
       elif page == "new_feature":
           show_new_feature()
   ```

### 代码规范

1. **导入规范**
   - 主入口使用绝对导入：`from pages.xxx import ...`
   - 模块内部使用相对导入：`from .xxx import ...`
   - 标准库 → 第三方库 → 本地模块

2. **命名规范**
   - 类名：PascalCase（如 `DataProcessor`）
   - 函数/变量：snake_case（如 `process_data`）
   - 常量：UPPER_SNAKE_CASE（如 `CORE_GROUPS`）

3. **文档规范**
   - 模块级文档字符串说明功能
   - 类/函数使用 Google 风格文档字符串
   - 复杂算法添加注释说明

4. **Streamlit 开发规范**
   - 使用 `st.session_state` 保持状态
   - 页面函数以 `show_` 开头
   - 避免在循环中调用 `st.write` 等渲染函数

---

## 🚀 运行与部署

### 本地开发

```bash
# 启动开发服务器
streamlit run app.py

# 指定端口
streamlit run app.py --server.port 8502

# 禁用热重载（调试时）
streamlit run app.py --server.runOnSave false
```

### 生产部署

推荐使用 Docker 部署：

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

---

## 📚 相关文档

- [README.md](README.md) - 项目简介与快速开始
- [src/accounting_anomaly/theory.md](src/accounting_anomaly/theory.md) - 异常检测算法理论
- [contra_analyzer/theory.md](contra_analyzer/theory.md) - 对方科目分析算法理论
