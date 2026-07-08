---
name: summary-cleaning
description: 对杂乱的会计摘要文本进行关键词匹配和聚类，迭代清洗成标准的业务桶分类。当用户需要整理摘要、训练关键词规则、聚类未命中文本、或准备从摘要到业务类别的映射时使用。
---

# 摘要清洗

将杂乱的会计摘要文本清洗归类为标准业务桶。核心闭环：

```
序时账 → 预处理 → 逐文件训练 → 收集结果 → AI决策 → 修改种子 → 日志 → 重训 → 导出
```

## 文件路由表

| 文件 | 类型 | 用途 | 何时使用 |
|------|------|------|----------|
| `config.json` | 配置 | 用户预设路径 | **第一步读取**，校验路径 |
| `assets/buckets_seed.json` | 数据 | 业务桶关键词+科目（**唯一版本**，持久积累） | 训练/聚类输入；AI 直接改它 |
| `assets/preferences.json` | 配置 | 桶偏好：clarity、anchors、anchor_bonus | 训练时自动加载；AI 调参时改它 |
| `assets/buckets_seed.template.json` | 模板 | 发布用干净备份 | **不要改**。发布时替换回此文件 |
| `assets/training_log.json` | 日志 | 训练变更记录 | log_change.py 自动写入 |
| `assets/review_notes.md` | 笔记 | 用户修正记录 | learn_from_review.py --apply 自动追加 |
| `references/工作指引.md` | 参考 | 桶设计原则、关键词法则、排错 | AI 决策时翻阅 |
| `scripts/preprocess_journal.py` | 脚本 | 序时账 → 训练文件（含期间损益过滤） | 第3步执行 |
| `scripts/train_bucket_classifier.py` | 脚本 | 训练（core_attention + preferences 偏好排序） | 第4步 |
| `scripts/suggest_buckets.py` | 脚本 | 聚类+Pattern+TF-IDF（只列数据） | 命中率不达标时执行 |
| `scripts/log_change.py` | 脚本 | 写训练日志 | AI 修改 buckets 后调用 |
| `scripts/export_to_journal.py` | 脚本 | 结果写回序时账 | 训练达标后导出 |
| `scripts/learn_from_review.py` | 脚本 | 从用户修正中学习 | 用户 review 后调用 |

## 程序做的事（脚本自动闭环）

### 预处理：序时账 → 独立训练文件

`preprocess_journal.py --input <文件或文件夹> --output-dir <训练数据目录>`

- 支持单文件或文件夹（递归查找子目录）
- 每个序时账**独立输出**一个训练文件，不合并
- **自动过滤**：摘要含"期间损益"且科目含"本年利润"的结转凭证直接排除，不进入训练数据

### 训练：单文件匹配 → 报告

`train_bucket_classifier.py --training-file <单个训练文件> --buckets <buckets_seed.json>`

- 使用 `--training-file` 每次只训练一个文件
- 输出 Excel 报告 + 控制台打印 `__SUMMARY__` JSON

### 聚类：全量未命中 → 建议

`suggest_buckets.py --training-dir <训练数据目录> --buckets <buckets_seed.json>`

- 读取目录下所有训练数据，做全局聚类
- 使用中文连续词提取（正则），非滑动切片

### 日志：程序写入

`log_change.py --file <文件名> --action <操作> --bucket <桶名> --details <详情> --log-path <路径>`

- 日志默认写到项目 `output/training_log.json`，不污染 skill 目录

### 导出：结果写回序时账

`export_to_journal.py --journal <序时账> --report <报告>`
或批量：`export_to_journal.py --journal <文件夹> --report-dir <报告目录>`

- 在摘要列旁插入"摘要分类"列
- 批量模式按 source_prefix 自动匹配报告

## 偏好系统：条件锚定（相关性）

`assets/preferences.json` 定义了每个桶的偏好参数。核心概念：**锚定科目有轻重之分**。

### 重石头（无条件锚定）

科目本身足以确定桶归属。命中即加分。

```json
"anchors": ["应付职工薪酬", "主营业务收入", "固定资产"]
```

如：`应付职工薪酬` 一出现就锚定职工薪酬桶——这块石头足够沉，自己就沉底。

### 轻石头（条件锚定）

科目**单独出现时不足信**，必须绑上其他信号才触发。

```json
{
  "account": "制造费用",
  "requires_any_account": ["生产成本"],
  "requires_any_keyword": ["领料", "退料", "补料", "车间", "产线", "工单", "报工"]
}
```

触发条件（OR 关系）：
- `制造费用` 在 match_text 中，**且**
- (任一 `requires_any_account` 也在 match_text 中) **或** (任一 `requires_any_keyword` 也在 match_text 中)

| 场景 | 制造费用 | 生产成本 | 制造关键词 | 锚定触发？ |
|------|----------|----------|------------|-----------|
| 报销差旅费 | ✓ | ✗ | ✗ | **不触发**（轻石头没绑重物） |
| 生产成本+制造费用 | ✓ | ✓ | - | **触发**（绑上了生产成本） |
| 生产领料 | ✓ | ✗ | ✓ | **触发**（绑上了"生产领用"） |

### AI 调参时

改 `preferences.json` 即可，不需要动 `buckets_seed.json`：
- 调 `clarity` 改变桶的默认优先级
- 调 `anchor_bonus` 改变锚定加分力度
- 把轻石头升为重石头（去掉条件）或反过来（加上条件）

## AI 做的事

