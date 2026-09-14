"""大 Block 处理：CSR 工程（特征工程 + 稀疏候选矩阵 + 动态贪心 + 余弦相似度筛选）

匹配流程（6 阶段）：
  Phase 1: 1v1 精确匹配（双向等价）
  Phase 2: GL→Bank 聚合漏斗（agg1→agg2→agg3→agg4→agg5）
  Phase 3: Bank→GL 聚合漏斗（agg1→agg2→agg3→agg4，动态贪心）
  Phase 4: 一对一回溯匹配
  Phase 5: DP 双向求解
  Phase 6: 疯狂聚合（纯时间窗口，最后兜底）
"""
import time
from typing import List, Tuple, Optional
from collections import defaultdict

from bank_statement_matcher.src.core.similarity_filter import select_best_match
from bank_statement_matcher.src.utils.performance_logger import perf_logger


def _has_party_info(gl_entry) -> bool:
	"""判断 GL 是否有客商信息"""
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


def _select_best_1v1(gl_entry, candidates: List, vectorizer=None):
	"""一对一选择：三级规则
	(1) gl_name == bank_name（精确匹配）
	(2) gl_name in bank_name 或 bank_name in gl_name（包含匹配）
	(3) 余弦相似度最高者
	返回选中的 bank_entry，如果只有一个候选直接返回
	"""
	if len(candidates) == 1:
		return candidates[0]

	gl_parties = getattr(gl_entry, 'counterparties', None)
	if gl_parties:
		gl_names = [p.lower().replace('(', '（').replace(')', '）') for p in gl_parties if p]
	else:
		gl_name = getattr(gl_entry, 'customer_name', '') or ''
		gl_names = [gl_name.lower().replace('(', '（').replace(')', '）')] if gl_name else []

	if not gl_names:
		return candidates[0]

	# Tier 1: exact name match
	for b in candidates:
		b_name = (getattr(b, 'counter_party', '') or '').lower().replace('(', '（').replace(')', '）')
		for gn in gl_names:
			if gn and b_name and gn == b_name:
				return b

	# Tier 2: containment match
	for b in candidates:
		b_name = (getattr(b, 'counter_party', '') or '').lower().replace('(', '（').replace(')', '）')
		for gn in gl_names:
			if gn and b_name and (gn in b_name or b_name in gn):
				return b

	# Tier 3: cosine similarity
	if vectorizer:
		best_result = select_best_match(gl_entry, [[b] for b in candidates], vectorizer)
		if best_result:
			return best_result[0][0]

	return candidates[0]


def _mark_bank_used(bank_list: List, bank_used: List[bool], bank_entry) -> None:
	"""将 bank_entry 标记为已使用"""
	for idx, b in enumerate(bank_list):
		if b is bank_entry:
			bank_used[idx] = True
			return


