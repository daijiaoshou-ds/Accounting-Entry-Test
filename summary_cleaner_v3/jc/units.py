# -*- coding: utf-8 -*-
"""切割的第二层：把「摘要很像」的单元并成一族，只让模型判一次。

## 为什么需要它

`unit_key_text` 用的是**摘要原样文本**。真账里摘要塞满了单号/金额/日期/公司名，
所以"一张凭证一个单元"几乎成立 —— 真账实测（11456 张凭证）：

| 口径 | 单元数 | 相对原样 |
|---|---|---|
| 原样（旧实现） | 11227 | 100% |
| 去数字 | 5759 | 51.3% |
| 去数字 + 标点空白 | 5422 | 48.3% |
| 再按相似度 ≥0.95 聚类 | **5142** | **45.8%** |

也就是说：**切割一直在做，只是去重键太严**。去掉数字这一条就把一半的
模型调用省掉了（金额/日期/单号本来就不影响语义）。

## 规则（用户定的口径）

1. **不看数字**：先把摘要里的数字（含金额 `xxx元`、日期、单号）全部去掉，
   再做比较。"贷款 1000 万"和"贷款 2000 万"去掉数字后是同一句话。
2. **只在同一个科目组合里比**：`银行存款[借] + 应收账款[贷]` 的
   "收分公司货款" 与 `应付账款[借] + 银行存款[贷]` 的 "付分公司货款"
   文本很像，但**不同科目组合，绝不互相比较**。
3. **同族只判一次**：模型只看代表摘要，同族其余摘要**抄它的作业**，
   结论再写回每张具体凭证。
4. 硬规则命中的单元**单独成组**，不与模型单元混族（否则硬规则结论会串味）。

## 代表怎么选（确定性）

同族内按「**出现凭证数最多** → 归一化文本最短 → 字典序」选代表。
不留随机性：同一份文件跑两次必须得到同一批单元。

## 风险（必须说清楚）

同族继承会把**一个错判放大到整族**——旧实现里每个变体独立判，
错了只错一张。所以：
  - 默认阈值取 **0.95（偏保守）**，可通过 `--sim` 调；
  - 单元会带 `同族变体数` / `同族摘要`，复核时看得见这一族里还有什么；
  - 落档与复核认的是**归一化后的单元键**（跨月更稳：金额月份变了也还是同一族）。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# 默认相似度阈值：用户定的区间是 90%~95%，取保守端
SIM_DEFAULT = 0.95

# 数字（半角 + 全角）
_DIGITS = re.compile(r"[0-9０-９]+")
# 金额：「1234.56 万元」整体（先去它，免得去掉数字后剩个"万元"）
_MONEY = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)?\s*(?:万|亿)?\s*元")
# 标点 / 空白 / 符号
_JUNK = re.compile(
    r"[\s\-—_.,，。、:：;；/\\()（）\[\]【】<>《》\"'“”‘’|*#￥$%&+=~!！?？]+")

# 归一化文本的展示上限（单元里带几个变体样本就够复核用了）
VARIANT_SAMPLES = 5


def normalize_summary(text: str) -> str:
    """摘要归一化：**忽略数字**，再去掉标点与空白。

    步骤（顺序有意义）：
      ① NFKC：全角 → 半角（真账里 `７月` 与 `7月` 得先统一）
      ② 去掉金额整体（`1234.56万元`）——否则会剩下"万元"这种噪声
      ③ 去掉所有数字
      ④ 去掉标点/空白/货币符号，转小写

    例：
        "公司向银行贷款1000万元" → "公司向银行贷款"
        "支付某供应商9月货款 RMB120,000.00" → "支付某供应商月货款rmb"
    """
    s = str(text or "")
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _MONEY.sub("", s)
    s = _DIGITS.sub("", s)
    s = _JUNK.sub("", s)
    return s.strip().lower()


def _cluster(texts: Sequence[str], thr: float) -> List[List[int]]:
    """归一化文本聚类（并查集）。texts 是**同一个科目组合内**的文本。

    `thr >= 1.0` 时只并"归一化后完全相同"的（不跑相似度，省时间）。
    长度预筛：SequenceMatcher.ratio() ≤ 2·min/(m+n)，
    所以长度差太多的对**不可能**达到阈值，直接不比较。
    """
    n = len(texts)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    order = sorted(range(n), key=lambda i: (len(texts[i]), texts[i]))
    exact_only = thr >= 1.0
    for pos, i in enumerate(order):
        ti = texts[i]
        if not ti:
            continue
        li = len(ti)
        for j in order[pos + 1:]:
            tj = texts[j]
            lj = len(tj)
            if 2 * li / (li + lj) < thr:      # 长度差太多，后面只会更短
                break
            if ti == tj:
                union(i, j)
            elif not exact_only and \
                    SequenceMatcher(None, ti, tj, autojunk=False).ratio() >= thr:
                union(i, j)

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    return list(groups.values())


def canonicalize(items: List[dict],
                 threshold: float = SIM_DEFAULT,
                 block_of: Optional[Callable[[dict], Any]] = None,
                 ) -> Dict[str, Any]:
    """给每个 item 挂上 `_rep_summary` / `_rep_norm` / `_variants`，返回统计。

    Args:
        items:     `etl.extract_items` 出来的逐凭证输入（含 subjects / summary）
        threshold: 相似度阈值；>=1.0 表示"只并归一化后完全相同的"；
                   <=0 表示**完全关闭**（退回原样文本去重）
        block_of:  分组键的取法，默认按科目组合。流水线会额外把硬规则桶
                   并进键里，避免硬规则结论串到模型单元上。

    Returns: {"on", "threshold", "variants_before", "families", "merged_vouchers",
              "biggest": [(族大小, 科目组合, 代表摘要), ...], "elapsed_s"}
    """
    import time
    t0 = time.perf_counter()
    on = threshold is not None and float(threshold) > 0
    thr = float(threshold) if on else 0.0

    if block_of is None:
        def block_of(it):
            return tuple(sorted(str(s) for s in (it.get("subjects") or [])))

    for it in items:                     # 默认：代表就是自己
        raw = str(it.get("summary") or "")
        it["_rep_summary"] = raw
        # 关掉归族时**不归一化**：单元键退回原样文本，与旧行为逐字一致
        it["_rep_norm"] = normalize_summary(raw) if on else raw
        it["_variants"] = 1

    stat = {"on": on, "threshold": thr, "variants_before": 0, "units_after": 0,
            "families": 0, "merged_vouchers": 0, "biggest": [],
            "elapsed_s": 0.0}
    if not on or not items:
        stat["variants_before"] = len({_raw_key(it) for it in items})
        stat["units_after"] = stat["variants_before"]
        stat["elapsed_s"] = round(time.perf_counter() - t0, 2)
        return stat

    blocks: Dict[Any, List[int]] = defaultdict(list)
    for i, it in enumerate(items):
        blocks[block_of(it)].append(i)

    for _block, idxs in blocks.items():
        if len(idxs) < 2:
            continue
        texts = [normalize_summary(items[i].get("summary")) for i in idxs]
        for group in _cluster(texts, thr):
            if len(group) < 2:
                continue
            raw = [str(items[idxs[g]].get("summary") or "") for g in group]
            norms = [texts[g] for g in group]
            # 代表：出现最多的归一化文本 → 最短 → 字典序
            cnt = Counter(norms)
            rep_norm = sorted(cnt, key=lambda t: (-cnt[t], len(t), t))[0]
            # 同一个归一化文本可能对应多个原样写法（金额不同），
            # 这里按"最短 → 字典序"定死，**不依赖输入顺序**（确定性）。
            rep_raw = min((r for r, n in zip(raw, norms) if n == rep_norm),
                          key=lambda r: (len(r), r))
            n_var = len(set(raw))
            for g in group:
                it = items[idxs[g]]
                it["_rep_summary"] = rep_raw
                it["_rep_norm"] = rep_norm
                it["_variants"] = n_var
            stat["families"] += 1
            stat["merged_vouchers"] += len(group) - 1
            stat["biggest"].append((len(group), rep_norm[:30], rep_raw[:30]))

    stat["variants_before"] = len({_raw_key(it) for it in items})
    stat["units_after"] = len({(block_of(it), it["_rep_norm"]) for it in items})
    stat["biggest"].sort(reverse=True)
    stat["biggest"] = stat["biggest"][:5]
    stat["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return stat


def _raw_key(it: dict) -> tuple:
    return (tuple(sorted(str(s) for s in (it.get("subjects") or []))),
            str(it.get("summary") or ""))
