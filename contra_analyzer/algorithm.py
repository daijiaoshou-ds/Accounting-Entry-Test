import itertools
import hashlib
import time
from collections import defaultdict

class ExhaustiveSolver:
    """
    穷举算法 v9.4 (严格结构保留版)
    修复：
    1. 聚合时严格保留 Key 的后缀 (__Pos__D / __Neg__D)，防止正负行逻辑丢失。
    2. 仅对完全相同的 Key 进行金额合并。
    3. 浮点数强制清洗。
    """
    
    # 定义敏感科目关键词
    SENSITIVE_KEYWORDS = ["银行", "现金", "Bank", "Cash", "支付宝", "微信"]

    @staticmethod
    def is_sensitive(key_name):
        return any(kw in key_name for kw in ExhaustiveSolver.SENSITIVE_KEYWORDS)

    @staticmethod
    def calculate_combinations(debit_ledger, credit_ledger, max_solutions=200, timeout=5.0):
        start_time = time.time()
        
        # 1. 预处理 (双重保险：再次强制 round 2)
        debits = {k: round(v, 2) for k, v in debit_ledger.items() if abs(v) > 0.001}
        credits = {k: round(v, 2) for k, v in credit_ledger.items() if abs(v) > 0.001}
        
        # 2. 智能转置
        n_debits = len(debits)
        n_credits = len(credits)
        is_transposed = False
        
        if n_credits > n_debits:
            is_transposed = True
            drivers = credits
            buckets = debits
        else:
            drivers = debits
            buckets = credits

        # === 3. 双轨计算 ===
        results_b, timeout_b = ExhaustiveSolver._core_solve(
            drivers, buckets, max_solutions, timeout * 0.7, start_time, use_perfect_lock=False
        )
        
        remaining_time = timeout - (time.time() - start_time)
        results_a = []
        if remaining_time > 0.1:
            results_a, _ = ExhaustiveSolver._core_solve(
                drivers, buckets, max_solutions, remaining_time, time.time(), use_perfect_lock=True
            )

        raw_results = results_b + results_a
        is_timeout = timeout_b

        # === 4. 结果还原 & 聚合 (不破坏结构) & 校验 ===
        deduped_results = []
        seen_hashes = set()

        for result in raw_results:
            # 4.1 基础还原 (处理转置)
            temp_res = {}
            for d_key in debits.keys(): temp_res[d_key] = {}

            if is_transposed:
                for c_key, d_map in result.items():
                    for d_key, amount in d_map.items():
                        if abs(amount) > 0.001:
                            # 还原时金额不取绝对值，保持原始符号逻辑
                            # (实际上这里的 amount 是由原始数据算出来的，自带符号)
                            temp_res[d_key][c_key] = amount
            else:
                for d_key, c_map in result.items():
                    for c_key, amount in c_map.items():
                        if abs(amount) > 0.001:
                            temp_res[d_key][c_key] = amount
            
            # 4.2 核心优化：基于完整 Key 的聚合 (Fix)
            # 之前错误地 split 了后缀，导致 Pos 和 Neg 混淆
            # 现在我们保留 full key
            final_res = defaultdict(lambda: defaultdict(float))
            
            for d_key, c_map in temp_res.items():
                for c_key, amt in c_map.items():
                    # 聚合：只有当 d_key 和 c_key 完全一致时才合并金额
                    # 这能消除 DFS 过程中产生的路径差异，但保留正负差异
                    final_res[d_key][c_key] += amt
            
            # 4.3 转换回普通字典并过滤微小值
            cleaned_res = {}
            for d_key, c_map in final_res.items():
                cleaned_c_map = {}
                for c_key, amt in c_map.items():
                    if abs(amt) > 0.001:
                        cleaned_c_map[c_key] = round(amt, 2) # 强制2位小数
                
                if cleaned_c_map:
                    cleaned_res[d_key] = cleaned_c_map

            # 4.4 后置校验：确保金额守恒 (针对原始借贷总额)
            is_valid_accounting = True
            
            # 校验借方
            for d_key, original_amt in debits.items():
                if d_key in cleaned_res:
                    split_sum = sum(cleaned_res[d_key].values())
                else:
                    split_sum = 0
                
                if abs(round(split_sum, 2) - round(original_amt, 2)) > 0.01:
                    is_valid_accounting = False; break
            if not is_valid_accounting: continue

            # 校验贷方
            c_received = defaultdict(float)
            for d_key, c_map in cleaned_res.items():
                for c_key, amt in c_map.items():
                    c_received[c_key] += amt
            
            for c_key, original_amt in credits.items():
                if abs(round(c_received[c_key], 2) - round(original_amt, 2)) > 0.01:
                    is_valid_accounting = False; break
            if not is_valid_accounting: continue

            # 4.5 签名去重
            temp_sig = []
            for d in sorted(cleaned_res.keys()):
                for c in sorted(cleaned_res[d].keys()):
                    amt = cleaned_res[d][c]
                    temp_sig.append(f"{d}:{c}:{amt}")
            
            sig_str = "|".join(temp_sig)
            res_hash = hashlib.md5(sig_str.encode()).hexdigest()
            
            if res_hash not in seen_hashes:
                seen_hashes.add(res_hash)
                deduped_results.append(cleaned_res)
                
            if len(deduped_results) >= max_solutions: break

        return deduped_results, is_timeout

    @staticmethod
    def _core_solve(drivers_dict, buckets_dict, max_sol, timeout, start_time, use_perfect_lock=True):
        # 排序：从小到大 (含负数)
        driver_items = sorted(drivers_dict.items(), key=lambda x: x[1], reverse=False)
        bucket_items = sorted(list(buckets_dict.items()), key=lambda x: x[1], reverse=False)
        
        locked_allocations = {}
        
        if use_perfect_lock:
            temp_buckets = buckets_dict.copy()
            temp_drivers = drivers_dict.copy()
            sorted_d_keys = sorted(temp_drivers.keys(), key=lambda k: temp_drivers[k])
            
            for d_key in sorted_d_keys:
                d_val = temp_drivers[d_key]
                found_b = None
                for b_key, b_val in temp_buckets.items():
                    if abs(round(d_val - b_val, 4)) < 0.001:
                        is_d_sens = ExhaustiveSolver.is_sensitive(d_key)
                        is_b_sens = ExhaustiveSolver.is_sensitive(b_key)
                        if (is_d_sens or is_b_sens) and (d_val * b_val < 0): continue 
                        found_b = b_key
                        break
                if found_b:
                    locked_allocations[d_key] = {found_b: d_val}
                    del temp_drivers[d_key]
                    del temp_buckets[found_b]
            
            driver_items = sorted(temp_drivers.items(), key=lambda x: x[1], reverse=False)
            bucket_items = sorted(list(temp_buckets.items()), key=lambda x: x[1], reverse=False)
        
        results = []
        is_timeout = [False]

        def generate_combinations(driver_name, target_amt, available_buckets):
            valid_splits = []
            n = len(available_buckets)
            seen = set()
            is_driver_sensitive = ExhaustiveSolver.is_sensitive(driver_name)
            
            # A. 全匹配
            for r in range(1, n + 1):
                for indices in itertools.combinations(range(n), r):
                    subset_sum = round(sum(available_buckets[i][1] for i in indices), 4)
                    if abs(subset_sum - target_amt) < 0.001:
                        if is_driver_sensitive:
                            has_mixed = False
                            for i in indices:
                                b = available_buckets[i][1]
                                if (target_amt>0 and b<-0.001) or (target_amt<0 and b>0.001): has_mixed=True
                            if has_mixed: continue

                        split_map = {}
                        for i in indices:
                            split_map[available_buckets[i][0]] = available_buckets[i][1]
                        
                        key = tuple(sorted((k, round(v, 4)) for k,v in split_map.items()))
                        if key not in seen:
                            seen.add(key)
                            valid_splits.append(split_map)

            # B. 部分匹配
            for i in range(n):
                partial_name, partial_cap = available_buckets[i]
                is_bucket_sensitive = ExhaustiveSolver.is_sensitive(partial_name)
                
                others_indices = [x for x in range(n) if x != i]
                n_others = len(others_indices)
                
                for r in range(n_others + 1):
                    for sub_indices in itertools.combinations(others_indices, r):
                        current_sum = round(sum(available_buckets[k][1] for k in sub_indices), 4)
                        needed = round(target_amt - current_sum, 4)
                        
                        if abs(needed) < 0.001: continue
                        
                        is_valid_part = False
                        if is_bucket_sensitive:
                            if partial_cap > 0:
                                if 0.001 < needed < partial_cap - 0.001: is_valid_part = True
                                # 新增修改：允许负数需求（比如 -1537）拆分正数桶（比如 66463）
                                if -(partial_cap) - 0.001 < needed < -0.001: is_valid_part = True
                            elif partial_cap < 0:
                                if partial_cap + 0.001 < needed < -0.001: is_valid_part = True
                        else:
                            if abs(needed - partial_cap) > 0.001: is_valid_part = True
                                
                        if is_valid_part:
                            # ===================================================================
                            # 【核心修复】超额分配检查（Over-allocation Guard）
                            # 防止"小额目标、大额回调"的荒谬解
                            # 例如：产成品(3914元) 不应产生 2064万元 的反向回调
                            # ===================================================================
                            if abs(needed) > abs(target_amt) * 10:
                                continue
                            if is_driver_sensitive:
                                has_mixed = False
                                for k in sub_indices:
                                    b = available_buckets[k][1]
                                    if (target_amt>0 and b<-0.001) or (target_amt<0 and b>0.001): has_mixed=True
                                if (target_amt>0 and needed<-0.001) or (target_amt<0 and needed>0.001): has_mixed=True
                                if has_mixed: continue

                            split_map = {}
                            for k in sub_indices:
                                split_map[available_buckets[k][0]] = available_buckets[k][1]
                            split_map[partial_name] = needed
                            
                            key = tuple(sorted((k, round(v, 4)) for k,v in split_map.items()))
                            if key not in seen:
                                seen.add(key)
                                valid_splits.append(split_map)
            return valid_splits

        def dfs(d_idx, current_allocations, current_buckets):
            if len(results) >= max_sol * 2: return 
            if time.time() - start_time > timeout:
                is_timeout[0] = True; return

            if d_idx == len(driver_items):
                remain = round(sum(amt for _, amt in current_buckets), 4)
                if abs(remain) < 0.01:
                    final_comb = current_allocations.copy()
                    if use_perfect_lock and locked_allocations:
                        final_comb.update(locked_allocations)
                    results.append(final_comb)
                return

            driver_name, driver_amt = driver_items[d_idx]
            possible_splits = generate_combinations(driver_name, driver_amt, current_buckets)
            
            if not possible_splits: return

            for split in possible_splits:
                if len(results) >= max_sol * 2: return
                
                next_alloc = current_allocations.copy()
                next_alloc[driver_name] = split
                
                next_buckets = []
                for b_name, b_amt in current_buckets:
                    used = split.get(b_name, 0)
                    remain = round(b_amt - used, 4)
                    if abs(remain) > 0.001:
                        next_buckets.append((b_name, remain))
                
                dfs(d_idx + 1, next_alloc, next_buckets)

        if not driver_items:
            if locked_allocations: results.append(locked_allocations)
        else:
            dfs(0, {}, bucket_items)
            
        return results, is_timeout[0]