def solve_large_block(
	gl_entries: List,
	bank_entries: List,
	tol: float = 0.01,
	time_window_days: int = 31,
	use_customer: bool = True,
	dynamic_greedy: bool = True,
	vectorizer=None
) -> Tuple[List[Tuple], List, List]:
	"""
	大 Block 匹配策略（CSR 工程）
	六阶段流程：1v1 → GL→Bank聚合 → Bank→GL聚合 → 一对一回溯 → DP兜底 → 疯狂聚合

	参数:
		dynamic_greedy: Phase 3(Bank→GL) 是否动态贪心（候选池最小的 GL 优先处理）
	"""
	matches = []
	bank_used = [False] * len(bank_entries)
	bank_list = list(bank_entries)
	gl_list = list(gl_entries)
	matched_gl = set()  # id(g)

	# ============================================================
	# Phase 1: 1v1 exact match
	# Same voucher + same counterparty GLs skip (leave for aggregation);
	# Same counterparty multi-Banks also skip
	# ============================================================
	# GL grouping: same voucher_no + same counterparty
	_voucher_party_count = defaultdict(int)
	for g in gl_list:
		parties = getattr(g, 'counterparties', None)
		if parties:
			for p in parties:
				_voucher_party_count[(g.voucher_no, p)] += 1
	grouped_gl_ids = set()
	for g in gl_list:
		parties = getattr(g, 'counterparties', None)
		if parties:
			for p in parties:
				if _voucher_party_count.get((g.voucher_no, p), 0) > 1:
					grouped_gl_ids.add(id(g))
					break
		else:
			abstract = getattr(g, 'abstract', '') or ''
			if abstract:
				_voucher_party_count[(g.voucher_no, f'__abstract__{abstract}')] += 1
	for g in gl_list:
		if id(g) in grouped_gl_ids:
			continue
		parties = getattr(g, 'counterparties', None)
		if not parties:
			abstract = getattr(g, 'abstract', '') or ''
			if abstract and _voucher_party_count.get((g.voucher_no, f'__abstract__{abstract}'), 0) > 1:
				grouped_gl_ids.add(id(g))
	# Bank grouping: same counterparty multi-entries
	_bank_party_count = defaultdict(int)
	for b in bank_list:
		bp = getattr(b, 'counter_party', '')
		if bp:
			_bank_party_count[bp] += 1
	grouped_bank_ids = set()
	for b in bank_list:
		bp = getattr(b, 'counter_party', '')
		if _bank_party_count.get(bp, 0) > 1:
			grouped_bank_ids.add(id(b))

	perf_logger.info(f"  大Block Phase1-1v1: GL={len(gl_list)}, Bank={len(bank_list)}, GL分组={len(grouped_gl_ids)}条, Bank分组={len(grouped_bank_ids)}条")
	p1_count = 0
	p1_skipped = 0
	for gi, g in enumerate(gl_list):
		if id(g) in matched_gl:
			continue
		if id(g) in grouped_gl_ids:
			p1_skipped += 1
			continue
		candidates = _build_candidates(
			g, bank_list, bank_used, tol, time_window_days, use_customer
		)
		one_to_one = [b for b in candidates if _amount_match(abs(g.amount), abs(b.amount), tol) and id(b) not in grouped_bank_ids]
		if not one_to_one:
			continue

		b = _select_best_1v1(g, one_to_one, vectorizer)
		_mark_bank_used(bank_list, bank_used, b)
		matches.append((g, [b], "csr_1v1"))
		matched_gl.add(id(g))
		p1_count += 1
	perf_logger.info(f"  大Block     Phase1-1v1 匹配: {p1_count}对, 跳过(GL分组+Bank分组): {p1_skipped}笔")
	# ============================================================
	# Phase 2: GL→Bank 聚合漏斗（agg1→agg2→agg3→agg4，不含DP）
	# ============================================================
	unmatched_gl = [g for g in gl_list if id(g) not in matched_gl]
	unmatched_bank = [bank_list[i] for i in range(len(bank_list)) if not bank_used[i]]

	if unmatched_gl and unmatched_bank:
		perf_logger.info(f"  大Block Phase2-GL→Bank聚合: 剩余GL={len(unmatched_gl)}, Bank={len(unmatched_bank)}")
		p2_start = time.time()
		_gl_to_bank_aggregation(unmatched_gl, unmatched_bank, matches, tol, time_window_days, vectorizer)
		p2_dt = time.time() - p2_start
		# 更新 tracked 状态
		for m in matches:
			if isinstance(m[2], str) and m[2].startswith('gl_agg'):
				matched_gl.add(id(m[0]))
				for b in m[1]:
					_mark_bank_used(bank_list, bank_used, b)
		p2_new = sum(1 for m in matches if isinstance(m[2], str) and m[2].startswith('gl_agg'))
		perf_logger.info(f"    Phase2完成: {p2_dt:.3f}s, GL→Bank聚合匹配 {p2_new}对")
	else:
		perf_logger.info(f"  大Block Phase2-GL→Bank聚合: 无可聚合项，跳过")


	# ============================================================
	# Phase 3: Bank→GL 聚合漏斗（agg1→agg2→agg3→agg4，不含DP）
	# ============================================================
	perf_logger.info(f"  大Block Phase3-Bank→GL聚合: dynamic_greedy={dynamic_greedy}")
	remaining_gl_indices = [i for i in range(len(gl_list)) if id(gl_list[i]) not in matched_gl]
	p3_iter = 0
	total_candidate_time = 0.0
	total_agg_time = 0.0

	while remaining_gl_indices:
		p3_iter += 1
		if p3_iter % 50 == 0:
			perf_logger.info(f"    Phase3 迭代#{p3_iter}, 剩余GL={len(remaining_gl_indices)}, 已匹配={len(matches)}")

		if dynamic_greedy:
			best_gi = None
			best_candidates = None
			best_count = float('inf')

			t0 = time.time()
			for gi in remaining_gl_indices:
				candidates = _build_candidates(
					gl_list[gi], bank_list, bank_used,
					tol, time_window_days, use_customer
				)
				if len(candidates) < best_count:
					best_count = len(candidates)
					best_gi = gi
					best_candidates = candidates
			total_candidate_time += time.time() - t0

			if best_gi is None or best_count == 0:
				remaining_gl_indices.remove(best_gi) if best_gi is not None else None
				continue
			g = gl_list[best_gi]
			candidates = best_candidates
			remaining_gl_indices.remove(best_gi)
		else:
			gi = remaining_gl_indices[0]
			g = gl_list[gi]
			candidates = _build_candidates(
				g, bank_list, bank_used, tol, time_window_days, use_customer
			)
			remaining_gl_indices.pop(0)
			if not candidates:
				continue

		# 只做聚合（agg1-4），不做DP
		t0 = time.time()
		result = _try_aggregation(g, candidates, tol, vectorizer, grouped_gl_ids)
		total_agg_time += time.time() - t0

		if result:
			matched_banks, match_type = result
			matches.append((g, matched_banks, match_type))
			matched_gl.add(id(g))
			for b in matched_banks:
				_mark_bank_used(bank_list, bank_used, b)

	perf_logger.info(
		f"    Phase3完成: 迭代{p3_iter}轮, 候选构建{total_candidate_time:.2f}s, 聚合匹配{total_agg_time:.2f}s"
	)

	# ============================================================
	# Phase 4: 一对一回溯匹配（残差池纯金额1v1，多候选取最相似）
	# ============================================================
	unmatched_gl = [g for g in gl_list if id(g) not in matched_gl]
	unmatched_bank = [bank_list[i] for i in range(len(bank_list)) if not bank_used[i]]

	if unmatched_gl and unmatched_bank:
		perf_logger.info(f"  大Block Phase4-一对一回溯: 剩余GL={len(unmatched_gl)}, Bank={len(unmatched_bank)}")
		p4_start = time.time()
		p4_match_start = len(matches)
		_retrospective_1v1(unmatched_gl, unmatched_bank, matches, tol, time_window_days, vectorizer)
		p4_dt = time.time() - p4_start
		# 更新 Phase 4 新匹配的 tracked 状态
		for i in range(p4_match_start, len(matches)):
			m = matches[i]
			matched_gl.add(id(m[0]))
			for b in m[1]:
				_mark_bank_used(bank_list, bank_used, b)
		p4_new = len(matches) - p4_match_start
		perf_logger.info(f"    Phase4完成: {p4_dt:.3f}s, 一对一回溯匹配 {p4_new}对")
	else:
		perf_logger.info(f"  大Block Phase4-一对一回溯: 无剩余项，跳过")

	# ============================================================
	# Phase 5: DP 双向求解（最后兜底）
	# ============================================================
	unmatched_gl = [g for g in gl_list if id(g) not in matched_gl]
	unmatched_bank = [bank_list[i] for i in range(len(bank_list)) if not bank_used[i]]

	if unmatched_gl and unmatched_bank:
		perf_logger.info(f"  大Block Phase5-DP: 剩余GL={len(unmatched_gl)}, Bank={len(unmatched_bank)}")
		p5_start = time.time()
		p5_match_start = len(matches)
		_dp_bidirectional(unmatched_gl, unmatched_bank, matches, tol, time_window_days, vectorizer)
		p5_dt = time.time() - p5_start
		for i in range(p5_match_start, len(matches)):
			m = matches[i]
			matched_gl.add(id(m[0]))
			for b in m[1]:
				_mark_bank_used(bank_list, bank_used, b)
		p5_new = len(matches) - p5_match_start
		perf_logger.info(f"    Phase5完成: {p5_dt:.3f}s, DP匹配 {p5_new}对")
	else:
		perf_logger.info(f"  大Block Phase5-DP: 无剩余项，跳过")

	# ============================================================
	# Phase 6: 疯狂聚合（纯时间窗口，最后兜底）
	# ============================================================
	unmatched_gl = [g for g in gl_list if id(g) not in matched_gl]
	unmatched_bank = [bank_list[i] for i in range(len(bank_list)) if not bank_used[i]]

	if unmatched_gl and unmatched_bank:
		perf_logger.info(f"  大Block Phase6-疯狂聚合: 剩余GL={len(unmatched_gl)}, Bank={len(unmatched_bank)}")
		p6_start = time.time()
		p6_match_start = len(matches)
		_wild_aggregation(unmatched_gl, unmatched_bank, matches, tol, vectorizer)
		p6_dt = time.time() - p6_start
		for i in range(p6_match_start, len(matches)):
			m = matches[i]
			matched_gl.add(id(m[0]))
			for b in m[1]:
				_mark_bank_used(bank_list, bank_used, b)
		p6_new = len(matches) - p6_match_start
		perf_logger.info(f"    Phase6完成: {p6_dt:.3f}s, 疯狂聚合匹配 {p6_new}对")
	else:
		perf_logger.info(f"  大Block Phase6-疯狂聚合: 无剩余项，跳过")

	# 最终汇总
	final_unmatched_gl = [g for g in gl_list if id(g) not in matched_gl]
	final_unmatched_bank = [bank_list[i] for i in range(len(bank_list)) if not bank_used[i]]
	perf_logger.info(
		f"  大Block完成: 匹配={len(matches)}, 未匹配GL={len(final_unmatched_gl)}, 未匹配Bank={len(final_unmatched_bank)}"
	)

	return matches, final_unmatched_gl, final_unmatched_bank


