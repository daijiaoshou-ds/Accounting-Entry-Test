# 会计分析工具箱

一站式会计数据分析平台，集成会计分录异常检测与对方科目分析两大核心功能。

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.28+-red.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

## 功能特性

### 📊 会计分录测试
基于业务群聚类和距离计算的异常分录检测系统

- **8大业务群自动分类**：采购、销售、薪酬、生产、研发、资产、资金、税务
- **跨群距离矩阵计算**：量化业务群之间的关联程度
- **异常风险评分**：综合跨模块得分、模块内得分和重要性水平
- **科目资金流向可视化**：交互式图表展示资金流向

### 🔄 对方科目分析
基于穷举算法的多借多贷分录对方科目解析工具

- **5类分录结构识别**：1借1贷、1借m贷、多借多贷、全借全贷、特殊分录
- **多借多贷穷举计算**：智能剪枝算法，高效计算所有可行组合
- **奥卡姆得分排序**：基于简单性原则选择最优方案
- **在线方案预览**：Web端直接查看和选择方案

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动应用

```bash
streamlit run app.py
```

或双击 `start.bat`（Windows）

### 使用流程

1. **首页导航**：选择需要的功能模块
2. **数据上传**：在左侧边栏上传Excel/CSV文件
3. **字段配置**：映射数据列到系统字段
4. **执行分析**：点击分析按钮，等待处理完成
5. **查看结果**：在Web界面查看分析结果和可视化
6. **导出数据**：下载Excel格式的结果文件

## 项目结构

```
├── app.py                      # 主入口
├── pages/                      # Streamlit页面
│   └── anomaly_test.py         # 会计分录测试页面
├── src/accounting_anomaly/     # 会计分录测试核心
├── contra_analyzer/            # 对方科目分析核心
└── ...
```

详细结构参见 [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)

## 技术栈

- **前端**：Streamlit
- **数据处理**：Pandas, NumPy
- **可视化**：Plotly, NetworkX
- **机器学习**：scikit-learn (KMeans)
- **算法**：穷举搜索 + 启发式剪枝

## 许可证

MIT License
