# 环境准备（INSTALL.md 第 0 步的展开）

仓库里所有模块共用一份环境：一份 venv、一份依赖、一份模型交付物。

- 读表与业务桶判定：`core/ledger/` + `core/classify/`
- 模型推理与交付物：`core/nn/`（交付物在 `core/nn/_storage/`）
- 现金流映射表：`features/cashflow/assets/cf/`

---

## 一、装环境

```cmd
:: ① 建 venv（在仓库根目录里，一次）
python -m venv venv

:: ② 装 torch（Windows 上 PyPI/阿里云给的就是 CPU 版，约 106MB）
venv\Scripts\python.exe -m pip install torch --index-url https://mirrors.aliyun.com/pypi/simple/

:: ③ 装其余依赖
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
venv\Scripts\python.exe -m pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/

:: ④ 自检：映射表自洽 + 模型 4 件齐
venv\Scripts\python.exe -X utf8 -m features.cashflow.cli status
```

`status` 要看到三件事：

| 字段 | 期望 |
|---|---|
| `missing_modules` | `[]` |
| `upstream.model_ready` | `true`（模型 4 件齐） |
| `cf_maps.validate_problems` | `0` |

### 镜像抽风 / 下载被截断

| 源 | 地址 | 备注 |
|---|---|---|
| 阿里云（默认） | `https://mirrors.aliyun.com/pypi/simple/` | 本项目长期实测稳定 |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple/` | 阿里云 `IncompleteRead` 时换它 |
| 清华 | `https://pypi.tuna.tsinghua.edu.cn/simple` | 最后的选择 |

网络差时**一次装一个包**，好定位是哪个在断；一次装六个，任意一个断掉整条命令都失败。

### CPU 版还是 CUDA 版？

| | 下载 | 装完占盘 | 1 万张凭证推理 | 命令 |
|---|---|---|---|---|
| **CPU（默认）** | 105.8MB | 约 540MB | 约 2 分钟 | `--index-url https://mirrors.aliyun.com/pypi/simple/` |
| CUDA（N 卡、显存空闲 ≥2.5GB） | 2.4GB | 4GB+ | 约 1 分钟 | `--index-url https://download.pytorch.org/whl/cu126` |

省 1 分钟/次、代价 2.4GB 下载 + 3.5GB 磁盘。一次性编表不值得装 CUDA；
要反复跑多份账、带宽充裕才值得。

> Windows 上 `pip install torch -i 阿里云` **就是 CPU 版**（实测
> `torch-2.9.1-cp313-cp313-win_amd64.whl` = 110,944,162 字节，与官方 `+cpu`
> 轮子只差 84KB）。想要 CUDA 版才必须显式走 `download.pytorch.org/whl/cuXXX`。
> Linux / macOS 上 PyPI 的 torch 自带 CUDA，装前先看轮子大小。

验证：

```cmd
:: 期望：版本号后带 +cpu，CUDA 为 None
venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda)"

:: GPU 版：期望 True
venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"
```

设备是**自动**选的（`core/classify/config.py::NN_INFERENCE_DEVICE = "auto"`）：
显存空闲 ≥2.5GB 走 GPU fp16，否则 CPU int8（量化后约 42 条/秒）。
两个版本判断结果一致，只是速度和浮点末位略有差别。

---

## 二、模型：4 件交付物

```
core/nn/_storage/
├── fine_tuned/                ← ① 微调后的 BGE 编码器（约 620MB）
├── finance_classifier.pt      ← ② 分类头权重（约 1.1MB）
├── subject_to_index.json      ← ③ 科目索引
└── index_to_bucket.json       ← ④ 桶索引
```

下载命令见 `INSTALL.md`「模型」一节。**这 4 件必须版本一致**：不一致时直接报错
（这是刻意的，宁可不跑也不出错账）。

首次推理还会把基础模型（BGE）落到 `core/nn/models/`，约 1.6GB。
`_storage/` 与 `models/` 都不进 git。

重训覆盖交付物用 `python -m tools.train.train_v10`。

---

## 三、`status` 输出怎么看

```cmd
venv\Scripts\python.exe -X utf8 -m features.cashflow.cli status
```

