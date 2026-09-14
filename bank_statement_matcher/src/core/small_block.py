"""小 Block 处理：漏斗策略 + 规划求解 + 余弦相似度筛选"""
import itertools
import time
from typing import List, Tuple, Optional, Dict, Set
from collections import defaultdict

from bank_statement_matcher.src.core.similarity_filter import select_best_match
from bank_statement_matcher.src.utils.performance_logger import perf_logger


def solve_small_block(
    gl_entries: List,
    bank_entries: List,
    tol: float = 0.01,
    date_window_days: int = 31,
    vectorizer=None
) -> Tuple[List[Tuple], List, List]:
    """
    小 Block 匹配策略（漏斗策略）
    新增：金额匹配后用余弦相似度筛选唯一解

    返回: (matches, unmatched_gl, unmatched_bank)
    matches: [(gl_entry, [bank_entries...], match_type), ...]
    """
    t_start = time.time()
    matches = []
    unmatched_gl = list(gl_entries)
    unmatched_bank = list(bank_entries)

    # ===== Phase 1: 1v1 精确匹配 =====
    # 同交易方出现多笔的GL跳过，留给聚合阶段
    party_count = defaultdict(int)
    for g in unmatched_gl:
        party = _get_primary_party(g)
        if party:
            party_count[party] += 1
    grouped_parties = {p for p, c in party_count.items() if c > 1}

    t0 = time.time()
    _match_one_to_one(unmatched_gl, unmatched_bank, matches, tol, date_window_days, vectorizer, grouped_parties)
    dt_1v1 = time.time() - t0

    # ===== Phase 2: Bank→GL 四级聚合漏斗 =====
    dt_agg = [0.0, 0.0, 0.0, 0.0]
    for agg_level in range(1, 5):
        if not unmatched_bank or not unmatched_gl:
            break
        t0 = time.time()
        _match_by_aggregation(unmatched_gl, unmatched_bank, matches, agg_level, tol, date_window_days, vectorizer, grouped_parties)
        dt_agg[agg_level - 1] = time.time() - t0

    # ===== Phase 3: GL→Bank 四级聚合漏斗 =====
    dt_gl_agg = 0.0
    if unmatched_gl and unmatched_bank:
        t0 = time.time()
        _gl_to_bank_aggregation_small(unmatched_gl, unmatched_bank, matches, tol, date_window_days, vectorizer)
        dt_gl_agg = time.time() - t0

    # ===== Phase 4: 一对一回溯匹配（残差池纯金额1v1） =====
    dt_backtrack = 0.0
    if unmatched_gl and unmatched_bank:
        t0 = time.time()
        _retrospective_1v1_small(unmatched_gl, unmatched_bank, matches, tol, date_window_days, vectorizer, grouped_parties)
        dt_backtrack = time.time() - t0

    # ===== Phase 5: DP 双向求解（最后兜底） =====
    dt_dp = 0.0
    if unmatched_gl and unmatched_bank:
        t0 = time.time()
        _match_subset_sum_dp(unmatched_gl, unmatched_bank, matches, tol, date_window_days, vectorizer, grouped_parties=grouped_parties)
        dt_dp = time.time() - t0

    dt_total = time.time() - t_start
    if dt_total > 2.0:
        perf_logger.info(
            f"    小Block耗时 {dt_total:.2f}s: 1v1={dt_1v1:.2f}s, "
            + " ".join(f"agg{i+1}={dt_agg[i]:.2f}s" for i in range(4) if dt_agg[i] > 0.001)
            + f", GL→Bank聚合={dt_gl_agg:.2f}s"
            + f", 一对一回溯={dt_backtrack:.2f}s"
            + f", DP={dt_dp:.2f}s, 匹配={len(matches)}, 未匹配GL={len(unmatched_gl)}, Bank={len(unmatched_bank)}"
        )

    return matches, unmatched_gl, unmatched_bank


def _has_party_info(gl_entry) -> bool:
    """判断 GL 是否有客商信息（用于决定是否走相似度筛选）"""
    parties = getattr(gl_entry, 'counterparties', None)
    if parties:
        return True
    name = getattr(gl_entry, 'customer_name', '')
    return bool(name)


def _get_primary_party(gl_entry) -> str:
    """获取GL的主要交易方名称（用于判断是否同交易方多笔）"""
    parties = getattr(gl_entry, 'counterparties', None)
    if parties:
        return parties[0] if parties else ''
    return getattr(gl_entry, 'customer_name', '') or ''


