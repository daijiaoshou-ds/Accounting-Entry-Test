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
> ① **功能版**：装得最快、占空间最小（约 1GB）。会计异常检测、对方科目分析、银行流水匹配、小工具都能用；
> 序时账清洗也能用，但走「程序规则」模式，没有 AI 模型打分。
> ② **模型版（推荐）**：多装 620MB 的 AI 模型（共约 2.5GB）。序时账清洗启用 AI 融合打分，分类更准。
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

一套面向会计师事务所 / 财务人员的**本地**会计分析工具，五大功能：

1. **会计分录测试** —— 异常凭证检测（规则 + ML 聚类）
2. **对方科目分析** —— 多借多贷的对方科目配对求解
3. **序时账清洗** —— 凭证业务自动分类（程序规则引擎 + 可选 NN 模型融合打分）
4. **银行流水匹配** —— 序时账与银行流水核对，定位未达账项与月度差异
5. **小工具** —— Excel数据提取 / 文件批量整理 / Excel格式互转 / PDF索引号生成 / PDF合并·分拆·转换
   （输入支持「上传文件 / 上传文件夹」，输出一律浏览器下载，不改动本机原文件）

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
| `summary_cleaner/nn/_storage/` 模型（约 620MB） | 太大，走 ModelScope 单独下载 | 只有②模型版 / ③训练版 |

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

- **微调交付物（4 件，共 622MB）**——必须手动下载，放到 `summary_cleaner/nn/_storage/`：

```
summary_cleaner/nn/_storage/
├── fine_tuned/                # ① 微调后的 BGE 编码器（model.safetensors 约 620MB）
├── finance_classifier.pt      # ② 分类头权重（约 1.1MB）
├── subject_to_index.json      # ③ 科目索引
└── index_to_bucket.json       # ④ 桶索引
```

- **基座模型（仅训练版）**——代码自动从 ModelScope 下载，缓存在 `summary_cleaner/nn/models/`；
  训练页默认用 `bge-large-zh-v1.5`（约 1.3GB），显存紧张时可在页面改成 `bge-base-zh-v1.5`（约 440MB）。

### 6.2 怎么下（三选一）

**方式一（推荐，零代码）：双击 `download_model.bat`** —— 自动激活 venv + 下载 + 验证。

**方式二（命令行）**，在项目目录执行：

```cmd
venv\Scripts\python.exe -m modelscope download daijiaoshou/hajishou-V1.0 --local_dir summary_cleaner\nn\_storage
```

**方式三（网络差/命令行失败）**：让用户打开模型页手动下载 4 件交付物，按 6.1 的目录结构放好：
**https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0**（MIT 许可，仅权重不含训练数据）

### 6.3 验证

```cmd
dir summary_cleaner\nn\_storage\fine_tuned\model.safetensors   :: 应存在，约 620MB
dir summary_cleaner\nn\_storage\finance_classifier.pt
dir summary_cleaner\nn\_storage\subject_to_index.json
dir summary_cleaner\nn\_storage\index_to_bucket.json
```

下载工具还会顺带拉下 `.gitattributes`、`README.md` 之类的仓库附属文件，**无害，忽略即可**。

---

## 七、Step 6 — 启动与停止

**启动**：双击 `start.bat`（自动激活 venv + 启动 + 防闪退）。
浏览器会自动打开 **http://localhost:8501**，首页能看到五大功能入口。

> 手动启动也行：`venv\Scripts\python.exe -m streamlit run app.py`

**如何停止（务必告诉用户）**：关掉浏览器标签页**不会**停掉进程，三种方式任选其一：

1. **关浏览器自动停**（推荐）：`run.py` 监控 8501 端口，浏览器关闭后约 12 秒自动停止；
2. **双击 `stop.bat`**：立即停止；
3. **关闭启动时的黑色窗口 / 按 Ctrl+C**：立即停止。

**训练版额外**：训练页面在 http://localhost:8501/nn_training （侧边栏 pages 分组里也能找到）。

