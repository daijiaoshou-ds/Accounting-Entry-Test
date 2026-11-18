# 环境准备（SKILL.md 第 0 步的展开）

> 本包**不含模型**。模型、依赖口径都在上游 `summary_cleaner`（序时账清洗），
> 所以这里装的环境，两个包共用一份。

---

## 一、装环境

```cmd
:: ① 建 venv（在 cashflow_writer 目录里）
python -m venv venv

:: ② 装 torch（Windows 上 PyPI/阿里云给的就是 CPU 版，约 106MB）
venv\Scripts\python.exe -m pip install torch --index-url https://mirrors.aliyun.com/pypi/simple/

:: ③ 装其余依赖（requirements.txt 里已写好阿里云源）
venv\Scripts\python.exe -m pip install -r requirements.txt

:: ④ 自检
venv\Scripts\python.exe -X utf8 cli.py status
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

### CPU 版还是 CUDA 版？把账摆出来再问用户

| | 下载 | 装完占盘 | 1 万张凭证推理 | 命令 |
|---|---|---|---|---|
| **CPU（默认）** | 105.8MB | 约 540MB | 约 2 分钟 | `--index-url https://mirrors.aliyun.com/pypi/simple/` |
| CUDA（N 卡、显存空闲 ≥2.5GB） | 2.4GB | 4GB+ | 约 1 分钟 | `--index-url https://download.pytorch.org/whl/cu126` |

省 1 分钟/次、代价 2.4GB 下载 + 3.5GB 磁盘。一次性编表不值得装 CUDA；
要反复跑多份账、带宽充裕才值得。**这条没有"哪个更对"，要花用户的磁盘和带宽，
拿不准就问他。**

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

设备是**自动**选的（`summary_cleaner/v2/config.py::NN_INFERENCE_DEVICE = "auto"`）：
显存空闲 ≥2.5GB 走 GPU fp16，否则 CPU int8（量化后约 42 条/秒）。
两个版本判断结果一致，只是速度和浮点末位略有差别。

---

## 二、上游模型：4 件交付物

模型不由本包装，位置在上游：

```
summary_cleaner/nn/_storage/
├── fine_tuned/                ← ① 微调后的 BGE 编码器（约 620MB）
├── finance_classifier.pt      ← ② 分类头权重（约 1.1MB）
├── subject_to_index.json      ← ③ 科目索引
└── index_to_bucket.json       ← ④ 桶索引
```

下载与校验见上游 `summary_cleaner` 的 `reference/SETUP.md`。
**这 4 件必须版本一致**：不一致时上游会直接报错（这是刻意的，宁可不跑也不出错账）。

### 部署形态：两个包必须在同一父目录

```
<anywhere>/
├── summary_cleaner/         ← 上游：读表 + 判业务桶
└── cashflow_writer/         ← 本包：编现金流量表
```

`cfw/config.py::ensure_upstream()` 会先在 `sys.path` 里找 `summary_cleaner`，
找不到就把**包根的上一级**（上面这个 `<anywhere>`）加进 `sys.path` 再试一次。
还是找不到就报 `stage: upstream` 并给出期望路径——**这是部署问题，不是代码 bug**。

---

## 三、`status` 输出怎么看

```cmd
venv\Scripts\python.exe -X utf8 cli.py status
```

| 字段 | 含义 |
|---|---|
| `release` / `version` | 本包名与版本 |
| `missing_modules` | 缺哪些第三方包（`find_spec` 查的，不 import） |
| `python` | Python 版本 |
| `upstream.path` | 实际用到的 `summary_cleaner` 在哪（**装错版本时最有用**） |
| `upstream.buckets` | 上游业务桶数（当前 33） |
| `upstream.model_artifacts` | 4 件交付物逐个的存在性 |
| `upstream.model_ready` | 4 件是否齐 |
| `cf_maps.items` | 现金流量项目数（24） |
| `cf_maps.buckets` | 映射表里的桶键数（含旧 18 桶兼容键） |
| `cf_maps.validate_problems` | 映射表自洽性问题的个数，应为 0 |

`ok: false` 时看 `stage`：`env` = 缺依赖，`upstream` = 找不到上游或模型缺件。

---

## 四、字段配置的细节

列名由**上游**识别（`summary_cleaner/v2/classifier.py::auto_detect_columns`），
本包只校验四列齐不齐：`voucher_no / subject / debit / credit`。

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

认错了列才手工覆盖：

```cmd
venv\Scripts\python.exe -X utf8 cli.py check "序时账.xlsx" --set 一级科目="科目" --set 借方金额="借方发生额"
```

---

## 五、目录结构与数据安全

```
cashflow_writer/
├── cli.py                 入口薄壳
├── cfw/                   代码（判定、写回、导出、编排、CLI）
├── assets/cf/             现金流映射表（JSON 即权威，手工可改）
├── reference/             口径与安装文档
└── _dev/                  本地测试账（不入库）
```

- **客户数据只留在用户指定的输出目录**，包目录里不留任何序时账/结果文件；
- 全部本地运行，**不上传、不联网**（除了装依赖时）；
- 产出文件永远由程序写（`run` 一次一个 Excel），AI 不手改。

---

## 六、常见故障

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `status` 报 `stage: upstream` | 两个包不在同一父目录 | 按第二节的目录摆放 |
| `upstream.model_ready: false` | 模型 4 件缺失/不全 | 按上游 SETUP 下载，注意 4 件要配套 |
| `KeyError: 'xxx'` 之类在 torch 里 | torch 装成了 CUDA 版但机器没 N 卡 | 卸掉重装 CPU 版 |
| `run` 报"必需字段缺失：一级科目" | 列名没认出来 | `check` 看 `mapping`，用 `--set` 指定 |
| 待复核特别多（>10%） | 上游桶名与映射表对不上 | `validate` 看 `problem_count`，补 `bucket_to_cf.json` |
| `validate` 报"引用了未知项目" | 映射表里项目名拼错 | 以 `cf_items.json` 的 24 个项目名为准 |
| 输出是 csv 不是 xlsx | 超过 50 万行 | 正常的，Excel 单表上限 104 万行 |
