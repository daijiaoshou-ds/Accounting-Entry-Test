import pandas as pd
import hashlib
from collections import defaultdict
from .algorithm import ExhaustiveSolver

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
        
        # 4. 科目去除空格
        subj_col = mapping['subject']
        self.df['_calc_subj'] = self.df[subj_col].astype(str).str.strip()

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
        
        grouped = self.df.groupby('_uid')
        processed_count = 0
        simple_count = 0

        for uid, group in grouped:
            if stop_event and stop_event.is_set(): break
            
            clean_group = self._get_cleaned_amounts(group)
            debits = clean_group[clean_group['_calc_debit'].abs() > 0.0001]
            credits = clean_group[clean_group['_calc_credit'].abs() > 0.0001]
            
            unique_subjs = set(clean_group['_calc_subj'])
            if "本年利润" in unique_subjs:
                processed_count += 1; simple_count += 1; continue
            
            # === 【新增代码开始】 ===
            # 汇兑损益拦截
            if self._is_exchange_gain_loss_entry(unique_subjs):
                processed_count += 1; simple_count += 1; continue
            # === 【新增代码结束】 ===

            if debits.empty and credits.empty: continue

            d_types = len(set(debits['_calc_subj']))
            c_types = len(set(credits['_calc_subj']))

            if (d_types == 1 and c_types == 1) or \
               (d_types == 1 and c_types > 1) or \
               (d_types > 1 and c_types == 1):
                simple_count += 1
            else:
                self._add_to_cluster(uid, debits, credits)
            
            processed_count += 1

        return {
            "processed": processed_count,
            "complex_groups": len(self.complex_clusters),
            "simple_solved": simple_count
        }

    def _get_cleaned_amounts(self, group):
        df = group.copy()
        total_d = df['_calc_debit'].abs().sum()
        total_c = df['_calc_credit'].abs().sum()
        
        is_all_debit = (total_c < 0.001) and (total_d > 0.001)
        is_all_credit = (total_d < 0.001) and (total_c > 0.001)

        if is_all_debit:
            mask_d_neg = df['_calc_debit'] < 0
            if mask_d_neg.any():
                df.loc[mask_d_neg, '_calc_credit'] = df.loc[mask_d_neg, '_calc_debit'].abs()
                df.loc[mask_d_neg, '_calc_debit'] = 0
            
        elif is_all_credit:
            mask_c_neg = df['_calc_credit'] < 0
            if mask_c_neg.any():
                df.loc[mask_c_neg, '_calc_debit'] = df.loc[mask_c_neg, '_calc_credit'].abs()
                df.loc[mask_c_neg, '_calc_credit'] = 0
            
        return df

    def _add_to_cluster(self, uid, debits, credits):
        d_subjs = sorted(debits['_calc_subj'].tolist())
        c_subjs = sorted(credits['_calc_subj'].tolist())
        # 生成模式名称
        key_str = "、".join(sorted(list(set(d_subjs) | set(c_subjs))))
        key_hash = hashlib.md5(key_str.encode()).hexdigest()
        
        self.complex_clusters[key_hash].append(uid)
        
        d_dict = defaultdict(float)
        for subj, amt in zip(debits['_calc_subj'], debits['_calc_debit']):
            suffix = "Pos" if amt >= 0 else "Neg"
            d_dict[f"{subj}__{suffix}__D"] += amt
            
        c_dict = defaultdict(float)
        for subj, amt in zip(credits['_calc_subj'], credits['_calc_credit']):
            suffix = "Pos" if amt >= 0 else "Neg"
            c_dict[f"{subj}__{suffix}__C"] += amt
        
        # === 核心修复：聚合后再次 round，防止浮点累积误差 ===
        clean_d_dict = {k: round(v, 2) for k, v in d_dict.items()}
        clean_c_dict = {k: round(v, 2) for k, v in c_dict.items()}

        # 缓存：同时保存 pattern_name，供后续使用
        self.complex_data_cache[uid] = {
            "debits": dict(clean_d_dict), 
            "credits": dict(clean_c_dict),
            "pattern_name": key_str
        }

        if key_hash not in self.cluster_samples:
            self.cluster_samples[key_hash] = {
                "name": key_str, "debits": dict(clean_d_dict), "credits": dict(clean_c_dict),
                "count": 1, "sample_uid": uid
            }
        else:
            self.cluster_samples[key_hash]["count"] += 1

    def finalize_report(self, kb, log_callback, user_selections=None):
        """
        生成最终报告
        
        Args:
            kb: KnowledgeBase 实例
            log_callback: 日志回调函数
            user_selections: 用户选择的方案映射 {pattern_name: option_id}
                             例如: {"应收账款,营业收入": "3-2"}
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
            
            # === 核心修复：生成报告时，必须使用清洗后的 clean_group ===
            clean_group = self._get_cleaned_amounts(group)
            
            debits = clean_group[clean_group['_calc_debit'].abs() > 0.0001]
            credits = clean_group[clean_group['_calc_credit'].abs() > 0.0001]
            
            if debits.empty and credits.empty:
                self._append_original_rows(final_rows, group, original_cols, "无效分录"); continue

            unique_subjs = set(clean_group['_calc_subj'])
            if "本年利润" in unique_subjs:
                self._append_closing_entry(final_rows, group, original_cols); continue
            
            # === 【新增代码开始】 ===
            # 汇兑损益拦截与处理
            if self._is_exchange_gain_loss_entry(unique_subjs):
                self._append_exchange_entry(final_rows, group, original_cols); continue
            # === 【新增代码结束】 ===

            d_types = len(set(debits['_calc_subj']))
            c_types = len(set(credits['_calc_subj']))

            if (d_types == 1 and c_types == 1) or \
               (d_types == 1 and c_types > 1) or \
               (d_types > 1 and c_types == 1):
                if d_types == 1 and c_types == 1:
                    # 传入 clean_group
                    self._append_simple_rows(final_rows, clean_group, original_cols, credits.iloc[0]['_calc_subj'], debits.iloc[0]['_calc_subj'])
                else:
                    # 传入 clean_group (实际上这个函数没用 group 而是用了 debits/credits，但保持一致性)
                    self._append_1vN_rows_reconstruct(final_rows, uid, original_cols, debits, credits, d_types==1)
            else:
                # 传入 clean_group (这是关键，否则负数搬家后的行找不到)
                # 检查是否有用户指定的方案选择
                data = self.complex_data_cache.get(uid, {})
                pattern_name = data.get('pattern_name', '')
                user_selection = user_selections.get(pattern_name) if user_selections else None
                self._append_complex_rows(final_rows, clean_group, original_cols, uid, kb, solver, user_selection)

        df_final = pd.DataFrame(final_rows)
        
        # === 核心修改：列重排 (对方科目移到贷方金额后面) ===
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

    def _append_complex_rows(self, final_rows, group, cols, uid, kb, solver, user_selection=None):
        data = self.complex_data_cache.get(uid)
        if not data:
            self._append_original_rows(final_rows, group, cols, "缓存丢失")
            return

        solutions, _ = solver.calculate_combinations(data['debits'], data['credits'], max_solutions=200, timeout=1.5)
        if not solutions:
            self._append_original_rows(final_rows, group, cols, "需人工分析(无解)")
            return

        # 修复：直接从缓存取 pattern_name，无需重建
        pattern_name = data.get('pattern_name', '')
        
        ranked = kb.rank_solutions(solutions, pattern_name)
        
        # 使用用户选择的方案（如果有），否则使用默认最高分方案
        best_sol = ranked[0]  # 默认使用最高分方案
        if user_selection:
            # user_selection 格式: "pattern_idx-sol_idx" 例如 "3-2"
            try:
                parts = user_selection.split('-')
                if len(parts) == 2:
                    selected_idx = int(parts[1]) - 1  # 转换为0-based索引
                    if 0 <= selected_idx < len(ranked):
                        best_sol = ranked[selected_idx]
            except (ValueError, IndexError):
                pass  # 如果解析失败，使用默认方案 
        
        # 借方重构 (遍历 clean_group)
        for d_key, c_map in best_sol.items():
            d_subj = d_key.split('__')[0]
            target_sign = 1 if "Pos" in d_key else -1
            
            d_rows = []
            for _, row in group.iterrows():
                if row['_calc_subj'] == d_subj:
                    amt = row['_calc_debit']
                    if abs(amt) > 0.001:
                        if (amt >= 0 and target_sign == 1) or (amt < 0 and target_sign == -1):
                            d_rows.append(row)
            
            total_alloc = sum(c_map.values())
            for row in d_rows:
                row_amt = row['_calc_debit']
                if abs(total_alloc) < 0.001: continue
                
                for c_key, target_amt in c_map.items():
                    c_subj = c_key.split('__')[0]
                    if abs(target_amt) > 0.001:
                        ratio = target_amt / total_alloc
                        split_amt = row_amt * ratio
                        
                        new_row = self._copy_row_data(row, cols)
                        new_row[self.mapping['debit']] = split_amt
                        new_row[self.mapping['credit']] = 0
                        new_row["对方科目"] = c_subj
                        final_rows.append(new_row)

        # 贷方重构
        c_side_map = defaultdict(dict)
        for d_key, c_map in best_sol.items():
            for c_key, amt in c_map.items():
                if abs(amt) > 0.001: c_side_map[c_key][d_key] = amt
        
        for c_key, d_map in c_side_map.items():
            c_subj = c_key.split('__')[0]
            target_sign = 1 if "Pos" in c_key else -1
            
            c_rows = []
            for _, row in group.iterrows():
                if row['_calc_subj'] == c_subj:
                    amt = row['_calc_credit']
                    if abs(amt) > 0.001:
                        if (amt >= 0 and target_sign == 1) or (amt < 0 and target_sign == -1):
                            c_rows.append(row)
            
            total_alloc = sum(d_map.values())
            for row in c_rows:
                row_amt = row['_calc_credit']
                if abs(total_alloc) < 0.001: continue
                
                for d_key, alloc_amt in d_map.items():
                    d_subj = d_key.split('__')[0]
                    if abs(alloc_amt) > 0.001:
                        ratio = alloc_amt / total_alloc
                        split_amt = row_amt * ratio
                        
                        new_row = self._copy_row_data(row, cols)
                        new_row[self.mapping['credit']] = split_amt
                        new_row[self.mapping['debit']] = 0
                        new_row["对方科目"] = d_subj
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