# ================================================================
# Phase 2 辅助: 聚合漏斗（agg1-4，无1v1，无DP）
# ================================================================

def _try_aggregation(
	gl_entry,
	candidates: List,
	tol: float,
	vectorizer=None,
	grouped_gl_ids: set = None
) -> Optional[Tuple[List, str]]:
	"""
	在候选池内尝试聚合匹配（agg1-4，不含1v1和DP）
	返回: ([bank_entries], match_type) 或 None
	"""
	g_amount = abs(gl_entry.amount) if hasattr(gl_entry, 'amount') else 0
	has_party = _has_party_info(gl_entry)
	is_grouped = grouped_gl_ids and id(gl_entry) in grouped_gl_ids

	for agg_level in range(1, 5):
		groups = defaultdict(list)
		for b in candidates:
			key = _agg_key_large(b, agg_level)
			groups[key].append(b)

		valid_groups = []
		for grp in groups.values():
			s = sum(abs(b.amount) for b in grp)
			if _amount_match(g_amount, s, tol):
				# 同交易方多笔的GL，不允许单Bank"聚合"（本质是绕过Phase1的1v1）
				if is_grouped and len(grp) < 2:
					continue
				valid_groups.append(grp)

		if valid_groups:
			if vectorizer and has_party:
				best_result = select_best_match(gl_entry, valid_groups, vectorizer)
				if best_result:
					return (best_result[0], f"csr_agg{agg_level}")
				# 相似度不够，继续尝试下级聚合
			else:
				return (valid_groups[0], f"csr_agg{agg_level}")

	return None


