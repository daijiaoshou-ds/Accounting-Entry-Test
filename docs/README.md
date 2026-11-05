# 文档索引

全仓库的文档统一放在 `docs/`，**按性质分四类**，别混。分类依据是「写给谁、处在哪个阶段」，不是它讲哪个模块。

| 目录 | 性质 | 给谁看 | 进不进 git |
|---|---|---|---|
| `publish/` | **披露稿** —— 已定稿、可对外发布的文章 | 公众号读者 / 客户 | ✅ 进 |
| `design/` | **初版设计稿** —— 立项、改版时的思路、理论、方案、实施计划 | 我自己 / AI | ❌ 不进 |
| `review/` | **review 文档** —— 代码复核、skill 实测、缺陷与整改清单 | 我自己 / AI | ❌ 不进 |
| `reports/` | **结果文档** —— 技术报告、测试报告、架构梳理等结论 | 我自己 / 客户 | 现行报告进，历史不进 |

> ⚠️ `review/` 是外部 AI 用**真账**跑出来的实测报告，含客户专名与真账细节。
> 仓库是公开的，这一整目录被 `.gitignore` 屏蔽，**绝不能进 git**。
>
> skill 包（`summary-cleaner/`、`summary_cleaner_v3/`）里的 `README.md` / `SKILL.md` /
> `reference/*.md` / `assets/*.md` 是 AI 执行 skill 时按需读的**运行时文档**，随包发布，
> 跟这里的过程稿是两回事，不要往 `docs/` 搬。

---

## ① publish/ —— 披露稿（进 git）

已定稿、面向读者，可以直接发。

| 文件 | 讲什么 |
|---|---|
| `CSA_对方科目分析理论.md` | 多借多贷的对方科目分析算法（已发布，文内有配图与网盘链接） |
| `JET_会计分录测试理论.md` | 会计分录测试理论：只从会计科目 + 金额出发找异常凭证 |

## ② design/ —— 初版设计稿（不进 git）

过程稿：为什么这么设计、当初想了什么。**不是现行实现说明**，看实现请看 `reports/technical_report3.0.md`。

### design/cleaner/ —— 序时账清洗（6 份）

| 文件 | 讲什么 | 状态 |
|---|---|---|
| `bucket_design_principles.md` | 业务桶划分的核心思路与踩坑复盘（记录的是「为什么是这些结论」） | **最新，改桶之前先读** |
| `bucket_taxonomy_v9.md` | v9 业务桶重构设计提案（取代 v8） | 设计提案，未改代码 |
| `speedup_design.md` | 4.0 提速的实测结论 + 设计约束，数字来自 `_dev/bench/` 真实测量 | 已落地 |
| `theory_consolidated.md` | 项目启动前 4 份理论稿整合（V2.1 规则引擎 + NN 演进） | 历史 |
| `update for 4.0.md` | 4.0 提速方案初稿（科目组合切割、摘要去重、模型/AI 分工） | 已落地为 3.0 输入折叠 |
| `工具箱技术设计.md` | 会计分析工具箱技术设计 v2.0（CSA / JET 的工程实现） | 参考 |

### design/skill/ —— 设计这个 skill 本身（2 份）

| 文件 | 讲什么 | 对 skill 的影响 |
|---|---|---|
| `skill设计心得.md` | 两条核心：① 做成工作流程 ② 区分程序与 AI 的分工 | **直接决定 SKILL.md 怎么写** |
| `现金流量表编写理论.md` | 一键编现金流量表的口径：业务桶 → 现金流项目 | 现金流模块的权威依据 |

> `skill设计心得.md` 只对**写 skill 的人**有用，对**执行 skill 的 AI** 没用——不进 skill 包，免得白烧 token。
> 包内口径以 `reference/CASHFLOW_GUIDE.md` 为准。

### design/bank/ —— 银行流水匹配（3 份）

