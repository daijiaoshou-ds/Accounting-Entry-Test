# -*- coding: utf-8 -*-
"""功能：会计分录测试（entry_test）2.0。

三步，前两步是地基，第三步（异常检验）按线索出、按凭证交付：

    第 0 步  总体完整性   这份账能不能用（凭证号连续 / 按月借贷平衡 / 数据质量）
    第 1 步  画像         这份账在讲什么（业务活动 + 集中度 + 抽凭建议）
    第 2 步  异常检验     三层网格（本福特 / 特征孤立 / 核算异常）+ 跨层聚合

设计文档在 `docs/design/entry_test/`。三处与 1.0 的根本差别：

1. **没有综合得分**。1.0 把"业务群 + 跨群程度 + 金额重要性"揉成一个分数再分档，
   分数本身没法解释、也没法验证；2.0 每层各自出线索，每条线索带自己的统计量与
   建议程序，"为什么可疑"是能读的。
2. **画像独立成一步**。1.0 一上来就找异常，人对面这本账一无所知；先讲清"都做了
   哪些经济活动、钱和谁往来、谁记的账"，后面的异常才有参照系。
3. **业务活动来自上游清洗**，不再自己聚类。`accepts_upstream=("cleaning",)`：
   同一份账不应该有两个"业务分类"。

功能 id 仍是 `entry_test`：`journal_io/fields.py` 的字段契约表按这个名字声明，
插件、老任务的产物名也都靠它。换算法不动 id。
"""

from __future__ import annotations

from jobflow.feature import Feature
from jobflow.stage import Stage

from ..cleaning import stages as clean_s
from . import stages as s


def _no_upstream(ctx) -> bool:
    return not clean_s.has_upstream(ctx)


def build() -> Feature:
    return Feature(
        id="entry_test",
        name="会计分录测试",
        description="先确认这份账可用（完整性），再讲清它在讲什么（业务活动画像）",
        requires=s.REQUIRES,
        accept=(".xlsx", ".xls", ".xlsm", ".csv"),
        accepts_upstream=("cleaning",),
        stages=(
            Stage(id="link", title="接入上游清洗的业务分类", actor="program",
                  run=clean_s.run_link, enabled=clean_s.has_upstream,
                  hash_extra=clean_s.upstream_signature,
                  skip_note="没有指定上游任务，本任务自己读表"),
            Stage(id="read", title="载入序时账", actor="program",
                  run=s.run_read, enabled=_no_upstream,
                  hash_extra=clean_s.prepared_signature,
                  skip_note="已从上游任务接过来"),
            Stage(id="integrity", title="总体完整性", actor="program",
                  depends_on=("link", "read"), run=s.run_integrity),
            Stage(id="profile", title="画像：业务活动与集中度", actor="program",
                  depends_on=("integrity",), run=s.run_profile),
            Stage(id="export", title="写回并导出", actor="program",
                  depends_on=("profile",), run=s.run_export),
        ),
        exports=(
            {"key": "result", "label": "会计分录测试（Excel/CSV）", "stage": "export"},
        ),
    )
