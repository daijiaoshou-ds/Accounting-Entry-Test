# SKILL — 会计分录分析工具箱：环境搭建与启动指南（给 AI 助手）

> 本文件是给你（AI助手）的操作手册。
> 当用户说「帮我装好这个工具并跑起来」「帮我配好环境」时，请严格按本文件 6 步执行。
> 用户是从零开始、不懂代码的财务人员，你要替 TA 把每一步都做到位并验证通过。

---

## 一、项目是什么

一套面向会计师事务所 / 财务人员的本地会计分析工具，三大功能：

1. **会计分录测试** —— 异常凭证检测（科目+金额规则 + ML 聚类）
2. **对方科目分析** —— 多借多贷的对方科目配对求解
3. **序时账清洗** —— 凭证业务分类（程序规则引擎 + NN 模型融合打分）

纯本地运行，数据不出本机。

---

## 二、AI 助手的总任务

用户拿到仓库后，你要帮 TA 从零完成：

1. 装好正确的 Python 版本（Step 0）
2. 建好虚拟环境（Step 0）
3. 克隆仓库（Step 1）
4. 装好依赖库（**常规使用档，含 NN 融合，见 Step 2**）
5. 下载并配好 NN 模型（Step 3，常规使用也需要）
6. 启动应用并验证成功（Step 4 / Step 5）

**核心原则：「常规使用」= 程序规则 + NN 模型融合打分（拿现成模型用，不训练）。默认就按这个档装，用户不需要微调时一律不要装训练依赖。**

---

## 三、Step 0 — Python 环境

- Python 版本：**3.11 ～ 3.13 均可**（推荐 3.13）。
- 未安装 Python：引导用户到 https://www.python.org/downloads/ 下载对应版本，
  Windows 安装时务必勾选 **Add python.exe to PATH**。
- 建虚拟环境（Windows，**PowerShell 和 cmd 二选一**）：

**PowerShell：**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```
> PowerShell 报「禁止运行脚本」时，先执行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 再激活。

**cmd（默认命令行）：**
```cmd
python -m venv venv
venv\Scripts\activate.bat
```

> 目标用户多为财务人员，大概率用默认 cmd——**优先教 cmd 方式**（`activate.bat`），少踩 PowerShell 执行策略的坑。

---

## 四、Step 1 — 克隆仓库

```powershell
git clone https://github.com/<账号>/Accounting-Entry-Test.git
cd Accounting-Entry-Test
```

> 克隆地址以 README 里给出的为准；如未安装 git，先引导用户装 git（https://git-scm.com/downloads）。

---

## 五、Step 2 — 安装依赖（常规使用含 NN 融合，微调才额外装）

**国内网络务必加清华镜像源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`，下载会快很多。**

| 档位 | 命令 | 适用场景 |
|------|------|----------|
| **常规使用**（默认，含 NN 融合打分） | `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` | 三大功能日常使用 + 序时账清洗 NN 融合打分 |
| **训练模型**（可选，仅开发者，需 NVIDIA GPU） | `pip install -r requirements-train.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`（另需把 torch 换成 CUDA 版，见下方） | 用户自己训练/微调 NN 模型 |

**关键事实（务必记住）：**

- 「常规使用」就**包含 NN 融合打分**——用户拿现成模型用，不训练。`requirements.txt` 已含 torch CPU 版 + transformers + safetensors + modelscope，**一份装完即够**。
- **默认装 CPU 版 torch**（`requirements.txt` 里就是，pip 默认源即 CPU 版，几百 MB，无需 CUDA）。CPU 跑推理会自动 int8 量化，实测 41.9 条/秒（1 万条约 4 分钟），精度与 GPU 一致，完全够用。
- 两份依赖清单的边界：
  - `requirements.txt`：基础（Streamlit/pandas 等）+ NN 推理（torch CPU + transformers + safetensors + modelscope）—— **常规使用，一份装完**；
  - `requirements-train.txt`：accelerate + peft —— **只有微调训练才需要**（且需把 torch 换成 CUDA 版，训练必须 GPU）。
- 极端情况下，若用户明确只要纯程序模式、完全不想装 torch，可让 AI 只挑 `requirements.txt` 里的非 torch 依赖装（序时账清洗自动降级纯程序规则模式，功能完整但无 NN 融合）。但**默认不要这么干**。

**训练档的 torch：必须换成 CUDA 版（GPU）**

训练不可能用 CPU（太慢），必须 GPU。`requirements-train.txt` 只含 accelerate/peft，torch 要单独换 CUDA 版：

