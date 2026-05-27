# 对方科目分析 — 代码实现报告 v2

## 一、整体流程

```
序时账导入
  │
  ├─ 字段配置 + 数据压缩（可选）
  │
  ├─ 按凭证(uid)分组
  │
  ├─ 特殊分录拦截（本年利润 / 汇兑损益）→ 直接填contra，不走后续流程
  │
  ├─ 轻量级分类 (_classify_voucher)
  │   │  按科目出现在借方列/贷方列去重计数（不管金额正负）
  │   │
  │   ├─ 1v1 / 1vN → 简单路径 (simple_vouchers)
  │   │
  │   └─ NvM（含全借全贷）→ 复杂路径
  │       ├─ SS归一化 (ss_normalizer)
  │       │   ├─ 先聚合(科目+原始方向) → 减少节点数
  │       │   ├─ 再搬移负数(借负→贷正, 贷负→借正)
  │       │   └─ 聚合归零的自冲销科目 → 定向逐行搬移
  │       │
  │       ├─ 穷举计算 (algorithm)
  │       │   ├─ 少数遍历多数 + 最小资源占优
  │       │   ├─ 约束：上限不可越、至多1非边界值、边界动态更新
  │       │   └─ 拒绝负数needed（全正数空间）
  │       │
  │       └─ 奥卡姆剃刀排序 (occams_razor)
  │
  └─ 生成最终报告 (finalize_report)
      ├─ 简单分录：_handle_simple_voucher
      ├─ 复杂分录：_append_complex_rows_v2 → _output_node_side
      └─ 对方科目拆为一级/二级两列
```

## 二、分类逻辑

**核心原则：按科目在借方列/贷方列的出现去重计数。**

```python
debit_subjs = set()   # 借方列有非零金额的科目
credit_subjs = set()  # 贷方列有非零金额的科目

for _, row in group.iterrows():
    if abs(row['_calc_debit']) > 0.001:    debit_subjs.add(subj)
    if abs(row['_calc_credit']) > 0.001:   credit_subjs.add(subj)

n_debit = len(debit_subjs)
n_credit = len(credit_subjs)

# 全借全贷 → NvM（统一走SS归一化）
if n_debit == 0 or n_credit == 0:   return NvM

# 标准借贷结构
if   n_debit==1 and n_credit==1:    return 1v1
elif n_debit==1 and n_credit>1:     return 1vN (单借方)
elif n_debit>1 and n_credit==1:     return 1vN (单贷方)
else:                               return NvM
```

## 三、简单分录输出

### 3.1 一借一贷

**规则**：借方科目行→贷方科目，贷方科目行→借方科目。

```python
if subj == d_subj:  contra = c_subj
elif subj == c_subj: contra = d_subj
```

### 3.2 多借一贷 / 一借多贷

**规则**：
- 多方行照抄（保留原始金额），contra = 单方科目
- 单方裂变为N个虚拟行，每多方科目一行，金额 = 该多方科目在多方侧的聚合金额

```python
for _, row in group.iterrows():
    if subj in multi_subjs:
        # 重叠科目：单方侧的行跳过（由虚拟行替代）
        if subj == single_subj:
            if single_side=='debit' and abs(row['_calc_debit'])>0.001: continue
            if single_side=='credit' and abs(row['_calc_credit'])>0.001: continue

        contra = single_subj
        amt = row['_calc_debit'] if single_side=='credit' else row['_calc_credit']
        multi_amounts[subj] += amt

# 单方裂变虚拟行
for multi_subj in multi_subjs:
    amt = multi_amounts[multi_subj]
    _create_virtual_row(uid, cols, single_subj, ..., amt, ..., multi_subj)
```

**科目重叠处理**：当 single_subj 也出现在多方列表中（如应收账款同时在借方和贷方），单方侧的行跳过不输出（由虚拟行替代），多方侧的行正常照抄。

## 四、复杂分录输出（NvM）

**核心逻辑**：SS归一化后的解(best_sol)中，每个节点的每条连接：

### 4.1 单连（节点只连一个对方科目）

所有原始行照抄，金额不变，只填 contra。

```python
if len(connections) == 1:
    for row_idx, row_amt in zip(node['row_indices'], node['row_amounts']):
        contra = node_map[contra_id]['subject']
        new_row[debit or credit] = row_amt    # 原始金额，符号不变
        new_row["对方科目"] = contra
```

### 4.2 多连（节点连多个对方科目）

原始行按金额降序排列，连接按金额降序排列，一一配对覆写。多余原始行被吸收，连接比行多时用第一行模板兜底。

```python
else:
    row_data = sorted(zip(row_indices, row_amounts), key=abs, reverse=True)
    connections.sort(key=abs, reverse=True)
    sign = 1 if orig_amt > 0 else -1

    for conn_idx, (contra_id, split_amt) in enumerate(connections):
        template_idx = row_data[conn_idx][0]  # 按顺序取模板行
        signed_amt = round(split_amt * sign, 2)
        new_row[debit or credit] = signed_amt  # 覆写金额
        new_row["对方科目"] = contra
```

对比原来的比例分配逻辑（`ratio = abs(row_amt) / abs_total`），覆写逻辑更简洁：不按比例拆分、不产生大量碎行、总金额不变。