| 文件 | 讲什么 |
|---|---|
| `theory.md` | 货币资金循环审计中流水核对的目标与难点（一对多、多对一等） |
| `Modify.md` | 重大修改说明：公司名称向量化、序时账对方科目分析、匹配逻辑重构 |
| `implementation_plan.md` | 上面这份 `Modify.md` 的实施计划 |

### design/ 根目录

| 文件 | 讲什么 |
|---|---|
| `CSA_theory2.0preview.md` | 对方科目分析 2.0 预览稿（未定稿，故未进 publish） |

## ③ review/ —— review 文档（不进 git）

历轮复核与实测。**按轮次分子目录**，每轮一个目录，README 里有阅读顺序；
被新轮次取代的整轮挪进 `cleaner/archive/`，不删（留痕）。详见 `docs/review/README.md`。

| 子目录 | 内容 |
|---|---|
| `review/cleaner/v10_retrain/` | **本轮（11-05）重训前的完整复核链**：一轮复核结论 → 错因报告 → 金标准修正 → 全局复核 → 二次复核 → 补样本 → 重训自检 → 运输类清理，共 13 份 |
| `review/cleaner/archive/` | 7 月那批 cleaner 代码复核（优化报告、pattern 设计、详细复核、修后复核、完整复核、4 轮汇总），已消化 |
| `review/skill/` | `review-1/3/4.md`（AI 按 SKILL.md 完整跑一遍的执行反馈）、`skill-defects.md`（缺陷裁定与处置）、`序时账清洗3.0-skill实测问题清单.md` |
| `review/bank/` | `Modify_audit.md`（Modify.md 实现审查）、`matching_logic_audit.md`（文字说明 vs 代码自查） |

> 规则：**新开一轮复核就新建一个 `cleaner/<轮次>/` 目录**，别再往 `cleaner/` 根目录堆文件。
> 一轮结束、结论已落进 `design/` 后，把该目录整体移到 `archive/`。

## ④ reports/ —— 结果文档

| 文件 | 讲什么 | 进不进 git |
|---|---|---|
| `technical_report3.0.md` | **系统唯一的现行完整技术报告**：V2.1 规则引擎 + NN 微调模型 + 融合打分 | ✅ 进 |
| `history/technical_report2.1.md` | 技术报告 2.1（Pattern 时代的得分公式） | ❌ 历史 |
| `history/contra_review_report.md` | 对方科目分析代码实现报告 v3 | ❌ 历史 |
| `history/迁移功能测试报告.md` | 小工具 + 银行流水匹配迁移到工具箱的测试报告 | ❌ 历史 |
| `history/项目架构梳理报告.md` | 银行流水核对系统完整架构梳理 | ❌ 历史 |

> **只保留一份现行技术报告。** 新版出来后，旧版移进 `history/`，不要留在根目录并列摆放。
> 之前索引里提到的 `summary_cleaner_v3/reference/technical_report3.0.md` 副本早已被移除，
> 现在全仓库只有上面这一份，别再按旧路径找。

---

## 别再建第二个 docs 目录

| 位置 | 是什么 | 为什么不在 `docs/` |
|---|---|---|
| `summary-cleaner/`、`summary_cleaner_v3/` 的 `README.md` / `SKILL.md` / `reference/` / `assets/*.md` | skill 运行时文档 | 由 `package_release.py` 打进用户包，AI 执行时要读 |
| `_dev/*.md`（`v10_result.md`、`v10.1_changes.md`、`bridge_report.md` 等） | 当前迭代的一次性训练/验证产物 | 临时工作区，随迭代作废；有长期价值的再提炼进 `reports/` |
| 根目录 `README.md` / `INSTALL.md` / `SKILL.md`、`other_tools/README.md` | 项目级使用说明 | 给使用者看的入口，跟着代码走 |

**新增文档时的规则**：先问「这是披露稿、设计稿、review 还是结果文档」，放进对应目录；
结果文档只在 `reports/` 根目录留一份现行版，旧版进 `history/`；任何含客户专名、真账数据的内容
一律放 `review/`（已被忽略），永远不要提交。
