# 业务桶训练工作流

本目录说明如何用真实审计数据迭代训练业务桶关键词。当前属于 theory.md 第 1-3 阶段（训练阶段），所有文件都隔离在 `CF_compile/training/` 下。

---

## 一、文件结构

```text
CF_compile/training/
├── config/
│   ├── buckets_seed.json          # 业务桶 → 关键词（仅关键词，AI 可直接修改）
│   └── bucket_cf_map.json         # 业务桶 → 现金流项目（解耦配置）
├── scripts/
│   ├── train_bucket_classifier.py # 主训练脚本：输出命中/未命中报告
│   └── suggest_buckets.py         # 辅助脚本：对未命中项自动聚类并给建议
├── assets/
│   └── training_data/             # 用户上传的训练数据
│       ├── 摘要文本1.xlsx
│       ├── 摘要文本2.xlsx
│       ├── 二级科目文本1.xlsx
│       └── 二级科目文本2.xlsx
├── output/
│   ├── bucket_training_report_*.xlsx   # 训练结果报告
│   └── bucket_suggestion_report_*.xlsx # 未命中项聚类建议报告
└── doc/
    └── training_workflow.md         # 本文档
```

---

## 二、配置文件说明

### buckets_seed.json

只放业务桶和关键词，结构扁平，方便 AI 读写：

```json
{
  "职工薪酬": {
    "keywords": ["工资", "社保", "公积金", "..."]
  },
  "税费": {
    "keywords": ["增值税", "所得税", "..."]
  }
}
```

### bucket_cf_map.json

业务桶到现金流量项目的映射，与关键词配置完全解耦：

```json
{
  "职工薪酬": ["支付给职工以及为职工支付的现金"],
  "税费": ["支付的各项税费", "收到的税费返还"]
}
```

这样 AI 在调整关键词时不会误改现金流映射关系。

---

## 三、训练数据格式

每个训练文件两列：

| 序号 | 摘要 / 二级科目 |
|---|---|
| 1 | 滴滴打车报销 |
| 2 | 支付采购款 |

- 摘要文件命名：`摘要文本1.xlsx`、`摘要文本2.xlsx`、...
- 二级科目文件命名：`二级科目文本1.xlsx`、`二级科目文本2.xlsx`、...
- 支持 `.xlsx`、`.xls`、`.csv`
- 系统会自动识别 `摘要` 或 `二级科目` 列

ID 规则：`文件名（不含扩展名）_序号`  
例如：`摘要文本1_2`、`二级科目文本1_5`

---

## 四、主训练脚本

### 运行

```bash
venv\Scripts\python.exe CF_compile\training\scripts\train_bucket_classifier.py
```

### 输出报告

`CF_compile/training/output/bucket_training_report_*.xlsx`，包含三个工作表：

- **统计摘要**：总记录数、命中数、未命中数、命中率、各桶命中次数
- **命中明细**：ID、来源、文本、命中桶、命中关键词、对应现金流项目
- **未命中明细**：没有被归纳到任何业务桶的条目

---

## 五、未命中项辅助归类脚本

当未命中项很多时，逐个看太繁琐。这个脚本会自动对未命中项做聚类，给出归类建议。

### 运行

```bash
venv\Scripts\python.exe CF_compile\training\scripts\suggest_buckets.py
```

### 输出报告

`CF_compile/training/output/bucket_suggestion_report_*.xlsx`，包含三个工作表：

- **聚类建议**：每个聚类的标签、数量、代表性文本、建议操作
  - 建议操作分两类：
    1. `补充关键词到现有业务桶`：该聚类与某个现有桶语义相关
    2. `考虑新建业务桶`：该聚类与现有桶均不相关，可能是新业务类型
- **未命中聚类明细**：每条未命中记录带的聚类标签
- **高频未命中词**：全局出现频率最高的未命中词汇

### 使用建议

1. 先跑 `suggest_buckets.py`
2. 看 `聚类建议` 表，优先处理数量大的聚类
3. 对“补充关键词到现有业务桶”的聚类，直接把代表性词汇加到 `buckets_seed.json`
4. 对“考虑新建业务桶”的聚类，如果反复出现且业务性质独立，就新建一个桶
5. 改完配置后，再跑 `train_bucket_classifier.py` 验证命中率

---

## 六、迭代训练流程

```text
1. 准备训练数据 → CF_compile/training/assets/training_data/
2. 运行 train_bucket_classifier.py
3. 看 output/ 中的 bucket_training_report_*.xlsx
4. 如果未命中项很多 → 运行 suggest_buckets.py
5. 根据 suggest_buckets.py 的聚类建议，修改 buckets_seed.json
   - 补充关键词到现有桶，或
   - 新建业务桶（同时补充 bucket_cf_map.json 的现金流映射）
6. 回到步骤 2，循环迭代
```

---

## 七、新建业务桶的规范

如果确定要新建业务桶，需要同时修改两个文件：

**buckets_seed.json**：

```json
"研发费用": {
  "keywords": ["研发", "技术开发", "专利费", "研发材料"]
}
```

**bucket_cf_map.json**：

```json
"研发费用": ["支付其他与经营活动有关的现金"]
```

---

## 八、注意事项

1. **关键词允许重叠**：一个关键词可以属于多个业务桶（如“模具”既可能在存货采购也可能在固定资产），这是正常的，后续候选池机制处理。
2. **命中率目标**：初期 70%~80% 即可，先把高频词覆盖到；后续通过多轮迭代逐步提升到 90% 以上。
3. **未命中项优先看高频聚类**：数量大的聚类往往对应一个真实业务类型，优先处理。
4. **不要过度拟合**：太罕见的词可以暂时不补，避免污染桶的纯度。
5. **现金流映射不要随意改**：AI 调整关键词时只动 `buckets_seed.json`，不动 `bucket_cf_map.json`。
