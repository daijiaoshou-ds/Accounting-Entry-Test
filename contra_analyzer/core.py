import pandas as pd
import hashlib
from collections import defaultdict
from .algorithm import ExhaustiveSolver
from .ss_normalizer import SSNormalizer

class ContraProcessor:
    def __init__(self):
        self.df = None
        self.mapping = {}
        self.complex_data_cache = {}
        self.meta_cache = {}
        self.pattern_cache = {}  # {key_hash: [topo_A, topo_B]} 每pattern最多2个拓扑缓存 

    def load_data(self, file_path, mapping):
        self.mapping = mapping
        # 1. 读取原始数据
        self.df = pd.read_excel(file_path, dtype=str)
        
        date_col = mapping['date']
        voucher_col = mapping['voucher_id']
        summ_col = mapping['summary']
        
        # 2. 生成唯一标识符
        self.df['_uid'] = self.df[date_col].astype(str) + "_" + self.df[voucher_col].astype(str)

        # 3. 只保留2位小数
        self.df['_calc_debit'] = pd.to_numeric(self.df[mapping['debit']], errors='coerce').fillna(0).round(2)
        self.df['_calc_credit'] = pd.to_numeric(self.df[mapping['credit']], errors='coerce').fillna(0).round(2)
        
        # 4. 科目：组合一级科目+明细科目作为唯一标识
        # 格式：一级科目-明细科目（当明细非空且与一级科目不同时）
        subj = self.df[mapping['subject']].astype(str).str.strip()
        self.df['_calc_subj'] = subj
        
        if 'detail_subject' in mapping and mapping['detail_subject'] in self.df.columns:
            detail = self.df[mapping['detail_subject']].astype(str).str.strip()
            # 明细非空且与一级科目不同才拼接（排除空值和NaN转字符串的情况）
            mask = (detail != '') & (detail != 'nan') & (detail != subj)
            self.df.loc[mask, '_calc_subj'] = subj + '-' + detail

        # 5. 缓存元数据
        for uid, group in self.df.groupby('_uid'):
            first_row = group.iloc[0]
            unique_summs = group[summ_col].dropna().unique()
            combined_summ = " | ".join([str(s) for s in unique_summs if str(s).strip()])
            self.meta_cache[uid] = {
                'date': first_row[date_col],
                'voucher_id': first_row[voucher_col],
                'summary': combined_summ
            }

    def process_all(self, stop_event=None):
        """
        v2.1: 先分类，再决定是否SS归一化
        流程：聚合 → 分类 → 1v1/1vN直接跳过 → NvM做SS归一化+穷举
        """
        self.complex_clusters = defaultdict(list)
        self.cluster_samples = {}
        self.complex_data_cache = {}
        self.ss_cache = {}
        self.simple_vouchers = {}   # {uid: classification_dict}

        grouped = self.df.groupby('_uid')
        processed_count = 0
        simple_count = 0

        for uid, group in grouped:
            if stop_event and stop_event.is_set(): break

            # 特殊分录检测（基于原始科目集合）
            unique_subjs = set(group['_calc_subj'])
            if any("本年利润" in s for s in unique_subjs):
                processed_count += 1; simple_count += 1; continue

            if self._is_exchange_gain_loss_entry(unique_subjs):
                processed_count += 1; simple_count += 1; continue

            # === 轻量级分类（只做聚合+负数搬移，不做ID分配） ===
            classification = self._classify_voucher(group)

            if classification['type'] in ('1v1', '1vN'):
                simple_count += 1
                self.simple_vouchers[uid] = classification
            elif classification['type'] == 'NvM':
                ss_result = SSNormalizer.normalize_voucher(group, uid)
                self.ss_cache[uid] = ss_result
                self._add_to_cluster_v2(uid, ss_result)

            processed_count += 1

        return {
            "processed": processed_count,
            "complex_groups": len(self.complex_clusters),
            "simple_solved": simple_count
        }

    def _classify_voucher(self, group):
        """
        轻量级凭证分类：按科目出现在借方列还是贷方列来计数。
        不管金额正负，只看科目在哪个列有非零金额。

        同科目出现在借方和贷方视为两个独立节点（聚合时已按方向分开），
        分类时不额外判定为NvM。
        """
        debit_subjs = set()
        credit_subjs = set()

        for _, row in group.iterrows():
            subj = str(row['_calc_subj']).strip()
            d = float(row['_calc_debit']) if pd.notna(row['_calc_debit']) else 0.0
            c = float(row['_calc_credit']) if pd.notna(row['_calc_credit']) else 0.0
            if abs(d) > 0.001:
                debit_subjs.add(subj)
            if abs(c) > 0.001:
                credit_subjs.add(subj)

        n_debit = len(debit_subjs)
        n_credit = len(credit_subjs)

        if n_debit == 0 and n_credit == 0:
            return {'type': 'invalid'}

        # 全借全贷：全部科目挤在一侧 → NvM，统一走SS归一化
        if n_debit == 0 or n_credit == 0:
            return {'type': 'NvM'}

        if n_debit == 1 and n_credit == 1:
            return {
                'type': '1v1',
                'debit_subj': list(debit_subjs)[0],
                'credit_subj': list(credit_subjs)[0],
            }
        elif n_debit == 1 and n_credit > 1:
            return {
                'type': '1vN',
                'single_side': 'debit',
                'single_subj': list(debit_subjs)[0],
                'multi_subjs': list(credit_subjs),
            }
        elif n_debit > 1 and n_credit == 1:
            return {
                'type': '1vN',
                'single_side': 'credit',
                'single_subj': list(credit_subjs)[0],
                'multi_subjs': list(debit_subjs),
            }
        else:
            return {'type': 'NvM'}

    def _add_to_cluster_v2(self, uid, ss_result):
        """
        v2.0: 使用SS归一化结果进行聚类，pattern_name包含方向信息
        """
        pattern_name = ss_result['pattern_name']
        key_hash = hashlib.md5(pattern_name.encode()).hexdigest()
        
        self.complex_clusters[key_hash].append(uid)
        
        # 缓存SS归一化结果（包含node_map用于后续映射）
        self.complex_data_cache[uid] = ss_result

        if key_hash not in self.cluster_samples:
            self.cluster_samples[key_hash] = {
                "name": pattern_name,
                "debits": dict(ss_result['debit_nodes']),
                "credits": dict(ss_result['credit_nodes']),
                "count": 1,
                "sample_uid": uid,
                "node_map": ss_result['node_map']
            }
        else:
            self.cluster_samples[key_hash]["count"] += 1

    def _extract_connectivity(self, sol, node_map):
        """从解中提取科目级连接关系，与具体金额无关"""
        edges = defaultdict(list)
        driver_keys = set()
        bucket_keys = set()

        for d_id, c_map in sol.items():
            d_node = node_map.get(d_id)
            if not d_node:
                continue
            d_key = (d_node['subject'], d_node['normalized_side'])
            driver_keys.add(d_key)

            for c_id, amt in c_map.items():
                if abs(amt) > 0.001:
                    c_node = node_map.get(c_id)
                    if c_node:
                        c_key = (c_node['subject'], c_node['normalized_side'])
                        bucket_keys.add(c_key)
                        if c_key not in edges[d_key]:
                            edges[d_key].append(c_key)

        return {
            'driver_keys': driver_keys,
            'bucket_keys': bucket_keys,
            'edges': dict(edges)
        }

    def _solve_flow(self, drivers, buckets, edges):
        """已知连接关系和各节点金额，推导拆分金额。叶节点迭代消元 + 余项比例分配"""
        d_remain = {k: round(v, 2) for k, v in drivers.items()}
        b_remain = {k: round(v, 2) for k, v in buckets.items()}
        result = defaultdict(lambda: defaultdict(float))

        changed = True
        while changed:
            changed = False

            for d_id, d_amt in list(d_remain.items()):
                if abs(d_amt) < 0.001:
                    del d_remain[d_id]; continue
                connected = [b for b in edges.get(d_id, [])
                           if b in b_remain and abs(b_remain[b]) > 0.001]
                if len(connected) == 1:
                    b = connected[0]
                    amt = round(min(abs(d_amt), abs(b_remain[b])), 2)
                    result[d_id][b] = amt
                    d_remain[d_id] = round(d_remain[d_id] - amt, 2)
                    b_remain[b] = round(b_remain[b] - amt, 2)
                    changed = True

            for b_id, b_amt in list(b_remain.items()):
                if abs(b_amt) < 0.001:
                    del b_remain[b_id]; continue
                connected = [d for d, bs in edges.items()
                           if b_id in bs and d in d_remain and abs(d_remain[d]) > 0.001]
                if len(connected) == 1:
                    d = connected[0]
                    amt = round(min(abs(d_remain[d]), abs(b_amt)), 2)
                    result[d][b_id] = amt
                    d_remain[d] = round(d_remain[d] - amt, 2)
                    b_remain[b_id] = round(b_remain[b_id] - amt, 2)
                    changed = True

        for d_id, d_amt in list(d_remain.items()):
            if abs(d_amt) < 0.001:
                continue
            connected = [b for b in edges.get(d_id, [])
                       if b in b_remain and abs(b_remain[b]) > 0.001]
            if not connected:
                return None
            total = sum(abs(b_remain[b]) for b in connected)
            if total < 0.001:
                return None
            remaining = d_amt
            for i, b_id in enumerate(connected):
                if i == len(connected) - 1:
                    amt = round(remaining, 2)
                else:
                    amt = round(d_amt * abs(b_remain[b_id]) / total, 2)
                result[d_id][b_id] = amt
                b_remain[b_id] = round(b_remain[b_id] - amt, 2)
                remaining = round(remaining - amt, 2)

        for b_id, b_amt in buckets.items():
            received = round(sum(result[d].get(b_id, 0) for d in result), 2)
            if abs(received - b_amt) > 0.02:
                return None

        return dict(result)

    def _apply_connectivity(self, connectivity, node_map):
        """将缓存的科目级连接关系应用到新凭证的node_map，推导拆分金额"""
        lookup = {}
        for nid, node in node_map.items():
            lookup[(node['subject'], node['normalized_side'])] = nid

        drivers = {}
        for d_key in connectivity['driver_keys']:
            nid = lookup.get(d_key)
            if nid and nid in node_map:
                drivers[nid] = node_map[nid]['normalized_amount']

        buckets = {}
        for b_key in connectivity['bucket_keys']:
            nid = lookup.get(b_key)
            if nid and nid in node_map:
                buckets[nid] = node_map[nid]['normalized_amount']

        edges = {}
        for d_key, b_keys in connectivity['edges'].items():
            d_nid = lookup.get(d_key)
            if not d_nid:
                continue
            edges[d_nid] = []
            for b_key in b_keys:
                b_nid = lookup.get(b_key)
                if b_nid:
                    edges[d_nid].append(b_nid)

        if not drivers or not buckets:
            return None

        return self._solve_flow(drivers, buckets, edges)

    def cache_pattern_solution(self, key_hash, best_sol, node_map):
        """UI预缓存：方案计算阶段已跑穷举，缓存连接关系供方案执行阶段复用"""
        if key_hash not in self.pattern_cache:
            conn = self._extract_connectivity(best_sol, node_map)
            self.pattern_cache[key_hash] = [conn]  # 初始只有拓扑A

    def finalize_report(self, kb, log_callback, user_selections=None, custom_solutions=None):
        """
        生成最终报告 (v2.1 - 分类驱动)
        简单分录(1v1/1vN)直接求解，复杂分录(NvM)走SS归一化+穷举
        """
        solver = ExhaustiveSolver()
        final_rows = []

        grouped = self.df.groupby('_uid', sort=False)
        total_groups = len(grouped)
        processed = 0
        original_cols = [c for c in self.df.columns if not c.startswith('_')]

        for uid, group in grouped:
            processed += 1
            if processed % 100 == 0: log_callback(f"生成进度: {processed}/{total_groups}...")

            # 特殊分录检测
            unique_subjs = set(group['_calc_subj'])
            if any("本年利润" in s for s in unique_subjs):
                self._append_closing_entry(final_rows, group, original_cols); continue

            if self._is_exchange_gain_loss_entry(unique_subjs):
                self._append_exchange_entry(final_rows, group, original_cols); continue

            # === v2.1: 从分类结果或SS缓存获取类型 ===
            classification = self.simple_vouchers.get(uid)
            if classification is not None:
                # 简单分录：直接用分类结果求解
                self._handle_simple_voucher(final_rows, uid, group, original_cols, classification)
            else:
                # NvM：走SS归一化+穷举
                ss_result = self.ss_cache.get(uid)
                if not ss_result:
                    # 可能是无效分录（n_debit==0 或 n_credit==0）
                    quick = self._classify_voucher(group)
                    if quick['type'] == 'invalid':
                        self._append_original_rows(final_rows, group, original_cols, "无效分录")
                    else:
                        self._append_original_rows(final_rows, group, original_cols, "SS归一化失败")
                    continue

                pattern_name = ss_result.get('pattern_name', '')
                user_selection = user_selections.get(pattern_name) if user_selections else None
                custom_solution = custom_solutions.get(pattern_name) if custom_solutions else None
                self._append_complex_rows_v2(final_rows, group, original_cols, uid, kb, solver, user_selection, custom_solution)

        df_final = pd.DataFrame(final_rows)

        # 拆分为对方科目-一级、对方科目-二级
        df_final["对方科目-一级"] = df_final["对方科目"].apply(self._contra_level1)
        df_final["对方科目-二级"] = df_final["对方科目"].apply(self._contra_level2)

        # 列重排 (对方科目两列移到贷方金额后面)
        output_cols = []
        credit_col_name = self.mapping['credit']
        if credit_col_name in original_cols:
            idx = original_cols.index(credit_col_name)
            output_cols = original_cols[:idx+1] + ["对方科目-一级", "对方科目-二级"] + original_cols[idx+1:]
        else:
            output_cols = original_cols + ["对方科目-一级", "对方科目-二级"]

        for c in output_cols:
            if c not in df_final.columns: df_final[c] = ""

        df_final[self.mapping['debit']] = pd.to_numeric(df_final[self.mapping['debit']], errors='coerce').fillna(0)
        df_final[self.mapping['credit']] = pd.to_numeric(df_final[self.mapping['credit']], errors='coerce').fillna(0)

        return df_final[output_cols]

    # --- 辅助函数 ---
    @staticmethod
    def _contra_level1(contra):
        if not isinstance(contra, str): return ''
        return contra.split('-', 1)[0]

    @staticmethod
    def _contra_level2(contra):
        if not isinstance(contra, str): return ''
        parts = contra.split('-', 1)
        return parts[1] if len(parts) > 1 else ''

    def _copy_row_data(self, row, cols): return {c: row[c] for c in cols}
    
    def _create_virtual_row(self, uid, cols, subj, debit_amt, credit_amt, contra):
        meta = self.meta_cache.get(uid, {})
        row = {}
        row[self.mapping['date']] = meta.get('date', '')
        row[self.mapping['voucher_id']] = meta.get('voucher_id', '')
        row[self.mapping['summary']] = meta.get('summary', '')
        # 科目拆分为一级科目和明细科目
        parts = subj.split('-', 1)
        row[self.mapping['subject']] = parts[0]
        if 'detail_subject' in self.mapping:
            row[self.mapping['detail_subject']] = parts[1] if len(parts) > 1 else ''
        for c in cols:
            if c not in row: row[c] = ""
        row[self.mapping['debit']] = debit_amt if debit_amt is not None else 0
        row[self.mapping['credit']] = credit_amt if credit_amt is not None else 0
        row["对方科目"] = contra
        return row

    def _append_original_rows(self, final_rows, group, cols, contra_msg):
        for _, row in group.iterrows():
            new_row = self._copy_row_data(row, cols)
            new_row["对方科目"] = contra_msg
            final_rows.append(new_row)

    def _append_closing_entry(self, final_rows, group, cols):
        for _, row in group.iterrows():
            new_row = self._copy_row_data(row, cols)
            if abs(row['_calc_debit']) > 0.001 or abs(row['_calc_credit']) > 0.001:
                new_row["对方科目"] = "本年利润"
            final_rows.append(new_row)

    def _handle_simple_voucher(self, final_rows, uid, group, cols, classification):
        """
        v2.3: 处理简单分录 (1v1, 1vN)

        1v1: 借方科目行→贷方科目，贷方科目行→借方科目
        1vN: 多方行照抄(contra=单方科目)，单方裂变为虚拟行
        """
        if classification['type'] == '1v1':
            d_subj = classification['debit_subj']
            c_subj = classification['credit_subj']
            for _, row in group.iterrows():
                subj = str(row['_calc_subj']).strip()
                new_row = self._copy_row_data(row, cols)
                if subj == d_subj:
                    new_row["对方科目"] = c_subj
                elif subj == c_subj:
                    new_row["对方科目"] = d_subj
                final_rows.append(new_row)

        elif classification['type'] == '1vN':
            single_side = classification['single_side']
            single_subj = classification['single_subj']
            multi_subjs = set(classification['multi_subjs'])
            multi_amounts = defaultdict(float)

            for _, row in group.iterrows():
                subj = str(row['_calc_subj']).strip()
                new_row = self._copy_row_data(row, cols)

                if subj in multi_subjs:
                    # 科目重叠：单方侧的行跳过（由虚拟行替代）
                    if subj == single_subj:
                        if single_side == 'debit':
                            d = float(row['_calc_debit']) if pd.notna(row['_calc_debit']) else 0.0
                            if abs(d) > 0.001: continue
                        else:
                            c = float(row['_calc_credit']) if pd.notna(row['_calc_credit']) else 0.0
                            if abs(c) > 0.001: continue

                    new_row["对方科目"] = single_subj
                    if single_side == 'debit':
                        amt = float(row['_calc_credit']) if pd.notna(row['_calc_credit']) else 0.0
                    else:
                        amt = float(row['_calc_debit']) if pd.notna(row['_calc_debit']) else 0.0
                    if abs(amt) > 0.001:
                        multi_amounts[subj] += amt
                    final_rows.append(new_row)

            for multi_subj in classification['multi_subjs']:
                amt = round(multi_amounts.get(multi_subj, 0), 2)
                if abs(amt) < 0.001:
                    continue
                if single_side == 'debit':
                    virtual = self._create_virtual_row(uid, cols, single_subj, amt, None, multi_subj)
                else:
                    virtual = self._create_virtual_row(uid, cols, single_subj, None, amt, multi_subj)
                final_rows.append(virtual)

    def _append_complex_rows_v2(self, final_rows, group, cols, uid, kb, solver, user_selection=None, custom_solution=None):
        """
        v2.3: 处理复杂分录 (多借多贷)
        同pattern只首次跑穷举，缓存科目级连接关系；后续凭证直接用缓存+流求解推导金额。
        单连：原始行照抄，只填对方科目
        多连：原始行按金额排序，覆写为拆分金额
        """
        data = self.complex_data_cache.get(uid)
        if not data:
            self._append_original_rows(final_rows, group, cols, "缓存丢失")
            return

        pattern_name = data.get('pattern_name', '')
        node_map = data.get('node_map', {})
        key_hash = hashlib.md5(pattern_name.encode()).hexdigest()

        # 用户自设计方案：直接使用拓扑并缓存为拓扑A，金额由流求解按当前凭证推导
        if custom_solution is not None:
            self.pattern_cache[key_hash] = [custom_solution]
            best_sol = self._apply_connectivity(custom_solution, node_map)
            if best_sol is None:
                # 用户拓扑在当前凭证失败，回退穷举
                best_sol = self._run_exhaustive(
                    data, final_rows, group, cols, kb, solver, pattern_name, node_map
                )
                if best_sol is None:
                    return
        # 用户指定方案：跑穷举，不缓存
        elif user_selection:
            best_sol = self._run_exhaustive(
                data, final_rows, group, cols, kb, solver, pattern_name, node_map, user_selection
            )
            if best_sol is None:
                return
        # 缓存未命中：跑穷举，缓存为拓扑A
        elif key_hash not in self.pattern_cache:
            best_sol = self._run_exhaustive(
                data, final_rows, group, cols, kb, solver, pattern_name, node_map
            )
            if best_sol is None:
                return
            conn = self._extract_connectivity(best_sol, node_map)
            self.pattern_cache[key_hash] = [conn]
        else:
            # 双槽位缓存命中：先试最新拓扑，失败回退到旧拓扑
            best_sol = self._try_cached_topologies(key_hash, node_map)
            if best_sol is None:
                # 两个拓扑都失败 → 回退穷举，新拓扑覆盖旧拓扑的第二个槽位
                best_sol = self._run_exhaustive(
                    data, final_rows, group, cols, kb, solver, pattern_name, node_map
                )
                if best_sol is None:
                    return
                conn = self._extract_connectivity(best_sol, node_map)
                cached = self.pattern_cache[key_hash]
                if len(cached) < 2:
                    cached.append(conn)
                else:
                    cached[1] = conn

        group_rows = list(group.iterrows())

        # 输出借方节点
        self._output_node_side(final_rows, group_rows, cols, best_sol, node_map)

        # 构建反向映射并输出贷方节点
        reverse_map = defaultdict(dict)
        for d_id, c_map in best_sol.items():
            for c_id, amt in c_map.items():
                if abs(amt) > 0.001:
                    reverse_map[c_id][d_id] = amt

        self._output_node_side(final_rows, group_rows, cols, reverse_map, node_map)

    def _run_exhaustive(self, data, final_rows, group, cols, kb, solver,
                        pattern_name, node_map, user_selection=None):
        """穷举计算 + 奥卡姆剃刀筛选最优解"""
        solutions, _ = solver.calculate_combinations(
            data['debit_nodes'], data['credit_nodes'],
            max_solutions=200, timeout=5.0
        )
        if not solutions:
            n_d = len(data['debit_nodes'])
            n_c = len(data['credit_nodes'])
            if (n_d == 1 and n_c == 1) or (n_d == 1 and n_c > 1) or (n_d > 1 and n_c == 1):
                self._handle_normalized_simple(final_rows, group, cols, data)
            else:
                self._append_original_rows(final_rows, group, cols, "需人工分析(无解)")
            return None

        ranked = kb.rank_solutions(solutions, pattern_name, node_map)
        best_sol = ranked[0]
        if user_selection:
            try:
                parts = user_selection.split('-')
                if len(parts) == 2:
                    selected_idx = int(parts[1]) - 1
                    if 0 <= selected_idx < len(ranked):
                        best_sol = ranked[selected_idx]
            except (ValueError, IndexError):
                pass
        return best_sol

    def _try_cached_topologies(self, key_hash, node_map):
        """双槽位缓存：先试最新拓扑(索引1)，失败回退到旧拓扑(索引0)"""
        cached = self.pattern_cache.get(key_hash)
        if not cached:
            return None

        # 先试最新的（列表末尾）
        latest = cached[-1]
        result = self._apply_connectivity(latest, node_map)
        if result is not None:
            return result

        # 回退到旧拓扑（列表首部），且两者不同
        if len(cached) > 1 and cached[0] is not latest:
            result = self._apply_connectivity(cached[0], node_map)
            if result is not None:
                return result

        return None

    def _output_node_side(self, final_rows, group_rows, cols, mapping, node_map):
        """
        输出一侧的所有节点。
        mapping: {node_id: {contra_node_id: amount}}

        单连：原始行照抄（保留原始金额和符号），只填对方科目
        多连：原始行排序后覆写，金额符号与节点原始方向一致
        """
        for node_id, contra_map in mapping.items():
            node = node_map[node_id]
            original_side = node['original_side']
            orig_amt = node['original_amount']
            connections = [(c_id, amt) for c_id, amt in contra_map.items() if abs(amt) > 0.001]
            if not connections:
                continue

            if len(connections) == 1:
                # 单连：所有原始行照抄
                contra_id, _ = connections[0]
                contra_subj = node_map[contra_id]['subject']
                for row_idx, row_amt in zip(node['row_indices'], node['row_amounts']):
                    _, original_row = group_rows[row_idx]
                    new_row = self._copy_row_data(original_row, cols)
                    if original_side == 'debit':
                        new_row[self.mapping['debit']] = row_amt
                        new_row[self.mapping['credit']] = 0
                    else:
                        new_row[self.mapping['credit']] = row_amt
                        new_row[self.mapping['debit']] = 0
                    new_row["对方科目"] = contra_subj
                    final_rows.append(new_row)
            else:
                # 多连：覆写原始行
                row_data = sorted(
                    zip(node['row_indices'], node['row_amounts']),
                    key=lambda x: abs(x[1]), reverse=True
                )
                connections.sort(key=lambda x: abs(x[1]), reverse=True)
                # 归一化后金额全正，但若节点被移动过（orig_amt<0），输出需还原符号
                sign = 1 if orig_amt > 0 else -1

                for conn_idx, (contra_id, split_amt) in enumerate(connections):
                    contra_subj = node_map[contra_id]['subject']
                    template_idx = row_data[conn_idx][0] if conn_idx < len(row_data) else row_data[0][0]
                    _, template_row = group_rows[template_idx]
                    new_row = self._copy_row_data(template_row, cols)

                    signed_amt = round(split_amt * sign, 2)
                    if original_side == 'debit':
                        new_row[self.mapping['debit']] = signed_amt
                        new_row[self.mapping['credit']] = 0
                    else:
                        new_row[self.mapping['credit']] = signed_amt
                        new_row[self.mapping['debit']] = 0

                    new_row["对方科目"] = contra_subj
                    final_rows.append(new_row)

    def _handle_normalized_simple(self, final_rows, group, cols, data):
        """
        SS归一化后的无解回退：归一化后如果是1v1/1vN结构，直接用简单方式处理。
        避免全借全贷等归一化后变简单的条目因穷举超时被标为"无解"。
        """
        node_map = data['node_map']
        debit_ids = list(data['debit_nodes'].keys())
        credit_ids = list(data['credit_nodes'].keys())
        n_d, n_c = len(debit_ids), len(credit_ids)

        debit_subjs = [node_map[nid]['subject'] for nid in debit_ids]
        credit_subjs = [node_map[nid]['subject'] for nid in credit_ids]

        if n_d == 1 and n_c == 1:
            classification = {
                'type': '1v1',
                'debit_subj': debit_subjs[0],
                'credit_subj': credit_subjs[0],
            }
        elif n_d == 1:
            classification = {
                'type': '1vN', 'single_side': 'debit',
                'single_subj': debit_subjs[0], 'multi_subjs': credit_subjs,
            }
        else:
            classification = {
                'type': '1vN', 'single_side': 'credit',
                'single_subj': credit_subjs[0], 'multi_subjs': debit_subjs,
            }

        uid = group['_uid'].iloc[0]
        self._handle_simple_voucher(final_rows, uid, group, cols, classification)

    def _is_exchange_gain_loss_entry(self, unique_subjs):
            """
            判断是否为汇兑损益调汇分录 (High Precision Version)
            策略：
            1. 必须包含 '财务费用'。
            2. 【白名单拦截】：严禁包含经营性损益科目 (管理/销售/研发/成本等)。
            一旦包含，说明是计提类分录，直接返回 False。
            3. 【精准计数】：统计往来/资金科目数量，但排除 '应付职工' 和 '应交税费'。
            """
            # 1. 核心特征检查
            if not any("财务费用" in s for s in unique_subjs):
                return False
                
            # 2. 【白名单拦截】经营性损益一票否决
            # 如果分录里有这些科目，说明主角是经营业务，不是调汇
            operating_keywords = [
                "管理费用", "销售费用", "研发费用", "制造费用", 
                "生产成本", "主营业务成本", "其他业务成本", 
                "主营业务收入", "其他业务收入","营业收入","营业成本"
            ]
            
            for s in unique_subjs:
                if any(op in s for op in operating_keywords):
                    return False # 发现杂质，判定为非调汇

            # 3. 【精准计数】统计纯粹的货币性项目
            # 关键词覆盖：资金、债权、债务
            monetary_keywords = ["应收", "应付", "预收", "预付", "借款", "现金"]
            
            # 排除词：虽然带有"应付/应交"字样，但不属于外币调汇范畴
            exclude_keywords = ["应付职工", "应交税", "应付股","应付利"]
            
            count = 0
            for s in unique_subjs:
                # 是货币性项目
                if any(kw in s for kw in monetary_keywords):
                    # 且不是职工薪酬或税费
                    if not any(ex in s for ex in exclude_keywords):
                        count += 1
            
            # 阈值保持为 3
            return count >= 3

    def _append_exchange_entry(self, final_rows, group, cols):
        """
        强制处理汇兑损益：非财务费用的对方全是财务费用
        """
        for _, row in group.iterrows():
            new_row = self._copy_row_data(row, cols)
            subj = str(row['_calc_subj'])
            
            # 只有金额不为0的行才填对方科目
            if abs(row['_calc_debit']) > 0.001 or abs(row['_calc_credit']) > 0.001:
                if "财务费用" in subj:
                    new_row["对方科目"] = "汇兑损益调整对象"
                else:
                    new_row["对方科目"] = "财务费用"
            
            final_rows.append(new_row)