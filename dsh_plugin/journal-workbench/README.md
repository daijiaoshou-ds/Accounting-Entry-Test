# 会计分录工作台（DSH 插件）

把 `Accounting-Entry-Test` 仓库里的 `journal` 命令行接到 DSH：程序阶段照常跑，
**AI 复核由当前会话的 agent 完成**，产物回到项目自己的 `_storage_jobs/<id>/`。

两半，同一个操作层：

- **agent 侧**：6 个 `journal_*` 工具，整条链在对话里就能跑完；
- **人这侧**：会话视图里的「会计分录」页签，选功能、传账、确认字段映射、
  看阶段时间线、在复核表里改几条、下载产物。

两边写的是同一份 `decisions/<stage>.json`——你在页面上改一条、agent 在对话里改一条，
谁后写谁生效，`_archive/` 留着旧版本。

## 它不做什么

| 事 | 归谁 |
|---|---|
| 状态机、幂等、重跑范围 | `jobflow`（`_storage_jobs/<id>/state.json`） |
| 判桶、编表、读写 Excel | `features/*` 的阶段实现 |
| 「怎么想」这一步 | 当前会话的 agent（插件不接模型 API） |

插件只补三样：**后台跑**（工具不能卡几分钟）、**给人/给模型看的排版**、
以及一个把同一批操作摆到屏幕上的页面。

## 安装

```text
plugin_manager install_bundle  target = <本目录绝对路径>
```

装完是新 bundle，`cordis.patch.yml` 插入一行 Host 插件，6 个工具立刻可用；
`package.json` 的 `dsh.client` 让页面那半边加载进 Web UI（会话视图里多一个
「会计分录」页签，在 Chat / 轨迹 / 上下文 旁边）。

改完 Host 代码要 `set_plugin` 关一次再开；改 `client.js` 要刷新页面。


## 配置（都可省）

默认：**项目根 = 当前会话的工作目录**，解释器自动找 `venv/Scripts/python.exe`，
任务目录用 `<项目根>/_storage_jobs`。要固定下来就改 profile 的 patch 行：

```yaml
- id: dsh-journal-workbench
  name: dsh-journal-workbench
  config:
    repoRoot: D:\path\to\Accounting-Entry-Test
    python: D:\path\to\venv\Scripts\python.exe
    jobsDir: D:\path\to\_storage_jobs
    timeoutMs: 1800000     # 单次后台跑的上限，默认 30 分钟
    reviewLimit: 20        # 每次内联多少条待办
```

只装在 `desktop`（web）profile 上生效。页面与工具共用同一份配置。

## 页面（会话视图 →「会计分录」）

```
选功能 ─┬─ 传账（拖进来 / 本机路径）→ 读表 → 确认字段映射 ─┐
        └─ 接着已完成的清洗任务跑（现金流用）───────────┴→ 开始
                                                          ↓
              阶段时间线（✓ ⏸ ○ ↷ ✕）+ 在跑时的进度尾巴
                                                          ↓
   复核表（凭证号 / 为什么要看 / 分录 / 模型桶 / 置信度 / 改为 / 依据）
        ├─ 改几条 → 提交改判
        ├─ 这批不改 → 弃权
        └─ 复制给 AI → 粘到对话里，让 agent 接着判剩下的
                                                          ↓
              结果卡：规模、复核改判数、业务桶分布、下载
```

页面不认功能名：功能卡、阶段链、必需字段、复核表的取值全部来自
`/plugin/journal/features` 与 `/plugin/journal/review`。加一个功能不用改前端。

进度不用问 Python：时间线读 `state.json`，在跑时再叠上后台进程的 stderr 尾巴，
所以刷新页面不会触发任何 Python 调用。

## 页面用的接口（排障用）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/plugin/journal/jobs` | 任务清单 + 项目根 |
| GET | `/plugin/journal/job?id=` | 状态、阶段链、在跑进度、产物 |
| GET | `/plugin/journal/review?id=&stage=` | 待办条目 + 合法取值 + 已交结论 |
| GET | `/plugin/journal/features` | 功能清单（含阶段链） |
| GET | `/plugin/journal/download?id=&key=` | 产物（只认 state 里登记过的路径） |
| POST | `/plugin/journal/upload?name=` | 上传原件（写进 `_storage_jobs/.incoming/`，建任务后自动删） |
| POST | `/plugin/journal/inspect` | 读表（不加载模型） |
| POST | `/plugin/journal/start` | 建任务并后台跑 |
| POST | `/plugin/journal/decide` | 交结论 / 弃权 |
| POST | `/plugin/journal/resume` | 失败后重试 |

都带 `sid=<sessionId>`：插件按**会话的工作目录**找项目，页面把当前会话号带过去。

## 工具

| 工具 | 什么时候用 |
|---|---|
| `journal_features` | 先看有哪些功能、每个功能的阶段链与必需字段 |
| `journal_inspect` | 读一份账，拿列名/建议映射/缺哪些必需字段（不加载模型，约 1s） |
| `journal_start` | 建任务并后台跑，立刻返 `job_id`；可 `upstream=<job_id>` 接上游 |
| `journal_next` | 给 `job_id` → 阶段链 + 待办（带整张凭证的分录）；不给 → 列在等的任务 |
| `journal_submit` | 交一批结论（或 `waive` 不改），然后后台继续 |
| `journal_report` | 结果摘要 + 产物路径 |

典型一轮：

```text
journal_features                       → 知道 cleaning / cashflow
journal_inspect  case/序时账1.xlsx      → 确认映射
journal_start    feature=cleaning file=case/序时账1.xlsx
journal_next     job_id=...            → 停在 review_bucket，12 条
  （逐条判：分录 > 摘要 > 模型 Top3；拿不准的不要写进 decisions）
journal_submit   job_id=... decisions=[...]
journal_next     job_id=...            → 下一批 / 已完成
journal_report   job_id=...            → 桶分布 + 产物路径
```

判据细节在项目根的 `AGENT_PLAYBOOK.md`。

## 前提

- 目标仓库里有 `journal.py` 与 `venv`（或配置 `python`）；
- 分类阶段要模型交付物（`summary_cleaner/nn/_storage/`）齐备，否则 `clean` 阶段会失败并写明原因；
- 后台跑并发固定 1（分类吃 GPU），排队执行。

## 已知限制

- 长任务靠轮询：页面 1.5 秒一次读 `state.json`，`journal_next` 在跑的时候会等
  最多 40 秒等阶段推进——都不是推送。
- 页面只覆盖"从零建任务 + 复核 + 下载"；批量删任务、看历史导出还没做。
- 还没做的：跑到复核位自动唤醒 agent（M3）、其余三个功能
  （对方科目 / 分录测试 / 银行流水）的 `features/*` 适配。