| 字段 | 含义 |
|---|---|
| `release` / `version` | 现金流功能名与版本 |
| `missing_modules` | 缺哪些第三方包（`find_spec` 查的，不 import） |
| `python` | Python 版本 |
| `root` | 本功能目录（`features/cashflow`） |
| `upstream.path` | 实际用到的判定内核在哪（**装错版本时最有用**） |
| `upstream.buckets` | 业务桶数（当前 33） |
| `upstream.model_artifacts` | 4 件交付物逐个的存在性 |
| `upstream.model_ready` | 4 件是否齐 |
| `cf_maps.items` | 现金流量项目数（24） |
| `cf_maps.buckets` | 映射表里的桶键数（含旧 18 桶兼容键） |
| `cf_maps.validate_problems` | 映射表自洽性问题的个数，应为 0 |

---

## 四、字段配置的细节

列名由 `core/classify/classifier.py::auto_detect_columns` 识别，
现金流只校验四列齐不齐：`voucher_no / subject / debit / credit`。

| 我们的字段名 | 认这些列名 |
|---|---|
| `voucher_no` | 凭证号、凭证编号、凭证字号、voucher、单号 |
| `subject` | 一级科目、会计科目；或**列名正好叫「科目 / 总账科目」** |
| `subject_name` | 科目名称、科目明细、明细科目、二级科目、会计科目名称 |
| `summary` | 摘要、说明、备注、业务描述、description |
| `debit` / `credit` | 借方金额、借方、借方发生额 / 贷方金额、贷方、贷方发生额 |

两个刻意的处理：

1. **「会计科目名称」不会被认成一级科目**——它含「会计科目」四个字，
   不排除的话整本账的科目都会变成明细名。
2. **光叫「科目」的列会被认**，但只做**精确相等**匹配（子串匹配会被「科目编码」抢走）。

认错了列才手工覆盖。**覆盖只有一处**：任务配置那一步定下来的映射，之后
四个功能都用它（以前现金流还带一个自己的 `check --set`，那套已经删了，
免得同一个映射要改两个地方）：

```cmd
:: 1) 认列名：哪些认出来了、哪些没认出来（只读，不建任务）
venv\Scripts\python.exe -X utf8 journal.py inspect "序时账.xlsx" --domain ledger

:: 2) 把确认后的映射写成文件（形如 {"subject_l1": "科目"}），建任务时带上
venv\Scripts\python.exe -X utf8 journal.py run "序时账.xlsx" --feature cashflow --mapping @mapping.json
```

`journal` 命令行与插件走同一套识别和同一次配置。

---

## 五、目录与数据安全

```
features/cashflow/
├── __init__.py            阶段编排（Feature 定义）
├── stages.py              本功能独有的阶段：编表、复核现金流条目、导出
├── judge.py               桶 + 货币资金方向 → 现金流量项目
├── rows.py                逐行写回（项目/方向/判定/复核标记）
├── export.py              出 Excel
├── stats.py               结果概况（阶段、页面、导出共用）
├── cli.py                 python -m features.cashflow.cli status|validate（只读自检）
├── config.py              映射表位置
└── assets/cf/             现金流映射表（JSON 即权威，手工可改）
```

- **客户数据只留在任务目录**（`_storage_jobs/<id>/source|out`）或用户指定的输出目录，
  代码目录里不留任何序时账/结果文件；
- 全部本地运行，**不上传、不联网**（除了装依赖时）；
- 产出文件永远由程序写，AI 不手改。

---

## 六、常见故障

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `upstream.model_ready: false` | 模型 4 件缺失/不全 | 按第二节下载，注意 4 件要配套 |
| `KeyError: 'xxx'` 之类在 torch 里 | torch 装成了 CUDA 版但机器没 N 卡 | 卸掉重装 CPU 版 |
| `run` 报"必需字段缺失：一级科目" | 列名没认出来 | `check` 看 `mapping`，用 `--set` 指定 |
| 待复核特别多（>10%） | 业务桶名与映射表对不上 | `validate` 看 `problem_count`，补 `bucket_to_cf.json` |
| `validate` 报"引用了未知项目" | 映射表里项目名拼错 | 以 `cf_items.json` 的 24 个项目名为准 |
| 输出是 csv 不是 xlsx | 超过 50 万行 | 正常的，Excel 单表上限 104 万行 |