## 五、SS归一化（仅NvM）

**策略**：先按（科目+原始方向）聚合保持压缩效果，再搬移负数。

**自冲销修复**：聚合后金额归零的科目（如借方+100和借方-100），对其原始行定向逐行搬移，拆为有效借方和有效贷方两个节点，避免被丢弃。

```python
# 检测聚合归零的科目
zero_keys = [(s, d) for (s, d), info in agg.items()
             if abs(round(info['amount'], 2)) < 0.001]

for key in zero_keys:
    info = agg.pop(key)
    subj, orig_side = key
    # 逐行确定有效方向，拆分为两个节点
    for idx, amt in zip(info['row_indices'], info['row_amounts']):
        eff_side = 'debit' if (orig_side=='debit' and amt>0) or (orig_side=='credit' and amt<0)
                   else 'credit'
        agg[(subj, eff_side)] = {...}  # 重新加入
```

## 六、穷举计算约束

SS归一化后全正数空间，三项核心约束：

| 约束 | 代码 |
|:---|:---|
| 至多1项非边界值 | 全匹配（全部取边界）+ 部分匹配（1个非边界） |
| 上限不可越 | `subset_sum <= target_amt` |
| 拒绝负数needed | `if needed < -0.001: continue` |

遍历规则：少数遍历多数 + 最小资源占优（金额小优先）。

## 七、分录处理示例

### 7.1 一借一贷

| 科目 | 借方 | 贷方 | 对方科目 |
|:---|:---|:---|:---|
| 原材料 | 100 | | 应付账款 |
| 应付账款 | | 100 | 原材料 |

### 7.2 多借一贷（折旧分配）

| 科目 | 借方 | 贷方 | 对方科目 |
|:---|:---|:---|:---|
| 管理费用-新大楼装修折旧 | 122,455.78 | | 累计折旧-累计折旧 |
| 管理费用-折旧费 | -940,032.15 | | 累计折旧-累计折旧 |
| 其他业务支出-租金 | 353.20 | | 累计折旧-累计折旧 |
| 销售费用-新大楼装修折旧 | 2,513.25 | | 累计折旧-累计折旧 |
| 研发费用-新大楼装修折旧 | 25,164.45 | | 累计折旧-累计折旧 |
| 制造费用-新大楼装修折旧 | 71,818.99 | | 累计折旧-累计折旧 |
| 累计折旧-累计折旧 (虚拟) | | 122,455.78 | 管理费用-新大楼装修折旧 |
| 累计折旧-累计折旧 (虚拟) | | -940,032.15 | 管理费用-折旧费 |
| ... (共6虚拟行) | | | |
| **合计** | **-717,726.48** | **-717,726.48** | 借贷平衡 ✓ |

### 7.3 多借一贷（科目重叠：汇兑损益调整）

| 科目 | 借方 | 贷方 | 对方科目 |
|:---|:---|:---|:---|
| 应收账款-人民币 | -10.63 | | 应收账款-人民币 |
| 财务费用-未实现汇兑损益 | -6,947.31 | | 应收账款-人民币 |
| 应收账款-人民币 (虚拟) | | -10.63 | 应收账款-人民币 |
| 应收账款-人民币 (虚拟) | | -6,947.31 | 财务费用-未实现汇兑损益 |
| **合计** | **-6,957.94** | **-6,957.94** | 借贷平衡 ✓ |

自洽性：财务费用借方-6947.31 ↔ 应收账款虚拟贷方-6947.31 金额对应 ✓

### 7.4 多借多贷（委托加工物资）

| 科目 | 借方 | 贷方 | 对方科目 |
|:---|:---|:---|:---|
| 半成品-其他半成品 | 193,966.63 | | 委托加工物资-加工费 |
| 半成品-其他半成品 | 428,064.09 | | 材料采购过渡-材料采购过渡 |
| 原材料-原材料 | 1,506,791.40 | | 委托加工物资-材料 |
| 原材料-原材料 | 13,547,579.66 | | 材料采购过渡-材料采购过渡 |

半成品 622,030.72 = 193,966.63 + 428,064.09 ✓
原材料 15,054,371.06 = 1,506,791.40 + 13,547,579.66 ✓

### 7.5 全借全贷（多科目）

| 科目 | 借方 | 贷方 |
|:---|:---|:---|
| 应收账款 | 100 | |
| 应付账款 | 100 | |
| 主营业务收入 | -200 | |

→ SS归一化 → debit{应收:100, 应付:100}, credit{主营:200} → 2v1 穷举求解

### 7.6 全借全贷（单科目自冲销）

| 科目 | 借方 | 贷方 |
|:---|:---|:---|
| 生产成本-工资 | 28,686.58 | |
| 生产成本-工资 | -28,686.58 | |

→ SS归一化(自冲销修复) → debit{生产成本:28686}, credit{生产成本:28686} → 1v1

### 7.7 特殊分录

- **期末结转**（含"本年利润"）：全部行 contra = 本年利润
- **汇兑损益**（财务费用 + 3个以上往来科目 + 无经营性损益）：非财务科目→财务费用，财务费用→汇兑损益调整对象
