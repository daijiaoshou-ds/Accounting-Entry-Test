# SKILL — 会计分录分析工具箱：部署指南（给 AI 助手）

> 本文件是给你（AI 助手）的操作手册。用户不懂代码，**你要替 TA 把每一步做到位并验证通过**。
> 用户全程只需要回答几个问题、双击几个 `.bat`。
>
> **执行顺序：第零步（问版本）→ Step 1～6 → Step 7（按版本逐项验收）。**

---

## 零、第零步：先问用户装哪个版本（必做，别跳过）

工具箱有三种装法，**先探测硬件、再问用户**，最后才动手装：

### 0.1 先探测（三条命令，30 秒）

```cmd
:: 1) Python 版本（需要 3.11 ~ 3.13）
python --version

:: 2) 有没有 NVIDIA 显卡、显存多大（没有输出/报「不是内部或外部命令」= 没有 N 卡）
nvidia-smi --query-gpu=name,memory.total --format=csv

:: 3) 磁盘剩余空间（Windows 11 24H2 起没有 wmic 了，用这条）
powershell -NoProfile -Command "Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}"
```

> 探测结果只用来「筛掉不可行的版本」：没 N 卡（或显存 <8GB）就别提供训练版；
> 剩余空间 <4GB 就别提供模型版。用户仍可自己决定要不要更轻/更全的版本。

### 0.2 再问用户（话术可直接用）

> 这个工具箱有三种装法，你想装哪种？
> ① **功能版**：装得最快、占空间最小（约 1GB）。对方科目分析、会计分录测试、银行流水匹配、小工具都能用；
> **序时账清洗与现金流量表跑不了**——业务桶 100% 由模型判定，没有模型这一环会明确报错停下（不会静默出错账）。
> ② **模型版（推荐）**：多装 620MB 的 AI 模型（共约 2.5GB）。四个功能全部可用。
> ③ **训练版**：给你自己训练模型用，**必须是 NVIDIA 显卡且显存 ≥8GB**，还要占约 7GB 磁盘。

### 0.3 决策规则

| 探测结果 | 怎么推荐 |
|---|---|
| 没有 `nvidia-smi` 输出，或显存 < 8GB | **不要提供训练版**（训练必须 GPU，CPU 训练慢到不可用）。只在①功能版 / ②模型版里选 |
| 磁盘剩余 < 4GB | 只能选 ①功能版（模型版约需 3GB，训练版约需 8GB） |
| 网络慢 / 用户赶时间 | 推荐 ②模型版也不亏（模型 620MB，一条命令）；实在赶时间才选 ① |
| 其它情况 | **默认推荐 ②模型版** |

### 0.4 三个版本装完的差别（给用户解释时用）

| | ① 功能版 | ② 模型版（推荐） | ③ 训练版 |
|---|---|---|---|
| 会计分录测试 / 对方科目分析 / 银行流水匹配 / 小工具 | ✅ | ✅ | ✅ |
| 序时账清洗 | ✅ 纯程序规则模式（页面顶部有黄色提示） | ✅ **+ AI 融合打分** | ✅ + 可自己训练模型 |
| 要装什么 | `requirements.txt` | `+ torch(CPU) + requirements-nn.txt` | `+ CUDA torch + requirements-train.txt` |
| 下载/占用 | 约 1GB | 约 2.5GB（含模型 620MB） | 约 7GB（含 CUDA torch 4GB + 基座模型） |
| 耗时 | 3～6 分钟 | 10～20 分钟 | 30～60 分钟（训练另算） |
| 硬件要求 | 无 | 无（CPU 推理，1 万条约 4 分钟） | **NVIDIA 显卡，显存 ≥8GB** |

---

## 一、这个项目是什么

一套面向会计师事务所 / 财务人员的**本地**会计工具，主界面是 **DSH 插件**
（左侧边栏的「审计工作台」+ 对话里的「会计分录」页签），命令行用 `journal.py`。

已经接进 jobflow 的四个功能：

1. **序时账清洗** —— 读表 → 科目归一化 → 模型判业务桶（33 桶）→ 四类硬约束 → 写回
2. **现金流量表编写** —— 接清洗的桶 + 货币资金方向，编现金流量项目
3. **对方科目分析** —— 多借多贷拆成一对一
4. **会计分录测试** —— 业务群聚类 + 异常打分

