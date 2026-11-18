# 目录结构说明

一句话：**计算行为没变，变的是代码归属**——每个功能在自己的目录里能读完，
四个功能共用的那一段只存一份、放在 `core/`。

---

## 一、三层

```
core/            运行时内核：谁都要用、且必须算出同一个结果的代码
  ledger/          读表与配账 / 业务分类 / 复核面 / 接上游
  classify/        桶表、桶规则、分类器、业务等价组
  nn/              模型推理 + 交付物 _storage/ + BGE 缓存 models/

features/<id>/   一个功能的全套实现，互相不 import
  contra           对方科目分析
  cleaning         序时账清洗
  cashflow         现金流量表编写
  entry_test       会计分录测试
  cost             成本计算（占位，未接入）

tools/train/     只在重训时用：微调循环、训练数据、分词可视化、评测

journal_io/      数据契约：字段字典、Journal 多视图、读写引擎
accounting_domain/  业务常量：货币资金、损益类、期末结转
jobflow/         阶段机：Stage(program|ai) / Job / state / decision
dsh_plugin/      插件包（JS + 一个表格预览脚本）
```

---

## 二、每一层负责什么

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `core/ledger/` | 读表（配账后的 parquet 或当场读原件）、业务分类阶段、复核面（业务等价组、分录聚合）、接上游任务 | 任何功能独有的业务判断 |
| `core/classify/` | 桶注册表与 taxonomy、别名重定向、温度校准、父级回退、四类机械凭证硬约束、科目扫描、业务等价组 | 训练代码、界面 |
| `core/nn/` | 模型结构、加载器、推理、摘要向量缓存、科目归一化、摘要数字归一化 | 训练循环、训练数据构建 |
| `features/<id>/` | 阶段编排（`__init__.py`）+ 本功能独有的判定与导出（`stages.py` 及同目录算法文件） | 不许 import 另一个 `features/*` |
| `tools/train/` | 微调、训练数据、分词可视化、评测 | 不许被运行时 import |

判断一段代码该放哪，只有两个问题：

1. **离开某个功能，这段代码还有意义吗？** 有 → `core/`；没有 → `features/<id>/`。
2. **它只在重训时跑吗？** 是 → `tools/train/`。

---

## 三、一个功能怎么读

### 现金流量表（`features/cashflow/`，最完整的一个）

```
__init__.py      阶段链：link → read → clean → review_bucket → judge → review_cf → export
stages.py        本功能独有的三段：编表、复核现金流条目、写回导出
judge.py         桶 + 货币资金方向 → 现金流量项目
rows.py          逐行写回：项目 / 方向 / 判定 / 复核标记
export.py        出 Excel（原始账 / 业务分类结果 / 货币资金凭证）
stats.py         结果概况：阶段、页面、导出读同一份统计口径
cli.py           python -m features.cashflow.cli status|validate（只读自检）
config.py        映射表位置
assets/cf/       现金流映射表三件（项目 / 桶→项目 / 歧义裁判），JSON 即权威
review-guide.md  AI 复核时随叫醒一起发给它的判定口径
```

前四段（`link` `read` `clean` `review_bucket`）不是它写的，`__init__.py` 里直接引用
`core.ledger` 的实现——同一份账，现金流与清洗的业务分类必须一致。

**读表与字段识别只有一处**：任务配置那一步（`journal inspect` 认列名 →
`journal run --mapping …`）。这里原来还有一个 `pipeline.py`（`check` / `run`
两个动作），自己读表、自己认列名、自己判、自己导出——和 jobflow 那条链并行，
同一个映射要改两个地方，已删；`cli.py` 只剩 `status` / `validate` 两个只读自检。

### 对方科目（`features/contra/`）