```powershell
# 1. 先卸载 CPU 版 torch
pip uninstall torch
# 2. 装 CUDA 版（cu126 = CUDA 12.6，按用户 CUDA 版本选）
pip install torch --index-url https://download.pytorch.org/whl/cu126
# 3. 再装训练增强
pip install -r requirements-train.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

- 训练必须 GPU：CUDA 版 torch + NVIDIA 独显 + 配套驱动，缺一不可。
- 推理不用 GPU：默认 CPU 版已够快够准，**不要主动给用户换 CUDA 版**。

---

## 六、Step 3 — 下载模型并配置（常规使用也需要，微调另需基座模型）

> ✅ 「常规使用」档就**需要下载模型**——因为 NN 融合打分要靠这个模型。
> 只有「纯程序模式」或用户明确不要 NN 融合时，才跳过本步。
> 微调的用户除了本步的 4 件交付物，代码还会自动额外下载基座模型（见 6.1）。

### 6.1 模型分两类

1. **基座模型**（BGE 中文模型，约 1.3GB）—— 代码会**自动从 ModelScope 下载**，无需手动操作，
   缓存在 `summary_cleaner/nn/models/`。
2. **微调交付物**（4 件，约 620MB）—— 已训练好的成品模型，**需手动下载**，放到 `summary_cleaner/nn/_storage/`。

### 6.2 微调交付物清单与放置位置

**4 件关键交付物**，全部放进 `summary_cleaner/nn/_storage/` 目录：

```
summary_cleaner/nn/_storage/
├── fine_tuned/                # ① 微调后的 BGE 编码器（含 model.safetensors 等）
├── finance_classifier.pt      # ② 分类头权重
├── subject_to_index.json      # ③ 科目索引
└── index_to_bucket.json       # ④ 桶索引
```

> 实际 `modelscope download` 会额外拉下 `.gitattributes`、`README.md`、`fine_tuned/.gitkeep` 等模型仓库附属文件，**无害，忽略即可**；上面 4 件是关键交付物。

### 6.3 下载方式

模型仓库：**https://www.modelscope.cn/models/daijiaoshou/hajishou-V1.0**（已上传，MIT 许可）。

**⚠️ 先激活 venv**（`venv\Scripts\activate.bat` 或 `venv\Scripts\Activate.ps1`），否则新终端里 `modelscope` 命令找不到。

**方式一（推荐）：双击 `download_model.bat`** —— 自动激活 venv + 下载 + 验证，适合零代码用户。

**方式二（命令行）**，在仓库根目录执行：

```powershell
# 先激活 venv，再下载
modelscope download daijiaoshou/hajishou-V1.0 --local_dir summary_cleaner/nn/_storage
```

下载完成后**验证**：确认 `summary_cleaner/nn/_storage/fine_tuned/model.safetensors` 存在
（约 620MB），且 `finance_classifier.pt` / `subject_to_index.json` / `index_to_bucket.json` 三个文件齐全。

> 若用户无法用 modelscope（未装），也可引导其手动到上述网页下载 4 件交付物，
> 按 §6.2 的目录结构放到 `summary_cleaner/nn/_storage/`。

---

## 七、Step 4 — 启动应用

启动只需一行（Windows）：**双击 `start.bat`**（或命令行 `start.bat`）。

> `start.bat` 已自包含：自动激活 venv + 自检 venv 是否存在 + 启动 + 防闪退，用户无需手动激活、也无需记忆 streamlit 命令。

等价命令：`streamlit run app.py --server.address=localhost --server.headless=false`。

启动成功后浏览器会自动打开 **http://localhost:8501**，即可看到首页三大功能入口。

---

## 八、Step 5 — 验证清单（做完要确认）

帮用户跑通后，逐项确认：

- [ ] Python 版本正确（3.11～3.13），虚拟环境已激活；
- [ ] 依赖安装成功（`pip list` 能看到 streamlit、pandas 等）；
- [ ] torch 可导入：`python -c "import torch; print(torch.__version__)"`（常规使用就应装 torch）；
- [ ] 模型已下载：`summary_cleaner/nn/_storage/fine_tuned/model.safetensors` 存在（约 620MB）；
- [ ] 浏览器打开 http://localhost:8501 能看到首页；
- [ ] 进入「序时账清洗」页，顶部显示 `🧠 NN 模型：已就绪`（绿色）——可一键确认模型配好；
- [ ] 三大功能入口都能点开、能上传数据（可用 `sample_data/` 示例文件验证）。

---

## 九、依赖分档速查表

| 用户需求 | 装什么 | 模型 | 序时账清洗表现 |
|----------|--------|------|----------------|
| **常规使用（默认）** | requirements.txt（含 CPU torch）| 需下载 4 件交付物 | 程序 + 模型融合（推荐）|
| 想自己微调模型 | requirements.txt + requirements-train.txt + CUDA torch | 需下载 4 件交付物 + 基座模型 | 可训练 |
| （特殊情况）只要纯程序模式 | 只挑 requirements.txt 里的非 torch 依赖 | 不需要 | 纯程序规则模式 |

---

## 十、常见问题

- **PowerShell 无法激活虚拟环境**：先 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`。
- **pip 下载特别慢**：加清华镜像源 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。
- **torch 安装特别慢**：常规使用默认装 CPU 版（`requirements.txt` 里就是，几百 MB）；训练才需换 CUDA 版（见 Step 2「训练档的 torch」）。
- **用户问「能不能用 GPU」**：推理默认不需要——CPU int8 已够快（1 万条约 4 分钟）；只有「训练模型」或用户坚持要 GPU 推理时才换 CUDA 版 torch。
- **序时账清洗提示「NN 融合不可用」**：说明没装 torch 或没下模型，已自动降级纯程序模式，功能仍可用；常规使用应装齐 torch + 模型。
- **境内网络下载基座模型失败**：优先走 ModelScope（代码已默认），勿用 HuggingFace。
- **依赖版本漂移**：requirements 已 pin 主版本上限。实测通过组合（2026-08-16）：Python 3.13.5 + torch 2.13.0+cpu + transformers 5.15.0 + safetensors 0.8.0 + modelscope 1.39.1 + streamlit 1.55.0。若用户遇到 API 报错，可参考该组合回退版本。

---