# ================================================================
# Phase 3: GL→Bank 聚合漏斗（agg1-4，无DP）
# ================================================================

def _gl_to_bank_aggregation(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	time_window_days: int,
	vectorizer=None
):
	"""
	Phase 3: GL→Bank 聚合漏斗
	对每个未匹配Bank，聚合未匹配GL来凑金额
	漏斗顺序：agg1→agg2→agg3→agg4→agg5（不做1v1，因为Phase 1已覆盖）
	"""
	used_gl = set()
	to_remove_bank = []

	# Sort by abs(amount) desc: large Banks first (likely multi-GL aggregation targets)
	sorted_banks = sorted(enumerate(unmatched_bank), key=lambda x: abs(x[1].amount), reverse=True)

	for bi, b in sorted_banks:
		target = abs(b.amount)
		available_indices = [i for i in range(len(unmatched_gl)) if i not in used_gl]
		available_gls = [unmatched_gl[i] for i in available_indices]

		if not available_gls:
			continue

		match_found = False

		# agg1-5
		for agg_level in range(1, 6):
			groups = defaultdict(list)
			for gi_local, gl in enumerate(available_gls):
				if available_indices[gi_local] in used_gl:
					continue
				key = _agg_key_gl(gl, agg_level)
				groups[key].append((gi_local, gl))

			# 收集所有金额+日期匹配的有效组
			valid_groups = []
			for key, group_list in groups.items():
				indices = [g[0] for g in group_list]
				gls = [g[1] for g in group_list]
				s = sum(abs(gl.amount) for gl in gls)
				if not _amount_match(s, target, tol):
					continue
				if not _check_gl_to_bank_dates(gls, b, time_window_days):
					continue
				valid_groups.append((indices, gls))

			if not valid_groups:
				continue

			# 选择最优组：相似度最高者
			if len(valid_groups) == 1 or not vectorizer:
				indices, gls = valid_groups[0]
			else:
				best_idx = 0
				best_sim = -1.0
				for gi, (vi, vg) in enumerate(valid_groups):
					sim = _compute_gl_group_similarity(vg, b, vectorizer)
					if sim > best_sim:
						best_sim = sim
						best_idx = gi
				indices, gls = valid_groups[best_idx]

			for i in indices:
				used_gl.add(available_indices[i])
			to_remove_bank.append(bi)
			for gl in gls:
				matches.append((gl, [b], f"gl_agg{agg_level}"))
			match_found = True
			break

	# 移除已匹配项
	for bi in sorted(to_remove_bank, reverse=True):
		unmatched_bank.pop(bi)
	for gi in sorted(used_gl, reverse=True):
		unmatched_gl.pop(gi)