def _match_one_to_one(
    gl_list: List,
    bank_list: List,
    matches: List,
    tol: float,
    date_window_days: int = 31,
    vectorizer=None,
    grouped_parties: set = None
):
    """一对一精确匹配：同交易方多笔的GL跳过，其余金额相等+日期接近"""
    if grouped_parties is None:
        grouped_parties = set()
    to_remove_gl = []
    to_remove_bank = set()

    for gi, g in enumerate(gl_list):
        # 同交易方出现多笔的GL，跳过1v1
        if _get_primary_party(g) in grouped_parties:
            continue

        # 收集所有金额+日期匹配的候选
        candidates = []
        for bi, b in enumerate(bank_list):
            if bi in to_remove_bank:
                continue
            if _amount_match(g.amount, b.amount, tol) and _date_close(g, b, date_window_days):
                candidates.append((bi, b))

        if not candidates:
            continue

        if vectorizer and _has_party_info(g) and len(candidates) > 1:
            # 多候选：用相似度筛选最优
            selected = None
            best_result = select_best_match(g, [[b] for _, b in candidates], vectorizer)
            if best_result:
                best_banks, sim = best_result
                if best_banks:
                    for bi, b in candidates:
                        if b is best_banks[0]:
                            selected = (bi, b)
                            break
            if selected is None:
                selected = candidates[0]
            bi, b = selected
        else:
            # 单候选或无客商信息：匹配第一个
            bi, b = candidates[0]
        matches.append((g, [b], "1v1"))
        to_remove_gl.append(gi)
        to_remove_bank.add(bi)

    # 移除已匹配
    for gi in sorted(to_remove_gl, reverse=True):
        gl_list.pop(gi)
    for bi in sorted(to_remove_bank, reverse=True):
        bank_list.pop(bi)


def _match_by_aggregation(
    gl_list: List,
    bank_list: List,
    matches: List,
    agg_level: int,
    tol: float,
    date_window_days: int = 31,
    vectorizer=None,
    grouped_parties: set = None
):
    """
    按聚合级别匹配 Bank 条目
    多候选聚合组时用相似度筛选最优
    """
    # 先按聚合键对 Bank 分组并求和
    groups = defaultdict(list)
    for b in bank_list:
        key = _agg_key(b, agg_level)
        groups[key].append(b)

    # 计算每组的累计金额
    group_sums = {key: sum(b.amount for b in grp) for key, grp in groups.items()}

    to_remove_gl = []
    used_groups = set()

    for gi, g in enumerate(gl_list):
        # 收集所有金额+日期匹配的聚合组
        candidates = []
        is_grouped = grouped_parties and _get_primary_party(g) in grouped_parties
        for key, grp in groups.items():
            if key in used_groups:
                continue
            # 同交易方多笔的GL，不允许单Bank"聚合"（本质是绕过Phase1的1v1）
            if is_grouped and len(grp) < 2:
                continue
            if _amount_match(g.amount, group_sums[key], tol) and _date_close_group(g, grp, date_window_days):
                candidates.append((key, grp))

        if not candidates:
            continue

        if vectorizer and _has_party_info(g):
            best_result = select_best_match(g, [grp for _, grp in candidates], vectorizer)
            if best_result:
                best_banks, sim = best_result
                # 找到 best_banks 对应的聚合键
                for key, grp in candidates:
                    if all(b in grp for b in best_banks):
                        matches.append((g, grp, f"agg{agg_level}"))
                        to_remove_gl.append(gi)
                        used_groups.add(key)
                        break
        else:
            key, grp = candidates[0]
            matches.append((g, grp, f"agg{agg_level}"))
            to_remove_gl.append(gi)
            used_groups.add(key)

    # 移除已匹配的 GL
    for gi in sorted(to_remove_gl, reverse=True):
        gl_list.pop(gi)

    # 移除已匹配的 Bank（整个组移除）
    to_remove_bank = set()
    for key in used_groups:
        for b in groups[key]:
            for i, existing in enumerate(bank_list):
                if existing is b:
                    to_remove_bank.add(i)
                    break
    for bi in sorted(to_remove_bank, reverse=True):
        bank_list.pop(bi)


