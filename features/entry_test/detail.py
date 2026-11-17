# -*- coding: utf-8 -*-
"""报告的附件：Excel 明细。

为什么明细不放进 HTML：一份 7,883 张凭证的账，线索命中 164 张——这些凭证号在
报告里排成几十行，没人会对着屏幕读；而它们恰恰是**下一步要动手的那一份**：拿去
抽凭、拿去核对、拿去分派给项目组成员。所以它该是一张能筛选、能标记、能粘到别的
底稿里的表，而不是报告里的一段。

五张表，各对应报告的一节：

    线索清单   全量线索（报告里按层分开了，这里一处看全）
    涉及凭证   一张凭证一行，命中的线索并在一起 —— **这张是交付用的**
    完整性     逐条明细（报告只留四行汇总）
    业务活动   画像全表（含 HHI）
    本福特     逐位分布（报告里是图，数在这里）
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from . import leads as lead_mod


def _frames(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _leads_frame(items: List[Dict[str, Any]]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame(columns=["层", "类型", "主体", "笔数", "金额", "证据",
                                     "涉及凭证", "建议程序", "优先级"])
    return pd.DataFrame([{
        "层": item["层"],
        "类型": item["类型"],
        "主体": item["主体"],
        "笔数": item["笔数"],
        "金额": item["金额"],
        "证据": item["证据"],
        # 报告里只列前两张，这里是全量——要拿去抽凭的就是这一列
        "涉及凭证": "、".join(item["凭证"]),
        "建议程序": item["建议程序"],
        "优先级": item["优先级"],
    } for item in items])


def _voucher_frame(items: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = lead_mod.voucher_rows(items)
    return pd.DataFrame(rows, columns=lead_mod.VOUCHER_COLUMNS)


def _benford_frame(stats: Dict[str, Any]) -> pd.DataFrame:
    """逐位分布：把图拆回数字。占比与期望并排放，偏差能直接算。"""
    from . import benford as benford_mod

    rows: List[Dict[str, Any]] = []
    for side, info in (stats.get("sides") or {}).items():
        for key, label in (("first", "首位"), ("second", "次位")):
            block = info.get(key) or {}
            obs = {int(k): int(v) for k, v in (block.get("obs") or {}).items()}
            total = sum(obs.values()) or 1
            expect = (benford_mod.FIRST_EXPECT if key == "first"
                      else benford_mod.SECOND_EXPECT)
            for digit in sorted(obs):
                share = obs[digit] / total
                rows.append({
                    "方向": side, "位置": label, "数字": digit,
                    "观察笔数": obs[digit],
                    "实际占比": round(share, 4),
                    "本福特期望": round(expect.get(digit, 0.0), 4),
                    "偏差": round(share - expect.get(digit, 0.0), 4),
                })
    return pd.DataFrame(rows)


def build(integrity: Dict[str, Any], profile: Dict[str, Any],
          leads_doc: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """五张表。空表不放（一张空 sheet 比没有更让人困惑）。"""
    items = leads_doc.get("leads") or []
    frames: Dict[str, pd.DataFrame] = {
        "线索清单": _leads_frame(items),
        "涉及凭证": _voucher_frame(items),
        "完整性": _frames(integrity.get("rows") or []),
        "业务活动": _frames(profile.get("activities") or []),
        "本福特": _benford_frame((leads_doc.get("stats") or {}).get("本福特") or {}),
    }
    return {name: frame for name, frame in frames.items() if len(frame)}
