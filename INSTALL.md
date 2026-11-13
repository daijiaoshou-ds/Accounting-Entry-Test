# 装哪个目录？

> 给 AI 助手/自动化脚本看的**防呆说明**。人只需要看第一张表。

| 目录 | 是什么 | 装吗 |
|---|---|---|
| **`summary_cleaner/`** | **序时账清洗 4.0**：读表 → 科目归一化 → 模型判业务桶（33 桶）→ 四类硬约束 → 写回。**模型交付物在这里**（`nn/_storage/`） | ✅ 要清洗就装 |
| **`cashflow_writer/`** | **现金流量表编写**：依赖 `summary_cleaner` 判桶，自己只做「桶 + 货币资金方向 → 现金流量项目」 | ✅ 要编现金流量表就装（两个一起装） |
| `journal_io/` `accounting_domain/` `jobflow/` `features/` | **编排层 + 数据层**：字段字典、读写引擎、阶段机、四个功能的实现 | ✅ 用插件或命令行就装 |
| **`dsh_plugin/journal-workbench/`** | **DSH 插件**：`journal_*` 工具 + 「审计工作台」页面（左侧边栏 + 对话页签） | ✅ 要用工作台就装 |
| `contra_analyzer/` `entry_test/` | 对方科目分析、会计分录测试的**业务核心**（被 `features/` 调用） | ✅ 用这两个功能就装 |
| `bank_statement_matcher/` | 银行流水匹配（独立子项目，**还没接进 jobflow**） | ⭕ 暂时不装 |
| `_archive/` | 旧 Streamlit / FastAPI + React 界面、3.0 打包产物、模型副本 | ❌ 不装（见 `_archive/README.md`） |

> ⚠️ 两个清洗包**必须放在同一父目录**下：`cashflow_writer` 启动时会去同级目录找
> `summary_cleaner`。它自己不带模型、不带清洗逻辑，单独拷走跑不了。

---

## 安装

```cmd
:: ① 拉仓库
git clone --depth 1 https://github.com/<owner>/<repo>.git
cd <repo>

:: ② 建环境（在仓库根执行一次，所有模块共用）
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
:: 模型版再加这一条（torch + transformers 等）
venv\Scripts\python.exe -m pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/

:: ③ 序时账清洗自检（模型能加载）
venv\Scripts\python.exe -X utf8 -c "from summary_cleaner.v2.classifier import JournalClassifier; from summary_cleaner.v2.config import load_subject_list; c=JournalClassifier(subject_list=load_subject_list()); c._load_nn_if_available(); print('model_available=', c._nn_available)"

:: ④ 现金流量表自检
venv\Scripts\python.exe -X utf8 cashflow_writer\cli.py status
```

**⑤ 装 DSH 插件**（网页界面）

```text
plugin_manager install_bundle
  target = <仓库绝对路径>/dsh_plugin/journal-workbench
```

装完侧边栏多一个「审计工作台」图标，对话里多一个页签。侧栏入口没有会话，
**必须配 `repoRoot`**：

```yaml
- id: dsh-journal-workbench
  name: dsh-journal-workbench
  config:
    repoRoot: <仓库绝对路径>
    python: <仓库绝对路径>/venv/Scripts/python.exe
```

## 模型

模型（约 622MB）**不在仓库里**，落在 `summary_cleaner/nn/_storage/`：

```cmd
venv\Scripts\python.exe -X utf8 -c "import sys; from modelscope import snapshot_download; print(snapshot_download('daijiaoshou/hajishou-V2.0', local_dir=sys.argv[1]))" "summary_cleaner\nn\_storage"
```

4 件交付物必须配套（`fine_tuned/` + `finance_classifier.pt` + `subject_to_index.json`
+ `index_to_bucket.json`），**版本不一致时直接报错**——这是刻意的。

## 各目录的关系

- `summary_cleaner/` 是**唯一的清洗实现**：读表、科目归一化、模型判定、硬约束、写回。
- `cashflow_writer/` **只依赖它**：`from summary_cleaner import pipeline` 拿业务桶，
  自己不加载模型、不判业务。它以前叫 `summary_cleaner_v3`，内嵌过一份清洗代码和旧模型，
  已经删掉了（同一份账在两边会得出不同桶，那是双口径）。
- `journal_io/` + `accounting_domain/` 是**数据层**：字段字典、`Journal` 多视图（原样/字符串/键/金额）、
  读写引擎与落盘策略（xlsx/csv/parquet 三档）、业务科目集合。所有功能都过它，不再各写一套。
- `jobflow/` + `features/` 是**编排层**：把"程序跑 → AI 复核 → 再算下游"做成有状态的任务
  （`state.json` + `decisions/*.json`），插件与命令行读同一份状态。
- `dsh_plugin/journal-workbench/` 是**界面与工具**：Host 侧注册 6 个 `journal_*` 工具，
  Client 侧提供工作台；它不实现业务，只负责后台跑、排版、叫醒复核会话。
- `_archive/` 是 3.0 及更早的界面与打包产物，与现行架构不一致，不参与运行。