# ================================================================
# Phase 4: DP 双向求解
# ================================================================

def _retrospective_1v1(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	time_window_days: int,
	vectorizer=None
):
	"""
	Phase 4: backtrack 1v1 match
	All remaining entries try amount 1v1, multi-candidate uses tiered selection
	"""
	to_remove_gl = []
	to_remove_bank = set()

	for gi, g in enumerate(unmatched_gl):
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
				if abs((g_date - b_date).days) > time_window_days:
					continue
			candidates.append((bi, b))

		if not candidates:
			continue

		b_list = [b for _, b in candidates]
		selected_b = _select_best_1v1(g, b_list, vectorizer)
		for bi, b in candidates:
			if b is selected_b:
				matches.append((g, [b], "csr_1v1_backtrack"))
				to_remove_gl.append(gi)
				to_remove_bank.add(bi)
				break

	for gi in sorted(to_remove_gl, reverse=True):
		unmatched_gl.pop(gi)
	for bi in sorted(to_remove_bank, reverse=True):
		unmatched_bank.pop(bi)

def _dp_bidirectional(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	time_window_days: int,
	vectorizer=None
):
	"""
	Phase 5: DP bidirectional (last resort)
	Phase 5a: Bank->GL DP (find subset sum of Banks for each GL)
	Phase 5b: GL->Bank DP (find subset sum of GLs for each Bank)
	"""
	from bank_statement_matcher.src.core.subset_sum import subset_sum_dp_with_fallback

	# ---- Phase 5a: Banks fit GL ----
	used_bank = set()
	matched_gl_dp = set()

	for gi, g in enumerate(unmatched_gl):
		if gi in matched_gl_dp:
			continue
		target = abs(g.amount)
		available_indices = [i for i in range(len(unmatched_bank)) if i not in used_bank]
		available_amounts = [abs(unmatched_bank[i].amount) for i in available_indices]

		if not available_amounts:
			continue

		combo_local, method = subset_sum_dp_with_fallback(
			available_amounts, target, tol, max_size=8
		)
		if combo_local is None:
			continue

		combo_entries = [unmatched_bank[available_indices[i]] for i in combo_local]

		# 日期检查
		g_date = getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None)
		if g_date:
			b_dates = [getattr(b, 'tx_date', None) or getattr(b, 'entry_date', None) for b in combo_entries]
			b_dates = [d for d in b_dates if d is not None]
			if b_dates and abs((g_date - min(b_dates)).days) > time_window_days:
				continue

		# 相似度验证
		if vectorizer and _has_party_info(g):
			best_result = select_best_match(g, [combo_entries], vectorizer)
			if not best_result:
				continue

		for i in combo_local:
			used_bank.add(available_indices[i])
		matches.append((g, combo_entries, method))
		matched_gl_dp.add(gi)

	# ---- Phase 5b: GL凑Bank ----
	remaining_gl = [g for i, g in enumerate(unmatched_gl) if i not in matched_gl_dp]
	remaining_bank = [b for i, b in enumerate(unmatched_bank) if i not in used_bank]

	if remaining_gl and remaining_bank:
		_dp_gl_to_bank(remaining_gl, remaining_bank, matches, tol, time_window_days, vectorizer)

	# 更新原始列表
	unmatched_gl[:] = [g for i, g in enumerate(unmatched_gl) if i not in matched_gl_dp]
	unmatched_bank[:] = [b for i, b in enumerate(unmatched_bank) if i not in used_bank]

