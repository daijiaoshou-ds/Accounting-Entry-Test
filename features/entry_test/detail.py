# -*- coding: utf-8 -*-
"""交付物里的 Excel：**原序时账 + 异常标记列**。

不是"报告的附件"。报告是给人读的（一屏一件事，图、结论、总览），而审计员真正要
动手的那一步在账上：把这一行拎出来看凭证、看合同、看期后。所以这里的形状跟清洗
一致——**用户传进来什么列，就还他什么列**，只在末尾加几列标记，原列原序一个不动。

加四列：

    是否异常    是 / 空
    异常原因    命中的线索类型，多条用「；」连（金额孤立；孤儿冲销）
    最高优先级  高 / 中 / 低
    白名单放行  被白名单放过的，写原因（资金内部往来）；正常行为空

为什么不做成"线索清单 / 涉及凭证 / 完整性 / 业务活动 / 本福特"六张表：那五张里
有四张跟报告说的是同一件事，等于把同一批结论印两遍；而"一条线索一行"的表又跟账
对不上——审计员找不到它落在第几行。按行标记之后，筛选「是否异常 = 是」就是全部
问题凭证，回原账对号入座，不用在两张表之间来回跳。

代价说明白：一条线索的统计证据（中位数多少倍、占该活动几成）不在这一列里，它在
报告那一层的总览里。这里只回答"哪几行要看"，不回答"为什么"——理由塞进单元格会
变成一句话都读不完的长文本，而且同一张凭证的每一行都要重复一遍。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

# 优先级排序：高在前
_WEIGHT = {"高": 0, "中": 1, "低": 2}


def marks(leads_doc: Dict[str, Any], keys: Iterable[Any]) -> Dict[str, pd.Series]:
    """按凭证号算出四列标记（与传入的凭证号序列等长、同序）。

    纯函数：不碰磁盘、不认识 Journal，测试可以直接喂一串凭证号。
    """
    book: Dict[str, Dict[str, Any]] = {}
    for item in leads_doc.get("leads") or []:
        for vid in item["凭证"]:
            slot = book.setdefault(str(vid).strip(), {"kinds": [], "priority": "低"})
            # 同一张凭证被同类线索命中两次（比如两条都是金额孤立）只记一次类型
            if item["类型"] not in slot["kinds"]:
                slot["kinds"].append(item["类型"])
            if _WEIGHT.get(item["优先级"], 3) < _WEIGHT.get(slot["priority"], 3):
                slot["priority"] = item["优先级"]

    white = {str(row.get("凭证号", "")).strip(): str(row.get("放行原因", ""))
             for row in (leads_doc.get("whitewash") or {}).get("vouchers") or []}

    series = pd.Series([str(key).strip() for key in keys])

    def kinds_of(vid: str) -> str:
        slot = book.get(vid)
        return "；".join(slot["kinds"]) if slot else ""

    return {
        "是否异常": series.map(lambda vid: "是" if vid in book else ""),
        "异常原因": series.map(kinds_of),
        "最高优先级": series.map(lambda vid: book[vid]["priority"] if vid in book else ""),
        "白名单放行": series.map(lambda vid: white.get(vid, "")),
    }


def build(journal, leads_doc: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """一张表：原序时账 + 四列标记。

    用 `journal.write_back` 而不是自己 copy：它认得哪些列是读表时算出来的中间列
    （折算的借方/贷方），交付物里不该出现。
    """
    mapping = journal.schema.legacy()
    v_col = mapping.get("voucher_no")
    if not v_col or v_col not in journal.typed.columns:
        raise ValueError(f"序时账里找不到凭证号列：{v_col}")

    keys = journal.typed[v_col]
    frame = journal.write_back(marks(leads_doc, keys))
    return {"序时账": frame}
