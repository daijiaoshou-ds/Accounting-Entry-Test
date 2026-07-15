# Summary Cleaner 技术报告

> 序时账自动清洗系统 — 基于 PMI 相关性矩阵 + 多维度偏置的业务分类引擎

---

## 目录

1. [最终得分公式](#1-最终得分公式)
2. [参数详解](#2-参数详解)
   - [2.1 v·w' — PMI 结构分](#21-vw--pmi-结构分)
   - [2.2 max(b,c) — 关键词偏置](#22-maxbc--关键词偏置)
   - [2.3 s — 金额惩罚分](#23-s--金额惩罚分)
   - [2.4 d — 纠错顺位增强](#24-d--纠错顺位增强)
   - [2.5 e — 制单人偏置](#25-e--制单人偏置)
3. [分类管线](#3-分类管线)
   - [3.1 Step 0: 硬规则预检](#31-step-0-硬规则预检)
   - [3.2 Step 1-2: PMI 矩阵构建与融合](#32-step-1-2-pmi-矩阵构建与融合)
   - [3.3 Step 3-4: 偏好传播 w'](#33-step-3-4-偏好传播-w)
   - [3.4 Step 5: 逐凭证分类](#34-step-5-逐凭证分类)
   - [3.5 Step 7-8: 学习与持久化](#35-step-7-8-学习与持久化)
4. [学习机制](#4-学习机制)
   - [4.1 通用 R 矩阵](#41-通用-r-矩阵)
   - [4.2 自动词特征](#42-自动词特征)
   - [4.3 金额特征](#43-金额特征)
   - [4.4 纠错回路](#44-纠错回路)
   - [4.5 制单人映射](#45-制单人映射)
5. [存储架构](#5-存储架构)
6. [关键常数速查](#6-关键常数速查)

---

## 1. 最终得分公式

```
Score(bucket) = λ_struct × (v · w')  +  max(b, c)  +  s  +  d  +  e
                └─ 结构分 ─┘    └─ 关键词 ─┘  └ 金额 ┘ └纠错┘ └制单人┘
```

| 符号 | 名称 | 含义 | 典型取值范围 | 配置常量 |
|------|------|------|-------------|---------|
| `v · w'` | PMI 结构分 | 凭证科目向量与传播后桶偏好的点积 | 0 ~ 1.0 | — |
| `λ_struct` | 结构分系数 | 放大结构分权重 | 1.5 | `LAMBDA_STRUCT` |
| `b` | 手工关键词偏置 | AC 自动机命中预定义关键词的得分 | 0 ~ 2.0+ | — |
| `c` | 自动词偏置 | jieba 分词后 PMI 发现的野生词得分 | 0 ~ 1.0 | — |
| `s` | 金额惩罚分 | 偏离桶历史金额分布的惩罚 | -0.3 ~ 0 | `LAMBDA_A = 0.3` |
| `d` | 纠错顺位增强 | 用户历史纠错信号的加权偏置 | 0 ~ 2.5 | `LAMBDA_RANK = 1.0` |
| `e` | 制单人偏置 | 模块会计对负责桶的偏好 | -0.1 ~ 0.5 | — |

**b 和 c 取 max 而非相加**，避免同一词同时出现在手工关键词表和自动词清单时被重复计分。

**税费桶特殊处理**：`TAX_DECAY = 0.5` 仅衰减税费桶的 b 和 c，结构分/金额分/纠错分/制单人分不受影响。

---

## 2. 参数详解

### 2.1 v·w' — PMI 结构分

#### 数学原理

**凭证向量 v**：将一张凭证的所有分录行压缩成一个科目向量。

对于凭证中涉及的每个一级科目 i：

```
amt_i = |debit_i| + |credit_i|
x_i = amt_i / Σ amt_j          (凭证内归一化)
v_i = x_i × C_i                (乘以清晰度系数)
```

**清晰度系数 C_i**（四级分类）：

| 层级 | 含义 | C 值 | 包含科目举例 |
|------|------|------|-------------|
| T0 | 绝对灵魂：自带强业务属性 | 1.5 | 应付职工薪酬、固定资产、在建工程、主营业务收入、生产成本 |
| T1 | 标准业务：正常业务承载 | 1.0 | 管理费用、销售费用、应收账款、大部分未归入其他层级的科目 |
| T2 | 垃圾桶/过渡：表达极不清晰 | 0.3 | 其他应收款、其他应付款、营业外收支 |
| T3 | 纯资金管道：无业务含义 | 0.0 | 银行存款、库存现金、其他货币资金 |

**传播后偏好向量 w'**：`w' = normalize(w × R)`

- w：每个桶的初始偏好向量（人工设定，如 职工薪酬桶 → {应付职工薪酬: 1.0, 其他: 0.0}）
- R：PMI 相关性矩阵（科目共现统计）
- w × R：矩阵乘法，偏好沿 PMI 相关性传播到关联科目
- L2 归一化：`w' = (w × R) / ‖w × R‖₂`

**PMI 矩阵 R 的计算**：

```
PMI(A,B) = max(0, min(7.0, ln(N × count_AB / (count_A × count_B))))
```

- 对角线恒为 1.0
- 共现次数为 0 → PMI = 0（Laplace 平滑）
- 钳位到 [0, 7.0]，防止稀疏共现产生极端 PMI

**融合**：`Final_R = (1-α) × universal_R + α × company_R`，α 默认 0.2。

#### 工程实现

| 模块 | 文件 | 类/方法 |
|------|------|---------|
| PMI 矩阵 | `engine.py` | `PMIMatrix.from_vouchers()` / `from_counters()` |
| 融合 | `engine.py` | `PMIMatrix.fuse(universal_R, alpha)` |
| 凭证向量化 | `engine.py` | `VoucherVectorizer.vectorize()` |
| 清晰度字典 | `config.py` | `build_clarity_dict()` → `SUBJECT_CLARITY` |
| 偏好传播 | `engine.py` | `CorrelationPropagator.propagate_one()` |
| 通用 R 构建 | `persistence.py` | `GlobalCounters.build_universal_R()` |

---

### 2.2 max(b,c) — 关键词偏置

#### 数学原理

**手工关键词 b**：AC 自动机在摘要 + 二级科目中扫描预定义关键词表，命中后累加偏置分。

```
b(bucket) = Σ keyword_score(bucket, keyword)
```

- 同一关键词在同一凭证中无论出现多少次只计一次
- 显式分数优先（`KEYWORD_EXPLICIT_SCORES`），未配置的关键词自动生成分数
- 支持负分抑制（如"报销"对生产制造桶 -0.3）

**自动词 c**：jieba 分词 + PMI 发现的野生词特征得分。

```
PMI(word, bucket) = ln( P(word|bucket) / (P(word|global) × P(bucket)) )
auto_score(word) = piecewise_linear_map(PMI)    # PMI 0.5~5.0 → score 0~1.0
c(bucket) = Σ auto_score(word, bucket)           # 命中了多少个自动词
```

**取 max 的原因**：同一词可能既在手工关键词表又在自动词清单，取 max 避免重复计分。

#### 工程实现

| 模块 | 文件 | 类/方法 |
|------|------|---------|
| 手工关键词匹配 | `matcher.py` | `KeywordMatcher.match_voucher()` |
| 关键词词典 | `config.py` | `KEYWORD_EXPLICIT_SCORES` + `assets/业务桶与keyword.json` |
| 自动词学习 | `memory_learner.py` | `WordFeatureLearner.update()` → `compute_auto_scores()` |
| 自动词匹配 | `memory_learner.py` | `WordFeatureLearner.match_voucher()` |
| 自动词存储 | `persistence.py` | `auto_words_tier1/2/3.json` + `auto_words/{hash}.json` |

---

### 2.3 s — 金额惩罚分

#### 数学原理

对每个桶，统计历史凭证金额的对数分布 (μ, σ)。

```
μ_b = Σ ln(amount) / n
σ_b = sqrt( Σ ln(amount)² / n - μ_b² )
```

对一张新凭证金额 amt：

```
z = (ln(amt) - μ_b) / σ_b
s(bucket) = λ_a × tanh(-z²/2)           λ_a = 0.3
```

- **纯惩罚项**：s ∈ [-0.3, 0]，金额完美匹配均值时 s = 0
- Tanh 截断保证极端偏离也只扣 0.3 分（≈ 一个弱关键词的加分）
- 桶样本数 < 10 时不参与（无监督）/ 纠错 EMA 样本数 < 3 时不参与（有监督）

**两个来源**：

| 来源 | 有监督？ | 学习方式 | 触发条件 |
|------|---------|---------|---------|
| AmountProfiler | 无 | 累加统计 | 自动分类后，n ≥ 10 |
| CorrectionManager EMA | 有 | EMA 动态追踪 | 用户纠错后，n ≥ 3 |

两者取 max 合并：纠错 EMA 覆盖无监督 profiler。

#### 工程实现

| 模块 | 文件 | 类/方法 |
|------|------|---------|
| 无监督金额学习 | `memory_learner.py` | `AmountProfiler.update()` → `compute_profiles()` |
| 有监督金额学习 | `correction.py` | `CorrectionManager._update_amount_ema()` |
| 金额打分 | `memory_learner.py` + `correction.py` | `score_all()` + `get_amount_ema_score()` |
| 金额统计存储 | `persistence.py` | `amount_stats` in `global_counters.json` |
| EMA 存储 | `correction.py` | `amount_ema` in `corrections.json` |

---

### 2.4 d — 纠错顺位增强

#### 数学原理

**信号结构**：四维联合主键 `ctx:{acc1}+{kw}+{native}`

- `acc1`：一级科目（过滤 T3 纯资金管道）
- `kw`：关键词（jieba 分词摘要 + 二级科目明细）
- `native`：原生桶（该凭证首次被纠正时的原始分类，永不改变）

**纠错时**：用户纠正 voucher X：原始桶 A → 正确桶 B，系统记录：

```
ctx:应付职工薪酬+奖品+职工薪酬 → 长期资产: +1
ctx:应付职工薪酬+年会+职工薪酬 → 长期资产: +1
```

所有 (acc1 × kw) 组合各产生一条信号，指向正确桶。

**打分时**：两轮分类

1. **第一轮（无 d）**：得到"纠正前"分类 → 作为查询键
2. **查纠错表**：`ctx:{acc1}+{kw}+{第一轮分类}` → 查历史信号
3. **第二轮（带 d）**：最终分类

```
P(纠错到桶 B | entity) = count(entity → B) / total_count(entity)
d(bucket) = P × λ_rank × multiplier

multiplier:  1 批次 = 2.0,  2 批次 = 2.25,  3+ 批次 = 2.5
λ_rank = 1.0
```

**设计要点**：

- **原生桶锁定**：无论纠正多少次，ctx key 的第三维始终是首次纠错时的原始分类。改判时新信号覆盖旧信号，同桶再确认时轮次 +1。
- **T3 过滤**：银行存款、库存现金等纯资金科目不参与纠错信号生成。
- **批次计数**：同一上传批次内，每个 (entity, bucket) 只 +1，避免一次 100 张的纠错表直接封顶。

#### 工程实现

| 模块 | 文件 | 类/方法 |
|------|------|---------|
| 纠错记录 | `correction.py` | `CorrectionManager.record_corrections_batch()` |
| 纠错查询 | `correction.py` | `CorrectionManager.compute_rank_bonus()` |
| 两轮分类 | `classifier.py` | Step 5f-5h in `classify()` |
| 纠错存储 | `correction.py` | `corrections.json` |
| 原生桶追踪 | `correction.py` | `_vid_native`, `_vid_signals`, `_vid_batches` |

---

### 2.5 e — 制单人偏置

#### 数学原理

模块会计岗位 → 偏好桶的静态映射：

| 岗位 | 偏好桶 | 偏好桶得分 | 其他模块桶得分 |
|------|--------|-----------|---------------|
| 应收会计 | 销售收入 | +0.5 | -0.1 |
| 应付会计 | 存货采购 | +0.5 | -0.1 |
| 资产会计 | 长期资产 | +0.5 | -0.1 |
| 工资会计 | 职工薪酬 | +0.5 | -0.1 |
| 生产会计 | 生产制造 | +0.5 | -0.1 |

"其他模块桶"仅限上述五个，不扩散到费用报销、税费等无关桶。无岗位映射的制单人（如费用会计、总账会计）选择"无"，不产生偏置。

#### 工程实现

| 模块 | 文件 | 类/方法 |
|------|------|---------|
| 岗位映射 | `config.py` | `BOOKKEEPER_ROLE_TO_BUCKET`, `SPECIALIST_BUCKETS` |
| 偏置计算 | `classifier.py` | Step 5e in `classify()` |
| UI 配置 | `ui.py` | `_render_bookkeeper_role_mapping()` |
| 存储 | `persistence.py` | `bookkeeper/{hash}.json` |

---

## 3. 分类管线

`JournalClassifier.classify()` 是主编排器，位于 `classifier.py`。

```
Step 0: 硬规则预检（结转损益 / 资金往来 / 汇兑损益）
  ↓
Step 1: 构建公司专属 PMI 矩阵 (company_R)
  ↓
Step 2: 加载通用 R → 融合 (final_R)
  ↓
Step 3: 初始化偏好向量 w
  ↓
Step 4: 相关性传播 w' = normalize(w × R)
  ↓
Step 4b-4c: 加载历史学习数据（金额/自动词/纠错）
  ↓
Step 5: 逐凭证分类（两轮评分：先无 d 得原生桶 → 查 d → 带 d 重算）
  ↓
Step 6: 映射回 DataFrame
  ↓
Step 7: 更新全局 PMI 计数器
  ↓
Step 8: 学习（金额特征 + 自动词 + 纠错）
```

---

### 3.1 Step 0: 硬规则预检

在评分之前，先拦截三类无需 PMI 判断的凭证：

**A. 期末结转损益**

检测条件：
- 科目含 `本年利润` 或 `以前年度损益调整`
- 且（摘要含"结转/损益/期末/本月"关键词 **或** 科目数 ≥ 5）

处理：直接归入「其他业务」，排除出 PMI 计算。

**B. 资金内部往来**

检测条件：凭证所有一级科目均为货币资金（`{库存现金, 银行存款, 其他货币资金}`）。

处理：直接归入「资金内部往来」。

**C. 汇兑损益**

检测条件：
- 必须有 `财务费用`
- 不能有其他损益类科目（管理费用、销售费用、主营业务收入/成本等）
- 不能有黑名单科目（应付职工薪酬、应交税费、应付票据等）
- 摘要必须含汇兑关键词（`汇兑/结汇/收汇`）

处理：直接归入「汇兑损益」。

---

### 3.2 Step 1-2: PMI 矩阵构建与融合

**Step 1 — 公司专属 R**：

从当前上传的序时账中按凭证统计科目共现，计算 PMI 矩阵。

```
输入: DataFrame (排除预分配凭证和结账科目)
统计: 每张凭证的科目集合 → count_A, count_AB
计算: PMI(A,B) = ln(N × count_AB / (count_A × count_B))
```

**Step 2 — 通用 R 融合**：

从 `GlobalCounters` 加载历史累加的全局计数器，生成 `universal_R`，按权重融合：

```
final_R = (1 - α) × universal_R + α × company_R    (α 默认 0.2)
```

科目并集对齐，缺失科目填 0。80% 来自海量历史统计（大数定律的会计常识），20% 来自当前公司特征。

---

### 3.3 Step 3-4: 偏好传播 w'

每个业务桶有一个人工设定的偏好向量 w（如 职工薪酬桶 = {应付职工薪酬: 1.0, 其他: 0}），乘以 PMI 矩阵 R 后偏好沿科目相关性传播：

```
w' = normalize(w × R)
```

L2 归一化确保所有桶的偏好向量在同一尺度，防止多锚定科目的桶（如存货采购有 9 个偏好科目）因向量模长大而占据优势。

---

### 3.4 Step 5: 逐凭证分类

对每张非预分配凭证，依次执行：

```
5a. 凭证向量化 v
    amt_i = |debit| + |credit|
    x_i = amt_i / total_amt
    v_i = x_i × clarity(s_i)

5b. 手工关键词偏置 b
    AC 自动机扫描摘要 + 二级科目
    b(bucket) = Σ keyword_score

5c. 自动词偏置 c
    jieba 分词 → 查 tier1/tier2 → 累加 auto_score

5d. 金额惩罚 s
    AmountProfiler 计算 z-score → s = λ_a × tanh(-z²/2)
    合并 CorrectionManager EMA → max(profiler, ema)

5e. 制单人偏置 e
    查制单人→岗位映射 → 偏好桶 +0.5, 其他模块桶 -0.1

5f. 第一轮评分（无 d）
    top_no_d = classify(v, w', b, c, s, d={}, e)

5g. 纠错顺位 d
    ctx:{acc1}+{kw}+{top_no_d} → 查 rank_table
    d = P × λ_rank × multiplier

5h. 第二轮评分（带 d）→ 最终分类
    top_bucket = classify(v, w', b, c, s, d, e)
```

**平局打破**：得分 → 桶清晰度 → 桶名字典序。

**分数明细**：每张凭证输出 7 列 × 18 桶 = 126 列的完整分解。

---

### 3.5 Step 7-8: 学习与持久化

**Step 7 — PMI 计数器更新**：

`GlobalCounters.update()` 将当前数据的科目共现统计累加入全局计数器。指纹去重防止同一批数据重复计入。

**Step 8 — 多维度学习**：

| 子步骤 | 学习内容 | 存储位置 |
|--------|---------|---------|
| 8a | 金额统计（无监督） | `global_counters.json` → `amount_stats` |
| 8b | 自动词词频（按 hash） | `auto_words/{hash}.json` |
| 8c | 自动词全局聚合 + Tier 分层 | `auto_words_tier1/2/3.json` + `word_data.json` |

---

## 4. 学习机制

### 4.1 通用 R 矩阵

**学什么**：科目共现的 PMI 统计。

**怎么学**：每批分类完成后，累加三个全局计数器：

```
N_global += 本次凭证数
count_A[科目] += 本次出现次数
count_AB[(科目A, 科目B)] += 本次共现次数
```

**怎么用**：下次分类时从计数器生成 `universal_R`，与 `company_R` 按 0.8:0.2 融合。

**越用越聪明**：✅ 大数定律。数据越多，PMI 越接近真实的会计科目关系。指纹去重防止单公司过拟合。

---

### 4.2 自动词特征

**学什么**：每个桶的野生关键词及其 PMI 强度。

**怎么学**：

1. **分词**：jieba 切分摘要 + 二级科目 → 5 重过滤（长度/数字/英文/发票号/停用词）
2. **统计**：按桶累加词频，每个凭证内同词只计一次
3. **PMI 计算**：`PMI(word, bucket) = ln(P(word|bucket) / (P(word) × P(bucket)))`
4. **分桶排他**：同一词在多个桶通过 PMI 阈值 → 丢弃（不具区分力）
5. **Tier 分层**：
   - Tier 1：count ≥ 5 → 高频强特征词
   - Tier 2：count < 5 → 低频累积中
   - Tier 3：跨 5+ 个 session 仍低频 → 垃圾桶（不参与打分）

**怎么用**：下次分类时，新凭证的 jieba 分词结果查 tier1/tier2，命中后累加 auto_score。

**越用越聪明**：✅ 词频累积 → 更多词进入 Tier 1 → 更丰富的特征信号。

---

### 4.3 金额特征

**学什么**：每个桶的典型金额分布 (μ, σ)。

**怎么学**：

| 来源 | 算法 | 学习率 |
|------|------|--------|
| 无监督 | 累加统计：n, Σln, Σln² | 全量 |
| 有监督 (EMA) | μ_new = (1-α)×μ_old + α×ln(amt) | α = 0.1 |

**怎么用**：`s = λ_a × tanh(-((ln(amt)-μ)/σ)² / 2)`，金额越偏离桶均值扣分越多。

**越用越聪明**：✅ 样本越多分布越精确；EMA 动态追踪用户纠错后的金额认知。

---

### 4.4 纠错回路

**学什么**：用户人工纠正的凭证特征 → 正确桶的映射。

**怎么学**：

1. 提取凭证信号：`acc1`（一级科目去 T3）+ `kw`（jieba 摘要 + 二级科目）
2. 记录联合主键：`ctx:{acc1}+{kw}+{native}` → 正确桶 +1
3. 批次去重 + 覆盖逻辑（改判撤销旧信号，再确认轮次 +1）

**怎么用**：第一轮分类得到原生桶 → 查纠错表 → 命中信号的桶获得 d 偏置。

**越用越聪明**：✅ 纠错信号随批次累积增强；原生桶锁定确保改判始终生效。

---

### 4.5 制单人映射

**学什么**：不学习。用户手动配置制单人 → 会计岗位的静态映射。

**怎么用**：配置后，每张凭证根据制单人加偏置 e。

**越用越聪明**：不适用。一次性配置，同 hash 重上传时自动回填。

---

## 5. 存储架构

```
_storage/
├── global_counters.json    # PMI 全局计数器 (N, count_A, count_AB, amount_stats)
├── auto_words_tier1.json   # Tier 1 高频自动词
├── auto_words_tier2.json   # Tier 2 低频自动词 + word_sessions
├── auto_words_tier3.json   # Tier 3 垃圾桶
├── word_data.json          # 自动词总览 + 删除记录
├── corrections.json        # 纠错回路 (amount_ema + rank_table + 纠错日志)
├── auto_words/             # 按哈希分存的自动词原始词频
│   ├── {hash1}.json
│   └── {hash2}.json
├── bookkeeper/             # 按哈希分存的制单人映射
│   └── {hash1}.json
└── backups/                # 自动备份（每文件保留 3 个版本）
    └── *.json
```

**安全写入**：所有 JSON 写入经过 `safe_write_json()`：先备份现文件 → 写 `.tmp` → 原子 rename。Windows 兼容（rename 失败时 delete-then-rename 兜底）。

**指纹去重**：按凭证 ID 排序后 SHA256 取前 16 位。同一序时账重复上传不重复计入 PMI 计数器，但自动词可按 hash 重新学习。

---

## 6. 关键常数速查

| 常量 | 值 | 位置 | 说明 |
|------|-----|------|------|
| `LAMBDA_STRUCT` | 1.5 | `config.py` | PMI 结构分放大系数 |
| `LAMBDA_RANK` | 1.0 | `config.py` | 纠错顺位增强系数 |
| `LAMBDA_A` | 0.3 | `memory_learner.py` | 金额特征最大影响 |
| `EMA_ALPHA` | 0.1 | `config.py` | 金额 EMA 学习率 |
| `TAX_DECAY` | 0.5 | `config.py` | 税费桶关键词衰减 |
| `DEFAULT_CLARITY` | 0.5 | `config.py` | 未知科目兜底清晰度 |
| `alpha` (融合) | 0.2 | `ui.py` | 通用 R 与专属 R 融合权重 |
| PMI 钳位 | [0, 7.0] | `engine.py` | PMI 值上下限 |
| Tier 1 阈值 | 5 | `memory_learner.py` | 高频词认定 count |
| Tier 3 阈值 | 5 sessions | `memory_learner.py` | 垃圾桶触发 session 数 |
| `MAX_BACKUPS` | 3 | `storage_utils.py` | 每文件保留备份数 |
| 纠错 boost (1批) | 2.0 | `correction.py` | |
| 纠错 boost (2批) | 2.25 | `correction.py` | |
| 纠错 boost (3+批) | 2.5 | `correction.py` | |
| 制单人偏好 | +0.5 | `config.py` | `BOOKKEEPER_PREFERRED_BONUS` |
| 制单人排斥 | -0.1 | `config.py` | `BOOKKEEPER_PENALTY` |

---

> 报告基于 `summary_cleaner v2.0.0` 生成。
