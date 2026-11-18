"""
SS归一化模块 v2.2 (Sink-Source Normalization)

策略：先按(科目+原始方向)聚合，再搬移负数。
压缩效果不变（同科目同方向多行→1节点）。

特别处理：聚合后金额归零的科目（正负冲销），对其原始行逐行搬移，
避免节点被丢弃导致无法求解。
"""
from collections import defaultdict
import pandas as pd


class SSNormalizer:

    @staticmethod
    def normalize_voucher(group, uid):
        # === Step 0: 先聚合（与原版相同） ===
        agg = defaultdict(lambda: {'amount': 0.0, 'row_indices': [], 'row_amounts': []})

        for row_idx, (_, row) in enumerate(group.iterrows()):
            subj = str(row['_calc_subj']).strip()
            debit_amt = float(row['_calc_debit']) if pd.notna(row['_calc_debit']) else 0.0
            credit_amt = float(row['_calc_credit']) if pd.notna(row['_calc_credit']) else 0.0

            if abs(debit_amt) > 0.001:
                agg[(subj, 'debit')]['amount'] += debit_amt
                agg[(subj, 'debit')]['row_indices'].append(row_idx)
                agg[(subj, 'debit')]['row_amounts'].append(debit_amt)

            if abs(credit_amt) > 0.001:
                agg[(subj, 'credit')]['amount'] += credit_amt
                agg[(subj, 'credit')]['row_indices'].append(row_idx)
                agg[(subj, 'credit')]['row_amounts'].append(credit_amt)

        # === Step 0.5: 自冲销修复 ===
        # 聚合后归零的科目（如借方+100和借方-100）
        # → 对原始行逐行搬移，按有效方向拆为两个节点
        zero_keys = [(s, d) for (s, d), info in agg.items()
                     if abs(round(info['amount'], 2)) < 0.001]
        for key in zero_keys:
            info = agg.pop(key)
            subj, orig_side = key

            # 按有效方向拆分行
            eff_d_rows = {'indices': [], 'amounts': [], 'eff_amt': 0.0}
            eff_c_rows = {'indices': [], 'amounts': [], 'eff_amt': 0.0}

            for idx, amt in zip(info['row_indices'], info['row_amounts']):
                if orig_side == 'debit':
                    if amt > 0:
                        eff_d_rows['indices'].append(idx)
                        eff_d_rows['amounts'].append(amt)
                        eff_d_rows['eff_amt'] += amt
                    else:
                        eff_c_rows['indices'].append(idx)
                        eff_c_rows['amounts'].append(amt)
                        eff_c_rows['eff_amt'] += abs(amt)
                else:  # credit
                    if amt > 0:
                        eff_c_rows['indices'].append(idx)
                        eff_c_rows['amounts'].append(amt)
                        eff_c_rows['eff_amt'] += amt
                    else:
                        eff_d_rows['indices'].append(idx)
                        eff_d_rows['amounts'].append(amt)
                        eff_d_rows['eff_amt'] += abs(amt)

            # 为每个有效方向创建节点
            for eff_side, rows in [('debit', eff_d_rows), ('credit', eff_c_rows)]:
                if rows['indices']:
                    side_code = 'D' if eff_side == 'debit' else 'C'
                    placeholder_key = f"_self_{subj}_{side_code}"
                    agg[(placeholder_key, eff_side)] = {
                        'amount': round(rows['eff_amt'], 2),
                        'row_indices': rows['indices'],
                        'row_amounts': rows['amounts'],
                        'subject': subj,
                        'original_side': orig_side,
                        'is_self_offset': True
                    }

        # === Step 1: 分配ID + SS归一化 ===
        nodes = []
        node_counter = 0

        for (key, side), info in agg.items():
            if info.get('is_self_offset'):
                subj = info['subject']
                orig_side = info['original_side']
                eff_side = side
                eff_amt = round(info['amount'], 2)
                orig_total = round(sum(info['row_amounts']), 2)
            else:
                subj = key
                orig_side = side
                total_amt = round(info['amount'], 2)
                if abs(total_amt) < 0.001:
                    continue
                orig_total = total_amt
                if side == 'debit' and total_amt < -0.001:
                    eff_side = 'credit'
                    eff_amt = round(abs(total_amt), 2)
                elif side == 'credit' and total_amt < -0.001:
                    eff_side = 'debit'
                    eff_amt = round(abs(total_amt), 2)
                else:
                    eff_side = side
                    eff_amt = round(abs(total_amt), 2)

            side_code = 'D' if eff_side == 'debit' else 'C'
            node_id = f"{uid}_{node_counter}_{side_code}"
            node_counter += 1

            nodes.append({
                'id': node_id,
                'subject': subj,
                'original_side': orig_side,
                'original_amount': orig_total,
                'normalized_side': eff_side,
                'normalized_amount': eff_amt,
                'row_indices': info['row_indices'],
                'row_amounts': info['row_amounts']
            })

        # === Step 2: 借贷结构 ===
        debit_nodes = {}
        credit_nodes = {}
        for node in nodes:
            if node['normalized_side'] == 'debit':
                debit_nodes[node['id']] = node['normalized_amount']
            else:
                credit_nodes[node['id']] = node['normalized_amount']

        # === Step 3: 指纹 ===
        debit_subjects = sorted(set(
            node['subject'] for node in nodes
            if node['normalized_side'] == 'debit'
        ))
        credit_subjects = sorted(set(
            node['subject'] for node in nodes
            if node['normalized_side'] == 'credit'
        ))
        pattern_parts = (
            [f"{s}[借方]" for s in debit_subjects] +
            [f"{s}[贷方]" for s in credit_subjects]
        )
        pattern_name = "、".join(pattern_parts)

        # === Step 4: node_map ===
        node_map = {node['id']: node for node in nodes}

        return {
            'debit_nodes': debit_nodes,
            'credit_nodes': credit_nodes,
            'pattern_name': pattern_name,
            'node_map': node_map
        }

    @staticmethod
    def validate_balance(debit_nodes, credit_nodes):
        total_debit = sum(debit_nodes.values())
        total_credit = sum(credit_nodes.values())
        return abs(total_debit - total_credit) < 0.01
