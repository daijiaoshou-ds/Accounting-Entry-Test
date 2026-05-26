"""
SS归一化模块 v2.1 (Sink-Source Normalization)

核心思想：
1. 按科目名称+原始方向聚合（同一科目同方向的多行合并为一行）
2. 给每个聚合后的条目分配唯一ID，标记原始方向（借方/贷方）
3. 负数金额归一化：借方负数→移到贷方变正数；贷方负数→移到借方变正数
4. 用ID索引做穷举计算，再将结果映射回原始科目+方向
5. 指纹包含方向信息，确保泛化更可靠
"""

from collections import defaultdict
import pandas as pd


class SSNormalizer:
    """
    Sink-Source 归一化器
    
    将任意凭证转换为标准正数结构，支持同科目借贷双方共存的情况。
    关键优化：先按科目名称聚合，大幅减少节点数，防止组合爆炸。
    """
    
    @staticmethod
    def normalize_voucher(group, uid):
        """
        对单个凭证进行SS归一化
        
        流程：
        Step 0: 按科目名称+原始方向聚合（核心优化，防止组合爆炸）
        Step 1: 为每个聚合条目分配唯一ID
        Step 2: SS归一化（负数移到对侧变正数）
        Step 3: 构建归一化后的借贷结构
        Step 4: 生成带方向的pattern_name
        Step 5: 构建node_map
        
        Args:
            group: DataFrame, 一个凭证的多行数据
            uid: 凭证唯一标识
            
        Returns:
            dict: {
                'debit_nodes': {node_id: normalized_amount},  # 归一化后借方节点（全正数）
                'credit_nodes': {node_id: normalized_amount}, # 归一化后贷方节点（全正数）
                'pattern_name': str,  # 带方向的指纹
                'node_map': {        # ID → 原始信息映射
                    node_id: {
                        'subject': str,
                        'original_side': 'debit'|'credit',
                        'original_amount': float,
                        'normalized_side': 'debit'|'credit',
                        'normalized_amount': float,
                        'row_indices': [int, ...]  # 聚合的所有原始行索引
                    }
                }
            }
        """
        # === Step 0: 按科目名称+原始方向聚合 ===
        # 同一凭证中，相同科目+相同方向的多行金额合并为一行
        # 这是防止组合爆炸的关键：将 N 个同类行 → 1 个聚合节点
        agg = defaultdict(lambda: {'amount': 0.0, 'row_indices': [], 'row_amounts': []})
        
        for row_idx, (_, row) in enumerate(group.iterrows()):
            subj = str(row['_calc_subj']).strip()
            debit_amt = float(row['_calc_debit']) if pd.notna(row['_calc_debit']) else 0.0
            credit_amt = float(row['_calc_credit']) if pd.notna(row['_calc_credit']) else 0.0
            
            if abs(debit_amt) > 0.001:
                key = (subj, 'debit')
                agg[key]['amount'] += debit_amt
                agg[key]['row_indices'].append(row_idx)
                agg[key]['row_amounts'].append(debit_amt)
            
            if abs(credit_amt) > 0.001:
                key = (subj, 'credit')
                agg[key]['amount'] += credit_amt
                agg[key]['row_indices'].append(row_idx)
                agg[key]['row_amounts'].append(credit_amt)
        
        # === Step 1: 为每个聚合条目分配ID ===
        nodes = []
        node_counter = 0
        
        for (subj, side), info in agg.items():
            total_amt = round(info['amount'], 2)
            if abs(total_amt) < 0.001:
                continue
            
            side_code = 'D' if side == 'debit' else 'C'
            node_id = f"{uid}_{node_counter}_{side_code}"
            node_counter += 1
            
            nodes.append({
                'id': node_id,
                'subject': subj,
                'original_side': side,
                'original_amount': total_amt,
                'row_indices': info['row_indices'],
                'row_amounts': info['row_amounts']
            })
        
        # === Step 2: SS归一化 ===
        # 规则：负数金额移到对侧变正数，正数保持原位
        for node in nodes:
            amt = node['original_amount']
            side = node['original_side']
            
            if side == 'debit' and amt < -0.001:
                # 借方负数 → 移到贷方
                node['normalized_side'] = 'credit'
                node['normalized_amount'] = round(abs(amt), 2)
            elif side == 'credit' and amt < -0.001:
                # 贷方负数 → 移到借方
                node['normalized_side'] = 'debit'
                node['normalized_amount'] = round(abs(amt), 2)
            else:
                # 正数保持原位
                node['normalized_side'] = side
                node['normalized_amount'] = round(abs(amt), 2)
        
        # === Step 3: 构建归一化后的借贷结构 ===
        debit_nodes = {}
        credit_nodes = {}
        
        for node in nodes:
            if node['normalized_side'] == 'debit':
                debit_nodes[node['id']] = node['normalized_amount']
            else:
                credit_nodes[node['id']] = node['normalized_amount']
        
        # === Step 4: 生成带方向的pattern_name ===
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
        
        # === Step 5: 构建node_map ===
        node_map = {node['id']: node for node in nodes}
        
        return {
            'debit_nodes': debit_nodes,
            'credit_nodes': credit_nodes,
            'pattern_name': pattern_name,
            'node_map': node_map
        }
    
    @staticmethod
    def validate_balance(debit_nodes, credit_nodes):
        """
        验证归一化后的借贷是否平衡
        """
        total_debit = sum(debit_nodes.values())
        total_credit = sum(credit_nodes.values())
        return abs(total_debit - total_credit) < 0.01
