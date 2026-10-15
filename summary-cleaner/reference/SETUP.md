# 环境与模型准备（SKILL.md 第 0–1 步的展开）

SKILL.md 只留流程与判断点，具体命令和踩坑清单放这里。

---

## 一、装环境：先选一条路，再装（默认走阿里云源）

> ⚠️ **skill 装在 `<项目>\.dsh\skills\summary-cleaner\` 时，`..\venv` 不存在**
> —— 那里的父目录是 `.dsh\skills\`，不是仓库根。**别去猜"父目录的 venv"**，
> 按下面 ① 自建。（旧文档把"复用父目录 venv"当默认，那只在开发者的仓库里成立。）

| 方案 | `%PY%` 是什么 | 什么时候用 |
|---|---|---|
| ① **skill 目录自建 venv（默认）** | `<skill>\venv\Scripts\python.exe` | 绝大多数情况：skill 是拷进来的 / 装成项目级 skill |
| ② 复用用户已有的解释器 | **显式绝对路径**（别写 `..\venv`） | 用户机器上已有能跑的 torch 环境 |
| ③ 宿主提供的运行时 | 宿主给的 python 路径 | 宿主明确说自己有带 torch 的解释器时 |

```cmd
:: ① 自建（默认，照抄这几行）
cd /d "<skill 目录>"
python -m venv venv
set PY=venv\Scripts\python.exe
%PY% -m pip install --upgrade pip

:: ② CPU 版 torch —— 一条命令，走阿里云源（约 106MB）
%PY% -m pip install torch --index-url https://mirrors.aliyun.com/pypi/simple/

:: ③ 其余依赖（requirements.txt 第一行已自带阿里云源，不用再加 -i）
%PY% -m pip install -r requirements.txt
```

> **为什么默认阿里云源**：国内直连 PyPI 装 torch 动辄几十分钟甚至超时，
> 这是本 skill 安装慢的**主要原因**。阿里云实测：CPU 版 torch 105.8MB、
> 一条命令搞定，不需要额外配源。清华 PyPI 镜像国内偶发同步不全/延迟，
> 本项目不推荐（装到一半 404 很难排查）。

### CPU 版还是 CUDA 版？把账摆出来再问用户

| | 下载 | 装完占盘 | 1 万张凭证推理 | 命令 |
|---|---|---|---|---|
| **CPU（默认）** | 105.8MB | 约 540MB | 约 2.5 分钟 | `--index-url https://mirrors.aliyun.com/pypi/simple/` |
| CUDA（N 卡、显存空闲 ≥8GB） | 2.4GB | 4GB+ | 约 1 分钟 | `--index-url https://download.pytorch.org/whl/cu126` |

**省 2.5 分钟/次、代价 2.4GB 下载 + 3.5GB 磁盘**。一次性清洗不值得装 CUDA；
要反复清洗多份账、带宽又充裕才值得。**这条没有"哪个更对"，要花用户的磁盘和带宽，
拿不准就问他**（别替他决定）。

> ⚠️ **旧文档那条"绝不要直接 `pip install torch`，会装 CUDA 版（2.4GB）"是
> Linux 上的经验，在 Windows 上不成立**。实测（2026-10）：
> Windows 上 PyPI / 阿里云镜像给的 torch **就是 CPU 版** ——
> `torch-2.9.1-cp313-cp313-win_amd64.whl` = 110,944,162 字节，
> 与官方 `torch-2.9.1+cpu-...whl`（110,859,971 字节）只差 84KB。
> 也就是说：**Windows 上想省事，直接 `pip install torch -i 阿里云` 就是 CPU 版**，
> 不必为了"避开 CUDA"去引官方源（官方源在国内很慢）。
> 想要 CUDA 版才必须显式走 `download.pytorch.org/whl/cuXXX`。
> （Linux / macOS 上 PyPI 的 torch 自带 CUDA，装前先看轮子大小再决定。）

### 验证 torch

```cmd
:: 期望：版本号后带 +cpu，且 CUDA 为 None
%PY% -c "import torch; print(torch.__version__, torch.version.cuda)"

:: GPU 版：期望 cuda_available 为 True
%PY% -c "import torch; print(torch.cuda.is_available())"
```

