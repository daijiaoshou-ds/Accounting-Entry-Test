# 🏦 会计分析工具箱

一站式本地会计数据分析平台，集成**序时账清洗**、**会计分录异常检测**、**对方科目分析**、**银行流水匹配**四大核心功能与 **小工具** 效率工具集，帮助财务人员快速清洗凭证分类、识别异常分录、解析复杂分录结构、核对序时账与银行流水。

![Python](https://img.shields.io/badge/Python-3.11~3.13-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-1.55-red.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

> 💡 不懂代码也没关系：本仓库附带 [`SKILL.md`](SKILL.md)，把它交给 AI 助手（Claude / Cursor / 任意 agent），AI 会自动帮你装环境、下模型、启动应用。
>
> 🧭 **只要「序时账清洗」这个 skill？** 直接装 [`summary-cleaner/`](summary-cleaner)
> 那个目录就行（发布版，约 0.3MB，模型另下）。仓库里有三个名字相近的目录，
> **别装错** —— 看 [`INSTALL.md`](INSTALL.md)。

---

## ✨ 功能特性

### 🧹 序时账清洗（Journal Voucher Cleaning）
**业务桶 100% 由模型判定**（4.0 起取消了程序打分与融合），把凭证归类到 **24 个业务桶**（注册表 33 项，含费用子桶折叠与哨兵桶）。

| 特性 | 描述 |
|------|------|
| **24 个业务桶全模型判定** | BGE-large 微调（摘要 + 187 维科目开关），没有关键词表、没有 PMI、没有加权融合 |
| **四类机械凭证硬约束** | 期末结转 / 资金内部往来 / 汇兑损益 / 员工借支，形态可枚举，程序直接判、不进模型 |
| **置信度与复核清单** | 温度校准后的概率分高/中/低档；明细带「组内张数」，越大越该抽查 |
| **去重后再推理** | L1 精确 → L2 去数字 → L3 相似 ≥0.95，全年账 2.22:1、八家公司合账 3.55:1 |
| **未知科目清单** | 归一化后对不上标准科目表的，输出 JSON 供维护别名表（不让模型退避，按摘要照判） |
| **改桶可追溯** | 复核改判写进 `decisions/*.json`（幂等键 + 依据 + 时间），产物里标记「判定来源=复核改判」 |

### 💵 现金流量表编写（Cashflow Statement）
先由 `summary_cleaner` 判业务桶，再把「业务桶 + 货币资金方向」映射成现金流量项目——**不重复判业务，也不自己读表**。

| 特性 | 描述 |
|------|------|
| **只依赖上游清洗** | 本包不加载模型、没有硬规则；同一份账，现金流与清洗的业务分类永远一致 |
| **方向看科目不看金额** | 货币资金在借方=收现、贷方=付现（借方负数=冲收现），红字凭证不会被判反 |
| **项目由桶决定** | 桶 → 候选项目 → 按方向筛；剩多个才拿一级科目当裁判，都不行标「待复核」 |
| **两处 AI 复核停靠点** | 复核业务分类（含"桶与钱的方向对不上"的高置信凭证）→ 复核判不下来的条目 |
| **可接已有的清洗任务** | 同一份账不重复分类、不重复复核；上游改桶后 resume 即重算 |
| **一个文件三张表** | 原始序时账 / 业务分类结果（含现金流列）/ 货币资金凭证；>5 万行自动改 CSV |

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

### 💱 银行流水匹配
基于明细科目配对与多算法匹配引擎的序时账-银行流水核对工具。

| 特性 | 描述 |
|------|------|
| **字段智能识别** | 自动识别序时账/银行流水的标准列，支持手动修正映射 |
| **科目-流水配对** | 按明细科目（如"银行存款-建行0911"）智能配对对应银行流水文件 |
| **多级匹配引擎** | 1v1 精确匹配 → 双向聚合漏斗 → 回溯/DP 求解 → 疯狂聚合兜底 |
| **相似度筛选** | 公司名称余弦相似度筛选唯一解，金额日期双重容差 |
| **诊断面板** | 对方科目分析、明细科目月度分布、核对过程详情 |
| **月度差异** | 序时账与银行流水按科目/月份的收入支出差异总览 |
| **结果导出** | 匹配/未达账项明细 Excel 一键下载 |

### 🧰 小工具
日常办公效率工具集（原「基米工具箱」桌面版功能，已全部移植为 Web 页面，复用原桌面版核心逻辑）。

| 工具 | 说明 | 能力 |
|------|------|------|
| **Excel数据提取** | 批量跨文件提取指定列，表头模糊匹配、自动映射，双引擎（Pandas / Polars+fastexcel） | ✅ 可用 |
| **文件批量整理** | 生成文件清单 Excel，按清单批量移动/复制/重命名 | ✅ 可用 |
| **Excel格式互转** | xls↔xlsx、csv/html→xlsx 批量互转，文件夹整包转换（保留目录层级） | ✅ 可用 |
| **PDF索引号生成** | 每页右上角打印红色索引（文件名+页码/总页数），Web 效果预览 | ✅ 可用 |
| **PDF合并/分拆/转换** | 批量合并 PDF、按步长分拆、图像/Excel 转 PDF（Word 转 PDF 需本机 Word） | ✅ 可用 |

> 使用方式：首页 →「小工具」→ 点选功能。**输入**支持「上传文件」与「上传文件夹」两种来源
> （文件夹会递归包含子文件夹并保留目录层级，不需要手填本机路径）；
> **输出**一律通过浏览器下载（单个文件直接下载，多个文件自动打包 zip），
> 既不写入项目目录、也不改动你本机的原文件。
> 说明：Word 转 PDF 依赖本机安装 Microsoft Word（docx2pdf COM），未安装时页面自动隐藏该选项；PDF 用系统宋体自动检测，无需额外资源。

---

## 🚀 快速开始

### 方式一：让 AI 帮你装（推荐，零代码）

把 [`SKILL.md`](SKILL.md) 的内容发给任意 AI 助手，说「帮我按 SKILL.md 装好这个工具」，AI 会自动完成环境搭建、依赖安装、模型下载和启动。

### 方式二：手动安装

**环境要求**：Python 3.11 ~ 3.13（推荐 3.13），建议使用虚拟环境。

```bash
# 克隆项目
git clone <仓库地址>
cd Accounting-Entry-Test

# 创建并激活虚拟环境
python -m venv venv
venv\Scripts\Activate.ps1   # Windows
source venv/bin/activate     # macOS/Linux

# ① 功能版：基础依赖（阿里云镜像源，国内快）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# ② 模型版（推荐，多这一步）：CPU 版 torch + NN 推理依赖 + 模型
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
modelscope download daijiaoshou/hajishou-V1.0 --local_dir summary_cleaner/nn/_storage

# 另需：把系统中文字体复制到资源目录（PDF 索引/转换用，约 18MB）
mkdir assets\fonts & copy C:\Windows\Fonts\simsun.ttc assets\fonts\
```

**启动**：双击 `start.bat`（Windows）。浏览器自动打开 http://localhost:8501 。

**停止**：Streamlit 是常驻服务，关浏览器标签页不会停进程。任选其一：

- 关闭浏览器 → 约 12 秒后自动停止（`run.py` 自动检测）；
- 双击 `stop.bat` → 立即停止；
- 关闭启动时的黑色窗口 / 按 Ctrl+C → 立即停止。

> **三个版本怎么选**：① 功能版（约 1GB，序时账清洗走纯程序规则模式）
> ② 模型版（约 2.5GB，序时账清洗带 NN 融合打分，推荐）
> ③ 训练版（约 7GB，需 NVIDIA 显卡、显存 ≥8GB）。
> 详细部署步骤见 [SKILL.md](SKILL.md)。
> **注意**：`pip install torch` 若不加 `--index-url .../whl/cpu`，装到的是 **CUDA 版**（2.4GB 下载 / 4GB 占用），
> 而 CPU 版只要 122MB，推理精度与速度一致。

### 训练模型（可选，仅开发者，需 NVIDIA GPU）

只有想自己训练/微调 NN 模型时才需要。训练必须用 GPU：

```bash
# 1. 先卸载 CPU 版 torch，换 CUDA 版（cu126 = CUDA 12.6，按你的版本选）
pip uninstall -y torch
pip install torch --index-url https://download.pytorch.org/whl/cu126

# 2. 装训练增强
pip install -r requirements-train.txt -i https://mirrors.aliyun.com/pypi/simple/
```

模型详情见 [ModelScope：hajishou-V1.0](https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0)（MIT 许可，仅含权重不含训练数据）。
训练数据需要自己积累（跑分类 + 纠错审核后，训练页才有数据）。

---

## 📖 使用指南

**四种入口，按推荐顺序：**

| 入口 | 怎么起 | 适合 | 状态 |
|---|---|---|---|
| **DSH 插件**（工作台） | Agent / 对话里那个「会计分录」页签；装法见 [`dsh_plugin/journal-workbench/README.md`](dsh_plugin/journal-workbench/README.md) | 日常用：传账、看阶段、复核、下载，AI 复核由当前会话的 agent 做 | **主入口** |
| 命令行 | `journal.bat` / `python journal.py …` | 脚本、批量、agent 直接驱动 | 一直有 |
| 独立网页版 | `uvicorn api.app:app --port 8502` + `web/` | 没装 DSH 时想用浏览器点 | **保留**（与插件功能对齐，但不是主路径） |
| Streamlit 版 | `start.bat` → 8501 | 银行流水匹配、小工具等还没接进 jobflow 的模块 | 老入口 |

> 插件和独立网页版是同一套后端（`features/*` + `jobflow`），差别只在"界面壳"：
> 插件挂在 DSH 的会话视图里，AI 复核由会话里的 agent 接手；独立网页版自己起
> 一个 FastAPI，AI 复核靠命令行或复制那句话给 agent。

### 独立网页版（React + FastAPI，保留）

```bash
# 后端（127.0.0.1:8502），同时把 web/dist 挂在同一个端口上
venv\Scripts\python.exe -m uvicorn api.app:app --port 8502

# 前端改代码时（热更新，/api 代理到 8502）
cd web && npm install && npm run dev      # http://127.0.0.1:5173
cd web && npm run build                   # 改完重建，后端直接吃 dist
```

| 路由 | 内容 |
|---|---|
| `#/` | 功能列表（来自 `/api/features`）+ 最近任务 |
| `#/f/cleaning` | 序时账清洗：上传 → 分类 → 复核中/低置信 → 导出（老链接 `#/clean` 会自动跳过来） |
| `#/f/cashflow` | 现金流量表编写：上传（或接已有的清洗任务）→ 复核 → 编表 → 导出 |
| `#/f/cashflow/<job_id>` | 回到某个任务的当前阶段（刷新也不丢） |

**两个功能可以接起来跑。** 同一份账先做清洗、把业务桶复核好；编现金流量表时
选「接着已有的清洗任务跑」，分类与中/低置信复核两段直接跳过（不再加载模型、
不会得出不同的桶）。现金流会只补一遍它才看得见的那类问题（"桶和钱的方向对不上"），
上游后来改了桶，`resume` 一次就把现金流重算。

### AI 复核：交给 agent，不是"网页调模型 API"

程序跑、AI 复核、再算下游这三段是**穿插**的：每个 `actor="ai"` 的阶段程序都不猜，
只产出待办（`reviews/<stage>.request.json`）然后停下，等结论交回再往下走。

**复核由 agent 执行**，不是网页点个按钮去调 completion API——复核是有状态的多轮
任务（一批几十条要一起看、判完要落文件、要触发下游重算、要核对产物），单轮 API
做不了，结论也回不来。所以：

| | 谁用 | 干什么 |
|---|---|---|
| 工作台页签 | 人 | 上传、看阶段、**自己改几条**、下载 |
| `journal_*` 工具 | AI（当前会话的 agent） | `journal_next` → 判 → `journal_submit` → `journal_report` |
| `_storage_jobs/` | 两边共用 | 同一份 `state.json` / `decisions`，谁后写谁生效 |

任务停在复核位时，插件会**自动叫醒这个任务所属会话的 agent**；也可以点页签上的
「叫 AI 复核」手动叫一次。没有 DSH 时，命令行等价（`journal` = `journal.bat`）：

```bash
journal inbox                                  # 有哪些任务在等我
journal next    <job_id>                       # 下一步 + 待办（含整张凭证的分录）
journal request <job_id> --offset 20           # 条数多时分页
journal submit  <job_id> --file d.json --by ai
journal submit  <job_id> --empty --stage review_bucket   # 这批不用改
journal report  <job_id>                       # 结果摘要（转述给用户用）
journal export  <job_id> --dest 输出目录
```

待办里每条都带「分录」（整张凭证按科目+方向聚合）——判业务桶看的是对方科目，
只给"一个科目 + 一个金额"判不出来。判断口径、常见判错模式、分批与出错处理见
**[`AGENT_PLAYBOOK.md`](AGENT_PLAYBOOK.md)**；分层设计见
`docs/design/architecture_v5.md` §3.8。

### 首页导航
Streamlit 版（8501）启动后进入首页，点击对应模块卡片进入功能页面。

### 序时账清洗

1. **数据上传**：在侧边栏上传 Excel/CSV 格式的序时账数据
2. **字段配置**：映射凭证号、一级科目、科目名称、摘要、借方金额、贷方金额等列
3. **自动检测**：系统自动检测列名，支持常见 ERP 导出格式
4. **分类**：点击「开始分类」→ 等待出结果
5. **查看结果**：分类概览、详细结果（得分明细）、PMI 矩阵热力图、自动词特征
6. **复核**：「🔎 复核」页看模式、置信度分布、待复核凭证，以及本次未审训练数据的位置
7. **纠错**：下载纠错表 → 修改纠错分类列 → 上传回传 → 系统学习，下次更准确

#### 两种启动方式（决定模型以什么口径判定）

| 启动 | 模式 | 适用场景 | 判定口径 |
|---|---|---|---|
| `start.bat` | 生产 | 日常清洗真实账 | 程序得分 + 模型概率融合，未知科目退避 |
| `start_eval.bat` | 评估（体检） | 检查模型真实水平、产出待复核训练数据 | 100% 模型，不融合 / 不退避 / 不回退父级 |

页面顶部横幅会显示当前模式。两种模式都会把未审训练数据写到
`summary_cleaner/nn/_storage/training/unreviewed/<hash>.json`，
但只有评估模式那份才是纯模型口径，可用作模型体检结论。

> 端口 8501 是固定的：旧的窗口没关时再启动新实例只会绑定失败、浏览器仍连着旧进程。
> `run.py` 现在会自动收掉残留的 python 实例，并在控制台打印当前模式。
>
> 解释器也锁死：两个 bat 都用 `venv\Scripts\python.exe` 直接启动，不靠 PATH。
> 若有人用系统 Python 跑 `python run.py`，`run.py` 会自动检测并切回 venv 重跑；
> 评估模式下若仍拿不到 torch，会直接报错退出，而不是静默退化成纯程序模式。
> 页面「NN 模型未配置」提示里会打印当前解释器路径，便于定位。

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

### 银行流水匹配

1. **数据上传**：侧边栏上传序时账 Excel + 一份或多份银行流水 Excel
2. **核对参数**：设置金额容差、日期容差天数、动态贪心排序开关
3. **字段映射**：确认序时账与各银行流水的字段自动识别结果（标 🔴 的需手动补全）
4. **科目配对**：每个银行存款明细科目选择对应的银行流水文件（支持智能默认配对）
5. **运行核对**：点击「开始核对」，按科目逐一执行匹配
6. **查看结果**：匹配结果 / 未匹配序时账 / 未匹配银行流水三个标签页 + 月度差异 + 数据诊断
7. **导出**：下载核对结果 Excel

### 小工具

1. **进入**：首页点击「小工具」→ 进入工具目录页
2. **选择工具**：目录页展示 5 个功能卡片（Excel数据提取 / 文件批量整理 / Excel格式互转 / PDF索引号生成 / PDF合并分拆转换）
3. **选输入**：「📁 上传文件」（可多选）或「🗂️ 上传文件夹」（浏览器直接选文件夹，递归含子文件夹、保留目录层级）
4. **拿结果**：处理完成后点下载按钮保存（多个文件自动打包成 zip），你本机的原文件不会被改动
5. **日志**：每个功能都有运行日志与实时进度，可随时停止任务

---

## 📁 项目结构

```
├── app.py                      # 主入口：统一导航框架
├── run.py                      # 启动包装器（关浏览器后自动停止服务）
├── start.bat                   # Windows 一键启动
├── stop.bat                    # Windows 一键停止
├── download_model.bat          # 一键下载 NN 模型
├── README.md                   # 项目简介
├── SKILL.md                    # 给 AI 的安装指导（零代码用户看这里）
├── requirements.txt            # 基础依赖（功能版：四大功能 + 小工具都能用）
├── requirements-nn.txt         # NN 推理依赖（模型版：torch CPU 版 + transformers/safetensors/modelscope）
├── requirements-train.txt      # 训练依赖（训练版：CUDA torch + accelerate/peft）
│
├── pages/                      # Streamlit 页面
│   ├── anomaly_test.py         # 会计分录异常检测页面
│   └── nn_training.py          # NN 模型训练页面（开发者用）
│
├── summary_cleaner/            # 序时账清洗核心（4.0：读表→归一化→模型判桶→硬约束→写回）
│   ├── pipeline.py             # 单一编排（没有程序打分）
│   ├── v2/                     # 桶注册表 / 别名 / 列名识别 / bucket_rules
│   └── nn/                     # 模型（数据/推理/训练/编码缓存）
├── cashflow_writer/            # 现金流量表编写（原 summary_cleaner_v3，依赖 summary_cleaner）
├── journal_io/                 # 数据层：字段字典 / Journal 多视图 / 读写引擎 / 落盘策略
├── accounting_domain/          # 业务常量：货币资金、损益类、期末结转、汇兑、借支
├── jobflow/                    # 编排层：Stage(program|ai) / Job / state / decision / CLI
├── AGENT_PLAYBOOK.md           # ★ AI 复核操作手册（给 agent 看的那份）
├── features/                   # 功能注册表
│   ├── cleaning/               #   读表/分类/复核桶（下游共用这一段）+ 自己的导出
│   └── cashflow/               #   接上游 → 编表 → 复核条目 → 导出
├── api/                        # FastAPI：app.py（骨架）+ flow_api.py（编排层路由）
├── web/                        # React 前端（Vite）：功能列表 / 工作台 / 通用复核表
│
├── entry_test/                 # 会计分录测试（异常检测）核心
├── contra_analyzer/            # 对方科目分析核心
├── bank_statement_matcher/     # 银行流水匹配核心（序时账-银行流水核对）
├── other_tools/                # 小工具集（原基米工具箱桌面版，已完整移植为 Web 页面）
│   ├── ui.py                   # Web 集成层：工具注册表 + 二级目录 + 详情页分发
│   ├── web/                    # 5 个工具的 Streamlit 页面（上传/下载、后台任务、日志）
│   └── modules/                # 原桌面版纯逻辑函数（无 GUI 依赖，页面直接复用）
│
├── tests/                      # 回归测试（独立脚本，见 tests/README.md）
│   ├── run_all.py              # 一键跑全部
│   └── test_*.py               # 各模块测试（含 AppTest 真实按钮交互）
│
└── docs/                       # 文档（按性质分四类，见 docs/README.md）
    ├── publish/                # ① 披露稿：可对外发布（进 git）
    ├── design/                 # ② 初版设计稿：cleaner/ skill/ bank/（不进 git）
    ├── review/                 # ③ review 报告：cleaner/ skill/ bank/（不进 git）
    └── reports/                # ④ 结果文档：技术报告、测试/架构报告
        ├── technical_report3.0.md   # 现行完整技术报告（进 git）
        └── history/                 # 历史报告（不进 git）
```

> `CF_compile/`（旧现金流量表项目）已弃用并移除；
> 现金流量表由 `cashflow_writer/`（现金流量表编写）承担——它依赖 `summary_cleaner`
> 判业务桶，自己只做「桶 + 货币资金方向 → 现金流量项目」。

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

### 银行流水匹配

**序时账（必需列）**

| 必需列 | 说明 | 示例 |
|--------|------|------|
| 记账日期 | 凭证日期 | 2024-01-15 |
| 凭证编号 | 凭证号 | 记-001 |
| 摘要 | 业务描述 | 支付货款 |
| 一级科目 | 需含"银行存款" | 银行存款 |
| 明细科目 | 银行账户名称 | 银行存款-建行0911 |
| 借方金额 | 借方发生额 | 10000.00 |
| 贷方金额 | 贷方发生额 | 0.00 |

**银行流水（必需列）**

| 必需列 | 说明 | 示例 |
|--------|------|------|
| 交易日期 | 银行记账日期 | 2024-01-15 |
| 交易方 | 对手方名称（用于相似度筛选） | XX科技有限公司 |
| 收入金额 | 入账金额 | 10000.00 |
| 支出金额 | 出账金额 | 0.00 |
| 摘要 | 流水摘要 | 转账 |
| 流水号（可选） | 银行流水编号 | 202401150001 |

---

## 📚 理论文档

**披露稿**（已完善，可对外发布）：

- [docs/publish/CSA_对方科目分析理论.md](docs/publish/CSA_对方科目分析理论.md) - 多借多贷的对方科目分析算法
- [docs/publish/JET_会计分录测试理论.md](docs/publish/JET_会计分录测试理论.md) - 会计分录测试理论：从会计科目 + 金额找异常凭证

**技术报告**：

- [docs/reports/technical_report3.0.md](docs/reports/technical_report3.0.md) - 序时账清洗完整技术报告

> 除披露稿外还有三类过程文档：`docs/design/`（初版设计稿）、`docs/review/`（历轮
> review 报告）、`docs/reports/history/`（历史技术 / 测试 / 架构报告）。这些都不进 git。
> 完整索引见 [docs/README.md](docs/README.md)。

## 🧠 模型下载

- [ModelScope：hajishou-V1.0](https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0) - 序时账清洗 NN 分类模型（18 桶，基于 BGE-large-zh 微调，MIT 许可，仅含权重不含训练数据）

## 📂 示例数据

仓库提供一份示例序时账 `sample_data/示例序时账.csv`（6 张凭证，覆盖薪酬/采购/销售/费用/资产/税费等常见业务），可用于验证：上传它即可跑通「字段配置 → 分类 → 查看结果」全流程。

## 🔄 版本升级

老用户更新代码后（`git pull`），依赖可能已变化，请重装：

```bash
# 激活 venv 后（阿里云镜像源）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# 模型版用户再补一句：
pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
```

模型**不用重下**：`summary_cleaner/nn/_storage/` 不在 git 内，`git pull` 不会影响它。
`assets/fonts/simsun.ttc` 同样不在 git 内，`git pull` 也不会删它（新克隆的机器才需要手动补，见 SKILL.md Step 4）。

## 📝 相关文章

- [会计分录测试](https://mp.weixin.qq.com/s/3uSWArpNR4u_rz1hC5H7og) - 会计分录异常检测的算法与实现
- [对方科目分析](https://mp.weixin.qq.com/s/hI94U3Jfi-OtAcHJv7ot4Q) - 多借多贷分录的对方科目解析方法

## 📢 公众号

更多会计财务技术干货，欢迎关注微信公众号 **呆叫兽2058**

---

## 📄 许可证

MIT License

