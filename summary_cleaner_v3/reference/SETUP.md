# 环境与模型准备（SKILL.md 第 0–1 步的展开）

SKILL.md 只留流程与判断点，具体命令和踩坑清单放这里。

---

## 一、选环境：优先复用，没有再建

```cmd
:: ① 如果项目里已有 venv（父目录的 venv），先用它试
..\venv\Scripts\python.exe -c "import pandas, torch, transformers, openpyxl; print('OK')"

:: ② 上面报错（没有 venv 或缺依赖）→ 在 skill 目录自建一个
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

**装 torch 有一条铁律**：

```cmd
:: CPU 版 torch：122MB、装完约 540MB —— 大多数用户用这个
venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
:: 国内慢就用上海交大镜像：
:: --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu

:: ❌ 绝对不要直接 pip install torch —— 那会装 CUDA 版（下载 2.4GB、装完 4GB+）
```

**有 NVIDIA 显卡的用户（显存 ≥8GB）**才装 CUDA 版，这样推理会自动走 GPU：

```cmd
venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu126
```

### 验证 torch

```cmd
:: CPU 版：期望输出带 +cpu，且 CUDA 为 None
venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda)"

:: GPU 版：期望 cuda_available 为 True
venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

### 判断依据（告诉用户该怎么选）

| 情况 | 用哪个 | 1 万张凭证的耗时 |
|---|---|---|
| 没有 N 卡 / 显存 <8GB | **CPU int8**（默认） | 约 **2.5 分钟** |
| 有 N 卡、显存空闲 ≥2.5GB | GPU fp16（自动切换，无需配置） | 约 **1 分钟** |

> 程序自己按显存自动选设备，配置项是 `INFERENCE_DEVICE = "auto"`。
> 两个版本判断结果一致，只是速度和浮点末位略有差别。

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

**缺失时的下载**（三选一）：

```cmd
:: ① 推荐：双击 download_model.bat（若项目里带了）
:: ② 命令行
venv\Scripts\python.exe -m modelscope download daijiaoshou/hajishou-V1.0 --local_dir _storage
:: ③ 网络差：让用户去 https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0
::    手动下载 4 件，按上面目录放好
```

### ⚠️ 没有"纯程序降级模式"

**3.0 的 18 个业务桶全部依赖模型。** 模型不齐就不能分类，
`status` 会返回退出码 3、`model_ready` 为 `false`。

这不是缺陷，是设计：3.0 已经把"程序规则打分"整套删掉了
（实测程序打分在 2,000 张凭证里一张都没改变过结果）。
所以**模型缺失时不要试图继续跑**，直接告诉用户需要下载模型。

---

## 三、status 输出怎么看

```cmd
venv\Scripts\python.exe -X utf8 cli.py status
```

输出是单行 JSON（前缀 `RESULT `）：

| 字段 | 含义 | 不满足怎么办 |
|---|---|---|
| `model_ready` | 4 件交付物是否齐全 | `false` → 走上面的模型下载 |
| `model_info.best_val_acc` | 模型验证准确率（当前 97.39%） | 偏低说明模型老旧，可提示用户 |
| `torch_installed` | torch 装没装 | `false` → 回第一节 |
| `cuda_available` | 有没有可用 GPU | 决定 `inference_device_will_be` |
| `bucket_count` | 桶总数（应为 **20**） | 不是 20 → 交付物版本不对，别继续 |
| `cashflow.problem_count` | 现金流映射表是否自洽 | 不为 0 → 跑 `scripts/build_cf_maps.py` 重新生成 |
| `cashflow.ambiguous_buckets` | 哪些桶有歧义 | 正常应有值（见 CASHFLOW_GUIDE 第三节） |

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
│   ├── cashflow.py                业务桶 → 现金流量项目 + 补丁
│   ├── pipeline.py                全流程编排
│   ├── config.py                  路径与常量
│   ├── cli.py                     命令实现（根目录 cli.py 是薄壳）
│   └── training/              ← 【仅开发版】训练代码，发布时剔除
├── assets/                    ← 业务知识 + 配置 + 模型
│   ├── bucket_definitions.md      20 个桶的权威定义（判断前必读）
│   ├── AI_JUDGMENT_NOTES.md       AI 积累库 ★ 要持续更新
│   ├── cf/                        现金流映射表（3 件，业务配置可手改）
│   └── model/                     4 件模型交付物
├── reference/                 ← 按需查阅（SKILL.md 会告诉你何时看）
│   ├── SETUP.md                   环境与模型准备（本文）
│   ├── FIELD_ENCODING.md          列名识别细节
│   ├── REVIEW_GUIDE.md            复核指引 + 错误处理
│   ├── AI_REVIEW_GUIDE.md         跨桶决策表（大量硬规则）
│   └── CASHFLOW_GUIDE.md          现金流量表编制口径
├── scripts/
│   ├── build_cf_maps.py           生成 assets/cf/
│   └── package_release.py         打包用户版
└── _dev/                      ← 【仅开发版】训练数据
```

**数据安全**：全流程**本地运行**，序时账不出本机。
运行期只在 `_storage/` 写缓存，不含客户凭证数据。
补丁文件由你（AI）写，也应放在输出目录，**不要写进 skill 目录**。

---

## 六、常见故障

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `check` 报 `missing_required_cn` 有「一级科目」 | 表头命名特殊（如「存货科目」） | 生成映射 JSON 用 `--set` 传 |
| `run` 报「必需字段缺失」 | 没跑 `check` 确认映射 | 先 `check`，对不上就补 `--set` |
| `sample` 报「这不是分类结果」 | 传了原始序时账 | 传 `run` 导出的 `<名>_结果.xlsx` |
| `coverage` 明显低于 1.0 | 有凭证没分到桶 | 看桶分布，排查科目是否缺失 |
| `cuda_available: false` 但用户有 N 卡 | torch 装成了 CPU 版 | 换 CUDA 版 torch |
| 模型加载报 `num_subjects` 不一致 | 交付物版本不匹配 | 重新下载 4 件交付物 |
| 报 `FileNotFoundError: 交付物①缺失` | 模型没下全 | 回第二节 |
| 报 `现金流映射表缺失` | `assets/cf/` 被删了 | 跑 `scripts/build_cf_maps.py` |
| 补丁报「行号不是整数 / 不是合法的现金流量项目」 | 补丁格式错 | 见 SKILL.md 第 4 步的补丁格式 |
| 内存不足 / OOM | 凭证量极大 + 低配机器 | 拆分文件分批跑；或关掉其他程序 |
| 中文乱码 | Windows 控制台编码 | 命令前加 `-X utf8`（本手册已统一加） |
