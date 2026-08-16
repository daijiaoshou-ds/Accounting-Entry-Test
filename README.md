# 🏦 会计分析工具箱

一站式本地会计数据分析平台，集成**序时账清洗**、**会计分录异常检测**、**对方科目分析**三大核心功能，帮助财务人员快速清洗凭证分类、识别异常分录、解析复杂分录结构。

![Python](https://img.shields.io/badge/Python-3.11~3.13-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.55-red.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

> 💡 不懂代码也没关系：本仓库附带 [`SKILL.md`](SKILL.md)，把它交给 AI 助手（Claude / Cursor / 任意 agent），AI 会自动帮你装环境、下模型、启动应用。

---

## ✨ 功能特性

### 🧹 序时账清洗（Journal Voucher Cleaning）
程序规则引擎（PMI 相关性矩阵 + 五维得分）+ NN 微调模型融合打分，将海量凭证自动归类到 **18 个业务桶**（17 业务桶 + 其他业务兜底桶）。

| 特性 | 描述 |
|------|------|
| **18 个业务桶自动分类** | 存货采购、销售收入、职工薪酬、费用报销、税费、长期资产、生产制造等全场景覆盖 |
| **PMI 科目共现矩阵** | 从历史凭证中学习科目间关联关系，构建通用相关性矩阵（预置进仓库） |
| **五维得分融合** | 结构分（PMI）+ 关键词偏置 + 金额特征 + 纠错增强 + 制单人维度 |
| **NN 语义融合打分** | BGE 中文模型微调，程序得分（20%）+ 模型概率（80%）加权，CPU 可跑（int8 量化） |
| **人类纠错闭环** | 用户纠正 → 模糊匹配 → 下次自动修正同类凭证 |
| **自动词特征发现** | 从历史分类中自动发现「顺丰→费用报销」等强特征词 |

### 📊 会计分录异常检测
基于业务群聚类和距离计算的异常分录检测系统。

| 特性 | 描述 |
|------|------|
| **9 大业务群自动分类** | 采购、销售、薪酬、生产、研发、资产、资金、税务、其他 |
| **跨群距离矩阵计算** | 量化业务群之间的关联程度，识别跨模块异常 |
| **异常风险评分** | 综合跨模块得分、模块内得分和重要性水平 |
| **科目资金流向可视化** | 交互式网络图展示科目间资金流向 |
| **重要性水平配置** | 帕累托分析剔除小额凭证，聚焦重点 |

### 🔄 对方科目分析
基于穷举算法的多借多贷分录对方科目解析工具。

| 特性 | 描述 |
|------|------|
| **5 类分录结构识别** | 1借1贷、1借m贷、多借多贷、全借全贷、特殊分录 |
| **多借多贷穷举计算** | 智能剪枝算法，高效计算所有可行组合 |
| **奥卡姆得分排序** | 基于简单性原则选择最优方案 |
| **在线方案预览** | Web 端直接查看和选择方案 |
| **批量导出** | 支持 Excel 格式结果下载 |

---

## 🚀 快速开始

### 方式一：让 AI 帮你装（推荐，零代码）

把 [`SKILL.md`](SKILL.md) 的内容发给任意 AI 助手，说「帮我按 SKILL.md 装好这个工具」，AI 会自动完成环境搭建、依赖安装、模型下载和启动。

### 方式二：手动安装

**环境要求**：Python 3.11 ~ 3.13（推荐 3.13），建议使用虚拟环境。

```bash
# 克隆项目
git clone <仓库地址>
cd 会计分录测试

# 创建并激活虚拟环境
python -m venv venv
venv\Scripts\Activate.ps1   # Windows
source venv/bin/activate     # macOS/Linux

# 安装依赖（常规使用 = 基础 + NN 融合，一份装完，加清华镜像源下载快）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 下载已训练模型（约 620MB）到指定目录
modelscope download daijiaoshou/hajishou-V1.0 --local_dir summary_cleaner/nn/_storage
```

**启动**：双击 `start.bat`（Windows），或 `streamlit run app.py`。浏览器自动打开 http://localhost:8501 。

> 说明：「常规使用」即包含 NN 模型融合打分，`requirements.txt` 已含 torch **CPU 版**（几百 MB，pip 默认源）+ transformers + safetensors + modelscope，一份装完、再下模型即可。推理自动 int8 量化（1 万条约 4 分钟），无需 CUDA。

### 训练模型（可选，仅开发者，需 NVIDIA GPU）

只有你想自己训练/微调 NN 模型时才需要。训练必须用 GPU：

```bash
# 1. 先卸载 CPU 版 torch，换 CUDA 版（cu126 = CUDA 12.6，按你的版本选）
pip uninstall torch
pip install torch --index-url https://download.pytorch.org/whl/cu126

# 2. 装训练增强
pip install -r requirements-train.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

模型详情见 [ModelScope：hajishou-V1.0](https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0)（MIT 许可，仅含权重不含训练数据）。

> 极端情况下，若只想用纯程序模式、完全不想装 torch，可只挑 `requirements.txt` 里的非 torch 依赖装（序时账清洗自动降级纯程序规则模式，功能完整但无 NN 融合）。

---

## 📖 使用指南

### 首页导航
启动后进入首页，点击对应模块卡片进入功能页面。

### 序时账清洗

1. **数据上传**：在侧边栏上传 Excel/CSV 格式的序时账数据
2. **字段配置**：映射凭证号、一级科目、科目名称、摘要、借方金额、贷方金额等列
3. **自动检测**：系统自动检测列名，支持常见 ERP 导出格式
4. **分类**：点击「开始分类」→ 等待出结果
5. **查看结果**：分类概览、详细结果（得分明细）、PMI 矩阵热力图、自动词特征
6. **纠错**：下载纠错表 → 修改纠错分类列 → 上传回传 → 系统学习，下次更准确

### 会计分录异常检测

1. **数据上传**：在左侧边栏上传 Excel/CSV 格式的会计凭证数据
2. **字段配置**：映射数据列（日期、凭证号、科目、金额等）
3. **重要性水平**：设置帕累托百分比，剔除不重要的凭证
4. **执行分析**：点击「开始分析」
5. **查看结果**：数据概览、群聚类结果、科目连接图、群距离矩阵、异常检测、统计报告

### 对方科目分析

1. **数据上传**：上传包含借方、贷方金额的凭证数据
2. **字段配置**：设置科目列、金额列等映射
3. **执行分析**：系统自动识别分录结构
4. **方案选择**：多借多贷分录查看穷举计算结果
5. **导出结果**：下载包含对方科目的完整数据

---

## 📁 项目结构

```
├── app.py                      # 主入口：统一导航框架
├── start.bat                   # Windows 启动脚本
├── README.md                   # 项目简介
├── SKILL.md                    # 给 AI 的安装指导（零代码用户看这里）
├── requirements.txt            # 常规使用（基础 + NN 推理，torch CPU 版）
├── requirements-train.txt      # 训练依赖（仅微调开发者，需 CUDA torch + GPU）
│
├── pages/                      # Streamlit 页面
│   ├── anomaly_test.py         # 会计分录异常检测页面
│   └── nn_training.py          # NN 模型训练页面（开发者用）
│
├── summary_cleaner/            # 序时账清洗核心
│   ├── v2/                     # V2.1 规则引擎（PMI/评分/纠错/持久化）
│   └── nn/                     # NN 模型（数据/推理/训练）
│
├── src/accounting_anomaly/     # 会计分录异常检测核心
├── contra_analyzer/            # 对方科目分析核心
│
└── docs/                       # 理论文档
    ├── CSA_theory2.0preview.md # 对方科目分析算法理论
    ├── JET_theory.md           # 会计分录测试理论
    └── technical_report3.0.md  # 序时账清洗完整技术报告
```

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| Web 框架 | Streamlit |
| 数据处理 | Pandas, NumPy |
| 可视化 | Plotly, NetworkX |
| 机器学习 | scikit-learn |
| 深度学习（可选）| PyTorch, Transformers, BGE 中文模型 |
| 算法 | 穷举搜索 + 启发式剪枝 + PMI 相关性矩阵 |

---

## 📝 数据格式要求

### 序时账清洗

| 必需列 | 说明 | 示例 |
|--------|------|------|
| 凭证编号 | 唯一凭证号 | 2025年1月-记-0001号 |
| 一级科目 | 会计科目 | 银行存款 |
| 借方金额 | 借方发生额 | 10000.00 |
| 贷方金额 | 贷方发生额 | 0.00 |
| 摘要（推荐）| 业务描述 | 支付1月份房租 |
| 科目名称（推荐）| 二级科目明细 | 招商银行深圳龙岗支行 |
| 制单人（可选）| 记账会计姓名 | 张三 |

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

## 📚 理论文档

- [docs/CSA_theory2.0preview.md](docs/CSA_theory2.0preview.md) - 对方科目分析算法理论
- [docs/JET_theory.md](docs/JET_theory.md) - 会计分录测试理论
- [docs/technical_report3.0.md](docs/technical_report3.0.md) - 序时账清洗完整技术报告（规则引擎 + NN 模型 + 融合打分）

## 🧠 模型下载

- [ModelScope：hajishou-V1.0](https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0) - 序时账清洗 NN 分类模型（18 桶，基于 BGE-large-zh 微调，MIT 许可，仅含权重不含训练数据）

## 📝 相关文章

- [会计分录测试](https://mp.weixin.qq.com/s/3uSWArpNR4u_rz1hC5H7og) - 会计分录异常检测的算法与实现
- [对方科目分析](https://mp.weixin.qq.com/s/hI94U3Jfi-OtAcHJv7ot4Q) - 多借多贷分录的对方科目解析方法

## 📢 公众号

更多会计财务技术干货，欢迎关注微信公众号 **呆叫兽2058**

---

## 📄 许可证

MIT License