def _dp_gl_to_bank(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	time_window_days: int,
	vectorizer=None
):
	"""Phase 4b: GL凑Bank DP"""
	from bank_statement_matcher.src.core.subset_sum import subset_sum_dp_with_fallback

	used_gl = set()
	to_remove_bank = []

	for bi, b in enumerate(unmatched_bank):
		target = abs(b.amount)
		available_indices = [i for i in range(len(unmatched_gl)) if i not in used_gl]
		available_amounts = [abs(unmatched_gl[i].amount) for i in available_indices]

		if not available_amounts:
			continue

		combo_local, method = subset_sum_dp_with_fallback(
			available_amounts, target, tol, max_size=8
		)
		if combo_local is None:
			continue

		combo_entries = [unmatched_gl[available_indices[i]] for i in combo_local]
		if not _check_gl_to_bank(combo_entries, b, time_window_days, vectorizer):
			continue

		for i in combo_local:
			used_gl.add(available_indices[i])
		to_remove_bank.append(bi)
		for gl in combo_entries:
			matches.append((gl, [b], f"gl_{method}"))

	for bi in sorted(to_remove_bank, reverse=True):
		unmatched_bank.pop(bi)
	for gi in sorted(used_gl, reverse=True):
		unmatched_gl.pop(gi)


# ================================================================
# 通用辅助函数
# ================================================================

def _build_candidates(
	gl_entry,
	bank_list: List,
	bank_used: List[bool],
	tol: float,
	time_window_days: int,
	use_customer: bool
) -> List:
	"""
	为单个 GL 构建 CSR 候选列表
	特征过滤：时间、金额（Bank 金额 <= GL 金额 + tol）
	"""
	g = gl_entry
	g_date = getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None)
	g_amount = abs(g.amount) if hasattr(g, 'amount') else 0

	candidates = []
	for i, b in enumerate(bank_list):
		if bank_used[i]:
			continue

		b_date = getattr(b, 'tx_date', None) or getattr(b, 'entry_date', None)
		b_amount = abs(b.amount) if hasattr(b, 'amount') else float('inf')

		if b_amount > g_amount + tol:
			continue

		if g_date and b_date:
			diff = abs((g_date - b_date).days)
			if diff > time_window_days:
				continue

		candidates.append(b)

	return candidates


def _check_gl_to_bank(gl_entries: List, bank_entry, time_window_days: int, vectorizer=None) -> bool:
	"""验证一组GL是否可以匹配一个Bank（日期+相似度）"""
	if not _check_gl_to_bank_dates(gl_entries, bank_entry, time_window_days):
		return False

	if vectorizer:
		for gl in gl_entries:
			if _has_party_info(gl):
				best_result = select_best_match(gl, [[bank_entry]], vectorizer)
				if not best_result:
					return False
	return True


def _check_gl_to_bank_dates(gl_entries: List, bank_entry, time_window_days: int) -> bool:
	"""仅日期验证（不含相似度），用于GL→Bank聚合多候选筛选"""
	b_date = getattr(bank_entry, 'tx_date', None) or getattr(bank_entry, 'entry_date', None)
	gl_dates = [getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None) for g in gl_entries]
	gl_dates = [d for d in gl_dates if d is not None]
	if b_date and gl_dates:
		max_gl_date = max(gl_dates)
		if abs((b_date - max_gl_date).days) > time_window_days:
			return False
	return True


