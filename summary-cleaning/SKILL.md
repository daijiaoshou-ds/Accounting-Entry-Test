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

执行本 Skill 前，先读懂每个文件的职责和使用时机：

| 文件 | 类型 | 用途 | 何时使用 |
|------|------|------|----------|
| `config.json` | 配置 | 用户预设路径 | **第一步读取**，校验路径 |
| `assets/buckets_seed.json` | 数据 | 业务桶种子（12个桶） | 训练/聚类输入；**AI 每次迭代修改它** |
| `assets/training_log.json` | 日志 | 变更记录 | **由 log_change.py 写入**，AI 不看也不改 |
| `references/工作指引.md` | 参考 | 桶设计原则、关键词法则、排错 | AI 决策时翻阅 |
| `scripts/preprocess_journal.py` | 脚本 | 序时账 → 独立训练文件 | 第2步，每个文件独立输出 |
| `scripts/train_bucket_classifier.py` | 脚本 | 单文件训练 → 报告 + 摘要 | 第3步，逐个文件训练 |
| `scripts/suggest_buckets.py` | 脚本 | 未命中聚类 → 建议 | 命中率不达标时执行 |
| `scripts/log_change.py` | 脚本 | 程序写变更日志 | AI 修改 buckets_seed.json 后调用 |
| `scripts/export_to_journal.py` | 脚本 | 结果写回原始序时账 | 训练达标后逐文件导出 |

## 程序做的事（脚本自动闭环）

### 预处理：序时账 → 独立训练文件

`preprocess_journal.py --input <文件或文件夹> --output-dir <训练数据目录>`

- 支持单文件或文件夹（递归查找子目录）
- 每个序时账**独立输出**一个训练文件，不合并
- 自动用父文件夹名区分同名文件（如 `摘要文本_公司A_序时账.xlsx`）

### 训练：单文件匹配 → 报告

`train_bucket_classifier.py --training-file <单个训练文件> --buckets ... --output-dir ...`

- 使用 `--training-file` 每次只训练一个文件
- 输出 Excel 报告 + 控制台打印 `__SUMMARY__` JSON（AI 直接读取）

### 聚类：全量未命中 → 建议

`suggest_buckets.py --training-dir <训练数据目录> --buckets ...`

- 读取目录下所有训练数据，做全局聚类
- 输出聚类建议报告

### 日志：程序写入

`log_change.py --file <文件名> --action <操作> --bucket <桶名> --details <详情>`

- AI 调脚本传参数，程序写 JSON，AI 不碰日志文件

### 导出：结果写回序时账

`export_to_journal.py --journal <原始序时账> --report <训练报告> --output-dir ...`

- 在摘要列旁插入"摘要分类"列
- 支持单文件或文件夹批量导出

## AI 做的事（你的决策职责）

### 第1步：校验配置

读取 `config.json`：

```json
{
  "journal_source": "序时账文件或文件夹路径",
  "training_data_dir": "预处理输出目录",
  "output_dir": "报告和结果输出目录"
}
```

校验路径是否有效，无效则提示用户修改。

### 第2步：预处理

```bash
python summary-cleaning/scripts/preprocess_journal.py \
    --input <journal_source> \
    --output-dir <training_data_dir>
```

记住 training_data_dir 下生成了哪些文件（文件列表）。

### 第3步：逐文件训练

对每个训练文件，逐个执行：

```bash
python summary-cleaning/scripts/train_bucket_classifier.py \
    --buckets summary-cleaning/assets/buckets_seed.json \
    --training-file <training_data_dir/摘要文本_xxx.xlsx> \
    --output-dir <output_dir>
```

收集每个文件的 `__SUMMARY__` JSON，汇总成表格向用户汇报：

```
文件              总记录  命中  未命中  命中率
公司A_序时账       523    498   25     95.2%
公司B_序时账       410    341   69     83.2%
合计              933    839   94     89.9%
```

### 第4步：决策

- **所有文件命中率 ≥ 90%** → 跳到第6步导出
- **任一文件 < 90%** → 进入第5步

### 第5步：聚类建议 + 修改种子

运行全局聚类（用 `--training-dir` 看全部数据）：

```bash
python summary-cleaning/scripts/suggest_buckets.py \
    --buckets summary-cleaning/assets/buckets_seed.json \
    --training-dir <training_data_dir> \
    --output-dir <output_dir>
```

根据 `聚类建议` sheet 和 `references/工作指引.md`，直接修改 `assets/buckets_seed.json`：

| 情况 | 操作 |
|------|------|
| 与已有桶语义重叠 | 把高频词加入 keywords |
| 独立新业务类型 | 新建桶 |
| 低频噪音 | 跳过 |

**修改后调用日志脚本**（程序写 JSON，你只传参数）：

```bash
python summary-cleaning/scripts/log_change.py \
    --file "全量" \
    --action "add_keywords" \
    --bucket "费用报销" \
    --details "新增关键词: 网约车, 打车费" \
    --hit-rate-before "83.2%"
```

然后回到第3步，重新逐文件训练。

### 第6步：逐文件导出

```bash
python summary-cleaning/scripts/export_to_journal.py \
    --journal <原始 journal_source> \
    --report <output_dir 下对应的训练报告> \
    --output-dir <output_dir>
```

### 第7步：汇报

- 最终每个文件的命中率
- 业务桶分布
- 本次修改了 buckets_seed.json 的哪些地方
- 导出文件位置
- 未分类条目及原因
