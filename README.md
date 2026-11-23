# 🏦 会计分析工具箱

面向财务人员 / 会计师事务所的本地会计工具，主界面是 **DSH 插件「审计工作台」**。
功能按**数据层**分组：`序时账处理`（对方科目分析 → 清洗 → 现金流量表编写 / 会计分录测试，
四个功能共用同一份账）与 `成本计算`（采购明细，与账完全独立，尚未接入）。同一层内共用
同一份已配好的数据与同一套阶段机；复核由当前会话的 agent 接手，数据不出本机。

![Python](https://img.shields.io/badge/Python-3.11~3.13-blue.svg)
![DSH Plugin](https://img.shields.io/badge/DSH-Plugin-4c63c4.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

> 💡 不懂代码也没关系：在 DSH 里 **插件 → 添加插件** 输入 `github:daijiaoshou-ds/dsh-auditor` 装上，
> 再到「审计工作台」页签点「环境配置」，从上到下点完六步，就有能跑的引擎了。
>
> 🧭 装哪个目录、模块之间谁依赖谁，看 [`INSTALL.md`](INSTALL.md)。
> 旧的 Streamlit、FastAPI + React、3.0 打包版都已归档到 `_archive/`（见其 README）。

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
先由 `core/classify` 判业务桶，再把「业务桶 + 货币资金方向」映射成现金流量项目——**不重复判业务，也不自己读表**。

| 特性 | 描述 |
|------|------|
| **只依赖共用判定** | 本功能不加载模型、没有硬规则；同一份账，现金流与清洗的业务分类永远一致 |
| **方向看科目不看金额** | 货币资金在借方=收现、贷方=付现（借方负数=冲收现），红字凭证不会被判反 |
| **项目由桶决定** | 桶 → 候选项目 → 按方向筛；剩多个才拿一级科目当裁判，都不行标「待复核」 |
| **两处 AI 复核停靠点** | 复核业务分类（含"桶与钱的方向对不上"的高置信凭证）→ 复核判不下来的条目 |
| **可接已有的清洗任务** | 同一份账不重复分类、不重复复核；上游改桶后 resume 即重算 |
| **一个文件两张表** | 现金流量表（按项目聚合）/ 货币资金凭证（整张凭证 + 项目）；整本账的分类在清洗那份产物里，不重复抄；>5 万行自动改 CSV |

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

### 方式一：装插件（推荐，零代码）

**联网装**：DSH → **插件 → 添加插件**，输入 `github:daijiaoshou-ds/dsh-auditor`。

**离线装**（GitHub 连不上时）：把 `dsh-auditor-<版本>.zip` 解压到**一个固定目录**
（例如 `D:\dsh-auditor`，别解压到下载目录——以后升级要覆盖同一个目录），然后在
**插件 → 添加插件**里填这个目录的绝对路径（`D:\dsh-auditor`）。

装完打开「审计工作台」→「环境配置」，从上到下点完六步：Python 解释器 → 虚拟环境 →
基础依赖 → CPU 版 torch → 模型文件 → 基座 BGE。引擎与模型落在
`%LOCALAPPDATA%\dsh-auditor\engine`、venv 落在 `%LOCALAPPDATA%\dsh-auditor\venv`（引擎目录
之外），**都与插件目录无关**：以后换新版本的 zip，不用重装环境。

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

# ① 基础依赖（阿里云镜像源，国内快）
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# ② 模型依赖（CPU 版 torch）+ 两个模型
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
modelscope download daijiaoshou/hajishou-V2.0 --local_dir core/nn/_storage
python -c "from core.nn.model_loader import resolve_model_dir; print(resolve_model_dir('BAAI/bge-large-zh-v1.5'))"
```

> 只想把插件分给别人：联网给 `github:daijiaoshou-ds/dsh-auditor`（包在
> [dsh-auditor](https://github.com/daijiaoshou-ds/dsh-auditor)，只有装配产物）；
> 网络不通就传 `dist/dsh-auditor-<版本>.zip`，解压后按上面的离线方式装。
> 出新版本：改完代码跑 `python _dev/scripts/release_dsh_auditor.py --push`——
> 它同时推仓库并打出 `dist/dsh-auditor-<版本>.zip`（版本号在
> `build_dsh_auditor.py` 顶部的 `VERSION`）。

**启动**：把 `dsh_plugin/journal-workbench` 装进 DSH（见下面的「使用指南」），
在「审计工作台」里传账、跑功能、复核、下载。

**停止**：任务在 DSH 后台跑，关掉页面不会停进程；在功能页右上角点「■ 中止」。

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

模型详情见 [ModelScope：hajishou-V2.0](https://www.modelscope.cn/models/daijiaoshou/hajishou-V2.0)（MIT 许可，仅含权重不含训练数据）。
训练数据需要自己积累（跑分类 + 纠错审核后，训练页才有数据）。

---

## 📖 使用指南

**两个入口，同一套后端（`features/*` + `jobflow`）：**

| 入口 | 怎么起 | 适合 |
|---|---|---|
| **DSH 插件**（审计工作台） | 装 `dsh_plugin/journal-workbench`；左侧边栏一个图标，对话里也有一个页签 | 日常用：传账、看阶段、复核、下载；复核交给这个任务自己的会话 |
| 命令行 | `journal.bat` / `python journal.py …` | 脚本、批量、agent 直接驱动 |

> 两边读写同一份 `_storage_jobs/<id>/`：页面上跑到一半关掉，
> `journal.py next <job_id>` 接着走；反过来也一样。
> 旧的 Streamlit（8501）与 FastAPI + React（8502）两套界面已整体移入 `_archive/`，
> 需要时按 [`_archive/README.md`](_archive/README.md) 取回。

### 装 DSH 插件

```text
# 在 DSH 里安装（target 指到本仓库下的插件目录）
plugin_manager install_bundle
  target = <仓库绝对路径>/dsh_plugin/journal-workbench
```

装完侧边栏会多一个「审计工作台」图标，对话里也多一个页签。**侧栏入口没有会话**，
所以要在插件配置里写明项目根：

```yaml
- id: dsh-journal-workbench
  name: dsh-journal-workbench
  config:
    repoRoot: <仓库绝对路径>
    python: <仓库绝对路径>/venv/Scripts/python.exe
```

改 Host 代码（`src/*.js`）要关一次再开插件；改 `client.js` 刷新页面即可。
细节见 [`dsh_plugin/journal-workbench/README.md`](dsh_plugin/journal-workbench/README.md)。

**四个功能串成一条管线。** 顺序是「对方科目分析 → 序时账清洗 → 现金流量表 / 会计分录测试」，
后一个自动接着前一个的产物，不用手选（同一任务里最近一次跑完的那个）：

- **对方科目排在最前面**，是因为它不需要模型（纯算术穷举）。放在清洗后面的话，
  "只想看看对方科目"也变成了必须先下载模型。
- **清洗接对方科目**，但它自己的交付物**不再贴「对方科目」两列**：对方科目是一对多的
  （应收账款的贷方可能对收入和税费两笔），行级表压成一行再拼接只是"看起来像"。
  真正的配对在对方科目分析那份产物里；清洗只把行表原样带给下游的「科目对」。
- **现金流与分录测试都只接清洗**：分类、复核结论、对方科目行表，清洗已经带出来了。
  上游改了桶，`resume` 一次就把下游重算。

接上游时 `read` / `clean` 整段跳过（不加载模型、不会得出不同的桶，也不会把已经
判过的桶再问一遍）；下游只补一遍它才看得见的那类问题（比如现金流看"桶和钱的方向
对不上"）。

### AI 复核：交给 agent，不是"网页调模型 API"

程序跑、AI 复核、再算下游这三段是**穿插**的：每个 `actor="ai"` 的阶段程序都不猜，
只产出待办（`reviews/<stage>.request.json`）然后停下，等结论交回再往下走。

**复核由 agent 执行**，不是网页点个按钮去调 completion API——复核是有状态的多轮
任务（一批几十条要一起看、判完要落文件、要触发下游重算、要核对产物），单轮 API
做不了，结论也回不来。所以：

| | 谁用 | 干什么 |
|---|---|---|
| 工作台（左侧边栏） | 人 | 上传、看阶段、**自己改几条**、下载 |
| `journal_*` 工具 | AI（任务自己的复核会话） | `journal_next` → 判 → `journal_submit` → `journal_report` |
| `_storage_jobs/` | 两边共用 | 同一份 `state.json` / `decisions`，谁后写谁生效 |

任务停在复核位时**不会自动叫 AI**（跑功能是跑功能，复核是另一件事）。你在页面上点
「AI 复核这批」才叫醒这个任务的复核会话；不想复核就点「不复核，直接用」，按原判出
结果。没有 DSH 时，命令行等价（`journal` = `journal.bat`）：

```bash
journal inbox                                  # 有哪些任务在等我
journal next    <job_id>                       # 下一步 + 待办（含整张凭证的分录）
journal request <job_id> --offset 20           # 条数多时分页
journal submit  <job_id> --file d.json --by ai
journal submit  <job_id> --empty --stage review_bucket   # 这批不用改
journal report  <job_id>                       # 结果摘要（转述给用户用）
journal resume  <job_id>                       # 失败/中止后从断点继续
journal abort   <job_id>                       # 中止（先杀掉进程再调它）
journal export  <job_id> --dest 输出目录
```

读表与认列在**配账那一步只做一次**（页面「新建任务」/`journal prepare`：解析成
`prepared/raw.parquet` + `schema.json`），四个功能都从它载入，不再各自解析 xlsx；
命令行直接给文件、没配过账时仍旧当场解析。

待办里每条都带「分录」（整张凭证按科目+方向聚合）——判业务桶看的是对方科目，
只给"一个科目 + 一个金额"判不出来。判断口径、常见判错模式、分批与出错处理见
**[`AGENT_PLAYBOOK.md`](AGENT_PLAYBOOK.md)**；分层设计见
`docs/design/architecture_v5.md` §3.8。

### 在插件里用

打开「审计工作台」→ 右栏「＋」新建任务（传账、确认字段映射）→ 主区点一个功能开始处理
→ 停在复核位就在表里改几条、或交给这个任务的 AI 会话 → 结果页直接看产物表（或下载）。
四个功能共用同一份账，按 对方科目 → 清洗 → 现金流 / 分录测试 的顺序接上游结果。

### 序时账清洗

1. **数据上传**：在侧边栏上传 Excel/CSV 格式的序时账数据
2. **字段配置**：映射凭证号、一级科目、科目名称、摘要、借方金额、贷方金额等列
3. **自动检测**：系统自动检测列名，支持常见 ERP 导出格式
4. **分类**：点击「开始分类」→ 等待出结果
5. **查看结果**：分类概览、详细结果（得分明细）、PMI 矩阵热力图、自动词特征
6. **复核**：「🔎 复核」页看模式、置信度分布、待复核凭证，以及本次未审训练数据的位置
7. **纠错**：下载纠错表 → 修改纠错分类列 → 上传回传 → 系统学习，下次更准确

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

核心在 `bank_statement_matcher/`，**还没接进 jobflow 与插件**；接入后会在
「审计工作台」里作为一个功能出现。目前它仍是独立子项目，入口见该目录自己的说明。

### 小工具

旧的小工具集（Excel 提取 / 文件整理 / 格式互转 / PDF 索引与合并）已随 Streamlit
界面移入 `_archive/legacy-streamlit/other_tools/`，不再随主流程安装。

---

## 📁 项目结构

```
├── journal.py                  # 命令行入口（薄壳，实现在 jobflow/cli.py）
├── journal.bat                 # Windows 包装
├── README.md                   # 项目简介
├── INSTALL.md                  # 装哪个目录、装什么（给 AI 的防呆说明）
├── AGENT_PLAYBOOK.md           # ★ AI 复核操作手册（给 agent 看的那份）
├── requirements.txt            # 基础依赖（功能版）
├── requirements-nn.txt         # NN 推理依赖（模型版）
├── requirements-train.txt      # 训练依赖（训练版）
│
├── dsh_plugin/journal-workbench/   # ★ DSH 插件（主入口）
│   ├── index.js                    #   Host：注册 journal_* 工具
│   ├── client.js                   #   工作台页面（手写 h()，无构建）
│   ├── src/                        #   HTTP 路由 / 后台跑 / 叫醒 / 任务存储
│   └── locale/                     #   中英文案
│
├── jobflow/                    # 编排层：Stage(program|ai) / Job / state / decision / CLI
├── features/                   # 四个功能，每个目录自包含
│   ├── contra/                 #   对方科目分析（穷举 + 奥卡姆排序）
│   ├── cleaning/               #   接对方科目 → 读表/分类/复核桶 → 写回
│   ├── cashflow/               #   接上游 → 编表 → 复核 → 导出（含映射表 assets/cf）
│   ├── entry_test/             #   会计分录测试（完整性 / 画像 / 检验）
│   └── cost/                   #   成本计算（占位，未接入）
│
├── core/                       # 运行时内核：各功能共用，只有一份
│   ├── ledger/                 #   读表 / 业务分类 / 复核面 / 接上游
│   ├── classify/               #   桶注册表 / 别名 / 列名识别 / 分类器 / 业务等价组
│   └── nn/                     #   模型推理（交付物 _storage/，BGE 缓存 models/）
│
├── tools/train/                # 重训：微调循环 / 训练数据 / 分词可视化 / 评测
├── journal_io/                 # 数据层：字段字典 / Journal 多视图 / 读写引擎
├── accounting_domain/          # 业务常量：货币资金、损益类、期末结转、汇兑、借支
├── bank_statement_matcher/     # 银行流水匹配核心（独立，尚未接进 jobflow）
├── assets/fonts/               # 中文字体（PDF 索引/转换用）
├── case/ training_case/        # 样例账 / 训练样例
│
├── _dev/tests/                 # 回归测试（渲染、jobflow、各功能）
├── _storage_jobs/              # 任务状态与产物（本地，不入 git）
├── _archive/                   # 旧框架（本地，不入 git，见 _archive/README.md）
│
└── docs/                       # 文档（按性质分四类，见 docs/README.md）
    ├── publish/                # ① 披露稿：可对外发布（进 git）
    ├── design/                 # ② 初版设计稿：cleaner/ skill/ bank/（不进 git）
    ├── review/                 # ③ review 报告：cleaner/ skill/ bank/（不进 git）
    └── reports/                # ④ 结果文档：技术报告、测试/架构报告
        ├── technical_report3.0.md   # 现行完整技术报告（进 git）
        └── history/                 # 历史报告（不进 git）
```

> 旧的两套界面（Streamlit / FastAPI + React）与 3.0 打包产物已移入 `_archive/`，
> 不在仓库主结构里；取回方式见 [`_archive/README.md`](_archive/README.md)。

---

## 🛠️ 技术栈

| 类别 | 技术 |
|------|------|
| 界面 | DSH 插件（宿主 React，手写 `h()`，无构建步骤） |
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

- [ModelScope：hajishou-V2.0](https://www.modelscope.cn/models/daijiaoshou/hajishou-V2.0) - 序时账清洗 NN 分类模型（33 桶，基于 BGE-large-zh 微调，MIT 许可，仅含权重不含训练数据）

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

模型**不用重下**：`core/nn/_storage/` 不在 git 内，`git pull` 不会影响它。

## 📝 相关文章

- [会计分录测试](https://mp.weixin.qq.com/s/3uSWArpNR4u_rz1hC5H7og) - 会计分录异常检测的算法与实现
- [对方科目分析](https://mp.weixin.qq.com/s/hI94U3Jfi-OtAcHJv7ot4Q) - 多借多贷分录的对方科目解析方法

## 📢 公众号

更多会计财务技术干货，欢迎关注微信公众号 **呆叫兽2058**

---

## 📄 许可证

MIT License