def _retrospective_1v1_small(
    unmatched_gl: List,
    unmatched_bank: List,
    matches: List,
    tol: float,
    date_window_days: int,
    vectorizer=None,
    grouped_parties: set = None
):
    """
    Phase 4: 一对一回溯匹配（小Block版本）
    对残差池做纯金额1v1，多候选时用相似度选最优，始终匹配
    同交易方多笔的GL跳过（留给聚合阶段）
    """
    to_remove_gl = []
    to_remove_bank = set()

    for gi, g in enumerate(unmatched_gl):
        # 同交易方多笔的GL跳过
        if grouped_parties and _get_primary_party(g) in grouped_parties:
            continue
        candidates = []
        g_amount = abs(g.amount) if hasattr(g, 'amount') else 0
        g_date = getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None)

        for bi, b in enumerate(unmatched_bank):
            if bi in to_remove_bank:
                continue
            b_amount = abs(b.amount) if hasattr(b, 'amount') else 0
            if not _amount_match(g_amount, b_amount, tol):
                continue
            b_date = getattr(b, 'tx_date', None) or getattr(b, 'entry_date', None)
            if g_date and b_date:
                if abs((g_date - b_date).days) > date_window_days:
                    continue
            candidates.append((bi, b))

        if not candidates:
            continue

        selected = None
        if vectorizer and _has_party_info(g) and len(candidates) > 1:
            best_result = select_best_match(g, [[b] for _, b in candidates], vectorizer)
            if best_result:
                best_banks = best_result[0]
                for bi, b in candidates:
                    if b is best_banks[0]:
                        selected = (bi, b)
                        break
        if selected is None:
            selected = candidates[0]

        bi, b = selected
        matches.append((g, [b], "1v1_backtrack"))
        to_remove_gl.append(gi)
        to_remove_bank.add(bi)

    for gi in sorted(to_remove_gl, reverse=True):
        unmatched_gl.pop(gi)
    for bi in sorted(to_remove_bank, reverse=True):
        unmatched_bank.pop(bi)


def _match_subset_sum_dp(
    gl_list: List,
    bank_list: List,
    matches: List,
    tol: float,
    date_window_days: int = 31,
    vectorizer=None,
    max_combo_size: int = 8,
    grouped_parties: set = None
):
    """
    双向 DP 子集和凑数：
    阶段1: Bank 凑 GL（为每个 GL 找一组 Bank）
    阶段2: GL 凑 Bank（为每个 Bank 找一组 GL）
    新增：找到组合后验证相似度，不够则继续找下一个组合
    """
    from bank_statement_matcher.src.core.subset_sum import subset_sum_dp_with_fallback

    # ========== 阶段1: Bank 凑 GL ==========
    used_bank = set()
    to_remove_gl = []

    for gi, g in enumerate(gl_list):
        # 同交易方多笔的GL跳过（留给聚合阶段）
        if grouped_parties and _get_primary_party(g) in grouped_parties:
            continue
        target = abs(g.amount)
        available_indices = [i for i in range(len(bank_list)) if i not in used_bank]
        available_amounts = [abs(bank_list[i].amount) for i in available_indices]

        if not available_amounts:
            continue

        combo_local, method = subset_sum_dp_with_fallback(
            available_amounts, target, tol, max_combo_size
        )

        if combo_local is not None:
            combo_entries = [bank_list[available_indices[i]] for i in combo_local]
            if not _date_close_group(g, combo_entries, date_window_days):
                combo_local = None

        # 相似度验证
        if combo_local is not None and vectorizer and _has_party_info(g):
            global_combo = [available_indices[i] for i in combo_local]
            combo_entries = [bank_list[i] for i in global_combo]
            best_result = select_best_match(g, [combo_entries], vectorizer)
            if not best_result:
                combo_local = None  # 相似度不够，放弃此解

        if combo_local is not None:
            global_combo = [available_indices[i] for i in combo_local]
            combo_entries = [bank_list[i] for i in global_combo]
            matches.append((g, combo_entries, method))
            for i in global_combo:
                used_bank.add(i)
            to_remove_gl.append(gi)

    for gi in sorted(to_remove_gl, reverse=True):
        gl_list.pop(gi)
    for bi in sorted(used_bank, reverse=True):
        bank_list.pop(bi)

    # ========== Phase 4b: GL 凑 Bank（DP only，聚合已在 Phase 3 完成） ==========
    if not gl_list or not bank_list:
        return

    used_gl = set()
    to_remove_bank = []

    for bi, b in enumerate(bank_list):
        target = abs(b.amount)
        available_indices = [i for i in range(len(gl_list)) if i not in used_gl]
        available_amounts = [abs(gl_list[i].amount) for i in available_indices]

        if not available_amounts:
            continue

        combo_local, method = subset_sum_dp_with_fallback(
            available_amounts, target, tol, max_combo_size
        )

        if combo_local is None:
            continue

        combo_entries = [gl_list[available_indices[i]] for i in combo_local]
        if not _check_gl_to_bank_small(combo_entries, b, date_window_days, vectorizer):
            continue

        for i in combo_local:
            used_gl.add(available_indices[i])
        to_remove_bank.append(bi)
        for gl in combo_entries:
            matches.append((gl, [b], f"gl_{method}"))

    for bi in sorted(to_remove_bank, reverse=True):
        bank_list.pop(bi)
    for gi in sorted(used_gl, reverse=True):
        gl_list.pop(gi)


def _agg_key(bank_entry, level: int) -> tuple:
    """生成聚合键"""
    b = bank_entry
    date_str = b.tx_date.strftime("%Y-%m-%d") if hasattr(b, 'tx_date') else ""
    abstract = getattr(b, 'abstract', '')
    counter_party = getattr(b, 'counter_party', '')
    amount = round(b.amount, 2)

    if level == 1:
        return (abstract, date_str, counter_party, amount)
    elif level == 2:
        return (abstract, date_str, counter_party)
    elif level == 3:
        return (date_str, counter_party)
    else:
        return (counter_party,)


