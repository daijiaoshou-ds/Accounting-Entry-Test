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
| `assets/buckets_seed.template.json` | 模板 | 12个基础桶模板 | **首次使用时复制到项目 output/** |
| `<项目>/output/buckets_seed.json` | 数据 | 用户的业务桶（迭代中成长） | 训练/聚类输入；**AI 每次迭代修改它** |
| `<项目>/output/training_log.json` | 日志 | 变更记录 | **由 log_change.py 写入**，路径默认 `output/` |
| `references/工作指引.md` | 参考 | 桶设计原则、关键词法则、排错 | AI 决策时翻阅 |
| `scripts/preprocess_journal.py` | 脚本 | 序时账 → 独立训练文件 | 第3步执行 |
| `scripts/train_bucket_classifier.py` | 脚本 | 单文件训练 → 报告 + 摘要 | 第4步逐个文件训练 |
| `scripts/suggest_buckets.py` | 脚本 | 未命中聚类 → 建议 | 命中率不达标时执行 |
| `scripts/log_change.py` | 脚本 | 程序写变更日志 | AI 修改 buckets_seed.json 后调用 |
| `scripts/export_to_journal.py` | 脚本 | 结果写回原始序时账 | 训练达标后逐文件导出 |

## 程序做的事（脚本自动闭环）

### 预处理：序时账 → 独立训练文件

`preprocess_journal.py --input <文件或文件夹> --output-dir <训练数据目录>`

- 支持单文件或文件夹（递归查找子目录）
- 每个序时账**独立输出**一个训练文件，不合并

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

## AI 做的事

### 第1步：初始化

检查项目 `output/` 下是否有 `buckets_seed.json`：
- **没有** → 从 `assets/buckets_seed.template.json` 复制一份到 `output/buckets_seed.json`
- **有** → 使用已有的（保留之前训练积累的关键词）

读取 `config.json`，校验路径。

### 第2步：准备 buckets_seed.json

训练使用项目 `output/buckets_seed.json`，**不是** `assets/` 下的模板。模板只在首次使用时复制一次。

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

**在每次决策前，抽查命中明细**（随机抽 20-30 条）：
- 看命中结果是否合理（"支付设备款"→固定资产 ✅，但如果→费用报销 ❌）
- 如果发现某个关键词导致明显误命中，从 buckets_seed.json 中删除
- 检查要点见 `references/工作指引.md`

### 第6步：聚类建议 + 修改种子

运行聚类：

```bash
python summary-cleaning/scripts/suggest_buckets.py \
    --buckets output/buckets_seed.json \
    --training-dir <training_data_dir> \
    --output-dir <output_dir>
```

根据报告修改 `output/buckets_seed.json`。**优先看 `未命中特征词_TFIDF` sheet**：

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

单文件：
```bash
python summary-cleaning/scripts/export_to_journal.py \
    --journal <原始序时账> \
    --report <对应的训练报告> \
    --output-dir <output_dir>
```

批量：
```bash
python summary-cleaning/scripts/export_to_journal.py \
    --journal <序时账文件夹> \
    --report-dir <output_dir> \
    --output-dir <output_dir>
```

### 第8步：汇报

- 每个文件的最终命中率
- 业务桶分布
- 本次修改了 buckets_seed.json 的哪些地方
- 导出文件位置
- 抽查命中结果的质量评估
