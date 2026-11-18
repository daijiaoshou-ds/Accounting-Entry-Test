# 装哪个目录？

> 给 AI 助手/自动化脚本看的**防呆说明**。人只需要看第一张表。

| 目录 | 是什么 | 装吗 |
|---|---|---|
| **`core/`** | **运行时内核**：`ledger/`（读表、判桶、复核面、接上游）、`classify/`（桶表 + 规则 + 分类器）、`nn/`（模型推理，**交付物在这里**：`nn/_storage/`） | ✅ 必装 |
| **`features/`** | **四个功能的实现**：`contra` / `cleaning` / `cashflow` / `entry_test`，每个目录自包含 | ✅ 用插件或命令行就装 |
| `journal_io/` `accounting_domain/` `jobflow/` | 数据契约（字段字典、`Journal` 多视图、读写引擎）、阶段机与任务状态 | ✅ 必装 |
| **`dsh_plugin/journal-workbench/`** | **DSH 插件**：`journal_*` 工具 + 「审计工作台」页面（左侧边栏 + 对话页签） | ✅ 要用工作台就装 |
| `tools/train/` | 重训模型用的：微调循环、训练数据、分词可视化 | ⭕ 只有要重训才用 |
| `bank_statement_matcher/` | 银行流水匹配（独立子项目，**还没接进 jobflow**） | ⭕ 暂时不装 |
| `_archive/` | 旧 Streamlit / FastAPI + React 界面、独立分发时的打包产物、模型副本、实验存储 | ❌ 不装（见 `_archive/README.md`） |

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

:: ③ 业务分类自检（模型能加载）
venv\Scripts\python.exe -X utf8 -c "from core.classify.classifier import JournalClassifier; from core.classify.config import load_subject_list; c=JournalClassifier(subject_list=load_subject_list()); c._load_nn_if_available(); print('model_available=', c._nn_available)"

:: ④ 现金流量表自检（映射表自洽 + 上游可用）
venv\Scripts\python.exe -X utf8 -m features.cashflow.cli status
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

模型（约 622MB）**不在仓库里**，落在 `core/nn/_storage/`：

```cmd
venv\Scripts\python.exe -X utf8 -c "import sys; from modelscope import snapshot_download; print(snapshot_download('daijiaoshou/hajishou-V2.0', local_dir=sys.argv[1]))" "core\nn\_storage"
```

4 件交付物必须配套（`fine_tuned/` + `finance_classifier.pt` + `subject_to_index.json`
+ `index_to_bucket.json`），**版本不一致时直接报错**——这是刻意的。
基础模型（BGE）缓存在 `core/nn/models/`，首次推理时自动下载；两者都不进 git。

## 各目录的关系

- `core/classify/` 是**唯一的业务桶判定实现**：桶表、别名重定向、父级回退、四类机械
  凭证硬约束。4.0 起没有程序打分，桶 100% 由模型判定。
- `core/ledger/` 是**所有功能共用的第一段**：读表与配账、业务分类、复核面、接上游。
  以前这段挂在"清洗"这个功能上，导致现金流要 `from ..cleaning import stages` 才能跑；
  现在功能之间不互相 import。
- `core/nn/` 是**模型推理**：BGE 微调模型 + 科目开关 + 分类头。训练不在这一层。
- `features/<id>/` 是**一个功能的全套实现**，打开一个目录就能读完它：阶段编排在
  `__init__.py`，判定与导出在 `stages.py` 及同目录的算法文件里。
- `journal_io/` + `accounting_domain/` 是**数据层**：字段字典、`Journal` 多视图（原样/字符串/键/金额）、
  读写引擎与落盘策略（xlsx/csv/parquet 三档）、业务科目集合。所有功能都过它，不再各写一套。
- `jobflow/` 是**编排层**：把"程序跑 → AI 复核 → 再算下游"做成有状态的任务
  （`state.json` + `decisions/*.json`），插件与命令行读同一份状态。
- `dsh_plugin/journal-workbench/` 是**界面与工具**：Host 侧注册 6 个 `journal_*` 工具，
  Client 侧提供工作台；它不实现业务，只负责后台跑、排版、叫醒复核会话。
- `_archive/` 是 3.0 及更早的界面与打包产物，与现行架构不一致，不参与运行。
