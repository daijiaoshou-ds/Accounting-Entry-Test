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
`package.json` 的 `dsh.client` 让页面那半边加载进 Web UI（**左侧边栏多一个
「审计工作台」图标，对话里也多一个页签**——两处是同一个工作台）。

改完 Host 代码要 `set_plugin` 关一次再开；改 `client.js` 要刷新页面。


## 配置（都可省）

默认：**项目根 = 当前会话的工作目录**，解释器自动找 `venv/Scripts/python.exe`，
任务目录用 `<项目根>/_storage_jobs`。工作台在侧边栏、没有会话可用，所以
**用页面的话必须配 `repoRoot`**（否则页面不知道去哪个仓库跑）：

```yaml
- id: dsh-journal-workbench
  name: dsh-journal-workbench
  config:
    repoRoot: D:\path\to\Accounting-Entry-Test
    python: D:\path\to\venv\Scripts\python.exe
    jobsDir: D:\path\to\_storage_jobs
    timeoutMs: 1800000     # 单次后台跑的上限，默认 30 分钟
    reviewLimit: 20        # 每次内联多少条待办
    autoWake: true         # 跑到复核位自动叫醒发起它的 agent（默认关，页面上的按钮手动叫）
    reviewPreset: standard # 复核会话用哪个 agent preset（决定它有没有读写文件/跑脚本的工具）
```

`autoWake: false` 关掉自动叫醒后，页面上的「叫 AI 复核」仍然可用（它是手动的）。
`reviewPreset` 默认 `standard`（DSH 自带的完整工具组合）。改成 `minimal` 之类
会**拿掉复核会话的文件与脚本工具**——它能干活，但只能一条条用眼睛看，条目多时会很辛苦。

只装在 `desktop`（web）profile 上生效。页面与工具共用同一份配置。

## 页面（左侧边栏 + 对话页签，两处同一个工作台）

两处入口、同一个组件、同一份数据，只是进的门不同：

- **左侧边栏**（`sidebar.panellist` 图标 + `main` 整页）：工作台不属于某一个对话，
  它是账自己的地方。这条入口拿不到"发起它的会话"，所以项目根要写在插件配置里
  （`repoRoot`，见上一节），页面请求不带 `sid`。
- **对话里的页签**（`conversation.view`，id `journal`）：就在当前会话里看，不用先
  离开对话。这条入口带着 `sessionId`，项目根可以按会话的工作目录解析。

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
- 功能页右上角只有一个「▶ 开始处理」：进哪个功能就只跑哪个，不给"还能改选别的"。
  在跑的时候按钮变成「■ 中止」——关掉页面不会停进程，必须点它（后端先杀进程、
  再写 `aborted`；`resume` 从断点继续，已完成的阶段不重算）。
- 同一份账上同一个功能只允许一个没跑完的运行：连点两下会被后端挡下来，
  并告诉你那个运行在哪儿。
- **功能之间自动衔接**：现金流会接同一任务里最近一次跑完的清洗（功能的
  `accepts_upstream` 声明的），`link` 阶段把上游的读表/分类/改桶结论搬进来，
  `read`、`clean` 显示"跳过"；页面上写一行"接着「序时账清洗」的结果跑"。
  它有自己要问的（桶和钱的方向对不上），但已经判过的桶不会再问一遍。
- **复核会话按任务绑**（`task.json` 的 `session`）：同一份账上先判清洗、再判
  现金流、再判分录测试，都在同一个会话里，口径不用重讲；换任务才换会话。

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
| POST | `/plugin/journal/tasks` | 建任务：复制原件 + **解析一次账**（`prepared/`）+ 算科目组合 |
| POST | `/plugin/journal/start` | 在这个任务上跑一个功能（默认载入任务已解析的账） |
| POST | `/plugin/journal/decide` | 交结论 / 弃权 |
| POST | `/plugin/journal/resume` | 失败/中止后重试 |
| POST | `/plugin/journal/stop` | 中止：先杀进程、再写 `aborted` |

只读接口不强制 `sid`：侧栏那条入口没有会话，插件按 `repoRoot` 或当前工作目录找项目；
从对话页签进来时带 `sid`，既能按会话目录找项目，也让 agent 侧的"叫醒"知道该叫谁。