另有 **银行流水匹配**（`bank_statement_matcher/`，独立子项目，还没接进 jobflow）；
旧的小工具集、Streamlit 界面、独立网页版已移入 `_archive/`，不参与安装。

纯本地运行，数据不出本机。

---

## 二、Step 1 — Python 环境

- Python 版本：**3.11 ～ 3.13 均可**（推荐 3.13）。
- 没装 Python：引导到 https://www.python.org/downloads/ 下载，
  Windows 安装时**务必勾选 Add python.exe to PATH**。
- 在项目目录建虚拟环境（**优先教 cmd**，财务人员基本用默认命令行）：

```cmd
cd /d 项目目录
python -m venv venv
venv\Scripts\activate.bat
```

> 后面所有 `pip` 命令都建议写成 `venv\Scripts\python.exe -m pip ...`，**不激活也不会装错地方**。

---

## 三、Step 2 — 获取代码

```cmd
git clone https://github.com/daijiaoshou-ds/Accounting-Entry-Test.git
cd Accounting-Entry-Test
```

> 没装 git 就引导装 https://git-scm.com/downloads；实在装不了，让用户从网页 **Download ZIP** 解压。
> 克隆地址以 README 里给出的为准。

**克隆完必须知道的两件事**（仓库里故意不带，见 Step 4 / Step 5）：

| 不带的东西 | 为什么 | 谁需要 |
|---|---|---|
| `assets/fonts/simsun.ttc` 中文字体（约 18MB） | 体积大且涉及字体版权 | 「PDF 索引号生成」与「PDF 合并里的 Excel/图片转换」 |
| `core/nn/_storage/` 模型（约 620MB） | 太大，走 ModelScope 单独下载 | 只有②模型版 / ③训练版 |

---

## 四、Step 3 — 装依赖（按第零步选的版本，三选一）

**国内统一用阿里云源**：`-i https://mirrors.aliyun.com/pypi/simple/`
（清华源近期不稳定，实测部分请求直接 403，不要再默认用它。）

> 每条命令都在项目目录执行；`venv\Scripts\python.exe` 是刚建好的虚拟环境解释器。

### ① 功能版（1 条命令）

```cmd
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

### ② 模型版（3 条命令，**顺序不能换**）

```cmd
:: 1) 基础依赖
venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

:: 2) CPU 版 torch（必须走 PyTorch 官方 CPU 源，见下方警告）
venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
::   国内若官方源慢，可用上海交大镜像：
::   venv\Scripts\python.exe -m pip install torch --index-url https://mirror.sjtu.edu.cn/pytorch-wheels/cpu

:: 3) NN 推理依赖（torch 已装好，这里不会再动它）
venv\Scripts\python.exe -m pip install -r requirements-nn.txt -i https://mirrors.aliyun.com/pypi/simple/
```

> **⚠️ 千万不要直接 `pip install torch`**：PyPI 及国内 PyPI 镜像上给的是 **CUDA 版**——
> 下载 2.4GB、装完 4GB+（本机实测 4.0GB）；而 CPU 版只有 **122MB、装完约 540MB**，
> 推理速度与精度完全一样。装错了就让用户跑
> `pip uninstall -y torch` 然后按上面第 2 步重装。
>
> **验证（必须带 `+cpu`，且 CUDA 为 None）：**
> ```cmd
> venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda)"
> :: 期望输出类似：2.14.0+cpu None
> ```

### ③ 训练版（在②的基础上再 2 步）

```cmd
:: 1) 卸载 CPU 版 torch，换 CUDA 版（cu126 = CUDA 12.6，按本机 CUDA 版本选）
venv\Scripts\python.exe -m pip uninstall -y torch
venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu126