```
__init__.py      阶段链：read → analyze → review_pattern → export
stages.py        四段的组织与复核表
engine.py        适配层：只暴露 build / solve / finalize 三个入口
algorithm.py     穷举所有配对
occams_razor.py  排序（奥卡姆剃刀）
core.py          处理器：按科目组合复用拓扑
ss_normalizer.py 科目名归一化
memory_web.py    已定案模式的知识库
```

`stages.py` 只跟 `engine.py` 打交道，算法内部结构不外泄。

### 序时账清洗（`features/cleaning/`）

```
__init__.py      阶段链：read → link → clean → review_bucket → export
stages.py        只有两件自己的事：把对方科目贴进结果表、写回成 Excel
review-guide.md  AI 复核口径
```

它的五个阶段里，前四个都是 `core.ledger` 的，所以这个目录很薄——这是对的，
不是漏了代码。

### 会计分录测试（`features/entry_test/`）

14 个文件全在自己目录里，只从 `core.ledger` 取读表与改桶结论：

```
stages.py        阶段链 + 各步之间的数据交接
integrity.py     第 0 步 总体完整性（凭证号连续 / 按月借贷平衡 / 数据质量）
profile.py       第 1 步 画像（业务活动 + 集中度）
benford.py       本福特
matrix.py        活动内科目 / 科目组合孤立
chains.py        孤儿冲销
leads.py         线索汇总与优先级
report.py        出 HTML 报告
whitewash.py     白名单放行
fingerprint.py   凭证指纹
detail.py        明细与标记列
activity.py      业务活动归类
engine.py        字段契约与数据视图
```

---

## 四、一次清洗任务的数据流

```
用户传账
  └─ jobflow 建任务：_storage_jobs/<id>/source/ 存原件，state.json 记 sha256
       └─ （可选）配账：解析一次，落 prepared/（raw.parquet + schema.json）
            └─ read       core/ledger/read.py      载入账，校验字段契约
                 └─ link  features/cleaning/stages.py   接对方科目分析（可选）
                      └─ clean  core/ledger/classify.py  模型判桶 + 四类硬约束
                           │     产物：out/clean/result.parquet（行级）
                           │           out/clean/detail.parquet（凭证级）
                           └─ review_bucket  core/ledger/review.py   按业务等价组出待办
                                │  decisions/review_bucket.json ← agent 交结论
                                └─ export  features/cleaning/stages.py
                                     产物：out/export/<账名>_业务分类.xlsx
```

复核待办是按「业务等价组」给的：同科目组合、摘要去数字后相同的凭证压成一组，
一条待办带「张数」和「样张」，改一条整组生效。

---

## 五、加一个新功能

1. 建 `features/<新id>/`，写 `__init__.py` 里的 `build()` 返回 `Feature`：
   `id` / `name` / `requires` / `stages` / `exports`。
2. 阶段函数放 `stages.py`。要读表就 `from core.ledger import read_journal`；
   要判桶就复用 `clean` 阶段；要复核业务桶就 `from core.ledger import review_items`。
3. 需要 AI 复核的阶段：`actor="ai"` + `review=` 返回 `ReviewRequest`。
4. 写 `review-guide.md`（复核口径），叫醒时会整份发给复核会话。
5. 在 `journal_io/contracts.py` 的 `DOMAINS` 里登记，前端会出现卡片。

不用改插件：功能清单、阶段链、必需字段、复核取值全部从 `journal features` 和
`review.request.json` 读。

---

## 六、路径对照

