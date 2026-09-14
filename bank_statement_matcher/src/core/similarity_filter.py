"""匹配结果相似度筛选：用公司名称余弦相似度选出唯一解"""
from typing import List, Tuple, Optional
import numpy as np
from bank_statement_matcher.src.utils.name_vectorizer import NameVectorizer
from bank_statement_matcher.src.utils.performance_logger import perf_logger

# 相似度阈值：低于此值不认定匹配
SIMILARITY_THRESHOLD = 0.6


def select_best_match(
    gl_entry,
    bank_candidates: List[List],
    vectorizer: Optional[NameVectorizer] = None
) -> Optional[Tuple[List, float]]:
    """
    对金额匹配的多个候选解，用公司名称余弦相似度筛选最优解。

    参数:
        gl_entry: GL 记录（需有 customer_name / counterparties）
        bank_candidates: 金额匹配的 Bank 候选解列表，每个元素是 [bank_entries...]
        vectorizer: 预训练的 NameVectorizer（None 则直接返回第一个候选）

    返回:
        (最优候选的 bank_entries, 最高相似度) 或 None
    """
    if not bank_candidates:
        return None

    if vectorizer is None:
        return (bank_candidates[0], 1.0)

    # 提取 GL 的客商列表
    gl_parties = _extract_parties(gl_entry)

    best_candidate = None
    best_score = -1.0

    # 候选数较多时记录
    if len(bank_candidates) > 20:
        perf_logger.info(f"      select_best_match: {len(gl_parties)} GL客商 vs {len(bank_candidates)} 候选组")

    for candidate in bank_candidates:
        # 提取该候选解的所有 Bank 交易方
        bank_parties = []
        for b in candidate:
            party = getattr(b, 'counter_party', '') or getattr(b, 'customer_name', '')
            if party:
                bank_parties.append(party)

        if not gl_parties or not bank_parties:
            # 没有客商信息，直接返回第一个（金额已匹配）
            return (bank_candidates[0], 1.0)

        # 计算多级余弦相似度
        score = _multi_level_similarity(gl_parties, bank_parties, vectorizer)

        if score > best_score:
            best_score = score
            best_candidate = candidate

    # 只有一个候选解时：金额已唯一匹配，跳过相似度阈值（名称差异不影响唯一解认定）
    if len(bank_candidates) == 1:
        return (best_candidate, best_score)

    if best_score < SIMILARITY_THRESHOLD:
        return None

    return (best_candidate, best_score)


def _extract_parties(gl_entry) -> List[str]:
    """从 GL 记录中提取客商列表"""
    # 优先使用 counterparties（变形后的列表）
    parties = getattr(gl_entry, 'counterparties', None)
    if parties:
        return [p for p in parties if p]

    # 回退到 customer_name（单个客商）
    name = getattr(gl_entry, 'customer_name', '')
    if name:
        return [name]

    return []


def _multi_level_similarity(
    gl_parties: List[str],
    bank_parties: List[str],
    vectorizer: NameVectorizer
) -> float:
    """
    多级余弦相似度计算

    对每个 GL 客商，与所有 Bank 交易方计算相似度，取 max。
    然后求和。

    实体数相同 → 倒挤分配（但不在这里处理，只返回总分）
    实体数不同 → 不能倒挤的留空（体现在总分较低）
    """
    total_score = 0.0

    for gl_party in gl_parties:
        max_sim = 0.0
        for bank_party in bank_parties:
            sim = vectorizer.cosine_similarity(gl_party, bank_party)
            if sim > max_sim:
                max_sim = sim
        total_score += max_sim

    # 归一化：除以 GL 客商数量，使得分数在 0~1 之间
    # 但如果 Bank 实体更多，也不惩罚（按 Modify.md 的描述）
    if len(gl_parties) > 0:
        avg_score = total_score / len(gl_parties)
    else:
        avg_score = 0.0

    return avg_score


def is_zero_similarity(gl_entry, bank_entry, vectorizer) -> bool:
    """单候选1v1时检查：双方名称的余弦相似度是否恰好为0（完全无关的名字）"""
    gl_parties = _extract_parties(gl_entry)
    bank_party = getattr(bank_entry, 'counter_party', '') or getattr(bank_entry, 'customer_name', '')
    if not gl_parties or not bank_party:
        return False
    for gp in gl_parties:
        if vectorizer.cosine_similarity(gp, bank_party) > 0.0:
            return False
    return True


def assign_counterparties(
    gl_parties: List[str],
    bank_parties: List[str],
    vectorizer: NameVectorizer
) -> List[Tuple[str, Optional[str], float]]:
    """
    分配：将 GL 客商一一对应到 Bank 交易方（倒挤/匹配）

    返回: [(gl_party, assigned_bank_party, similarity), ...]
    """
    if not gl_parties or not bank_parties:
        return [(p, None, 0.0) for p in gl_parties]

    # 计算相似度矩阵
    sim_matrix = np.zeros((len(gl_parties), len(bank_parties)))
    for i, gp in enumerate(gl_parties):
        for j, bp in enumerate(bank_parties):
            sim_matrix[i, j] = vectorizer.cosine_similarity(gp, bp)

    assigned_bank = set()
    result = []

    for i, gp in enumerate(gl_parties):
        best_j = None
        best_sim = -1.0
        for j in range(len(bank_parties)):
            if j in assigned_bank:
                continue
            if sim_matrix[i, j] > best_sim:
                best_sim = sim_matrix[i, j]
                best_j = j

        if best_j is not None and best_sim >= SIMILARITY_THRESHOLD:
            result.append((gp, bank_parties[best_j], best_sim))
            assigned_bank.add(best_j)
        else:
            result.append((gp, None, best_sim if best_sim > 0 else 0.0))

    return result
