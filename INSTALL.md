# 装哪个目录？（仓库里三个名字很像的目录）

> 给 AI 助手/自动化脚本看的**防呆说明**。人只需要看第一张表。

| 目录 | 是什么 | 装吗 |
|---|---|---|
| **`summary_cleaner/`** | **序时账清洗 4.0**：读表 → 科目归一化 → 模型判业务桶（33 桶）→ 四类硬约束 → 写回。**模型交付物在这里**（`nn/_storage/`） | ✅ 要清洗就装 |
| **`cashflow_writer/`** | **现金流量表编写**：依赖 `summary_cleaner` 判桶，自己只做「桶 + 货币资金方向 → 现金流量项目」 | ✅ 要编现金流量表就装（两个一起装） |
| `summary-cleaner/` | **旧的打包产物**（3.0 那版：自带清洗代码 + 18 桶模型） | ❌ 暂不要装，等按新架构重打包 |

> ⚠️ 两个包**必须放在同一父目录**下：`cashflow_writer` 启动时会去同级目录找
> `summary_cleaner`。它自己不带模型、不带清洗逻辑，单独拷走跑不了。

---

## 安装

```cmd
:: ① 拉仓库（只需要这两个目录也行）
git clone --depth 1 https://github.com/<owner>/<repo>.git
cd <repo>

:: ② 建环境（在仓库根执行一次，两个包共用）
python -m venv venv
venv\Scripts\python.exe -m pip install torch --index-url https://mirrors.aliyun.com/pypi/simple/
venv\Scripts\python.exe -m pip install -r cashflow_writer\requirements.txt

:: ③ 序时账清洗自检（上游没有独立 CLI，用一行 API 验证模型能加载）
venv\Scripts\python.exe -X utf8 -c "from summary_cleaner.v2.classifier import JournalClassifier; from summary_cleaner.v2.config import load_subject_list; c=JournalClassifier(subject_list=load_subject_list()); c._load_nn_if_available(); print('model_available=', c._nn_available)"

:: ④ 现金流量表自检
cd cashflow_writer
..\venv\Scripts\python.exe -X utf8 cli.py status
```

模型（约 622MB）**不在仓库里**，落在 `summary_cleaner/nn/_storage/`：

```cmd
venv\Scripts\python.exe -X utf8 -c "import sys; from modelscope import snapshot_download; print(snapshot_download('daijiaoshou/hajishou-V2.0', local_dir=sys.argv[1]))" "summary_cleaner\nn\_storage"
```

4 件交付物必须配套（`fine_tuned/` + `finance_classifier.pt` + `subject_to_index.json`
+ `index_to_bucket.json`），**版本不一致时直接报错**——这是刻意的。

---

## 三个目录的关系

- `summary_cleaner/` 是**唯一的清洗实现**：读表、科目归一化、模型判定、硬约束、写回。
- `cashflow_writer/` **只依赖它**：`from summary_cleaner import pipeline` 拿业务桶，
  自己不加载模型、不判业务。它以前叫 `summary_cleaner_v3`，内嵌过一份清洗代码和旧模型，
  已经删掉了（同一份账在两边会得出不同桶，那是双口径）。
- `summary-cleaner/` 是 3.0 时代打出来的 skill 包，**与新架构不一致**，等重打包。
  打包脚本也从 `cashflow_writer/` 里移除了（旧脚本指向已删除的 `jc/`、`assets/model/`），
  需要时从 git 历史里取（`git show <旧commit>:summary_cleaner_v3/scripts/package_release.py`）。
