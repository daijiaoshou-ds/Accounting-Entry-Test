# -*- coding: utf-8 -*-
"""推理侧去重：业务等价的凭证只判一次，其余"抄作业"。

序时账里同一业务会按月重复，摘要只差期间和金额：

    支付1月当期税费1000元   应交税费[借] + 银行存款[贷]
    支付2月当期税费2000元   应交税费[借] + 银行存款[贷]

它们的科目组合一模一样，摘要去掉数字后也一模一样，业务性质显然相同。
逐张送模型等于把同一道题做 N 遍。

三层，成本递增、收益递减：

| 层 | 判据 | 真实账压缩比 | 风险 |
|---|---|---:|---|
| L1 精确 | 摘要 + 科目组合完全相同 | 1.03:1 | 零（输入相同，输出必相同） |
| L2 数字归一 | 去数字/单号/标点后相同 | **2.01:1** | 零（差异只是数字，业务性质不变） |
| L3 相似度 | 同组合内归一化后相似度 ≥ 0.95 | 累计 **2.19:1** | 低（文本有细微差异，靠阈值兜） |

**同科目组合是硬前提**，不是优化而是正确性约束：

    「支付当期税费」应交税费[借]+银行存款[贷]
    「支付当期水费」应付账款[借]+银行存款[贷]

两句话去掉数字后相似度约 0.8，但即便更高也不能合并——业务性质完全不同（税费 vs 费用）。
科目组合不同就绝不比较，这一条挡住 L3 的主要误合并风险。

送模型的代表取组内**第一条原始摘要**（保留数字），模型看到的是真实文本，
不是归一化串；归一化只用于判重。
"""

from __future__ import annotations

import difflib
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .nn.training_data import _normalize_summary

# L3 的相似度阈值。财务摘要去掉数字后，"同一业务的不同措辞"通常 ≥0.95，
# 而"不同业务但句式相同"（税费 vs 水费）在 0.75–0.85。取 0.95 留足安全边际。
# 设成 >=1.0 即关闭 L3（只做精确 + 数字归一化），用于对拍 L3 的代价。
SIMILARITY_THRESHOLD = float(os.getenv("SUMMARY_CLEANER_DEDUP_SIM", "0.95"))

# L3 只用前 N 个字分桶做候选筛选（避免组内 O(n²) 全比）。
# 中文摘要的前几个字通常就是业务动词，分桶后桶内再做精确比较。
_PREFIX_LEN = 4
# 长度差超过这个比例的直接跳过（相似度不可能到 0.95）
_LEN_TOLERANCE = 0.1


def _union_find_cluster(
    norms: List[str],
    threshold: float,
) -> List[List[int]]:
    """同组合内把相似度 ≥ threshold 的归一化摘要聚成一簇，返回簇下标列表。

    先按前缀分桶再桶内比较：整组 O(n²) 在 2,475 条的大组上会到 300 万次比较，
    分桶后每桶通常几十条。
    """
    n = len(norms)
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

    buckets: Dict[str, List[int]] = defaultdict(list)
    for i, s in enumerate(norms):
        buckets[s[:_PREFIX_LEN]].append(i)

    matcher = difflib.SequenceMatcher(None, "", "")
    for idxs in buckets.values():
        if len(idxs) <= 1:
            continue
        for a_pos, a in enumerate(idxs):
            sa = norms[a]
            for b in idxs[a_pos + 1:]:
                sb = norms[b]
                # 长度差过大不可能达到 0.95，先挡掉
                if abs(len(sa) - len(sb)) > max(4, len(sa) * _LEN_TOLERANCE):
                    continue
                matcher.set_seqs(sa, sb)
                # 三级过滤：长度上界 → 字符计数上界（都是 O(n)）→ 完整 LCS。
                # 直接算 ratio() 是 O(n²)，在 2,475 条的大组上会到几秒。
                if matcher.real_quick_ratio() < threshold:
                    continue
                if matcher.quick_ratio() < threshold:
                    continue
                if matcher.ratio() >= threshold:
                    union(a, b)

    clusters: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    return list(clusters.values())


def dedup_vouchers(
    vids: Sequence[str],
    subjects_map: Dict[str, List[str]],
    summary_map: Dict[str, str],
    threshold: float = SIMILARITY_THRESHOLD,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], Dict[str, int]]:
    """把业务等价的凭证压成代表，返回 (代表列表, 凭证→代表下标, 统计)。

    Args:
        vids: 要送模型的凭证号（已排除硬约束命中的）
        subjects_map: {凭证号: ['应付账款[借]', ...]}（已归一化）
        summary_map: {凭证号: 摘要拼接串}
        threshold: L3 相似度阈值

    Returns:
        reps: [{"summary", "subjects", "vids": [...]}]，每条要送模型一次
        vid_to_rep: {凭证号: reps 下标}
        stats: {"vouchers", "unique", "l1_l2_merged", "l3_merged", "ratio"}
    """
    # 按科目组合分组：不同组合绝不比较（正确性约束，不是优化）
    groups: Dict[Tuple[str, ...], List[str]] = defaultdict(list)
    for v in vids:
        groups[tuple(sorted(subjects_map.get(v) or []))].append(v)

    reps: List[Dict[str, Any]] = []
    vid_to_rep: Dict[str, int] = {}
    l1_l2_merged = 0   # 精确 + 数字归一化合并掉的凭证数
    l3_merged = 0      # 纯相似度合并掉的

    for _key, group in groups.items():
        # 归一化串 → 原始凭证号列表（L1 与 L2 在这里一次完成：
        # 摘要与组合都相同时归一化串必然相同，所以不必单独做 L1）
        by_norm: Dict[str, List[str]] = {}
        for v in group:
            norm = _normalize_summary(summary_map.get(v, ""))
            by_norm.setdefault(norm, []).append(v)

        norms = list(by_norm.keys())
        # L2 已合并掉的：同归一化串下除第一条以外的
        l1_l2_merged += sum(len(vs) - 1 for vs in by_norm.values())

        # L3：把归一化串再聚类
        for cluster in _union_find_cluster(norms, threshold):
            cluster_vids: List[str] = []
            for idx in cluster:
                cluster_vids.extend(by_norm[norms[idx]])
            # 代表用组内第一条的原始摘要（保留数字，模型看到真实文本）
            head = cluster_vids[0]
            rep_idx = len(reps)
            reps.append({
                "summary": summary_map.get(head, ""),
                "subjects": subjects_map.get(head) or [],
                "vids": cluster_vids,
            })
            for v in cluster_vids:
                vid_to_rep[v] = rep_idx
            if len(cluster) > 1:
                # 簇内多余归一化串的数量
                l3_merged += sum(len(by_norm[norms[i]]) - 1 for i in cluster[1:])

    total = len(vids)
    unique = len(reps)
    return reps, vid_to_rep, {
        "vouchers": total,
        "unique": unique,
        "l1_l2_merged": l1_l2_merged,
        "l3_merged": l3_merged,
        "ratio": round(total / unique, 2) if unique else 1.0,
    }
