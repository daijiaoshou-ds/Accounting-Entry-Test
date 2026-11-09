# -*- coding: utf-8 -*-
"""数据指纹：用来给"同一本账"生成稳定文件名（训练数据按哈希分离存储）。

范围说明：4.0 删掉了全局 PMI 计数器（程序不再累积科目共现统计），
只剩下这一个纯函数。
"""

from __future__ import annotations

import hashlib

import pandas as pd


def compute_fingerprint(df: pd.DataFrame, voucher_col: str) -> str:
    """所有凭证号排序后取 SHA256 前 16 位。

    只依赖凭证号集合：同一本账导两次（格式/列序/行序变化）指纹相同，
    因此可以覆盖同一份未审训练数据而不是堆一堆近似重复的文件。
    """
    if voucher_col not in df.columns:
        return ""
    ids = sorted(df[voucher_col].dropna().astype(str).unique())
    if not ids:
        return ""
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]