:: 2) 训练增强
venv\Scripts\python.exe -m pip install -r requirements-train.txt -i https://mirrors.aliyun.com/pypi/simple/
```

前置条件（不满足就别装训练版）：

- `nvidia-smi` 能列出显卡，**显存 ≥8GB**（fp16 BGE-large 约 1.3GB + 批量激活余量）；
- CUDA 版 torch 装完约 4GB（下载 2.4GB）；
- **训练数据要自己积累**：跑几次「序时账清洗」并人工纠错后，训练页才能看到数据
  （模型仓库只放权重，不含训练数据）。所以训练版用户通常先当模型版用一段时间。

---

## 五、Step 4 — 补字体资源（PDF 功能需要）

**背景**：仓库不携带中文字体。「PDF索引号生成」和「PDF 合并里勾选『转换 Excel/图片』」需要它，
缺了会报「缺少字体文件 simsun.ttc」。

**做法（Windows，系统自带宋体，直接复制一份）：**

```cmd
mkdir assets\fonts
copy C:\Windows\Fonts\simsun.ttc assets\fonts\
```

PowerShell：

```powershell
New-Item -ItemType Directory -Force assets\fonts | Out-Null
Copy-Item C:\Windows\Fonts\simsun.ttc assets\fonts\
```

- 文件名**必须是 `simsun.ttc`**（程序按这个名字找）；没有 simsun 时可用 `msyh.ttc`（微软雅黑）
  或 `simhei.ttf` 复制过来后**改名**为 `simsun.ttc`。
- 验证：`dir assets\fonts` 应看到 `simsun.ttc`，大小约 18MB。
- 兜底：就算忘了这一步，页面在真正用到时也会自动从系统字体复制一份（仅 Windows 有效）；
  只有系统里确实没有中文字体时才会失败，那就需要用户自己找一个中文字体文件放进去。

---

## 六、Step 5 — 下载模型（仅②模型版 / ③训练版）

> 只装功能版（①）的用户跳过本节。

### 6.1 要下什么

- **微调交付物（4 件，共 622MB）**——必须手动下载，放到 `core/nn/_storage/`：

```
core/nn/_storage/
├── fine_tuned/                # ① 微调后的 BGE 编码器（model.safetensors 约 620MB）
├── finance_classifier.pt      # ② 分类头权重（约 1.1MB）
├── subject_to_index.json      # ③ 科目索引
└── index_to_bucket.json       # ④ 桶索引
```

- **基座模型（仅训练版）**——代码自动从 ModelScope 下载，缓存在 `core/nn/models/`；
  训练页默认用 `bge-large-zh-v1.5`（约 1.3GB），显存紧张时可在页面改成 `bge-base-zh-v1.5`（约 440MB）。

### 6.2 怎么下（三选一）

**方式一（推荐，零代码）：双击 `download_model.bat`** —— 自动激活 venv + 下载 + 验证。

**方式二（命令行）**，在项目目录执行：

```cmd
venv\Scripts\python.exe -m modelscope download daijiaoshou/hajishou-V2.0 --local_dir core\nn\_storage
```

**方式三（网络差/命令行失败）**：让用户打开模型页手动下载 4 件交付物，按 6.1 的目录结构放好：
**https://www.modelscope.cn/models/daijiaoshou/hajishou-V2.0**（MIT 许可，仅权重不含训练数据）

### 6.3 验证

```cmd
dir core\nn\_storage\fine_tuned\model.safetensors   :: 应存在，约 620MB
dir core\nn\_storage\finance_classifier.pt
dir core\nn\_storage\subject_to_index.json
dir core\nn\_storage\index_to_bucket.json
```

下载工具还会顺带拉下 `.gitattributes`、`README.md` 之类的仓库附属文件，**无害，忽略即可**。

---

## 七、Step 6 — 装 DSH 插件（界面）与命令行

**界面**：把插件装进 DSH：

```text
plugin_manager install_bundle
  target = <仓库绝对路径>/dsh_plugin/journal-workbench
