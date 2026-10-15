# 装哪个目录？（仓库里有三个名字很像的目录）

> 给 AI 助手/自动化脚本看的**防呆说明**。人只需要看第一张表。

| 目录 | 是什么 | 装吗 |
|---|---|---|
| **`summary-cleaner/`** | **发布版**（= 用户版 skill，24 个文件、约 0.3MB，**不含模型**） | ✅ **就装这个** |
| `summary_cleaner_v3/` | **开发版**（同一份代码 + 训练代码 `jc/training/` + `_dev/` 训练数据） | ❌ 除非你要训练模型 |
| `summary_cleaner/` | **1.0 时代的旧 Python 包**（`__init__.py` / `nn/` / `v2/`），**里面没有 SKILL.md** | ❌ 装了也发现不了 skill |

> ⚠️ `summary-cleaner/` 与 `summary_cleaner_v3/` 的 **`SKILL.md` 内容完全相同、
> frontmatter 里的 `name` 也都是 `summary-cleaner`** —— 靠"找 SKILL.md"来安装的
> 自动化流程**只能靠目录名区分**，所以这里单独写清楚。
> 另外本仓库根目录还有一个 `SKILL.md`，那是**整个会计工具箱**的部署指南，不是这个 skill。

---

## 安装（三步）

```cmd
:: ① 只取发布版目录（sparse checkout，别把整个仓库拉下来）
git clone --depth 1 --filter=blob:none --sparse https://github.com/<owner>/<repo>.git
cd <repo>
git sparse-checkout set summary-cleaner

:: ② 把 summary-cleaner/ 整个目录拷进你的 skill 目录
::    项目级： <你的项目>\.dsh\skills\summary-cleaner\
::    用户级： %USERPROFILE%\.dsh\skills\summary-cleaner\
::    （目录名必须还是 summary-cleaner = SKILL.md 里的 name）

:: ③ 在 skill 目录里建环境 + 下模型（详见 summary-cleaner/SKILL.md 第 0~1 步）
cd <skill 目录>
python -m venv venv
venv\Scripts\python.exe -m pip install torch --index-url https://mirrors.aliyun.com/pypi/simple/
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -X utf8 cli.py status
```

模型（约 622MB）**不在仓库里**，要自己下 —— 一条命令，落到 `assets/model/`：

```cmd
venv\Scripts\python.exe -X utf8 -c "import sys; from modelscope import snapshot_download; print(snapshot_download('daijiaoshou/hajishou-V1.0', local_dir=sys.argv[1]))" "assets\model"
```

装完约占 **1.5GB**（venv 0.92GB + 模型 0.62GB）。之后让 AI 助手读 `summary-cleaner/SKILL.md` 即可。

---

## 为什么有两个 `summary_cleaner*`

- `summary_cleaner_v3/` 是**唯一代码库**：开发版 = 用户版 + 训练能力。
- `summary-cleaner/` 是**打包产物**（由 `summary_cleaner_v3/scripts/package_release.py`
  生成，剔除了训练代码、训练数据、模型权重与开发侧记录），用户从仓库拉取的就是它。
- 改代码请改 `summary_cleaner_v3/`，然后重新打包；**不要直接改 `summary-cleaner/`**
  （下次打包会被覆盖）。
- **不要改动 `summary_cleaner/`**：那是 1.0 的历史包，只作留档。
