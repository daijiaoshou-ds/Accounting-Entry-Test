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
    autoWake: true         # 跑到复核位自动叫醒发起它的 agent（默认开）
```

`autoWake: false` 关掉自动叫醒后，页面上的「叫 AI 复核」仍然可用（它是手动的）。

只装在 `desktop`（web）profile 上生效。页面与工具共用同一份配置。

## 页面（会话视图 →「会计分录」）

```
┌ 主区 ───────────────────────────────────┬ 右栏 ────────────┐
│ 环境配置  可以用了                  打开 │ 任务           ＋ │
│ Python 3.13.5 · venv ✓                   │ 11 月账    当前   │
│ torch 2.13.0+cu126（CUDA 12.6）          │ 序时账1.xlsx …    │
│                                          │ [清洗✓][现金流⏸]  │
│ 功能                                     │ [对方科目✕]…      │
│ ┌────────┐┌────────┐┌────────┐┌────────┐│                  │
│ │序时账清洗││现金流量││对方科目││分录测试││ 全年账            │
│ │ 已完成  ││等你复核 ││ 失败   ││依赖未完成││ …               │
│ └────────┘└────────┘└────────┘└────────┘│                  │
└──────────────────────────────────────────┴──────────────────┘
                             ↑ 竖条可拖（双击复位）
```

- **主区**：环境配置压在左上角（配一次就不动），下面是一个功能一个框、一行四个。
  框里只有名字和状态；描述、阶段链、为什么现在不能跑，都在点开之后那一页——
  不能跑也点得动，进去会写清原因。
- **右栏**：全部属于任务。一个任务装一份账，四个功能共用它；每块下面的 chip 显示
  各功能跑到哪，点一下直接进那个功能。删除、重算科目组合也在这里。
- 右栏宽度可拖（260–460，默认 288），拖过就记住（`localStorage`）。复核表宽
  不够时往右拖一下就行。
- 点功能后，主区换成那个功能的阶段时间线 + 复核表 + 结果，右栏不动。

页面不认功能名：功能框、阶段链、必需字段、复核表的取值全部来自
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
journal_features                       → 知道有哪些功能（cleaning / cashflow / contra / entry_test）
journal_inspect  case/序时账1.xlsx      → 确认映射（--feature 决定要哪些字段）
journal_start    feature=cleaning file=case/序时账1.xlsx
journal_next     job_id=...            → 停在 review_bucket，12 条
  （逐条判：分录 > 摘要 > 模型 Top3；拿不准的不要写进 decisions）
journal_submit   job_id=... decisions=[...]
journal_next     job_id=...            → 下一批 / 已完成
journal_report   job_id=...            → 结果摘要 + 产物路径
```

判据细节在项目根的 `AGENT_PLAYBOOK.md`。

## 自动交接（M3）

任务是后台跑的，跑到 AI 阶段会停下。插件在那一刻**叫醒发起它的那个 agent**：

```
agent.followup(message)   → 投进 inbox 的 next-turn 并唤醒 driver
```

于是整条链不需要任何人粘一句话：页面上点「开始」→ 程序跑完 → 会话里的 agent
自己接上「有 29 条待办」→ 判 → 交 → 程序继续 → 出结果。

- **只叫自己人**：任务由哪个会话发起（工具调用里的 agent、页面带来的 `sessionId`），
  就只叫那个 agent；命令行起的任务没有归属，不叫（叫错人比不叫更糟）。
- **同一处只叫一次**：键是 `任务:阶段:条数`。agent 交完结论、下游又停回同一处且
  条数没变（比如弃权后又停回来），不再叫——否则会自己叫自己转圈。
- **agent 正在开车时不叫**：提交/轮询/续跑之后 90 秒内不再投提醒。它在复核循环里
  本来就会自己接着看，这时候再投一条，只会在它下一轮开工时冒出来，而且往往已经过时。
  `start` 不算开车——建完任务它就该走了，正需要有人叫它回来。
- **提醒自带"先核对"**：`followup` 是排队投递，排进去的时候任务确实停在复核位，
  送到时可能已经跑完。所以每条提醒末尾都写着"先 journal_next 看当前状态"。
- **失败也叫**：任务 `failed` 时发一条带 error 的消息，不然没人知道它挂了。
- 不想被"主动打断"就配 `autoWake: false`；页面上的「叫 AI 复核」是手动版本。

## 前提

- 目标仓库里有 `journal.py` 与 `venv`（或配置 `python`）；
- 分类阶段要模型交付物（`summary_cleaner/nn/_storage/`）齐备，否则 `clean` 阶段会失败并写明原因；
- 后台跑并发固定 1（分类吃 GPU），排队执行。

## 已知限制

- 长任务靠轮询：页面 1.5 秒一次读 `state.json`，`journal_next` 在跑的时候会等
  最多 40 秒等阶段推进——都不是推送。唯一的推送是"停到复核位"那一次。
- 页面只覆盖"从零建任务 + 复核 + 下载"；批量删任务、看历史导出还没做。
- 还没做的：**银行流水匹配**（要两个输入文件，jobflow 现在只收一个）。

## 目前接进来的四个功能

| 功能 id | 名字 | 复核的是什么 | 典型待办条数 |
|---|---|---|---|
| `cleaning` | 序时账清洗 | 中/低置信凭证的业务桶 | 2,216 凭证 → 29 条 |
| `cashflow` | 现金流量表编写 | 业务桶方向冲突 + 现金流条目 | 视账而定 |
| `contra` | 对方科目分析 | 多借多贷模式的配对方案（每模式一条） | 136 模式 → 91 条候选 |
| `entry_test` | 会计分录测试 | 高/中风险凭证是真异常还是误报 | 2,214 凭证 → 129 条 |

四个功能都是"程序阶段跑完 → 停在 AI 阶段 → agent 判 → 程序继续出表"。加一个功能
不用改插件：功能清单、阶段链、必需字段、复核取值全部从 `journal features` 与
`review.request.json` 读。待办里插件不认识的字段会原样输出（`候选方案`、`异常类型`
就是这么显示出来的），页面复核表把「模型桶 / 置信度」并成一列「现状」，其余字段
跟在「分录」下面。