| 原来 | 现在 |
|---|---|
| `summary_cleaner/pipeline.py`、`subjects.py`、`dedup.py` | `core/classify/` 下同名文件 |
| `summary_cleaner/v2/` | `core/classify/`（`config` / `classifier` / `bucket_rules` / `bucket_merge` / `bucket_registry_v10` / `bucket_bridge` / `io_utils` / `fingerprint` / `errors`） |
| `summary_cleaner/v2/assets/` | `core/classify/assets/` |
| `summary_cleaner/nn/`（推理部分） | `core/nn/` |
| `summary_cleaner/nn/`（训练部分） | `tools/train/` |
| `summary_cleaner/nn/_storage/` | `core/nn/_storage/` |
| `summary_cleaner/nn/models/` | `core/nn/models/` |
| `cashflow_writer/cfw/{judge,rows,export,config,cli}.py` | `features/cashflow/` 下同名文件（`pipeline.py` 已删，见下） |
| `cashflow_writer/assets/cf/` | `features/cashflow/assets/cf/` |
| `cashflow_writer/reference/` | `docs/reference/` |
| `contra_analyzer/{algorithm,core,memory_web,occams_razor,ss_normalizer}.py` | `features/contra/` 下同名文件 |
| `features/cleaning/stages.py` 里的共享段 | `core/ledger/` |
| `cashflow_writer/_dev/序时账1.xlsx` | `_dev/序时账1.xlsx` |

命令也跟着变：

| 原来 | 现在 |
|---|---|
| `python -m cfw.cli status` | `python -m features.cashflow.cli status` |
| `python summary_cleaner/nn/train_v10.py` | `python -m tools.train.train_v10` |
| `python -m summary_cleaner.nn.review_tools` | `python -m tools.train.review_tools` |
| `python -m summary_cleaner.subjects` | `python -m core.classify.subjects` |

`summary_cleaner/`、`cashflow_writer/`、`contra_analyzer/` 三个目录已不存在；
独立分发时代的 README / SKILL / requirements、57 个探针脚本、v9 备份、2.8G 实验存储
在 `_archive/`，运行时不碰。

---

## 七、没变的东西

| 项 | 状态 |
|---|---|
| 任务目录 `_storage_jobs/<id>/` | 结构不变（`source/` `prepared/` `out/` `decisions/` `state.json`） |
| `state.json` / `decisions/*.json` 格式 | 不变，历史任务可以照旧 `report` / `next` / `resume` |
| 阶段 id 与顺序 | 不变（`read` `link` `clean` `review_bucket` `export` 等） |
| 命令行与插件接口 | 不变（`journal.py` 子命令、`journal_*` 工具、`/plugin/journal/*`） |
| 复核粒度 | 不变（业务等价组，一条待办带张数与样张） |
| 产物文件名 | 不变（`<账名>_业务分类.xlsx` / `_现金流量表.xlsx` / `_会计分录测试.html`） |
| 判桶结果 | 不变（模型文件只换了目录；同一份账同一批桶） |

11-17 建的老任务在新结构下 `resume`，各阶段全部标记「未变，跳过」并返回 `done`，
产物路径一致——这是老 state 兼容的直接证据。

---

## 八、怎么自己确认

```cmd
:: 东西在哪：仓库 / 任务目录 / 模型 / 桶表 / 复核指导（插件问的就是这一条）
venv\Scripts\python.exe -X utf8 journal.py env --pretty

:: 五个功能都在
venv\Scripts\python.exe journal.py features

:: 模型能加载（从 core/nn/_storage）
venv\Scripts\python.exe -X utf8 -c "from core.classify.classifier import JournalClassifier; from core.classify.config import load_subject_list; c=JournalClassifier(subject_list=load_subject_list()); c._load_nn_if_available(); print('model_available=', c._nn_available)"

:: 现金流映射表自洽 + 模型四件齐
venv\Scripts\python.exe -X utf8 -m features.cashflow.cli status

:: 回归测试（8 个文件）
venv\Scripts\python.exe _dev\tests\run_all.py

:: 真账跑一遍
venv\Scripts\python.exe journal.py run case/序时账1.xlsx --feature cleaning
```

插件侧不拼引擎的目录：模型目录、桶表、复核指导、任务目录都由
`journal env` 报出来（`src/layout.js`，缓存 10 分钟）。
所以**改引擎的目录结构不用动插件**；只有改插件自己的 JS 才需要
在插件管理里把 `dsh-journal-workbench` 关一次再打开（Host 代码不热加载）。
