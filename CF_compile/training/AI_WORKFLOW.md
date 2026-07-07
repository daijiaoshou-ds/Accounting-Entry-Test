# AI 工作流指南：业务桶训练

> 本文档面向 AI 助手。当你需要协助用户训练/维护业务桶关键词时，请按本文档执行。

---

## 一、项目背景

本项目目标：自动编制现金流量表。

当前处于 **theory.md 第 1-3 阶段**：只训练三类映射表：
1. 一级科目映射表（`account_cf_map.json`）
2. 业务桶 → 关键词映射（`buckets_seed.json`）
3. 业务桶 → 现金流项目映射（`bucket_cf_map.json`）

所有训练阶段文件都隔离在 `CF_compile/training/` 目录下。

**最终交付物位置**：`CF_compile/training/deliverables/`

---

## 二、目录结构说明

```text
CF_compile/
├── assets/
│   ├── 一级科目明细.md              # 原始资料：标准一级科目列表（勿删）
│   └── 现金流量项目明细.md          # 原始资料：现金流量项目列表（勿删）
├── doc/
│   ├── theory.md                    # 用户写的理论文档（勿删）
│   └── TODO.md                      # 用户写的任务清单（勿删）
└── training/                        # 【本阶段工作区】
    ├── assets/
    │   └── training_data/           # 用户上传训练数据的位置
    │       ├── 摘要文本1.xlsx       # 示例文件（用户会替换为真实数据）
    │       └── 二级科目文本1.xlsx   # 示例文件（用户会替换为真实数据）
    ├── config/                      # 工作过程中的草稿配置
    │   ├── buckets_seed.json        # 业务桶 → 关键词（AI 主要维护此文件）
    │   └── bucket_cf_map.json       # 业务桶 → 现金流项目（勿随意改动）
    ├── deliverables/                # 【最终交付物】
    │   ├── account_cf_map.json      # 一级科目映射表
    │   ├── buckets_seed.json        # 业务桶归纳表（最终版）
    │   ├── bucket_cf_map.json       # 业务桶映射表（最终版）
    │   └── README.md                # 交付物说明
    ├── scripts/
    │   ├── train_bucket_classifier.py   # 主训练脚本
    │   └── suggest_buckets.py           # 未命中项辅助归类脚本
    ├── output/                      # 脚本输出报告位置
    └── doc/
        └── training_workflow.md     # 人类可读的工作流说明
```

### 关键文件职责

| 文件 | 用途 | AI 是否可以修改 |
|---|---|---|
| `CF_compile/training/config/buckets_seed.json` | 定义业务桶及其关键词 | ✅ 主要修改对象 |
| `CF_compile/training/config/bucket_cf_map.json` | 定义业务桶对应的现金流项目 | ⚠️ 仅在新建业务桶时补充 |
| `CF_compile/training/deliverables/account_cf_map.json` | 一级科目映射表 | ✅ 逐步完善 |
| `CF_compile/training/deliverables/buckets_seed.json` | 业务桶归纳表最终版 | ⚠️ 训练稳定后从 config 同步 |
| `CF_compile/training/deliverables/bucket_cf_map.json` | 业务桶映射表最终版 | ⚠️ 训练稳定后从 config 同步 |
| `CF_compile/training/scripts/train_bucket_classifier.py` | 训练主脚本 | ❌ 不要改逻辑 |
| `CF_compile/training/scripts/suggest_buckets.py` | 未命中项聚类建议脚本 | ❌ 不要改逻辑 |
| `CF_compile/training/assets/training_data/*` | 用户上传的真实训练数据 | ❌ 只读取 |
| `CF_compile/training/output/*.xlsx` | 脚本生成的报告 | ❌ 只读取 |

---

## 三、训练数据规范

用户会把训练数据放在：

```text
CF_compile/training/assets/training_data/
```

### 文件命名规则

