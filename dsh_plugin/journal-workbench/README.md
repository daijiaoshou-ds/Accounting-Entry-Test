# 会计分录工作台（DSH 插件）

把 `Accounting-Entry-Test` 仓库里的 `journal` 命令行接到 DSH：程序阶段照常跑，
**AI 复核由当前会话的 agent 完成**，产物回到项目自己的 `_storage_jobs/<id>/`。

M1 只提供工具，没有页面——整条链在对话里跑完：上传/给路径 → 确认字段映射 →
程序读表分类 → agent 逐条复核 → 交结论 → 拿结果文件。

## 它不做什么

| 事 | 归谁 |
|---|---|
| 状态机、幂等、重跑范围 | `jobflow`（`_storage_jobs/<id>/state.json`） |
| 判桶、编表、读写 Excel | `features/*` 的阶段实现 |
| 「怎么想」这一步 | 当前会话的 agent（插件不接模型 API） |

插件只补两样：**后台跑**（工具不能卡几分钟）和**给人/给模型看的排版**。

## 安装

```text
plugin_manager install_bundle  target = <本目录绝对路径>
```

装完是新 bundle，`cordis.patch.yml` 会插入一行 Host 插件，6 个工具立刻可用。

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

只装在 `desktop`（web）profile 上生效；没有任何页面，所以不需要 `dsh.client`。

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

- 长任务靠轮询：`journal_next` 在跑的时候读 `state.json` + 后台进程的 stderr 尾巴，
  不阻塞、但也不是推送。
- 还没做的：工作台页面（M2）、跑到复核位自动唤醒 agent（M3）、
  其余三个功能（对方科目/分录测试/银行流水）的 `features/*` 适配。