---

## 八、Step 7 — 验证清单（按版本逐项确认，别只看启动成功）

### 所有版本都要过

- [ ] Python 3.11～3.13，虚拟环境已建好；
- [ ] `pip list` 能看到 streamlit / pandas / openpyxl / PyMuPDF；
- [ ] 浏览器打开 http://localhost:8501，首页五大功能入口都能点开；
- [ ] **小工具抽一个验证上传下载**：既能「📁 上传文件」也能「🗂️ 上传文件夹」，
      点运行后有下载按钮、点保存能存下结果；
- [ ] 「PDF索引号生成」页面能显示检测到的系统中文字体（`assets/fonts` 已按 Step 4 补齐）；
- [ ] 「序时账清洗」页面能打开（顶部提示文字见下方版本差异）。

### ① 功能版

- [ ] 序时账清洗页顶部显示**黄色**「未装 torch…将使用纯程序规则模式」——**这是正常的**
      （该版本序时账清洗照样跑，只是没有 AI 融合打分）。

### ② 模型版

- [ ] `venv\Scripts\python.exe -c "import torch; print(torch.__version__)"` → 带 **`+cpu`**；
- [ ] 序时账清洗页顶部显示**绿色**「NN 模型：已就绪（CPU 推理 int8 量化）」。

### ③ 训练版

- [ ] `venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available())"` → **True**；
- [ ] 训练页（/nn_training）能打开，能看到「训练数据 / 基座模型」相关选项；
- [ ] 提醒用户：先日常用一段时间积累纠错数据，再来训练。

---

## 九、常见问题

- **pip 下载慢 / 中途断**：确认用的是阿里云源；再加 `--timeout 60 --retries 5` 重试。
  仍失败可换腾讯云源 `-i https://mirrors.cloud.tencent.com/pypi/simple/`。
- **torch 装成了 CUDA 版（4GB）**：`pip uninstall -y torch`，再按 Step 3 的 CPU 源命令重装。
- **序时账清洗页显示「未装 torch」**：功能版的正常现象；想要 AI 融合打分就补 Step 3 的②+Step 5。
- **序时账清洗页显示「缺失 fine_tuned/」**：模型没下全，回 Step 5 重下并核对 4 件交付物。
- **提示「缺少字体文件 simsun.ttc」**：回 Step 4 复制字体；PDF 索引页可手动指定系统字体。
- **8501 端口被占用**：先双击 `stop.bat`，再 `netstat -ano | findstr :8501` 查残留进程。
- **用户问「能不能用 GPU 推理」**：默认 CPU 就够（1 万条约 4 分钟、精度一致）；
  若已装 CUDA 版 torch 且显存空闲 ≥2.5GB，程序会**自动**改用 GPU，无需额外配置。
- **训练页看不到训练数据**：正常，需要先跑分类并在纠错流程里积累（模型仓库不含训练数据）。
- **用户想换版本**：功能版→模型版只需补装 Step 3 的②③ + Step 5；模型版→训练版再按 ③ 换 torch。

---

## 十、依赖文件速查表

| 文件 | 作用 | 谁需要 |
|---|---|---|
| `requirements.txt` | 基础：Streamlit / pandas / openpyxl / scikit-learn / PyMuPDF / reportlab / polars 等 | **所有版本** |
| `requirements-nn.txt` | NN 推理：transformers + safetensors + modelscope（torch 单独用 CPU 源装） | ②模型版 / ③训练版 |
| `requirements-train.txt` | 训练增强：accelerate + peft（torch 换 CUDA 版） | ③训练版 |

> 装依赖统一加阿里云源：`-i https://mirrors.aliyun.com/pypi/simple/`
> 开发/交付侧还有两个东西不入 git，部署者在本地自行生成或补装：`assets/fonts/simsun.ttc`（Step 4）、
> `summary_cleaner/nn/_storage/`（Step 5）、`tests/_work/`（跑测试时才产生）。