- 摘要文件：`摘要文本1.xlsx`、`摘要文本2.xlsx`、...
- 二级科目文件：`二级科目文本1.xlsx`、`二级科目文本2.xlsx`、...
- 支持格式：`.xlsx`、`.xls`、`.csv`

### 文件内容格式

每个文件必须包含两列：

| 序号 | 摘要 / 二级科目 |
|---|---|
| 1 | 滴滴打车报销 |
| 2 | 支付采购款 |

- 第一列必须叫 `序号`（整数）
- 第二列叫 `摘要` 或 `二级科目`，脚本会自动识别
- 每一行的唯一 ID = `文件名（不含扩展名）_序号`
  - 例如：`摘要文本1_2`、`二级科目文本1_5`

---

## 四、标准训练流程

### 步骤 1：运行主训练脚本

执行命令：

```bash
venv\Scripts\python.exe CF_compile\training\scripts\train_bucket_classifier.py
```

脚本会：
1. 读取 `buckets_seed.json` 和 `bucket_cf_map.json`
2. 扫描 `CF_compile/training/assets/training_data/` 下的训练文件
3. 用 AC 自动机对每条文本匹配关键词
4. 输出报告到 `CF_compile/training/output/bucket_training_report_YYYYMMDD_HHMMSS.xlsx`

### 步骤 2：查看训练报告

报告包含三个工作表：

| 工作表 | 用途 |
|---|---|
| `统计摘要` | 总记录数、命中数、未命中数、命中率、各桶命中次数 |
| `命中明细` | 已命中业务桶的明细 |
| `未命中明细` | 没有命中任何业务桶的明细 |

**重点关注**：`未命中明细` 中的行数和具体内容。

### 步骤 3：判断是否需要辅助归类

- 如果未命中数量 **≤ 20**：可以直接逐条查看 `未命中明细`，跳到步骤 5。
- 如果未命中数量 **> 20**：先运行辅助归类脚本 `suggest_buckets.py`。

### 步骤 4：运行辅助归类脚本（可选）

执行命令：

```bash
venv\Scripts\python.exe CF_compile\training\scripts\suggest_buckets.py
```

脚本会：
1. 提取未命中项的高频字符 n-gram
2. 把相似未命中项聚成一类
3. 对每一类给出建议：
   - `补充关键词到现有业务桶`：该聚类与现有某个/某些桶语义相关
   - `考虑新建业务桶`：该聚类与现有桶均不相关，可能是新业务类型
4. 输出报告到 `CF_compile/training/output/bucket_suggestion_report_YYYYMMDD_HHMMSS.xlsx`

报告包含三个工作表：

| 工作表 | 用途 |
|---|---|
| `聚类建议` | 每个聚类的标签、数量、代表性文本、建议操作、建议目标、建议理由 |
| `未命中聚类明细` | 每条未命中记录带的聚类标签 |
| `高频未命中词` | 全局出现频率最高的未命中词汇 |

**使用建议**：优先处理 `聚类建议` 中数量大的聚类。

### 步骤 5：修改业务桶配置

根据未命中明细或聚类建议，修改 `CF_compile/training/config/buckets_seed.json`。

#### 情况 A：补充关键词到现有业务桶

例如，发现很多未命中摘要都包含“五险一金”，但 `职工薪酬` 桶里没有这个词：

```json
"职工薪酬": {
  "keywords": [
    "工资", "社保", "公积金", "...",
    "五险一金", "五险"
  ]
}
```

直接在对应桶的 `keywords` 列表末尾添加新词。

#### 情况 B：新建业务桶

如果某个聚类反复出现，且与现有 12 个桶都无关，则新建业务桶。

**必须同时修改两个文件**：

`buckets_seed.json`：

```json
"研发费用": {
  "keywords": ["研发", "技术开发", "专利费", "研发材料"]
}
```

`bucket_cf_map.json`：

```json
"研发费用": ["支付其他与经营活动有关的现金"]
```

### 步骤 6：重新运行训练脚本

修改完配置后，再次运行：

