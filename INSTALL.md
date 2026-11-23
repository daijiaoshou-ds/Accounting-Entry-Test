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
:: ① 运行时依赖（读表 / 分类 / 现金流 / 对方科目 / 分录测试）
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
:: ② CPU 版 torch（122MB）。**别让 pip 从默认源装**：Windows 上那是 CUDA 版，2.4GB
venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
:: ③ 模型版依赖（transformers / safetensors / modelscope）
venv\Scripts\python.exe -m pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
:: ④ 两个模型（都走 ModelScope，国内直连）
venv\Scripts\python.exe -c "from modelscope import snapshot_download; snapshot_download('daijiaoshou/hajishou-V2.0', local_dir=r'core\nn\_storage')"
venv\Scripts\python.exe -c "from core.nn.model_loader import resolve_model_dir; print(resolve_model_dir('BAAI/bge-large-zh-v1.5'))" 

:: ③ 业务分类自检（模型能加载）
venv\Scripts\python.exe -X utf8 -c "from core.classify.classifier import JournalClassifier; from core.classify.config import load_subject_list; c=JournalClassifier(subject_list=load_subject_list()); c._load_nn_if_available(); print('model_available=', c._nn_available)"

:: ④ 现金流量表自检（映射表自洽 + 上游可用）
venv\Scripts\python.exe -X utf8 -m features.cashflow.cli status
```

**⑤ 装 DSH 插件**（只在 DSH 里点，不用命令行）

**插件 → 添加插件**，输入：

```text
dsh-auditor
```

这是 npm 上的包（`npmjs.com/package/dsh-auditor`）。装不上时依次退：GitHub 的
`github:daijiaoshou-ds/dsh-auditor`、或离线 zip（解压到固定目录后填该目录的绝对路径）。

这个包是装配好的插件 + 随包 Python 引擎（源码仍在本仓库）：插件第一次运行
把引擎复制到 `%LOCALAPPDATA%\dsh-auditor\engine`，模型装在那棵树的
`core/nn/_storage` 与 `core/nn/models` 下；**venv 单独放在 `%LOCALAPPDATA%\dsh-auditor\venv`**，
不在引擎目录里（沙箱会挡住引擎目录里的 venv 启动器，见打包版 `INSTALL.md` 第六节）。
插件升级不会冲掉这些。装完在「审计工作台」里点「环境配置」，六步点完即可用。

**离线装（网络都不通时）**：把 `dsh-auditor-<版本>.zip` 解压到一个固定目录
（例如 `D:\dsh-auditor`），在 **插件 → 添加插件** 里填该目录绝对路径即可；
环境仍装在 `%LOCALAPPDATA%\dsh-auditor` 下，换新版本不必重装环境。

要在本仓库上改代码、而不是用打包版，才装开发版 `dsh_plugin/journal-workbench`
（`plugin_manager install_bundle target=<仓库绝对路径>/dsh_plugin/journal-workbench`，
侧栏入口没有会话，必须配 `repoRoot` 与 `python`）。两个版本的 `journal_*` 工具同名，
**不能同时开**。

## 模型

模型（约 622MB）**不在仓库里**，落在 `core/nn/_storage/`；基座 BGE（约 1.6GB）
落在 `core/nn/models/`，第一次跑分类时自动下（也可以按上面第 ④ 步先下好）：

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
