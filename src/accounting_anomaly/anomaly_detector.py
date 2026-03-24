"""
异常检测模块
基于群距离和频次分析识别异常会计凭证
"""
import pandas as pd
import numpy as np
import math
from collections import defaultdict
from itertools import combinations
from itertools import combinations


class AnomalyDetector:
    """
    异常检测器
    """
    
    def __init__(self):
        self.distance_matrix = {}
        self.feature_freq = {}
        self.group_voucher_count = {}
        self.cross_counts = defaultdict(int)
        self.inner_threshold = 0.01
        
    def build_group_distance_matrix(self, df, group_col='primary_group', 
                                    feature_col='voucher_feature',
                                    voucher_col='voucher_unique_id',
                                    involved_col='involved_groups'):
        """
        构建群距离矩阵（严格按用户定义的公式）
        
        关键修复：
        1. group_voucher_count 基于 involved_groups 统计（不是 primary_group）
        2. 读取 involved_groups 判断跨群
        """
        # 统计各群凭证数量（用于显示）：只按 primary_group 统计，避免重复计数
        self.group_voucher_count_display = df.groupby(group_col)[voucher_col].nunique().to_dict()
        
        # 统计各群凭证数量（用于距离计算）：基于 involved_groups，包含跨群凭证
        self.group_voucher_count = defaultdict(int)
        for voucher_id, group in df.groupby(voucher_col):
            if involved_col in group.columns:
                involved_str = group[involved_col].iloc[0]
                groups_list = involved_str.split('、') if '、' in involved_str else [involved_str]
                for g in groups_list:
                    self.group_voucher_count[g] += 1
            else:
                primary = group[group_col].iloc[0]
                self.group_voucher_count[primary] += 1
        
        print(f"[Debug] 各群凭证数（显示用）: {dict(self.group_voucher_count_display)}")
        print(f"[Debug] 各群凭证数（距离计算用，含跨群）: {dict(self.group_voucher_count)}")
        print(f"[Debug] 总凭证数（去重）: {df[voucher_col].nunique()}")
        print(f"[Debug] 总行数: {len(df)}")
        
        # 统计跨群凭证：按 involved_groups 读取所有涉及的群
        self.cross_counts = defaultdict(int)
        
        for voucher_id, group in df.groupby(voucher_col):
            # 关键修复：读取 involved_groups 字段，而不是 primary_group
            if involved_col in group.columns:
                involved_str = group[involved_col].iloc[0]
                # 解析涉及的群列表（格式："群A、群B"）
                groups_in_voucher = tuple(sorted(involved_str.split('、')))
            else:
                # 如果没有 involved_groups，退回到使用 primary_group
                groups_in_voucher = tuple(sorted(group[group_col].unique()))
            
            k = len(groups_in_voucher)
            
            if k >= 2:
                self.cross_counts[groups_in_voucher] += 1
                if len(self.cross_counts) <= 5:  # 只打印前几个
                    print(f"[Debug] 跨群凭证 {voucher_id}: {groups_in_voucher}")
        
        print(f"[Debug] 跨群组合数: {len(self.cross_counts)}")
        print(f"[Debug] 跨群统计详情: {dict(self.cross_counts)}")
        # 计算每个群从跨群凭证中获得的额外计数
        extra_counts = defaultdict(int)
        for groups_tuple, count in self.cross_counts.items():
            if len(groups_tuple) >= 2:
                for g in groups_tuple:
                    extra_counts[g] += count
        print(f"[Debug] 各群从跨群获得的额外计数: {dict(extra_counts)}")
        
        # 获取所有业务群
        all_groups = list(self.group_voucher_count.keys())
        
        # 计算群距离矩阵
        self.distance_matrix = {}
        
        for g1 in all_groups:
            self.distance_matrix[g1] = {}
            for g2 in all_groups:
                if g1 == g2:
                    self.distance_matrix[g1][g2] = 0.0
                else:
                    distance = self._calculate_distance(g1, g2)
                    self.distance_matrix[g1][g2] = distance
        
        matrix_df = pd.DataFrame(self.distance_matrix).fillna(0)
        
        print(f"[Debug] 距离矩阵:\n{matrix_df}")
        
        return matrix_df
    
    def _calculate_distance(self, group_a, group_b):
        """
        计算两个群之间的距离（按用户定义的公式）
        
        公式:
        对于每个包含A和B的组合S（k=2,3,4...）:
            x_S = (count_S + 1) / (sum_{g in S} count_g + 2)
        X = Σ x_S  # 所有包含A和B的组合的共现率之和
        D = min(100, 1/X - 1)
        """
        total_x = 0.0
        debug_info = []
        
        for groups_tuple, count in self.cross_counts.items():
            if group_a in groups_tuple and group_b in groups_tuple:
                # 分母 = 该组合涉及的所有群的凭证数之和 + 2
                denominator = 2
                group_counts_str = []
                for g in groups_tuple:
                    cnt = self.group_voucher_count.get(g, 0)
                    denominator += cnt
                    group_counts_str.append(f"{g}={cnt}")
                
                x_k = (count + 1) / denominator
                total_x += x_k
                debug_info.append(f"  组合{groups_tuple}: count={count}, 群=[{', '.join(group_counts_str)}], 分母={denominator}, x={x_k:.4f}")
        
        # 打印调试信息
        if debug_info and (group_a == '研发活动群' and group_b == '生产活动群' or group_a == '生产活动群' and group_b == '研发活动群'):
            print(f"[Debug] 距离计算 {group_a}-{group_b}:")
            for line in debug_info:
                print(line)
            print(f"  总X={total_x:.4f}, 距离={round(min(100.0, (1.0/total_x - 1) if total_x > 0 else 100.0), 2) if total_x > 0 else 100.0}")
        
        if total_x == 0:
            return 100.0  # 最大距离
        
        distance = (1.0 / total_x) - 1
        # 距离上限100，保留2位小数
        return round(min(100.0, distance), 2)
    
    def calculate_feature_frequency(self, df, group_col='primary_group',
                                    feature_col='voucher_feature',
                                    voucher_col='voucher_unique_id'):
        """计算特征频次"""
        self.feature_freq = defaultdict(dict)
        
        for group in df[group_col].unique():
            group_data = df[df[group_col] == group]
            feature_counts = group_data.groupby(feature_col)[voucher_col].nunique()
            total_vouchers = group_data[voucher_col].nunique()
            
            for feature, count in feature_counts.items():
                freq = count / total_vouchers if total_vouchers > 0 else 0
                self.feature_freq[group][feature] = {
                    'count': count,
                    'total': total_vouchers,
                    'frequency': freq
                }
        
        return self.feature_freq
    
    def calculate_cross_group_score(self, df, group_col='primary_group',
                                    voucher_col='voucher_unique_id',
                                    involved_col='involved_groups'):
        """计算跨模块得分（读取 involved_groups）"""
        scores = {}
        
        for voucher_id, group in df.groupby(voucher_col):
            # 关键修复：读取 involved_groups 而不是 primary_group
            if involved_col in group.columns:
                involved_str = group[involved_col].iloc[0]
                groups_in_voucher = tuple(sorted(involved_str.split('、')))
            else:
                groups_in_voucher = tuple(sorted(group[group_col].unique()))
            
            if len(groups_in_voucher) <= 1:
                scores[voucher_id] = 0.0
                continue
            
            # 其他群惩罚
            penalty = 0.0
            if '其他群' in groups_in_voucher:
                non_other = [g for g in groups_in_voucher if g != '其他群']
                if len(non_other) == 0:
                    penalty = 20.0
                else:
                    penalty = 10.0
            
            # 计算群对距离之和
            cross_score = 0.0
            for g1, g2 in combinations(groups_in_voucher, 2):
                if g1 != '其他群' and g2 != '其他群':
                    distance = self.distance_matrix.get(g1, {}).get(g2, 0)
                    cross_score += distance
            
            scores[voucher_id] = cross_score + penalty
        
        return scores
    
    def calculate_group_hhi(self, group_name):
        """
        计算指定群的赫芬达尔指数（HHI）
        HHI = Σ(frequency_i)^2
        
        HHI越接近1，表示群越集中（如采购群90%都是原材料-应付账款）
        HHI越接近0，表示群越分散（如资金群各种业务均匀分布）
        """
        if group_name not in self.feature_freq or not self.feature_freq[group_name]:
            return 0.0
        
        freqs = [info['frequency'] for info in self.feature_freq[group_name].values()]
        
        if not freqs:
            return 0.0
        
        # 直接平方和，不再二次归一化（因为frequency已经是占比）
        hhi = sum(f**2 for f in freqs)
        
        return hhi

    def calculate_inner_group_score(self, df, group_col='primary_group',
                                    feature_col='voucher_feature',
                                    voucher_col='voucher_unique_id'):
        """
        计算模块内异常得分（Tsallis-HHI自适应公式）
        """
        scores = {}
        
        # 预计算所有群的HHI
        group_hhi_cache = {}
        for group in df[group_col].unique():
            group_hhi_cache[group] = self.calculate_group_hhi(group)
            print(f"[Debug] 群 {group} 的HHI: {group_hhi_cache[group]:.4f}")
        
        for voucher_id, group in df.groupby(voucher_col):
            primary_group = group[group_col].iloc[0]
            feature = group[feature_col].iloc[0]
            
            freq_info = self.feature_freq.get(primary_group, {}).get(feature, {})
            freq = freq_info.get('frequency', 0)
            
            # 获取该群的HHI
            hhi = group_hhi_cache.get(primary_group, 0.0)
            
            # 其他群惩罚（保持原有逻辑）
            penalty = 0.0
            if primary_group == '其他群':
                penalty = 20.0
            elif 'involved_groups' in group.columns:
                if '其他群' in str(group['involved_groups'].iloc[0]):
                    penalty = 10.0
            
            # Tsallis-HHI自适应评分
            if freq == 0:
                score = 100.0
            else:
                # 处理HHI接近1的极限情况
                if hhi >= 0.9999:
                    score = -math.log(freq)
                else:
                    exponent = 1 - hhi
                    numerator = (freq ** (-exponent)) - 1
                    denominator = exponent
                    
                    score = numerator / denominator
                
                score = min(100.0, score)
            
            scores[voucher_id] = score + penalty
        
        return scores
    
    def detect_anomalies(self, df, group_col='primary_group',
                        feature_col='voucher_feature',
                        voucher_col='voucher_unique_id'):
        """主检测流程"""
        # 1. 构建群距离矩阵（传入 involved_groups 列名）
        self.build_group_distance_matrix(df, group_col, feature_col, voucher_col, 
                                         involved_col='involved_groups')
        
        # 2. 计算特征频次
        self.calculate_feature_frequency(df, group_col, feature_col, voucher_col)
        
        # 3. 计算得分（传入 involved_groups 列名）
        cross_scores = self.calculate_cross_group_score(df, group_col, voucher_col, 'involved_groups')
        inner_scores = self.calculate_inner_group_score(df, group_col, feature_col, voucher_col)
        
        # 4. 合并结果
        df_result = df.copy()
        df_result['cross_score'] = df_result[voucher_col].map(cross_scores)
        df_result['inner_score'] = df_result[voucher_col].map(inner_scores)
        df_result['total_score'] = df_result['cross_score'] + df_result['inner_score']
        
        def get_anomaly_type(row):
            if row['cross_score'] > 0 and row['inner_score'] > 0:
                return '跨模块+内部异常'
            elif row['cross_score'] > 0:
                return '跨模块异常'
            elif row['inner_score'] > 0:
                return '模块内异常'
            else:
                return '正常'
        
        df_result['anomaly_type'] = df_result.apply(get_anomaly_type, axis=1)
        df_result['risk_level'] = df_result['total_score'].apply(self._get_risk_level)
        
        return df_result
    
    def _get_risk_level(self, score):
        """风险等级判断"""
        if score >= 80:
            return '高风险 🔴'
        elif score >= 30:
            return '中风险 🟠'
        elif score >= 10:
            return '中低风险 🟡'
        else:
            return '正常 🟢'
    
    def get_distance_matrix_dataframe(self):
        """获取距离矩阵"""
        if not self.distance_matrix:
            return None
        return pd.DataFrame(self.distance_matrix).fillna(0)
    
    def get_anomaly_summary(self, result_df, voucher_col='voucher_unique_id'):
        """获取统计摘要"""
        voucher_df = result_df.groupby(voucher_col).agg({
            'total_score': 'first',
            'risk_level': 'first',
            'anomaly_type': 'first',
            'is_cross_group': 'first' if 'is_cross_group' in result_df.columns else lambda x: False
        }).reset_index()
        
        total = len(voucher_df)
        
        cross_count = 0
        if 'is_cross_group' in voucher_df.columns:
            cross_count = len(voucher_df[voucher_df['is_cross_group'] == True])
        
        return {
            'total_vouchers': total,
            'high_risk': len(voucher_df[voucher_df['risk_level'].str.contains('高风险')]),
            'medium_risk': len(voucher_df[voucher_df['risk_level'].str.contains('中风险')]),
            'low_risk': len(voucher_df[voucher_df['risk_level'].str.contains('低风险')]),
            'normal': len(voucher_df[voucher_df['risk_level'].str.contains('正常')]),
            'cross_group': cross_count,
            'avg_score': voucher_df['total_score'].mean(),
            'max_score': voucher_df['total_score'].max()
        }
