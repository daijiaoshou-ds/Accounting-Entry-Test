# -*- coding: utf-8 -*-
"""摘要归一化：数字 → `#` 占位符。

三处要用同一套口径：判桶引擎算「业务等价组」（`core/classify/dedup.py`）、推理算
缓存键（`core/nn/inference.py`）、训练数据去重。它既不属于训练、也不只属于推理，
所以单独放这里。

归一化的理由：日期/凭证号/金额对分类没有信息量。「暂估计提2024年12月份厂房电费」
与「暂估计提2025年1月份厂房电费」归一化后完全相同 → 相似度 100% → 正确合并。
实测原始相似度 35%，归一化后 95%+。
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\d+")


def normalize_summary(text: str) -> str:
    """摘要里的连续数字替换成 `#`。"""
    return _DIGITS_RE.sub("#", text)