## 工具

| 工具 | 什么时候用 |
|---|---|
| `journal_features` | 先看有哪些功能、每个功能的阶段链与必需字段 |
| `journal_inspect` | 读一份账，拿列名/建议映射/缺哪些必需字段（不加载模型，约 1s） |
| `journal_start` | 建任务并后台跑，立刻返 `job_id`；同一任务的现金流自动接最近一次清洗，也可显式 `upstream=<job_id>` |
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

任务是后台跑的，跑到 AI 阶段会停下。插件在那一刻**叫醒这个任务自己的复核会话**：

```
agent.followup(message)   → 投进 inbox 的 next-turn 并唤醒 driver
```

于是整条链不需要任何人粘一句话：页面上点「开始」→ 程序跑完 → 复核会话自己接上
「有 29 条待办」→ 判 → 交 → 程序继续 → 出结果。

**叫醒的时候给全上下文，不是只丢一句"有 N 条待办"**：账多大、停在哪个阶段、
这个功能的**复核指导整份**（`features/<id>/review-guide.md`，见下）、可选值是什么
（从待办的 `value_enum` 来，定义查 `summary_cleaner/v2/assets/bucket_taxonomy_v10.json`）、
这一批要你做什么（待办里的 `help`）、经验文件与全量待办的路径。
只给一句"去判吧"，AI 第一轮还得自己猜这是什么账、桶是什么意思。

### 复核套件（每个功能一套）

| 东西 | 放哪 | 谁维护 | 共享范围 |
|---|---|---|---|
| **复核指导**：任务、流程、工具、判定口径、交付 | `features/<功能>/review-guide.md` | 人（改完下一次叫醒就生效，不用发版） | 功能之间**隔离**，所有会话共享同一份 |
| **经验**：跨任务攒下来的"怎么判" | `_storage_jobs/.lessons/<功能>.md` | 复核会话自己追加 | 功能之间**隔离**，所有会话共享 |
| **工具**：`journal_next/submit/report` + 读写文件 / grep / shell / python | `reviewPreset`（默认 standard）给的组合 | DSH | 功能与会话**都共享** |

指导文件里用 `{{feature}}` `{{ledger}}` `{{task}}` `{{job_id}}` `{{stage}}`
`{{stage_id}}` `{{count}}` `{{dir}}` `{{request}}` `{{lessons}}` 占位，叫醒时替换成
这次运行的实际值——同一份文件，每份账、每个任务都能直接用。
`features/cleaning` 与 `features/cashflow` 已有；对方科目、会计分录测试还没写，
它们退回一段通用流程说明（只讲流程，不讲判定口径——口径得功能自己给）。

**复核会话带 `agentPreset`（默认 `standard`）**：它和普通会话一样有读写文件、grep、
跑 shell/python 的工具。不带 preset 建出来的会话只挂得住插件自己注册的 `journal_*`
工具——AI 只能一条条肉眼看，条目一多就只能硬熬。老会话（`task.json` 里没有
`session_preset`）不会接着用，下次叫醒会换一个带工具的。

- **只叫自己人**：任务由哪个会话发起（工具调用里的 agent、页面带来的 `sessionId`），
  就只叫那个 agent；命令行起的任务没有归属，不叫（叫错人比不叫更糟）。
- **同一处只叫一次**：键是 `任务:阶段:条数:已答条数`。agent 交完结论、下游又停回
  同一处且条数没变（比如弃权后又停回来），不再叫——否则会自己叫自己转圈。
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

- 长任务靠轮询：页面在跑的时候 0.5~1 秒读一次 `state.json`（刚建好 0.5s、跑起来
  1s、停在复核位 4s），`journal_next` 在跑的时候会等最多 40 秒等阶段推进。
  **每个状态都要继续问**——`created` 也算"还在跑"：`journal create` 建完就返回，
  python 写下 `running` 还要一两秒，这段空档里停掉轮询，页面就永远停在"刚建好"
  那一页（只能退出功能再进来才刷新，踩过）。读失败会 2 秒后重试，不放弃。
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
