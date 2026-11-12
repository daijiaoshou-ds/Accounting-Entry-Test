# -*- coding: utf-8 -*-
"""会计业务常量 —— 科目的**业务含义**，不是"字段"。

「哪些科目算货币资金」被抄过 5 份（清洗的 pipeline、bucket_bridge、config，
现金流的 judge，分录测试的 cluster_engine）。抄错一次的后果是静默的：
货币资金集合少一个科目，那一类凭证就不进现金流量表，报表还是平的。

这里集中定义，谁要用谁 import。**feature 不许再写 `cash = {...}`。**
"""

from .subjects import (  # noqa: F401
    CLOSING_KEYWORDS,
    CLOSING_MARKS,
    EMPLOYEE_LOAN_KEYWORDS,
    EMPLOYEE_LOAN_EXCLUDE,
    EMPLOYEE_LOAN_SUBJECTS,
    FX_BLACKLIST,
    FX_KEYWORDS,
    MONEY_FUNDS,
    MONEY_FUNDS_ORDER,
    PL_SUBJECTS,
    regex_or,
)

__all__ = [
    "MONEY_FUNDS", "MONEY_FUNDS_ORDER", "PL_SUBJECTS",
    "CLOSING_MARKS", "CLOSING_KEYWORDS",
    "FX_KEYWORDS", "FX_BLACKLIST",
    "EMPLOYEE_LOAN_SUBJECTS", "EMPLOYEE_LOAN_KEYWORDS", "EMPLOYEE_LOAN_EXCLUDE",
    "regex_or",
]
