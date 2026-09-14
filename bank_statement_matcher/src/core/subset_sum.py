"""子集和求解器：DP 优化版（替代暴力枚举）"""
from typing import List, Optional, Tuple
from bank_statement_matcher.src.utils.performance_logger import perf_logger


def subset_sum_dp(
    amounts: List[float],
    target: float,
    tol: float = 0.01,
    max_size: int = 10
) -> Optional[List[int]]:
    """
    动态规划求解子集和问题
    
    核心思路：
    1. 金额转为整数（分），避免浮点数精度问题
    2. 逐层 DP：{sum_value: indices_list}
    3. 每层保留最优解（相同和保留更短的组合）
    4. 限制组合大小，防止状态爆炸
    
    参数:
        amounts: Bank 金额列表
        target: 目标金额（GL 金额）
        tol: 容差
        max_size: 最大组合大小
    
    返回:
        匹配的索引列表，或 None
    """
    if not amounts:
        return None

    # 转为整数（分）
    scale = 100
    int_amounts = [round(abs(a) * scale) for a in amounts]
    int_target = round(abs(target) * scale)
    int_tol = round(tol * scale)

    # 快速检查：是否有单个元素直接命中
    for i, amt in enumerate(int_amounts):
        if abs(amt - int_target) <= int_tol:
            return [i]

    # DP: {sum_value: indices_list}
    # 限制状态数，防止内存爆炸
    MAX_STATES = 50000

    dp = {0: []}  # sum -> indices

    for i, amt in enumerate(int_amounts):
        new_dp = dict(dp)
        items = list(dp.items())

        for s, indices in items:
            if len(indices) >= max_size:
                continue

            new_sum = s + amt
            new_indices = indices + [i]

            # 检查是否命中目标
            if abs(new_sum - int_target) <= int_tol:
                return new_indices

            # 只保留不超过目标太多（target + 2*tol 范围内）的状态
            if new_sum > int_target + int_tol * 2:
                continue

            # 相同和，保留更短的组合
            if new_sum not in new_dp or len(new_dp[new_sum]) > len(new_indices):
                new_dp[new_sum] = new_indices

        # 状态数控制：如果太多，按和目标接近程度裁剪
        if len(new_dp) > MAX_STATES:
            # 保留最接近目标的 MAX_STATES//2 个状态
            sorted_items = sorted(new_dp.items(), key=lambda x: abs(x[0] - int_target))
            new_dp = dict(sorted_items[:MAX_STATES // 2])

        dp = new_dp

    return None


def subset_sum_dp_with_fallback(
    amounts: List[float],
    target: float,
    tol: float = 0.01,
    max_size: int = 10
) -> Tuple[Optional[List[int]], str]:
    """
    带 fallback 的子集和求解
    先尝试 DP，如果失败且有少量元素，用回溯枚举兜底

    返回: (indices_list, method)
    """
    n = len(amounts)
    if n > 30:
        perf_logger.info(f"      DP子集和: {n}个元素, 目标={target:.2f}, max_size={max_size} [大数据集!]")

    result = subset_sum_dp(amounts, target, tol, max_size)
    if result is not None:
        if n > 30:
            perf_logger.info(f"      DP命中: {len(result)}个元素")
        return result, "dp_subset"

    # Fallback: 回溯枚举（元素少的时候才用）
    if len(amounts) <= 15:
        if n > 8:
            perf_logger.info(f"      DP未命中, 回溯枚举 {n}个元素...")
        result = _backtrack_subset(amounts, target, tol, max_size)
        if result is not None:
            return result, "backtrack_subset"

    return None, ""


def _backtrack_subset(
    amounts: List[float],
    target: float,
    tol: float,
    max_size: int
) -> Optional[List[int]]:
    """回溯枚举（小数据兜底）"""
    n = len(amounts)
    abs_amounts = [abs(a) for a in amounts]
    abs_target = abs(target)

    # 按金额从大到小排序（更快剪枝）
    indexed = sorted(enumerate(abs_amounts), key=lambda x: -x[1])
    sorted_idx = [x[0] for x in indexed]
    sorted_amts = [x[1] for x in indexed]

    result = None

    def dfs(pos, current_sum, chosen):
        nonlocal result
        if result is not None:
            return
        if abs(current_sum - abs_target) <= tol:
            result = chosen[:]
            return
        if len(chosen) >= max_size:
            return
        if pos >= n:
            return
        # 剪枝：即使后面全选也达不到目标
        remaining_max = sum(sorted_amts[pos:])
        if current_sum + remaining_max < abs_target - tol:
            return
        # 剪枝：当前和已经超过目标
        if current_sum > abs_target + tol:
            return

        # 选当前
        chosen.append(sorted_idx[pos])
        dfs(pos + 1, current_sum + sorted_amts[pos], chosen)
        chosen.pop()

        # 不选当前
        dfs(pos + 1, current_sum, chosen)

    dfs(0, 0.0, [])
    return result
