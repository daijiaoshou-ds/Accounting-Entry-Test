# 训练阶段最终交付物

本目录存放 theory.md 第 1-3 阶段的三个核心产出：

| 文件名 | 中文名 | 作用 |
|---|---|---|
| `account_cf_map.json` | 一级科目映射表 | 每个一级科目可能对应的现金流量项目（无方向） |
| `buckets_seed.json` | 业务桶归纳表 | 每个业务桶包含哪些关键词，用于归纳摘要/二级科目 |
| `bucket_cf_map.json` | 业务桶映射表 | 每个业务桶可能对应的现金流量项目（无方向） |

---

## 交付规则

1. **工作版本** 存放在 `CF_compile/training/config/`：
   - `config/buckets_seed.json`
   - `config/bucket_cf_map.json`

2. **最终交付版本** 存放在本目录：
   - 当训练迭代完成、配置稳定后，从 `config/` 复制到本目录
   - `account_cf_map.json` 由 AI / 人根据 `CF_compile/assets/一级科目明细.md` 手工整理

3. **验收标准**：
   - `buckets_seed.json` 对训练数据的命中率 ≥ 90%
   - `bucket_cf_map.json` 中每个业务桶都有至少一个现金流项目
   - `account_cf_map.json` 中主要一级科目都有映射

---

## 三个文件的协同关系

```text
输入数据
  ├── 摘要文本 / 二级科目文本 ──→ buckets_seed.json（业务桶归纳表）──┐
  └── 一级科目 ──────────────────→ account_cf_map.json（一级科目映射表）┤
                                                                      ↓
                                                          bucket_cf_map.json（业务桶映射表）
                                                                      ↓
                                                            现金流量项目候选池
```

当一条记录的：
- 摘要/二级科目 命中某个业务桶 → 从 `bucket_cf_map.json` 拿到候选现金流项目
- 对方科目（一级科目）→ 从 `account_cf_map.json` 拿到候选现金流项目
- 两个候选集取并集，结合货币资金借贷方向，得到最终现金流量项目

---

## 当前状态

- `buckets_seed.json`：包含 12 个初始业务桶，227 个关键词
- `bucket_cf_map.json`：12 个业务桶的现金流映射
- `account_cf_map.json`：基于 `一级科目明细.md` 的初版映射，覆盖常见科目，待进一步完善

---

## 下一步

当本目录三个文件都稳定后，进入 theory.md 第 4-5 阶段：
- 方向判断（货币资金借=收现，贷=付现）
- 候选池处理 / 感知机决策