```

装完侧边栏多一个「审计工作台」，对话里多一个「会计分录」页签。**侧栏入口没有会话**，
必须在插件配置里写 `repoRoot` 与 `python`（模板见 `INSTALL.md`）。
改 Host 代码（`src/*.js`）要关一次再开；改 `client.js` 刷新页面即可。

**命令行**（不装插件也能跑完整条链）：

```cmd
venv\Scripts\python.exe journal.py features
venv\Scripts\python.exe journal.py inspect case\序时账1.xlsx
venv\Scripts\python.exe journal.py run case\序时账1.xlsx --feature cleaning
venv\Scripts\python.exe journal.py next <job_id>
```

**如何停止（务必告诉用户）**：关掉页面**不会**停掉进程。
在功能页右上角点「■ 中止」；命令行任务用 `journal.py abort <job_id>`。

**训练（仅训练版）**：入口是 `tools/train/train_v10.py`，没有网页训练页了。

---

## 八、Step 7 — 验证清单（按版本逐项确认，别只看启动成功）

### 所有版本都要过

- [ ] Python 3.11～3.13，虚拟环境已建好；
- [ ] `venv\Scripts\python.exe journal.py features` 能列出四个功能；
- [ ] `venv\Scripts\python.exe journal.py inspect case\序时账1.xlsx` 能读出列名与建议映射；
- [ ] DSH 里侧边栏「审计工作台」能打开，环境卡显示「可以用了」；
- [ ] 插件里传一份账、建任务，能在右栏看到它。

### ① 功能版

- [ ] 不装 torch 也能跑清洗（纯规则模式）；插件环境卡会显示「还不能用」时，
      检查 `requirements.txt` 是否装全。

### ② 模型版

- [ ] `venv\Scripts\python.exe -c "import torch; print(torch.__version__)"` → 带 **`+cpu`**；
- [ ] 清洗自检命令输出 `model_available= True`（见 `INSTALL.md`）。

### ③ 训练版

- [ ] `venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"` → **True**；
- [ ] `tools/train/train_v10.py` 能读到训练数据；
- [ ] 提醒用户：先日常用一段时间积累纠错数据，再来训练。

---

## 九、常见问题

- **pip 下载慢 / 中途断**：确认用的是阿里云源；再加 `--timeout 60 --retries 5` 重试。
  仍失败可换腾讯云源 `-i https://mirrors.cloud.tencent.com/pypi/simple/`。
- **torch 装成了 CUDA 版（4GB）**：`pip uninstall -y torch`，再按 Step 3 的 CPU 源命令重装。
- **清洗任务失败，提示没装 torch**：功能版的正常现象；要模型判桶就补 Step 3 的② + Step 5。
- **清洗任务失败，提示缺 `fine_tuned/`**：模型没下全，回 Step 5 重下并核对 4 件交付物。
- **提示「缺少字体文件 simsun.ttc」**：回 Step 4 复制字体；PDF 索引页可手动指定系统字体。
- **插件页面一直显示「正在连接工作台」**：应用刚启动时路由还没挂上，等几秒；
  超过一分钟就检查插件配置里的 `repoRoot` 是否指向本仓库。
- **插件页面读不到任务 / 404**：侧栏入口没有会话，必须配 `repoRoot`；
  对话页签入口按会话工作目录解析，确认会话就开在本仓库。
- **用户问「能不能用 GPU 推理」**：默认 CPU 就够（1 万条约 4 分钟、精度一致）；
  若已装 CUDA 版 torch 且显存空闲 ≥2.5GB，程序会**自动**改用 GPU，无需额外配置。
- **训练页看不到训练数据**：正常，需要先跑分类并在纠错流程里积累（模型仓库不含训练数据）。
- **用户想换版本**：功能版→模型版只需补装 Step 3 的②③ + Step 5；模型版→训练版再按 ③ 换 torch。

---

## 十、依赖文件速查表

| 文件 | 作用 | 谁需要 |
|---|---|---|
| `requirements.txt` | 基础：pandas / openpyxl / scikit-learn / PyMuPDF / reportlab / polars 等 | **所有版本** |
| `requirements-nn.txt` | NN 推理：transformers + safetensors + modelscope（torch 单独用 CPU 源装） | ②模型版 / ③训练版 |
| `requirements-train.txt` | 训练增强：accelerate + peft（torch 换 CUDA 版） | ③训练版 |

> 装依赖统一加阿里云源：`-i https://mirrors.aliyun.com/pypi/simple/`
> 开发/交付侧还有两个东西不入 git，部署者在本地自行生成或补装：`assets/fonts/simsun.ttc`（Step 4）、
> `core/nn/_storage/`（Step 5）、`tests/_work/`（跑测试时才产生）。