def _compute_gl_group_similarity(gl_entries: List, bank_entry, vectorizer) -> float:
	"""计算一组GL与一个Bank的平均余弦相似度，用于多候选组选择"""
	bank_party = getattr(bank_entry, 'counter_party', '') or ''
	if not bank_party or not vectorizer:
		return 0.0

	total = 0.0
	count = 0
	for gl in gl_entries:
		parties = getattr(gl, 'counterparties', None)
		if parties:
			for p in parties:
				if p:
					total += vectorizer.cosine_similarity(p, bank_party)
					count += 1
		else:
			name = getattr(gl, 'customer_name', '')
			if name:
				total += vectorizer.cosine_similarity(name, bank_party)
				count += 1

	return total / count if count > 0 else 0.0


def _agg_key_large(bank_entry, level: int) -> tuple:
	"""Bank 聚合键（Bank→GL 方向）"""
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


def _agg_key_gl(gl_entry, level: int) -> tuple:
	"""GL 聚合键（GL→Bank 方向）"""
	g = gl_entry
	date_str = str(getattr(g, 'entry_date', None) or getattr(g, 'tx_date', '') or '')
	abstract = getattr(g, 'abstract', '') or ''
	parties = getattr(g, 'counterparties', None)
	parties_str = '，'.join(parties) if parties else (getattr(g, 'customer_name', '') or '')
	amount = round(g.amount, 2)
	voucher_no = getattr(g, 'voucher_no', '') or ''

	if level == 1:
		return (abstract, date_str, parties_str, amount)
	elif level == 2:
		return (abstract, date_str, parties_str)
	elif level == 3:
		return (date_str, parties_str)
	elif level == 4:
		return (parties_str,)
	else:
		return (voucher_no,)


def _amount_match(a: float, b: float, tol: float) -> bool:
	return abs(a - b) <= tol


# ================================================================
# Phase 6: 疯狂聚合（纯时间窗口，DP之后最后兜底）
# ================================================================
def _merge_date_groups(date_groups: dict, max_diff: int, same_month: bool = False) -> list:
	"""合并相邻日期组：将日期差 <= max_diff 天的日期串联为同一组（链式聚类）
	若 same_month=True，跨月也断开"""
	sorted_dates = sorted(date_groups.keys())
	if not sorted_dates:
		return []
	clusters = []
	cluster_start = 0
	for i in range(1, len(sorted_dates)):
		gap_too_large = (sorted_dates[i] - sorted_dates[i - 1]).days > max_diff
		cross_month = same_month and (sorted_dates[i].month != sorted_dates[i - 1].month)
		if gap_too_large or cross_month:
			clusters.append(sorted_dates[cluster_start:i])
			cluster_start = i
	clusters.append(sorted_dates[cluster_start:])
	result = []
	for cluster_dates in clusters:
		merged_group = []
		for d in cluster_dates:
			merged_group.extend(date_groups[d])
		result.append(merged_group)
	return result


def _wild_aggregation(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	vectorizer=None
):
	"""Phase 6: 疯狂聚合 — 纯时间窗口聚合，不做特征键匹配
	Bank→GL: 按时间窗口聚合Bank凑GL
	GL→Bank: 按时间窗口聚合GL凑Bank
	"""
	if not unmatched_gl or not unmatched_bank:
		return

	_wild_bank_to_gl(unmatched_gl, unmatched_bank, matches, tol, vectorizer)
	_wild_gl_to_bank(unmatched_gl, unmatched_bank, matches, tol, vectorizer)


