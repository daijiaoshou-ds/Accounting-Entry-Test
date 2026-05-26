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
            # 明细非空且与一级科目不同才拼接
            mask = (detail != '') & (detail != subj)
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
        self.complex_clusters = defaultdict(list)
        self.cluster_samples = {}
        self.complex_data_cache = {}
        self.ss_cache = {}  # SS归一化结果缓存 {uid: ss_result}
        
        grouped = self.df.groupby('_uid')
        processed_count = 0
        simple_count = 0

        for uid, group in grouped:
            if stop_event and stop_event.is_set(): break
            
            # === SS归一化 (v2.0核心) ===
            ss_result = SSNormalizer.normalize_voucher(group, uid)
            self.ss_cache[uid] = ss_result
            
            # 获取原始科目集合（用于检测特殊分录）
            unique_subjs = set(group['_calc_subj'])
            if "本年利润" in unique_subjs:
                processed_count += 1; simple_count += 1; continue
            
            # 汇兑损益拦截
            if self._is_exchange_gain_loss_entry(unique_subjs):
                processed_count += 1; simple_count += 1; continue

            # 根据SS归一化后的节点数分类
            n_debit = len(ss_result['debit_nodes'])
            n_credit = len(ss_result['credit_nodes'])
            
            if n_debit == 0 or n_credit == 0:
                continue  # 无效分录

            if (n_debit == 1 and n_credit == 1) or \
               (n_debit == 1 and n_credit > 1) or \
               (n_debit > 1 and n_credit == 1):
                simple_count += 1
            else:
                self._add_to_cluster_v2(uid, ss_result)
            
            processed_count += 1

        return {
            "processed": processed_count,
            "complex_groups": len(self.complex_clusters),
            "simple_solved": simple_count
        }

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

    def finalize_report(self, kb, log_callback, user_selections=None):
        """
        生成最终报告 (v2.0 - 基于SS归一化)
        
        Args:
            kb: KnowledgeBase 实例
            log_callback: 日志回调函数
            user_selections: 用户选择的方案映射 {pattern_name: option_id}
                             例如: {"应收账款[借方]、营业收入[贷方]": "3-2"}
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
            
            # 获取SS归一化结果
            ss_result = self.ss_cache.get(uid)
            if not ss_result:
                self._append_original_rows(final_rows, group, original_cols, "SS归一化失败"); continue
            
            # 获取归一化后的节点数
            n_debit = len(ss_result['debit_nodes'])
            n_credit = len(ss_result['credit_nodes'])
            
            if n_debit == 0 or n_credit == 0:
                self._append_original_rows(final_rows, group, original_cols, "无效分录"); continue

            # 获取原始科目集合（用于检测特殊分录）
            unique_subjs = set(group['_calc_subj'])
            if "本年利润" in unique_subjs:
                self._append_closing_entry(final_rows, group, original_cols); continue
            
            # 汇兑损益拦截与处理
            if self._is_exchange_gain_loss_entry(unique_subjs):
                self._append_exchange_entry(final_rows, group, original_cols); continue

            # 根据归一化后的节点数分类处理
            if (n_debit == 1 and n_credit == 1) or \
               (n_debit == 1 and n_credit > 1) or \
               (n_debit > 1 and n_credit == 1):
                # 简单分录：使用SS归一化结果重构
                self._append_simple_rows_v2(final_rows, uid, group, original_cols, ss_result)
            else:
                # 复杂分录：使用SS归一化 + 穷举
                pattern_name = ss_result.get('pattern_name', '')
                user_selection = user_selections.get(pattern_name) if user_selections else None
                self._append_complex_rows_v2(final_rows, group, original_cols, uid, kb, solver, user_selection)

        df_final = pd.DataFrame(final_rows)
        
        # 列重排 (对方科目移到贷方金额后面)
        output_cols = []
        credit_col_name = self.mapping['credit']
        if credit_col_name in original_cols:
            idx = original_cols.index(credit_col_name)
            output_cols = original_cols[:idx+1] + ["对方科目"] + original_cols[idx+1:]
        else:
            output_cols = original_cols + ["对方科目"]
            
        for c in output_cols:
            if c not in df_final.columns: df_final[c] = ""
            
        df_final[self.mapping['debit']] = pd.to_numeric(df_final[self.mapping['debit']], errors='coerce').fillna(0)
        df_final[self.mapping['credit']] = pd.to_numeric(df_final[self.mapping['credit']], errors='coerce').fillna(0)
        
        return df_final[output_cols]

    # --- 辅助函数 ---
    def _copy_row_data(self, row, cols): return {c: row[c] for c in cols}
    
    def _create_virtual_row(self, uid, cols, subj, debit_amt, credit_amt, contra):
        meta = self.meta_cache.get(uid, {})
        row = {}
        row[self.mapping['date']] = meta.get('date', '')
        row[self.mapping['voucher_id']] = meta.get('voucher_id', '')
        row[self.mapping['summary']] = meta.get('summary', '') 
        row[self.mapping['subject']] = subj
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

    def _append_simple_rows(self, final_rows, group, cols, target_c, target_d):
        for _, row in group.iterrows():
            new_row = self._copy_row_data(row, cols)
            if abs(row['_calc_debit']) > 0.001: 
                new_row[self.mapping['debit']] = row['_calc_debit']
                new_row[self.mapping['credit']] = 0
                new_row["对方科目"] = target_c
            elif abs(row['_calc_credit']) > 0.001: 
                new_row[self.mapping['credit']] = row['_calc_credit']
                new_row[self.mapping['debit']] = 0
                new_row["对方科目"] = target_d
            final_rows.append(new_row)

    def _append_1vN_rows_reconstruct(self, final_rows, uid, cols, debits, credits, is_1_debit):
        single_side_subj = debits.iloc[0]['_calc_subj'] if is_1_debit else credits.iloc[0]['_calc_subj']
        multi_side_rows = credits if is_1_debit else debits
        for _, row in multi_side_rows.iterrows():
            row_multi = self._copy_row_data(row, cols)
            
            # 使用计算金额覆盖 (确保符号正确)
            if is_1_debit:
                row_multi[self.mapping['credit']] = row['_calc_credit']
                row_multi[self.mapping['debit']] = 0
            else:
                row_multi[self.mapping['debit']] = row['_calc_debit']
                row_multi[self.mapping['credit']] = 0
                
            row_multi["对方科目"] = single_side_subj
            final_rows.append(row_multi)
            
            amount = row['_calc_credit'] if is_1_debit else row['_calc_debit']
            if is_1_debit:
                row_single = self._create_virtual_row(uid, cols, single_side_subj, amount, None, row['_calc_subj'])
            else:
                row_single = self._create_virtual_row(uid, cols, single_side_subj, None, amount, row['_calc_subj'])
            final_rows.append(row_single)

    def _append_simple_rows_v2(self, final_rows, uid, group, cols, ss_result):
        """
        v2.1: 处理简单分录 (1v1, 1vN)，基于SS归一化结果
        关键改动：支持聚合后的多行节点（row_indices + row_amounts）
        """
        node_map = ss_result['node_map']
        
        debit_node_ids = list(ss_result['debit_nodes'].keys())
        credit_node_ids = list(ss_result['credit_nodes'].keys())
        
        n_debit = len(debit_node_ids)
        n_credit = len(credit_node_ids)
        
        group_rows = list(group.iterrows())
        
        if n_debit == 1 and n_credit == 1:
            # 1借1贷：直接互填
            d_node = node_map[debit_node_ids[0]]
            c_node = node_map[credit_node_ids[0]]
            
            # 借方节点的所有原始行
            for d_row_idx, d_row_amt in zip(d_node['row_indices'], d_node['row_amounts']):
                _, d_row = group_rows[d_row_idx]
                new_row = self._copy_row_data(d_row, cols)
                if d_node['original_side'] == 'debit':
                    new_row[self.mapping['debit']] = d_row_amt
                    new_row[self.mapping['credit']] = 0
                else:
                    new_row[self.mapping['credit']] = d_row_amt
                    new_row[self.mapping['debit']] = 0
                new_row["对方科目"] = c_node['subject']
                final_rows.append(new_row)
            
            # 贷方节点的所有原始行
            for c_row_idx, c_row_amt in zip(c_node['row_indices'], c_node['row_amounts']):
                _, c_row = group_rows[c_row_idx]
                new_row = self._copy_row_data(c_row, cols)
                if c_node['original_side'] == 'debit':
                    new_row[self.mapping['debit']] = c_row_amt
                    new_row[self.mapping['credit']] = 0
                else:
                    new_row[self.mapping['credit']] = c_row_amt
                    new_row[self.mapping['debit']] = 0
                new_row["对方科目"] = d_node['subject']
                final_rows.append(new_row)
            
        elif n_debit == 1 and n_credit > 1:
            # 1借n贷：借方裂变为多个
            d_node = node_map[debit_node_ids[0]]
            
            for c_node_id in credit_node_ids:
                c_node = node_map[c_node_id]
                
                for c_row_idx, c_row_amt in zip(c_node['row_indices'], c_node['row_amounts']):
                    _, c_row = group_rows[c_row_idx]
                    
                    # 贷方行
                    new_row = self._copy_row_data(c_row, cols)
                    if c_node['original_side'] == 'debit':
                        new_row[self.mapping['debit']] = c_row_amt
                        new_row[self.mapping['credit']] = 0
                    else:
                        new_row[self.mapping['credit']] = c_row_amt
                        new_row[self.mapping['debit']] = 0
                    new_row["对方科目"] = d_node['subject']
                    final_rows.append(new_row)
                    
                    # 借方虚拟行（对应这个贷方行的金额）
                    virtual = self._create_virtual_row(
                        uid, cols, d_node['subject'],
                        abs(c_row_amt) if d_node['original_side'] == 'debit' else None,
                        abs(c_row_amt) if d_node['original_side'] == 'credit' else None,
                        c_node['subject']
                    )
                    final_rows.append(virtual)
                
        elif n_debit > 1 and n_credit == 1:
            # n借1贷：贷方裂变为多个
            c_node = node_map[credit_node_ids[0]]
            
            for d_node_id in debit_node_ids:
                d_node = node_map[d_node_id]
                
                for d_row_idx, d_row_amt in zip(d_node['row_indices'], d_node['row_amounts']):
                    _, d_row = group_rows[d_row_idx]
                    
                    # 借方行
                    new_row = self._copy_row_data(d_row, cols)
                    if d_node['original_side'] == 'debit':
                        new_row[self.mapping['debit']] = d_row_amt
                        new_row[self.mapping['credit']] = 0
                    else:
                        new_row[self.mapping['credit']] = d_row_amt
                        new_row[self.mapping['debit']] = 0
                    new_row["对方科目"] = c_node['subject']
                    final_rows.append(new_row)
                    
                    # 贷方虚拟行
                    virtual = self._create_virtual_row(
                        uid, cols, c_node['subject'],
                        abs(d_row_amt) if c_node['original_side'] == 'debit' else None,
                        abs(d_row_amt) if c_node['original_side'] == 'credit' else None,
                        d_node['subject']
                    )
                    final_rows.append(virtual)

    def _append_complex_rows_v2(self, final_rows, group, cols, uid, kb, solver, user_selection=None):
        """
        v2.1: 处理复杂分录 (多借多贷)，基于SS归一化 + 节点ID映射
        关键改动：支持聚合后的多行节点，按比例分配金额到每个原始行
        """
        data = self.complex_data_cache.get(uid)
        if not data:
            self._append_original_rows(final_rows, group, cols, "缓存丢失")
            return

        # 使用归一化后的节点进行穷举
        solutions, _ = solver.calculate_combinations(
            data['debit_nodes'], data['credit_nodes'],
            max_solutions=200, timeout=1.5
        )
        if not solutions:
            self._append_original_rows(final_rows, group, cols, "需人工分析(无解)")
            return

        pattern_name = data.get('pattern_name', '')
        node_map = data.get('node_map', {})
        ranked = kb.rank_solutions(solutions, pattern_name, node_map)
        
        # 使用用户选择的方案（如果有），否则使用默认最高分方案
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
        
        node_map = data['node_map']
        group_rows = list(group.iterrows())
        
        # === 输出驱动方（归一化后的借方节点 → 贷方节点）===
        for d_node_id, c_map in best_sol.items():
            d_node = node_map[d_node_id]
            d_original_side = d_node['original_side']
            d_orig_amt = d_node['original_amount']
            
            total_norm_alloc = sum(abs(v) for v in c_map.values() if abs(v) > 0.001)
            
            for c_node_id, norm_alloc in c_map.items():
                if abs(norm_alloc) < 0.001:
                    continue
                
                c_node = node_map[c_node_id]
                c_subject = c_node['subject']
                
                # 按比例分配给该节点的每个原始行
                abs_total = sum(abs(a) for a in d_node['row_amounts'])
                for d_row_idx, d_row_amt in zip(d_node['row_indices'], d_node['row_amounts']):
                    _, original_row = group_rows[d_row_idx]
                    
                    ratio = abs(d_row_amt) / abs_total if abs_total > 0.001 else 0
                    split_amt = norm_alloc * ratio
                    # 如果原始行金额为负（红字），分配结果也取负
                    if d_row_amt < 0:
                        split_amt = -split_amt
                    
                    new_row = self._copy_row_data(original_row, cols)
                    
                    if d_original_side == 'debit':
                        new_row[self.mapping['debit']] = round(split_amt, 2)
                        new_row[self.mapping['credit']] = 0
                    else:
                        new_row[self.mapping['credit']] = round(split_amt, 2)
                        new_row[self.mapping['debit']] = 0
                    
                    new_row["对方科目"] = c_subject
                    final_rows.append(new_row)
        
        # === 输出被遍历方（反向映射：贷方节点 → 借方节点）===
        reverse_map = defaultdict(dict)
        for d_node_id, c_map in best_sol.items():
            for c_node_id, amt in c_map.items():
                if abs(amt) > 0.001:
                    reverse_map[c_node_id][d_node_id] = amt
        
        for c_node_id, d_map in reverse_map.items():
            c_node = node_map[c_node_id]
            c_original_side = c_node['original_side']
            c_orig_amt = c_node['original_amount']
            
            abs_total = sum(abs(a) for a in c_node['row_amounts'])
            
            for c_row_idx, c_row_amt in zip(c_node['row_indices'], c_node['row_amounts']):
                _, original_row = group_rows[c_row_idx]
                
                for d_node_id, norm_alloc in d_map.items():
                    if abs(norm_alloc) < 0.001:
                        continue
                    
                    d_node = node_map[d_node_id]
                    d_subject = d_node['subject']
                    
                    ratio = abs(c_row_amt) / abs_total if abs_total > 0.001 else 0
                    split_amt = norm_alloc * ratio
                    # 如果原始行金额为负（红字），分配结果也取负
                    if c_row_amt < 0:
                        split_amt = -split_amt
                    
                    new_row = self._copy_row_data(original_row, cols)
                    
                    if c_original_side == 'debit':
                        new_row[self.mapping['debit']] = round(split_amt, 2)
                        new_row[self.mapping['credit']] = 0
                    else:
                        new_row[self.mapping['credit']] = round(split_amt, 2)
                        new_row[self.mapping['debit']] = 0
                    
                    new_row["对方科目"] = d_subject
                    final_rows.append(new_row)

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