def _amount_match(a: float, b: float, tol: float) -> bool:
    """金额匹配（考虑正负号）"""
    return abs(a - b) <= tol


def _check_gl_to_bank_small(gl_entries: List, bank_entry, date_window_days: int, vectorizer=None) -> bool:
    """验证一组GL是否可以匹配一个Bank（日期+相似度），小Block版本"""
    if not _date_close_group_bank(bank_entry, gl_entries, date_window_days):
        return False
    if vectorizer:
        for gl in gl_entries:
            if _has_party_info(gl):
                best_result = select_best_match(gl, [[bank_entry]], vectorizer)
                if not best_result:
                    return False
    return True


def _gl_to_bank_aggregation_small(
    unmatched_gl: List,
    unmatched_bank: List,
    matches: List,
    tol: float,
    date_window_days: int,
    vectorizer=None
):
    """
    Phase 3: GL→Bank 聚合漏斗（小Block版本）
    对每个未匹配Bank，聚合未匹配GL来凑金额
    漏斗顺序：agg1→agg2→agg3→agg4
    """
    used_gl = set()
    to_remove_bank = []

    for bi, b in enumerate(unmatched_bank):
        target = abs(b.amount)
        available_indices = [i for i in range(len(unmatched_gl)) if i not in used_gl]
        available_gls = [unmatched_gl[i] for i in available_indices]

        if not available_gls:
            continue

        match_found = False

        for agg_level in range(1, 5):
            groups = defaultdict(list)
            for gi_local, gl in enumerate(available_gls):
                if available_indices[gi_local] in used_gl:
                    continue
                key = _agg_key_gl(gl, agg_level)
                groups[key].append((gi_local, gl))

            for key, group_list in groups.items():
                indices = [g[0] for g in group_list]
                gls = [g[1] for g in group_list]
                s = sum(abs(gl.amount) for gl in gls)
                if not _amount_match(s, target, tol):
                    continue
                if _check_gl_to_bank_small(gls, b, date_window_days, vectorizer):
                    for i in indices:
                        used_gl.add(available_indices[i])
                    to_remove_bank.append(bi)
                    for gl in gls:
                        matches.append((gl, [b], f"gl_agg{agg_level}"))
                    match_found = True
                    break
            if match_found:
                break

    for bi in sorted(to_remove_bank, reverse=True):
        unmatched_bank.pop(bi)
    for gi in sorted(used_gl, reverse=True):
        unmatched_gl.pop(gi)


def _agg_key_gl(gl_entry, level: int) -> tuple:
    """GL 条目聚合键（用于 GL→Bank 方向）"""
    g = gl_entry
    date_str = str(getattr(g, 'entry_date', None) or getattr(g, 'tx_date', '') or '')
    abstract = getattr(g, 'abstract', '') or ''
    parties = getattr(g, 'counterparties', None)
    parties_str = '，'.join(parties) if parties else (getattr(g, 'customer_name', '') or '')
    amount = round(g.amount, 2)

    if level == 1:
        return (abstract, date_str, parties_str, amount)
    elif level == 2:
        return (abstract, date_str, parties_str)
    elif level == 3:
        return (date_str, parties_str)
    else:
        return (parties_str,)


def _date_close(g, b, max_days: int = 30) -> bool:
    """判断日期是否接近"""
    g_date = getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None)
    b_date = getattr(b, 'tx_date', None) or getattr(b, 'entry_date', None)
    if g_date is None or b_date is None:
        return True
    diff = abs((g_date - b_date).days)
    return diff <= max_days


def _date_close_group(g, bank_group, max_days: int = 30) -> bool:
    """判断 GL 日期与 Bank 组内最近日期是否接近"""
    g_date = getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None)
    if g_date is None or not bank_group:
        return True
    dates = [getattr(b, 'tx_date', None) or getattr(b, 'entry_date', None) for b in bank_group]
    dates = [d for d in dates if d is not None]
    if not dates:
        return True
    min_date = min(dates)
    return abs((g_date - min_date).days) <= max_days


def _date_close_group_bank(bank, gl_group, max_days: int = 30) -> bool:
    """判断 Bank 日期与 GL 组内日期是否接近（GL 凑 Bank 场景）"""
    b_date = getattr(bank, 'tx_date', None) or getattr(bank, 'entry_date', None)
    if b_date is None or not gl_group:
        return True
    dates = [getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None) for g in gl_group]
    dates = [d for d in dates if d is not None]
    if not dates:
        return True
    max_gl_date = max(dates)
    return abs((b_date - max_gl_date).days) <= max_days
