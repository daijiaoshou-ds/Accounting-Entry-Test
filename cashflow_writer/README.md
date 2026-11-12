# 现金流量表编写（cashflow-writer）

> **这是一个 AI skill。** 用法是让 AI 助手读 [SKILL.md](SKILL.md)，由它带着你走完
> 配置与跑账——你不需要懂代码，也不需要点网页。
>
> 现金流口径怎么定 → **[reference/CASHFLOW_GUIDE.md](reference/CASHFLOW_GUIDE.md)**
> 装环境 → **[reference/SETUP.md](reference/SETUP.md)**

## 它做什么

```
序时账 ─► 业务分类（模型，summary_cleaner）─► 现金流量项目（程序查表）─► 一个 Excel
```

只做最后一段：**业务桶 + 货币资金借贷方向 → 现金流量项目**。

| 层 | 谁负责 | 在哪 |
|---|---|---|
| 读表、认列名、科目归一化 | `summary_cleaner` | 另一个包 |
| 业务桶判定（模型 + 四类硬约束） | `summary_cleaner` | 另一个包 |
| 桶 + 方向 → 现金流量项目 | **本包** | `cfw/judge.py` |
| 写回、导出 | **本包** | `cfw/rows.py` / `cfw/export.py` |
| 编排、CLI | **本包** | `cfw/pipeline.py` / `cfw/cli.py` |

### 为什么不再自带一套清洗逻辑

这个包以前（`summary_cleaner_v3`）内嵌了一份序时账清洗代码和一份旧模型，
和上游各走各的：上游出了 4.0（取消程序打分、33 桶、v10.4 模型），这边还是
18 桶的旧模型，同一份账在两边会得出不同的业务分类。

现在改成**单向依赖**：本包不判业务、不加载模型，只消费上游的 `业务分类`。
好处是上游改模型/改桶/改硬约束，这边一行代码都不用改；代价是本包**必须**
和 `summary_cleaner` 一起部署（见 `cfw/config.py::ensure_upstream`）。

## 目录

```
cashflow_writer/
├── cli.py                 唯一入口（薄壳，转发到 cfw.cli）
├── cfw/
│   ├── config.py          路径、版本、上游引导
│   ├── judge.py           ★ 现金流判定（桶→项目、方向、裁判、补丁、自检）
│   ├── rows.py            货币资金行抽取、逐行写回、落盘
│   ├── export.py          一个 Excel 三张 sheet
│   ├── pipeline.py        编排：上游判桶 → 编现金流 → 导出
│   └── cli.py             status / check / run / validate
├── assets/cf/             现金流映射表三件套（手工维护，JSON 即权威）
│   ├── cf_items.json      24 个现金流量项目 + 方向 + 报表分组
│   ├── bucket_to_cf.json  业务桶 → 候选项目
│   └── account_ruling.json 歧义裁判表 + 兜底表
├── reference/             口径与安装文档
└── _dev/                  本地测试账（不入库）
```

## 安装

依赖与模型都来自上游，本包只需要 Python 依赖：

```cmd
python -m venv venv
venv\Scripts\python.exe -m pip install torch --index-url https://mirrors.aliyun.com/pypi/simple/
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -X utf8 cli.py status
```

`status` 里要看到 `missing_modules: []`、`upstream.model_ready: true`、
`cf_maps.validate_problems: 0`。模型怎么下见上游
`summary_cleaner/nn/_storage/`（4 件交付物）。

> **部署要求**：`cashflow_writer/` 与 `summary_cleaner/` 必须在同一父目录下。
> 找不到上游时 `status` 会直接报 `stage: upstream` 并给出期望路径。

## 命令

```cmd
venv\Scripts\python.exe -X utf8 cli.py status
venv\Scripts\python.exe -X utf8 cli.py check  "序时账.xlsx"
venv\Scripts\python.exe -X utf8 cli.py run    "序时账.xlsx" --out "输出目录"
venv\Scripts\python.exe -X utf8 cli.py run    "序时账.xlsx" --cf "502=吸收投资收到的现金"
venv\Scripts\python.exe -X utf8 cli.py validate
```

`run` 的 stdout 只有一行 JSON（前缀 `RESULT `），进度走 stderr；完整出参加
`--out-json 结果.json`。

## 产物

`<文件名>_现金流量表.xlsx`，三张 sheet：

| sheet | 内容 |
|---|---|
| 原始序时账 | 用户给什么就是什么，列名也不改 |
| 业务分类结果 | 原始列 + 业务分类/置信度/判定来源 + 现金流量项目/方向/判定/复核 |
| 货币资金凭证 | 含货币资金的**整张**凭证（编现金流要看对方在干什么） |

超过 50 万行改写成多个 csv（Excel 单表上限 104 万行）。

## 已知边界

| 项 | 状态 |
|---|---|
| 正式现金流量表（期初余额、表间勾稽） | 未做，目前只出"按项目聚合 + 净增加额" |
| 歧义桶（投资 / 利息支出） | 靠一级科目裁判 + 兜底表过渡；根治要拆桶重训 |
| 待复核条目 | 不硬选，交人工/AI 结论后走 `--cf` 补丁 |
| 单元级口径修正（旧 `--unit`） | 已移除：那属于上游清洗层，本包按凭证号 `--bucket` 只保留应急把手 |