### 判断依据（告诉用户该怎么选）

| 情况 | 用哪个 | 1 万张凭证的耗时 |
|---|---|---|
| 没有 N 卡 / 显存 <8GB | **CPU int8**（默认） | 约 **2.5 分钟** |
| 有 N 卡、显存空闲 ≥2.5GB | GPU fp16（自动切换，无需配置） | 约 **1 分钟** |

> 程序自己按显存自动选设备，配置项是 `INFERENCE_DEVICE = "auto"`。
> 两个版本判断结果一致，只是速度和浮点末位略有差别。
> 真账实测（11456 张凭证 / CPU int8）：`units` 213s、`run` 256s
> —— **两条命令各跑一次全量推理**，所以迭代时务必用 `--reuse-units`
> （见 SKILL.md 第 5 步）。
>
> 📦 **装完占多大**：venv 约 0.92GB + 模型 0.62GB ≈ **1.5GB**。
> skill 装在用户的项目里时要先说清楚，别让人以为是自己项目变大了。


---

## 二、模型：4 件交付物

放在 `<SKILL_DIR>/assets/model/`：

```
assets/model/
├── fine_tuned/                ← ① 微调后的 BGE 编码器（model.safetensors 约 620MB）
├── finance_classifier.pt      ← ② 分类头权重（约 1.1MB）
├── subject_to_index.json      ← ③ 科目索引
└── index_to_bucket.json       ← ④ 桶索引
```

**缺失时的下载**（①最稳，三选一）：

```cmd
:: ① python API（推荐：与 modelscope 版本无关，1.39 / 1.4x 都能用）
%PY% -X utf8 -c "import sys; from modelscope import snapshot_download; print(snapshot_download('daijiaoshou/hajishou-V1.0', local_dir=sys.argv[1]))" "assets\model"

:: ② venv 里的 CLI（modelscope ≥1.40 把命令入口挪到了 exe）
venv\Scripts\modelscope.exe download daijiaoshou/hajishou-V1.0 --local-dir assets\model

:: ③ 网络差：让用户去 https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0
::    手动下载这 4 件，按上面目录结构放进 assets/model/
```

> ⚠️ **`%PY% -m modelscope download ...` 已经不能用了**：modelscope 把 CLI 挪到
> 入口 exe 之后，`modelscope` 成了普通包、没有 `__main__`，会报
> `No module named modelscope.__main__; 'modelscope' is a package and cannot be
> directly executed`（本项目 venv 里的 1.39.1 就已经如此）。**别照旧文档抄。**
> 下载会多带下 `.gitattributes` / `README.md` / `fine_tuned/.gitkeep` 几件非交付物，
> **不影响** `status` 判 `model_ready: true`。

> ⚠️ **下载目录必须是 `assets/model/`**：程序只认 `config.py` 里的
> `MODEL_DIR = assets/model`。下到别处（比如 `_storage/`）`status` 会一直
> 报 `model_ready: false`。下完用 `%PY% -X utf8 cli.py status` 确认。
> 发布包里 `assets/model/` 有一个 `.gitkeep` 占位文件 —— 就是为了让手动下载的人
> **找得到落点**（空目录不会进 git）。

### ⚠️ 没有"纯程序降级模式"

**3.0 的 18 个业务桶全部依赖模型。** 模型不齐就不能分类，
`status` 会返回退出码 3、`model_ready` 为 `false`。

这不是缺陷，是设计：3.0 已经把"程序规则打分"整套删掉了
（实测程序打分在 2,000 张凭证里一张都没改变过结果）。
所以**模型缺失时不要试图继续跑**，直接告诉用户需要下载模型。

---

## 三、status 输出怎么看

```cmd
%PY% -X utf8 cli.py status
```

输出是单行 JSON（前缀 `RESULT `）：