```bash
venv\Scripts\python.exe CF_compile\training\scripts\train_bucket_classifier.py
```

观察命中率是否提升，未命中项是否减少。

### 步骤 7：循环迭代

重复步骤 2-6，直到：
- 命中率稳定在 90% 以上，或
- 剩余未命中项都是零星、低频、无法归类的特殊项

### 步骤 8：维护一级科目映射表

在训练业务桶的同时，逐步完善：

```text
CF_compile/training/deliverables/account_cf_map.json
```

参考依据：
- `CF_compile/assets/一级科目明细.md`：标准一级科目列表
- `CF_compile/assets/现金流量项目明细.md`：现金流量项目列表

填写规则：
- 键是一级科目名称
- 值是该科目可能对应的现金流量项目列表（无方向）
- 货币资金类科目（如库存现金、银行存款）映射为空列表 `[]`
- 方向无关：例如“短期借款”既可能收现也可能付现，所以同时列出两个项目

### 步骤 9：同步最终交付物

当训练结果稳定后，将工作版本同步到交付目录：

```bash
cp CF_compile/training/config/buckets_seed.json CF_compile/training/deliverables/buckets_seed.json
cp CF_compile/training/config/bucket_cf_map.json CF_compile/training/deliverables/bucket_cf_map.json
```

最终三个交付物为：

```text
CF_compile/training/deliverables/
├── account_cf_map.json      # 一级科目映射表
├── buckets_seed.json        # 业务桶归纳表
└── bucket_cf_map.json       # 业务桶映射表
```

---

## 五、修改配置时的注意事项

1. **只改 `buckets_seed.json` 的关键词部分**，不要改 `bucket_cf_map.json`，除非新建业务桶。
2. **关键词允许重叠**：一个关键词可以属于多个业务桶（如“模具”可属于 `存货采购` 和 `固定资产`）。
3. **不要过度拟合**：只补充有代表性的高频词，极其罕见的词可以不补。
4. **保留 JSON 格式**：确保修改后文件仍是合法 JSON，关键词用双引号，列表末尾不要有多余逗号。
5. **新增桶命名规范**：使用简洁的中文业务名称，如 `研发费用`、`政府补助`、`手续费` 等。

---

## 六、常见问题处理

### Q1：运行脚本时报编码错误？

脚本已内置 stdout UTF-8 处理。如果仍有问题，在 Windows PowerShell 中先执行：

```powershell
chcp 65001
```

### Q2：命中率一直上不去？

检查：
- 训练数据是否有大量无效/空值行
- 是否存在某个大类业务完全没有对应桶
- `suggest_buckets.py` 的聚类建议中是否有高频新建桶需求

### Q3：一个文本命中多个桶怎么办？

这是正常行为。当前阶段只负责把文本归纳到可能的业务桶，多桶命中会进入后续候选池，由 theory.md 第 4-5 阶段的机制处理。

### Q4：训练数据文件格式不对？

确保：
- 第一列叫 `序号`
- 第二列叫 `摘要` 或 `二级科目`
- 文件名包含 `摘要文本` 或 `二级科目文本`

---

## 七、何时结束训练阶段

当满足以下条件时，可以认为业务桶训练基本完成：

1. `buckets_seed.json` 覆盖了 90% 以上的训练数据
2. 剩余未命中项都是零星、无法归类的噪声
3. 没有明显需要新建的业务桶
4. `bucket_cf_map.json` 中每个业务桶都有对应的现金流项目
5. `account_cf_map.json` 中主要一级科目都已映射

完成后，执行 **步骤 9 同步最终交付物**，然后进入下一阶段：构建候选池处理逻辑。

---

## 八、速查命令

```bash
# 运行训练
venv\Scripts\python.exe CF_compile\training\scripts\train_bucket_classifier.py

# 运行未命中辅助归类
venv\Scripts\python.exe CF_compile\training\scripts\suggest_buckets.py

# 查看输出
ls CF_compile\training\output\
```
