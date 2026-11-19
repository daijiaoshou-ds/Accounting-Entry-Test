# -*- coding: utf-8 -*-
"""功能：会计分录测试（entry_test）2.0。

三步，前两步是地基，第三步（异常检验）按线索出、按凭证交付：

    第 0 步  总体完整性   这份账能不能用（凭证号连续 / 按月借贷平衡 / 数据质量）
                         + 金额首位分布（背景判断，不出线索）
    第 1 步  画像         这份账在讲什么（业务活动 + 集中度）
    第 2 步  检验         分块各查一段：金额孤立、活动里的科目、科目对（个体）
                         / 孤儿冲销（核算）

设计文档在 `docs/design/entry_test/`。三处与 1.0 的根本差别：

1. **没有综合得分**。1.0 把"业务群 + 跨群程度 + 金额重要性"揉成一个分数再分档，
   分数本身没法解释、也没法验证；2.0 每层各自出线索，每条线索带自己的统计量，
   "为什么可疑"是能读的。报告只摆风险、不给建议——查什么怎么查是项目组的判断。
2. **画像独立成一步**。1.0 一上来就找异常，人对面这本账一无所知；先讲清"都做了
   哪些经济活动、钱和谁往来、谁记的账"，后面的异常才有参照系。
3. **业务活动来自上游清洗，对方科目来自它的上游**：管线是
   `contra → cleaning → entry_test`，清洗的 link 把对方科目并进结果表，分录测试的
   link 再把它搬过来，科目组合那一层就吃到了这份账每一行的对方科目。
   同一份账不应该有两个"业务分类"，也不该有两个"对方科目"。

功能 id 仍是 `entry_test`：`journal_io/fields.py` 的字段契约表按这个名字声明，
插件、老任务的产物名也都靠它。换算法不动 id。
"""

from __future__ import annotations

from core.ledger import (has_upstream, prepared_signature, run_link,
                         upstream_signature)
from jobflow.feature import Feature
from jobflow.stage import Stage

from . import stages as s


def _no_upstream(ctx) -> bool:
    return not has_upstream(ctx)


def build() -> Feature:
    return Feature(
        id="entry_test",
        name="会计分录测试",
        description="完整性（含金额首位分布背景）→ 业务活动画像 → 分块检验（金额孤立 / 活动里的科目 / 科目对 / 孤儿冲销），出一份报告",
        requires=s.REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        accepts_upstream=("cleaning",),
        stages=(
            Stage(id="link", title="接入上游清洗的业务分类", actor="program",
                  run=run_link, enabled=has_upstream,
                  hash_extra=upstream_signature,
                  skip_note="没有指定上游任务，本任务自己读表"),
            Stage(id="read", title="载入序时账", actor="program",
                  run=s.run_read, enabled=_no_upstream,
                  hash_extra=prepared_signature,
                  skip_note="已从上游任务接过来"),
            Stage(id="integrity", title="总体完整性", actor="program",
                  depends_on=("link", "read"), run=s.run_integrity),
            Stage(id="profile", title="画像：业务活动与集中度", actor="program",
                  depends_on=("integrity",), run=s.run_profile),
            Stage(id="detect", title="分层检验：金额 / 科目组合 / 孤儿冲销", actor="program",
                  depends_on=("profile",), run=s.run_detect),
            Stage(id="export", title="出报告", actor="program",
                  depends_on=("detect",), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "会计分录测试报告（HTML）", "stage": "export"},
            {"key": "detail", "label": "序时账（含异常标记列）", "stage": "export"},
        ),
    )