| 字段 | 含义 | 不满足怎么办 |
|---|---|---|
| `model_ready` | 4 件交付物是否齐全 | `false` → 走上面的模型下载 |
| `model_info.best_val_acc` | 模型验证准确率（当前 97.39%） | 偏低说明模型老旧，可提示用户 |
| `torch_installed` | torch 装没装 | `false` → 回第一节 |
| `cuda_available` | 有没有可用 GPU | 决定 `inference_device_will_be` |
| `inference_device_will_be` | **实际会用的设备**，如 `cpu(int8)` / `cuda(fp16)` | 装了 CUDA 版却是 `cpu(int8)` → 说明 CUDA 没生效 |
| `artifacts` | 4 件交付物逐个的存在性 | 哪个 `false` 就补哪个 |
| `bucket_count` | 桶总数（应为 **20**） | 不是 20 → 交付物版本不对，别继续 |
| `cashflow.problem_count` | 现金流映射表是否自洽 | 不为 0 → 跑 `scripts/build_cf_maps.py` 重新生成 |
| `cashflow.ambiguous_buckets` | 哪些桶有歧义 | 正常应有值（见 CASHFLOW_GUIDE 第三节） |
| `next` | 程序给的下一步建议（人话） | 照做 |

> 缺依赖时 `status` **不会**再甩一串 traceback，而是给规范出参：
> `{"ok": false, "stage": "env", "missing": ["torch"], "hint": "..."}`，
> 退出码 `4`。看到 `stage: "env"` 就去装依赖，**这不是代码 bug、别改代码**。

---

## 四、字段配置的细节（SKILL.md 第 2 步展开）

**必需字段**：凭证号、一级科目、借方金额、贷方金额。
**选填字段**：摘要（强烈建议有，模型主要靠它）、科目明细/二级科目、制单人。

程序是**角色化匹配**（每个字段有专属列名 + 排除名单），能识别这些真实表头：

| 字段 | 会被识别为 |
|---|---|
| 凭证号 | 凭证号、凭证编号、凭证字号、voucher、单号 |
| 一级科目 | 科目、一级科目、会计科目、科目编码、总账科目 |
| 科目明细 | 科目名称、科目明细、明细科目、二级科目 |
| 摘要 | 摘要、说明、备注、业务描述 |
| 借方金额 | 借方金额、借方、借方发生额、借 |
| 贷方金额 | 贷方金额、贷方、贷方发生额、贷 |
| 制单人 | 制单人、制表人、记账人、录入人 |

### 为什么「科目名称」不会抢走「科目」

旧实现是"所有字段一起子串扫描"，结果「科目名称」含子串「科目」，
会把一级科目列抢走，导致整份结果作废。

现在按**角色**匹配：`subject` 字段有专属列名集合，并显式排除
`{科目名称, 科目明细, 明细科目, 二级科目, 会计科目名称, 一级科目名称}`。
且**一列只能被一个字段占用**（命中即从池子里拿走）。

### 映射对不上时

写一份映射 JSON 传给 `--set`：

```json
{
  "voucher_no": "凭证字号",
  "subject": "存货科目",
  "debit": "借方发生额",
  "credit": "贷方发生额"
}
```

再跑一次 `check` 确认 `ok` 为 `true` 且 `preview` 的样例值对得上，再进第 3 步。

---

## 五、目录结构与数据安全

```
summary_cleaner_v3/            ← 本 skill 根目录
├── SKILL.md                   ← ★ 操作流程（AI 主读）
├── cli.py                     ← 唯一对外接口
├── requirements.txt
├── jc/                        ← 代码（AI 不需要直接碰）
│   ├── etl.py                     读文件 / 认列名 / 写回 / 导出
│   ├── hard_rules.py              两条写死的机械规则
│   ├── model.py                   加载交付物 / 批量推理 / 算置信度
│   ├── units.py                   切割第二层：摘要归族
│   ├── review_views.py            units --summary / --families 两个复核视图
│   ├── cashflow.py                业务桶 → 现金流量项目 + 补丁
│   ├── pipeline.py                全流程编排
│   ├── config.py                  路径与常量
│   ├── cli.py                     命令实现（根目录 cli.py 是薄壳）
│   └── training/              ← 【仅开发版】训练代码，发布时剔除
├── assets/                    ← 业务知识 + 配置 + 模型
│   ├── bucket_definitions.md      20 个桶的权威定义（判断前必读）
│   ├── AI_JUDGMENT_NOTES.md       AI 积累库 ★ 要持续更新
│   ├── cf/                        现金流映射表（3 件，业务配置可手改）
│   └── model/                     ← 模型落点（发布包里有 .gitkeep 占位）
│       ├── fine_tuned/                ① 微调后的 BGE 编码器（约 620MB）
│       ├── finance_classifier.pt      ② 分类头权重（约 1.1MB）
│       ├── subject_to_index.json      ③ 科目索引
│       └── index_to_bucket.json       ④ 桶索引
├── reference/                 ← 按需查阅（SKILL.md 会告诉你何时看）
│   ├── SETUP.md                   环境与模型准备（本文）
│   ├── REVIEW_GUIDE.md            复核指引 + 错误分流
│   ├── AI_REVIEW_GUIDE.md         A 表：跨桶模式 → 桶（B/C 表是训练数据筛选口径）
│   └── CASHFLOW_GUIDE.md          现金流量表编制口径
├── scripts/
│   ├── build_cf_maps.py           生成 assets/cf/
│   └── package_release.py         打包用户版（【仅开发版】）
└── _dev/                      ← 【仅开发版】训练数据
```

