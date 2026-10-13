# -*- coding: utf-8 -*-
"""序时账清洗 3.0 — 融合版代码流程

不再区分"程序"与"模型"两套代码，而是一条流程：

    etl.py         读文件 / 认列名 / 规范化 / 抄回结果     ← 程序
    hard_rules.py  两条写死的机械规则                       ← 程序
    model.py       加载交付物 / 批量推理 / 算置信度          ← 引用模型
    pipeline.py    把上面串起来                             ← 编排
    cli.py         对外唯一命令行入口                       ← 排插

程序不参与业务桶打分：18 个业务桶全部来自模型，中/低置信度交给 AI 复核。
"""

from .config import __version__, RELEASE_NAME, ALL_BUCKETS, MODEL_BUCKETS, HARD_RULE_BUCKETS

__all__ = [
    "__version__", "RELEASE_NAME",
    "ALL_BUCKETS", "MODEL_BUCKETS", "HARD_RULE_BUCKETS",
    "JournalClassifier", "check_file", "run_file",
]


def __getattr__(name):
    """惰性导入重型符号（避免 `import jc` 就拉 torch）。"""
    if name == "JournalClassifier":
        from .model import JournalClassifier
        return JournalClassifier
    if name == "check_file":
        from .pipeline import check_file
        return check_file
    if name == "run_file":
        from .pipeline import run_file
        return run_file
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
