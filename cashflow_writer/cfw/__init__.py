# -*- coding: utf-8 -*-
"""现金流量表编写 — 代码结构

一条流程，只有最后一段属于本包：

    ① 读表 + 认列名          ← summary_cleaner
    ② 业务桶判定             ← summary_cleaner（模型 + 四类硬约束）
    ③ 现金流判定             ← judge.py    桶 + 方向 → 现金流量项目
    ④ 写回                   ← rows.py     逐行：项目/方向/判定/复核
    ⑤ 导出                   ← export.py   原始账 / 业务分类结果 / 货币资金凭证
    ⑥ 编排与入口             ← pipeline.py / cli.py

**没有模型、没有清洗逻辑、没有硬约束。** 序时账怎么读、字段怎么认、业务桶怎么判，
全在 `summary_cleaner`；本包只做"业务桶 + 货币资金方向 → 现金流量项目"。
"""

from .config import __version__, RELEASE_NAME

__all__ = ["__version__", "RELEASE_NAME",
           "check_file", "run_file", "validate"]


def __getattr__(name):
    """惰性导入（避免 `import cfw` 就拉 torch）。"""
    if name in ("check_file", "run_file"):
        from . import pipeline
        return getattr(pipeline, name)
    if name == "validate":
        from . import judge
        return judge.validate
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