> **跑起来会有 `jc/__pycache__`**（Python 正常现象，可以删，删了会再生成）。
> 整个 skill 目录装完约 **1.5GB**（venv 0.92GB + 模型 0.62GB）。

> **列名识别细节在本文第四节**（列名那套规则就写在那里）。

**数据安全**：全流程**本地运行**，序时账不出本机。
运行期**不写任何缓存目录**（`_storage/` 是 1.0/2.0 的遗留概念，3.0 已无此物）：
结果只落到 `--out` 指定的输出目录。
补丁文件由你（AI）写，也应放在输出目录，**不要写进 skill 目录**。

---

## 六、常见故障

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `stage: "env"`、`missing: ["torch"]` | 依赖没装齐（**不是代码 bug**） | 回第一节装；别改代码 |
| `No module named modelscope.__main__` | 照旧文档用了 `-m modelscope` | 换第二节 ① 的 python API 一行命令 |
| `--reuse-units` 报「不像是 units 命令的出参」 | 传的不是 `units --out-json` 那份 | 先跑 `units ... --out-json 单元.json` |
| `--reuse-units` 命中率很低 | 清单用了 `--limit`/`--offset`（不完整），或 `--sim` 与本次不同 | 用**完整**清单；`--sim` 和 `units` 保持一致 |
| `check` 报 `missing_required_cn` 有「一级科目」 | 表头命名特殊（如「存货科目」） | 生成映射 JSON 用 `--set` 传 |
| `run` 报「必需字段缺失」 | 没跑 `check` 确认映射 | 先 `check`，对不上就补 `--set` |
| `units` 报「不支持的格式」 | 传了非序时账文件 | 传 xlsx/xls/xlsm/csv |
| `coverage` 明显低于 1.0 | 有凭证没分到桶 | 看桶分布，排查科目是否缺失 |
| `cuda_available: false` 但用户有 N 卡 | torch 装成了 CPU 版 | 换 CUDA 版 torch |
| 模型加载报 `num_subjects` 不一致 | 交付物版本不匹配 | 重新下载 4 件交付物 |
| 报 `FileNotFoundError: 交付物①缺失` | 模型没下全 | 回第二节 |
| 报 `现金流映射表缺失` | `assets/cf/` 被删了 | 跑 `scripts/build_cf_maps.py` |
| 补丁报「行号不是整数 / 不是合法的现金流量项目」 | 补丁格式错 | 见 SKILL.md 第 4 步的补丁格式 |
| 内存不足 / OOM | 凭证量极大 + 低配机器 | 拆分文件分批跑；或关掉其他程序 |
| 重定向/管道拿到的出参读不了 | **shell 的锅**：PowerShell 的 `1>` 写出来的是 **UTF-16LE**（带 0xFF 0xFE BOM），按 UTF-8 解必崩 —— 程序本身已经输出 UTF-8 了，但 shell 会再转一道 | 别用重定向：加 `--out-json 出参.json`，让**程序自己**用 UTF-8 写纯 JSON |
| 中文乱码 | 控制台代码页不是 65001 | 出参被**管道/重定向**时程序已自己用 UTF-8；只有在**控制台里直接看**才可能乱（那是终端代码页的问题，`chcp 65001` 或换 Windows Terminal） |