def _wild_bank_to_gl(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	vectorizer=None
):
	"""Bank→GL 疯狂聚合：按Bank自身交易日逐步扩大窗口聚合，凑GL金额
	时间窗口拉伸全在Bank日期上，GL只是待匹配的金额池（不做日期判定）"""
	used_bank = set()
	matched_gl_idx = set()

	time_levels = [(0, "wild_bank_sameday", False), (2, "wild_bank_2d", True), (7, "wild_bank_7d", False), (31, "wild_bank_month", False)]

	for max_diff, match_type, same_month in time_levels:
		# 1. 按Bank自身交易日分组
		date_groups = {}
		for bi, b in enumerate(unmatched_bank):
			if bi in used_bank:
				continue
			b_date = getattr(b, 'tx_date', None) or getattr(b, 'entry_date', None)
			if b_date is None:
				continue
			date_groups.setdefault(b_date, []).append((bi, b))

		if not date_groups:
			continue

		# 2. 按时间窗口合并相邻日期组（链式聚类）
		merged = _merge_date_groups(date_groups, max_diff, same_month)

		# 3. 每个合并组尝试匹配GL（GL按金额降序，贪婪匹配，先到先得）
		gl_sorted = sorted(
			[(gi, g) for gi, g in enumerate(unmatched_gl) if gi not in matched_gl_idx],
			key=lambda x: abs(x[1].amount), reverse=True
		)

		for group in merged:
			if len(group) < 2:
				continue
			total = sum(abs(b.amount) for _, b in group)

			for gi, g in gl_sorted:
				if not _amount_match(total, abs(g.amount), tol):
					continue

				bank_entries = [b for _, b in group]
				if vectorizer and _has_party_info(g):
					best_result = select_best_match(g, [bank_entries], vectorizer)
					if not best_result:
						continue

				for bi, _ in group:
					used_bank.add(bi)
				matches.append((g, bank_entries, match_type))
				matched_gl_idx.add(gi)
				gl_sorted = [(gi2, g2) for gi2, g2 in gl_sorted if gi2 != gi]
				break

	unmatched_gl[:] = [g for i, g in enumerate(unmatched_gl) if i not in matched_gl_idx]
	unmatched_bank[:] = [b for i, b in enumerate(unmatched_bank) if i not in used_bank]



def _wild_gl_to_bank(
	unmatched_gl: List,
	unmatched_bank: List,
	matches: List,
	tol: float,
	vectorizer=None
):
	"""GL→Bank 疯狂聚合：按GL自身日期逐步扩大窗口聚合，凑Bank金额
	时间窗口拉伸全在GL日期上，Bank只是待匹配的金额池（不做日期判定）"""
	used_gl = set()
	matched_bank_idx = set()

	time_levels = [(0, "wild_gl_sameday", False), (2, "wild_gl_2d", True), (7, "wild_gl_7d", False), (31, "wild_gl_month", False)]

	for max_diff, match_type, same_month in time_levels:
		# 1. 按GL自身日期分组
		date_groups = {}
		for gi, g in enumerate(unmatched_gl):
			if gi in used_gl:
				continue
			g_date = getattr(g, 'entry_date', None) or getattr(g, 'tx_date', None)
			if g_date is None:
				continue
			date_groups.setdefault(g_date, []).append((gi, g))

		if not date_groups:
			continue

		# 2. 按时间窗口合并相邻日期组（链式聚类）
		merged = _merge_date_groups(date_groups, max_diff, same_month)

		# 3. 每个合并组尝试匹配Bank（Bank按金额降序，贪婪匹配，先到先得）
		bank_sorted = sorted(
			[(bi, b) for bi, b in enumerate(unmatched_bank) if bi not in matched_bank_idx],
			key=lambda x: abs(x[1].amount), reverse=True
		)

		for group in merged:
			if len(group) < 2:
				continue
			total = sum(abs(g.amount) for _, g in group)

			for bi, b in bank_sorted:
				if not _amount_match(total, abs(b.amount), tol):
					continue

				gl_entries = [g for _, g in group]
				if vectorizer:
					ok = True
					for g_entry in gl_entries:
						if _has_party_info(g_entry):
							best_result = select_best_match(g_entry, [[b]], vectorizer)
							if not best_result:
								ok = False
								break
					if not ok:
						continue

				for gi, _ in group:
					used_gl.add(gi)
				for g_entry in gl_entries:
					matches.append((g_entry, [b], match_type))
				matched_bank_idx.add(bi)
				bank_sorted = [(bi2, b2) for bi2, b2 in bank_sorted if bi2 != bi]
				break

	unmatched_gl[:] = [g for i, g in enumerate(unmatched_gl) if i not in used_gl]
	unmatched_bank[:] = [b for i, b in enumerate(unmatched_bank) if i not in matched_bank_idx]

