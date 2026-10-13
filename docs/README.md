# 文档索引

文档按**性质**分两类，别混：

| 目录 | 是什么 | 给谁看 | 进不进 git |
|---|---|---|---|
| `publish/` | **披露稿**——已完善、可对外发布的文章 | 公众号读者 / 客户 | ✅ 进 |
| `design/` | **设计稿**——过程中写给我自己和 AI 看的稿子 | 我自己 / AI | ❌ 不进（`.gitignore`） |

> 这份索引本身**不写进 skill 包**。skill 包里 `reference/` 那几份是
> 给 AI 执行时按需读的（SETUP / REVIEW_GUIDE / CASHFLOW_GUIDE 等），
> 两回事。

---

## publish/ —— 披露稿

已定稿、面向读者，可以直接发。

| 文件 | 讲什么 |
|---|---|
| `CSA_对方科目分析理论.md` | 多借多贷的对方科目分析算法（已发布，文内有配图与网盘链接） |
| `JET_会计分录测试理论.md` | 会计分录测试理论：只从会计科目 + 金额出发找异常凭证 |

---

## design/ —— 设计稿

过程稿。思路、方案、速查、review 都在这里，**不对外**。

### design/skill/ —— 设计这个 skill 本身

| 文件 | 讲什么 | 对 skill 的影响 |
|---|---|---|
| `skill设计心得.md` | skill 设计两条核心：① 做成工作流程 ② 区分程序与 AI 的分工 | **直接决定 SKILL.md 怎么写** |
| `现金流量表编写理论.md` | 一键编现金流量表的口径：业务桶 → 现金流项目，方向看借贷 | **现金流模块的权威依据** |

> ⚠️ `skill设计心得.md` 只对**写 skill 的人**有用，
> 对**执行 skill 的 AI** 没用——所以它不进 skill 包，免得白烧 token。

### design/cleaner/ —— 序时账清洗

| 文件 | 讲什么 | 状态 |
|---|---|---|
| `update for 4.0.md` | 4.0 提速方案（科目组合切割、摘要去重、模型/AI 分工） | **已落地**为 3.0 的输入折叠 |
| `theory_consolidated.md` | 项目启动前的 4 份理论稿整合（V2.1 规则引擎 + NN 演进） | 历史 |
| `technical_report2.1.md` | 序时账清洗技术报告 2.1（Pattern 时代的得分公式） | 历史 |
| `review_consolidated.md` | 4 轮代码 review 汇总 + 改进情况 | 历史 |
| `工具箱技术设计.md` | 会计分析工具箱技术设计（CSA / JET 的工程实现） | 参考 |

> `cleaner/` 里后 4 份是从原项目 `summary_cleaner/docs/` **复制**过来的
> （原项目的文件我没动）。`technical_report3.0.md` 留在了 `docs/` 根目录，
> 因为它是工具箱层面的，不只属于清洗。

### design/review/ —— 历轮 review

7 份 review 报告（优化报告、pattern 设计、详细复核、修后复核等）。

### design/ 根目录

| 文件 | 性质 |
|---|---|
| `CSA_theory2.0preview.md` | 对方科目分析 2.0 预览稿（未定稿，故未进 publish） |
| `contra_review_report.md` | 对方科目分析代码实现报告 v3 |
| `迁移功能测试报告.md` | 工具箱迁移的测试报告 |

---

## 技术报告 3.0 在哪

`technical_report3.0.md` 在 `docs/` 根目录（工具箱层面）。
skill 包内另有一份副本：`summary_cleaner_v3/reference/technical_report3.0.md`，
因为 AI 执行时需要读它。