### 第1步：初始化

读取 `config.json`，校验路径。训练直接使用 `summary-cleaning/assets/buckets_seed.json`。

### 第2步：准备 buckets_seed.json

**不需要额外操作**。`assets/buckets_seed.json` 已经积累了之前训练的关键词和科目映射。直接用它训练。

### 第3步：预处理

```bash
python summary-cleaning/scripts/preprocess_journal.py \
    --input <journal_source> \
    --output-dir <training_data_dir>
```

### 第4步：逐文件训练

对每个训练文件：

```bash
python summary-cleaning/scripts/train_bucket_classifier.py \
    --buckets output/buckets_seed.json \
    --training-file <单个训练文件> \
    --output-dir <output_dir>
```

收集 `__SUMMARY__` JSON，汇报每个文件的命中率和桶分布。

### 第5步：决策

命中率阈值是**参考值**，不是硬标准：

- 命中率 ≥ 90% 且剩余未命中都是低频噪音 → 导出
- 命中率较高但聚类建议中仍有明显的新业务模式 → 继续补关键词
- 某个文件数据质量差（摘要本身混乱），命中率 70-80% 也可以接受
- 连续两轮没有出现有意义的新聚类 → 停止迭代

**决策前，先做两件事**：

#### A. 跨桶审核（必做）

训练输出的 `__MULTI_BUCKET__` 列出了多桶命中的组合和样例。逐条检查：

| 情况 | 操作 |
|------|------|
| 组合合理（如 存货采购+固定资产） | 保留，这是正常的候选重叠 |
| 明显误匹配（如"滴滴打车"→存货采购） | 找到导致误匹配的关键词，从该桶删除 |

常见的误匹配根因：关键词太宽。如 `发票入账` 在存货采购里，但费用报销也有发票入账 → 换成更精准的 `采购发票`。

#### B. 抽查命中明细（建议）

随机抽 20-30 条命中明细，看是否有明显不合理的桶归属。如果发现问题，去掉对应的关键词。

### 第6步：聚类建议 + 修改种子

运行聚类：

```bash
python summary-cleaning/scripts/suggest_buckets.py \
    --buckets output/buckets_seed.json \
    --training-dir <training_data_dir> \
    --output-dir <output_dir>
```

根据报告修改 `output/buckets_seed.json`。**优先看 `未命中高频Pattern` 和 `未命中特征词_TFIDF`**：

每个桶有两个可修改字段：
- `keywords`：摘要文本关键词（AC 自动机匹配）
- `accounts`：标准一级科目列表（科目锚定，只用标准名称）

修改时机：
| 信号来源 | 操作 |
|----------|------|
| Pattern 分析显示科目锚定到现有桶 | 如果置信度高（≥80%），**在 keywords 里加代表性词**，让关键词能直接命中 |
| Pattern 分析显示科目锚定到未知 | 检查该科目是否属于已建桶 → 是则补 accounts，否则考虑建新桶 |
| 建新桶 | 同时填 keywords 和 accounts |
| accounts 用到非标准科目 | **只填标准一级科目名**（参考 `references/工作指引.md` 附录），前缀匹配会自动处理公司变体 |

**TF-IDF 特征词指引**：

| 集中度 | 含义 | 操作 |
|--------|------|------|
| ≥ 90% | 该词几乎只在未命中中出现 | **强烈建议建新桶**（如果语义独立）或补充关键词 |
| 70-90% | 主要在未命中中出现 | 建议建新桶或补充关键词 |
| 50-70% | 未命中偏多 | 考虑补充关键词 |
| < 50% | 分布均匀 | 可忽略 |

**建新桶是正常的、被鼓励的**。现有桶只是起点，不是限制。看到独立的业务模式就该大胆建桶——参考 `references/工作指引.md` 第 2.3 节判断标准。然后再参考 `聚类建议` sheet 做细粒度调整。

**修改后调用日志**：

```bash
python summary-cleaning/scripts/log_change.py \
    --file "文件名" \
    --action "add_keywords" \
    --bucket "费用报销" \
    --details "新增关键词: 网约车, 打车费" \
    --hit-rate-before "83.2%" \
    --log-path output/training_log.json
```

回到第4步重新训练。

### 第7步：导出

导出文件自带三列：`摘要分类`（AI 分类结果）、`用户修正`（空）、`修正原因`（空）。未匹配的自动归入"其他业务"兜底。

单文件：
```bash
python summary-cleaning/scripts/export_to_journal.py \
    --journal <原始序时账> \
    --report <对应的训练报告> \
    --output-dir <output_dir>
```

### 第8步：人机交互

导出文件有 `用户修正` 和 `修正原因` 两列。告知用户可在此修正。

用户 review 完毕并告知 AI 后，AI 执行：

```bash
# 1. 先分析（dry-run，AI 读 JSON 向用户确认）
python summary-cleaning/scripts/learn_from_review.py <用户修正后的文件>

# 2. 用户确认后，实际应用
python summary-cleaning/scripts/learn_from_review.py <用户修正后的文件> --apply
```

`--apply` 同时做三件事：
- 修改 `assets/buckets_seed.json`（补关键词）
- 追加 `assets/review_notes.md`（固定范式的修正笔记）
- 下次训练自动生效

### 第9步：汇报

- 每个文件的最终命中率
- 业务桶分布
- 本次修改了 buckets_seed.json 的哪些地方
- 导出文件位置
- 抽查命中结果的质量评估
