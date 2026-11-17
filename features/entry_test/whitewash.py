# -*- coding: utf-8 -*-
"""风险白名单：有些凭证不该出现在任何一层的线索里。

不是"降噪"，是**这类凭证的金额和科目组合本来就不携带判断信息**：

- **汇兑损益**：金额由期末汇率与外币余额算出来，不是人选的数。它天然整数化、
  天然随汇率跳，本福特与金额孤立对它说的每一句都是废话。
- **结转期末损益**：把损益科目清零，金额是当期汇总数，必然巨大、必然占「金额
  最大」的位置。它在金额孤立里永远跳，因为结转本来就该跳。
- **资金内部往来**：集团内的调拨，科目与金额由资金安排决定，账面两边都是自己人。

放过必须是**显式**的：报告脚注写"按白名单放过 N 张（汇兑 12 / 结转 30 / 内部
往来 5）"，明细 Excel 里有一张「白名单」逐张列着。静默过滤才是问题——那种"看
起来没有异常"的报告，没人知道是被过滤了还是真的没有。

判定按**整张凭证**：一张凭证里过半的行命中同一原因才放过。按单行放过的话，
一张正常凭证里只要有一行提到"内部往来"，整张就从三层里消失。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# 业务活动（上游清洗判出来的桶）→ 白名单原因。有上游时优先用它：
# 分类是模型看过整张凭证判的，比"摘要里有没有某个词"准得多——这份账里
# 「资金内部往来」83 张、大部分摘要并不含"内部往来"四个字。
ACTIVITIES = {
    "汇兑损益": "汇兑损益",
    "期末结转": "结转期末损益",
    "资金内部往来": "资金内部往来",
}

# 原因 → 摘要/科目里认得出的写法。没有上游时的退路；顺序即优先级
RULES: Tuple[Tuple[str, re.Pattern], ...] = (
    ("汇兑损益", re.compile(r"汇兑|调汇|汇率变动|外币折算")),
    # 「结转期间损益」是同一件事的另一种写法：账套里用哪个词不由我们定，
    # 所以这段正则要认得期间/本期/本年/当期四种前缀，以及"期末结转""结转本年利润"。
    ("结转期末损益",
     re.compile(r"结转(本期|本年|当期|期间|期末)?(损益|利润)|期末结转|结转本年利润")),
    ("资金内部往来", re.compile(r"内部往来|内部划转|资金调拨|资金归集|内部资金")),
)

# 一张凭证里至少这么大比例的行命中，才整张放过
MAJORITY = 0.5


def _reason_of(summary: str, subject: str) -> str:
    text = f"{summary} {subject}"
    for name, pattern in RULES:
        if pattern.search(text):
            return name
    return ""


def match(frame: pd.DataFrame) -> Dict[str, str]:
    """凭证号 → 放过原因。只对命中的凭证给值。"""
    if frame is None or frame.empty:
        return {}
    summary = frame["summary"].astype(str)
    subject = frame["subject"].astype(str)
    reasons = [_reason_of(text, subj) for text, subj in zip(summary, subject)]

    work = pd.DataFrame({"vid": frame["vid"].astype(str), "原因": reasons})
    out: Dict[str, str] = {}
    for vid, group in work.groupby("vid", sort=False):
        counted = group[group["原因"] != ""]["原因"].value_counts()
        if counted.empty:
            continue
        top = str(counted.index[0])
        if counted.iloc[0] / len(group) >= MAJORITY:
            out[vid] = top
    return out


def apply(frame: pd.DataFrame, mapping: Dict[str, str],
          buckets: Optional[Dict[str, str]] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """把白名单凭证整张剔掉，返回（剩下的行, 说明）。

    `buckets` 是 {凭证号: 业务活动}（上游清洗的结论）。给了就按它判——一个有 83 张
    凭证的业务活动，靠关键词是抓不全的。没给就退回摘要/科目关键词。

    说明里带逐张清单：报告只要计数，明细 Excel 要能一张一张看。
    """
    hit = match(frame)
    for vid, name in (buckets or {}).items():
        reason = ACTIVITIES.get(str(name).strip())
        if reason:
            hit.setdefault(str(vid), reason)
    if not hit:
        return frame, {"count": 0, "by_reason": {}, "vouchers": []}

    keep = ~frame["vid"].astype(str).isin(set(hit))
    rest = frame[keep]

    by_reason: Dict[str, int] = {}
    for reason in hit.values():
        by_reason[reason] = by_reason.get(reason, 0) + 1

    rows: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for _, row in frame[frame["vid"].astype(str).isin(set(hit))].iterrows():
        vid = str(row["vid"])
        if vid in seen:
            continue
        seen[vid] = hit[vid]
        rows.append({
            "凭证号": vid,
            "放行原因": hit[vid],
            "摘要": str(row.get("summary") or ""),
            "科目": str(row.get("subject") or ""),
        })

    return rest, {
        "count": len(hit),
        "rows_dropped": int(len(frame) - len(rest)),
        "by_reason": by_reason,
        # 逐张清单按原因、凭证号排，Excel 里一眼看到放过的是什么
        "vouchers": sorted(rows, key=lambda item: (item["放行原因"], item["凭证号"])),
    }
