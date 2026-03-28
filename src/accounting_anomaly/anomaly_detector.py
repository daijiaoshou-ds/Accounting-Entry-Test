"""
异常检测模块
基于群距离和频次分析识别异常会计凭证
"""
import pandas as pd
import numpy as np
import math
from collections import defaultdict, Counter
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
        # 存储各群的科目连接频次
        self.group_connection_freq = defaultdict(Counter)
        self.group_hhi_cache = {}
        
    def build_group_distance_matrix(self, df, group_col='primary_group', 
                                    feature_col='voucher_feature',
                                    voucher_col='voucher_unique_id',
                                    involved_col='involved_groups'):
        """
        构建群距离矩阵（使用新连乘公式）
        
        新公式：
        X_ij = 1 - ∏(k=2 to n) ∏(S in C_k) (1 - (n_S + 1) / (Σ|M_u| + 2))
        D = min(1/X_ij - 1, 100)
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
            if involved_col in group.columns:
                involved_str = group[involved_col].iloc[0]
                groups_in_voucher = tuple(sorted(involved_str.split('、')))
            else:
                groups_in_voucher = tuple(sorted(group[group_col].unique()))
            
            k = len(groups_in_voucher)
            
            if k >= 2:
                self.cross_counts[groups_in_voucher] += 1
        
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
                    distance = self._calculate_distance_new(g1, g2)
                    self.distance_matrix[g1][g2] = distance
        
        matrix_df = pd.DataFrame(self.distance_matrix).fillna(0)
        
        print(f"[Debug] 距离矩阵:\n{matrix_df}")
        
        return matrix_df
    
    def _calculate_distance_new(self, group_a, group_b):
        """
        计算两个群之间的距离（新连乘公式）
        
        公式:
        X_ij = 1 - ∏(k=2 to n) ∏(S in C_k) (1 - (n_S + 1) / (Σ|M_u| + 2))
        D = min(1/X_ij - 1, 100)
        """
        # 按阶数 k 分组存储组合
        cross_by_order = defaultdict(list)
        
        for groups_tuple, count in self.cross_counts.items():
            if group_a in groups_tuple and group_b in groups_tuple:
                k = len(groups_tuple)
                cross_by_order[k].append((groups_tuple, count))
        
        if not cross_by_order:
            return 100.0  # 最大距离
        
        # 计算连乘：∏(1 - p_S)
        product = 1.0
        debug_details = []
        
        for k in sorted(cross_by_order.keys()):
            for groups_tuple, count in cross_by_order[k]:
                # 分母 = 该组合涉及的所有群的凭证数之和 + 2
                denominator = 2
                group_counts_sum = 0
                for g in groups_tuple:
                    cnt = self.group_voucher_count.get(g, 0)
                    group_counts_sum += cnt
                denominator += group_counts_sum
                
                # p_S = (n_S + 1) / (Σ|M_u| + 2)
                p_s = (count + 1) / denominator
                
                # 连乘 (1 - p_S)
                product *= (1 - p_s)
                
                debug_details.append({
                    'groups': groups_tuple,
                    'count': count,
                    'denominator': denominator,
                    'p_s': p_s,
                    '1-p_s': 1 - p_s
                })
        
        # X_ij = 1 - 连乘结果
        x_ij = 1 - product
        
        # 调试输出
        if (group_a == '研发活动群' and group_b == '生产活动群') or \
           (group_a == '生产活动群' and group_b == '研发活动群'):
            print(f"[Debug] 距离计算 {group_a}-{group_b} (新公式):")
            for d in debug_details:
                print(f"  组合{d['groups']}: n_S={d['count']}, 分母={d['denominator']}, "
                      f"p_S={d['p_s']:.4f}, 1-p_S={d['1-p_s']:.4f}")
            print(f"  连乘结果={product:.4f}, X_ij={x_ij:.4f}, "
                  f"距离={round(min(100.0, (1.0/x_ij - 1) if x_ij > 0 else 100.0), 2)}")
        
        if x_ij <= 0:
            return 100.0  # 最大距离
        
        distance = (1.0 / x_ij) - 1
        return round(min(100.0, distance), 2)
    
    def _build_subject_connections(self, df, group_col='primary_group',
                                   subject_col='first_level_subject',
                                   counter_col='counter_subject',
                                   voucher_col='voucher_unique_id',
                                   credit_col='credit',
                                   involved_col='involved_groups'):
        """
        构建各群的科目连接频次统计
        
        统计所有 primary_group 属于该群的凭证的连接
        包括跨群凭证，因为它们的连接也对群的HHI有贡献
        
        连接定义：贷方科目 -> 对方科目
        """
        self.group_connection_freq = defaultdict(Counter)
        
        for group_name in df[group_col].unique():
            # 选择所有 primary_group == group_name 的凭证（包括跨群的）
            group_df = df[df[group_col] == group_name]
            
            # 只取贷方行（有贷方金额的行）
            credit_df = group_df[group_df[credit_col] > 0]
            
            # 按凭证统计连接 - 使用set去重，确保每个凭证的连接只算一次
            for voucher_id, voucher_group in credit_df.groupby(voucher_col):
                # 收集该凭证的所有连接（去重）
                voucher_connections = set()
                for _, row in voucher_group.iterrows():
                    from_subject = row[subject_col]  # 贷方科目
                    to_subjects = str(row[counter_col]).split('、') if pd.notna(row[counter_col]) else []
                    
                    for to_subject in to_subjects:
                        if to_subject and to_subject.strip():
                            connection = (from_subject.strip(), to_subject.strip())
                            voucher_connections.add(connection)
                
                # 将该凭证的连接加入统计
                for connection in voucher_connections:
                    self.group_connection_freq[group_name][connection] += 1
        
        # 计算每个群的HHI
        self._calculate_all_groups_hhi()
    
    def _calculate_all_groups_hhi(self):
        """
        计算所有群的赫芬达尔指数(HHI)
        HHI = Σ(f_i)^2, 其中 f_i = n_i / N
        """
        self.group_hhi_cache = {}
        
        for group_name, connections in self.group_connection_freq.items():
            if not connections:
                self.group_hhi_cache[group_name] = 0.0
                continue
            
            # 总连接数
            total_connections = sum(connections.values())
            if total_connections == 0:
                self.group_hhi_cache[group_name] = 0.0
                continue
            
            # 计算HHI
            hhi = 0.0
            for connection, count in connections.items():
                f = count / total_connections
                hhi += f ** 2
            
            self.group_hhi_cache[group_name] = hhi
            print(f"[Debug] 群 {group_name} 的HHI: {hhi:.4f}, 总连接数: {total_connections}")
    
    def calculate_inner_group_score(self, df, group_col='primary_group',
                                    feature_col='voucher_feature',
                                    voucher_col='voucher_unique_id',
                                    subject_col='first_level_subject',
                                    counter_col='counter_subject',
                                    credit_col='credit',
                                    involved_col='involved_groups'):
        """
        计算模块内异常得分（新HHI自适应公式）
        
        公式:
        HHI = Σ(f_i)^2
        f = n/N (连接频次)
        S(f) = (f^-(1-HHI) - 1) / (1-HHI)  [HHI < 1]
        S(f) = -ln(f)                       [HHI = 1]
        Score_inner = min(100, S(f))
        """
        # 首先构建科目连接统计（只统计非跨群凭证）
        self._build_subject_connections(df, group_col, subject_col, counter_col, voucher_col, credit_col, involved_col)
        
        scores = {}
        
        for voucher_id, group in df.groupby(voucher_col):
            primary_group = group[group_col].iloc[0]
            
            # 获取该群的HHI
            hhi = self.group_hhi_cache.get(primary_group, 0.0)
            
            # 其他群惩罚
            penalty = 0.0
            if primary_group == '其他群':
                penalty = 20.0
            elif 'involved_groups' in group.columns:
                if '其他群' in str(group['involved_groups'].iloc[0]):
                    penalty = 10.0
            
            # 计算该凭证的连接得分（取所有连接中的最高异常分）
            connection_scores = []
            
            # 获取该凭证的贷方科目和对应连接
            credit_rows = group[group[credit_col] > 0]
            for _, row in credit_rows.iterrows():
                from_subject = row[subject_col]
                to_subjects = str(row[counter_col]).split('、') if pd.notna(row[counter_col]) else []
                
                for to_subject in to_subjects:
                    if not to_subject or not to_subject.strip():
                        continue
                    
                    connection = (from_subject.strip(), to_subject.strip())
                    count = self.group_connection_freq[primary_group].get(connection, 0)
                    total = sum(self.group_connection_freq[primary_group].values())
                    
                    if count == 0 or total == 0:
                        connection_scores.append(100.0)
                    else:
                        f = count / total
                        
                        # 新公式
                        if hhi >= 0.9999:
                            # HHI = 1 的情况
                            score = -math.log(f)
                        else:
                            # HHI < 1 的情况
                            exponent = 1 - hhi
                            numerator = (f ** (-exponent)) - 1
                            denominator = exponent
                            score = numerator / denominator
                        
                        score = min(100.0, score)
                        connection_scores.append(score)
            
            # 取该凭证所有连接中的最高异常分
            if connection_scores:
                max_score = max(connection_scores)
            else:
                # 如果没有连接（比如只有一行且没有对方科目），使用特征频次计算
                feature = group[feature_col].iloc[0]
                freq_info = self.feature_freq.get(primary_group, {}).get(feature, {})
                freq = freq_info.get('frequency', 0)
                
                if freq == 0:
                    max_score = 100.0
                elif freq < self.inner_threshold:
                    # 使用简化公式
                    if hhi >= 0.9999:
                        max_score = -math.log(freq)
                    else:
                        exponent = 1 - hhi
                        numerator = (freq ** (-exponent)) - 1
                        denominator = exponent
                        max_score = min(100.0, numerator / denominator)
                else:
                    max_score = 0.0
            
            scores[voucher_id] = round(max_score + penalty, 2)
        
        return scores
    
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
    
    def calculate_materiality_levels(self, df, group_col='primary_group',
                                     voucher_col='voucher_unique_id',
                                     amount_col='voucher_abs_amount',
                                     pareto_percent=0.95):
        """
        使用帕累托累计法计算各群的重要性水平
        
        逻辑：
        1. 在每个独立的业务群内，把凭证按金额从大到小排序
        2. 开始累加金额，直到累加的金额达到了该群总交易额的 pareto_percent%
        3. 此时碰到的最后一张凭证的金额，就是这个群的重要性水平
        
        参数:
            pareto_percent: 帕累托百分比，默认0.95（95%）
        
        返回:
            dict: {group_name: materiality_level}
        """
        materiality_levels = {}
        
        # 按凭证去重，获取每个凭证的群和金额
        voucher_df = df.groupby(voucher_col).agg({
            group_col: 'first',
            amount_col: 'first'
        }).reset_index()
        
        # 按群计算重要性水平
        for group_name in voucher_df[group_col].unique():
            group_vouchers = voucher_df[voucher_df[group_col] == group_name]
            
            if len(group_vouchers) == 0:
                materiality_levels[group_name] = 0.0
                continue
            
            # 按金额从大到小排序
            group_vouchers = group_vouchers.sort_values(amount_col, ascending=False)
            
            # 计算总金额
            total_amount = group_vouchers[amount_col].sum()
            
            if total_amount == 0:
                materiality_levels[group_name] = 0.0
                continue
            
            # 累加金额直到达到阈值
            cumsum_amount = 0.0
            materiality_level = 0.0
            
            for _, row in group_vouchers.iterrows():
                cumsum_amount += row[amount_col]
                materiality_level = row[amount_col]
                
                if cumsum_amount >= total_amount * pareto_percent:
                    break
            
            materiality_levels[group_name] = materiality_level
            print(f"[Debug] 群 {group_name} 的重要性水平: {materiality_level:,.2f} (帕累托: {pareto_percent*100}%)")
        
        self.materiality_levels = materiality_levels
        return materiality_levels
    
    def calculate_alpha_coefficient(self, amount, materiality_level, k=5.36):
        """
        使用激活函数计算α系数
        
        公式:
        α = 1, 当 Amount >= M_base
        α = e^(k * (Amount/M_base - 1)), 当 Amount < M_base
        
        参数:
            amount: 凭证金额（借方绝对值化后）
            materiality_level: 重要性水平基准值
            k: 系数，默认5.36
        
        返回:
            float: α系数，范围(0, 1]
        """
        if materiality_level <= 0:
            return 1.0
        
        if amount >= materiality_level:
            return 1.0
        else:
            # Amount < M_base
            exponent = k * (amount / materiality_level - 1)
            alpha = math.exp(exponent)
            return min(1.0, max(0.0, alpha))
    
    def detect_anomalies(self, df, group_col='primary_group',
                        feature_col='voucher_feature',
                        voucher_col='voucher_unique_id',
                        subject_col='first_level_subject',
                        counter_col='counter_subject',
                        credit_col='credit',
                        involved_col='involved_groups',
                        amount_col='voucher_abs_amount',
                        pareto_percent=0.95,
                        original_cols=None):
        """
        主检测流程（带重要性水平）
        
        参数:
            pareto_percent: 帕累托百分比，默认0.95
            original_cols: 用户原始上传的列名列表，用于避免重复
        """
        # 1. 构建群距离矩阵
        self.build_group_distance_matrix(df, group_col, feature_col, voucher_col, involved_col)
        
        # 2. 计算特征频次
        self.calculate_feature_frequency(df, group_col, feature_col, voucher_col)
        
        # 3. 计算重要性水平（帕累托累计法）
        self.calculate_materiality_levels(df, group_col, voucher_col, amount_col, pareto_percent)
        
        # 4. 计算得分
        cross_scores = self.calculate_cross_group_score(df, group_col, voucher_col, involved_col)
        inner_scores = self.calculate_inner_group_score(df, group_col, feature_col, voucher_col,
                                                        subject_col, counter_col, credit_col)
        
        # 5. 计算α系数和最终得分
        alpha_scores = {}
        final_scores = {}
        
        for voucher_id, group in df.groupby(voucher_col):
            primary_group = group[group_col].iloc[0]
            amount = group[amount_col].iloc[0]
            
            # 获取该群的重要性水平
            materiality_level = self.materiality_levels.get(primary_group, 0.0)
            
            # 计算α系数
            alpha = self.calculate_alpha_coefficient(amount, materiality_level)
            alpha_scores[voucher_id] = alpha  # α系数不保留小数，保持完整精度
            
            # 计算最终得分
            cross_score = cross_scores.get(voucher_id, 0.0)
            inner_score = inner_scores.get(voucher_id, 0.0)
            raw_score = cross_score + inner_score
            final_score = raw_score * alpha
            
            final_scores[voucher_id] = round(final_score, 2)
        
        # 6. 合并结果
        df_result = df.copy()
        
        # 添加得分列（使用中文名）
        df_result['跨模块得分'] = df_result[voucher_col].map(cross_scores)
        df_result['模块内得分'] = df_result[voucher_col].map(inner_scores)
        df_result['α系数'] = df_result[voucher_col].map(alpha_scores)
        df_result['综合得分'] = df_result[voucher_col].map(final_scores)
        
        # 添加重要性水平列
        df_result['重要性水平'] = df_result[group_col].map(self.materiality_levels)
        df_result['凭证金额'] = df_result[amount_col]
        
        def get_anomaly_type(row):
            if row['跨模块得分'] > 0 and row['模块内得分'] > 0:
                return '跨模块+内部异常'
            elif row['跨模块得分'] > 0:
                return '跨模块异常'
            elif row['模块内得分'] > 0:
                return '模块内异常'
            else:
                return '正常'
        
        df_result['异常类型'] = df_result.apply(get_anomaly_type, axis=1)
        df_result['风险等级'] = df_result['综合得分'].apply(self._get_risk_level)
        
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
    
    def get_materiality_preview(self, df, group_col='primary_group',
                                 voucher_col='voucher_unique_id',
                                 amount_col='voucher_abs_amount',
                                 pareto_percent=0.95):
        """
        获取重要性水平预览信息
        
        返回预览统计信息，包括各群的重要性水平、剔除凭证数量等
        """
        # 按凭证去重
        voucher_df = df.groupby(voucher_col).agg({
            group_col: 'first',
            amount_col: 'first',
            'debit': 'sum'  # 原始借方金额
        }).reset_index()
        
        total_vouchers = len(voucher_df)
        total_abs_amount = voucher_df[amount_col].sum()
        total_debit = voucher_df['debit'].sum()
        
        # 计算各群的重要性水平
        materiality_levels = self.calculate_materiality_levels(
            df, group_col, voucher_col, amount_col, pareto_percent
        )
        
        # 统计各群预览信息
        group_previews = []
        
        for group_name in voucher_df[group_col].unique():
            group_vouchers = voucher_df[voucher_df[group_col] == group_name]
            materiality = materiality_levels.get(group_name, 0.0)
            
            # 统计该群的凭证
            group_total = len(group_vouchers)
            group_amount = group_vouchers[amount_col].sum()
            
            # 统计低于重要性水平的凭证
            below_threshold = group_vouchers[group_vouchers[amount_col] < materiality]
            excluded_count = len(below_threshold)
            excluded_amount = below_threshold[amount_col].sum()
            excluded_original_amount = below_threshold['debit'].sum()
            
            group_previews.append({
                '业务群': group_name,
                '重要性水平': materiality,
                '总凭证数': group_total,
                '总金额': group_amount,
                '剔除凭证数': excluded_count,
                '剔除占比': excluded_count / group_total if group_total > 0 else 0,
                '剔除金额': excluded_amount,
                '剔除金额占比': excluded_amount / group_amount if group_amount > 0 else 0,
                '剔除原始金额': excluded_original_amount,
            })
        
        # 整体统计
        total_excluded = sum(p['剔除凭证数'] for p in group_previews)
        total_excluded_amount = sum(p['剔除金额'] for p in group_previews)
        total_excluded_original = sum(p['剔除原始金额'] for p in group_previews)
        
        summary = {
            '帕累托百分比': pareto_percent,
            '总凭证数': total_vouchers,
            '总金额': total_abs_amount,
            '总原始金额': total_debit,
            '总剔除凭证数': total_excluded,
            '总剔除占比': total_excluded / total_vouchers if total_vouchers > 0 else 0,
            '总剔除金额': total_excluded_amount,
            '总剔除金额占比': total_excluded_amount / total_abs_amount if total_abs_amount > 0 else 0,
            '总剔除原始金额': total_excluded_original,
            '总剔除原始金额占比': total_excluded_original / total_debit if total_debit != 0 else 0,
            '各群详情': group_previews
        }
        
        return summary
    
    def get_distance_matrix_dataframe(self):
        """获取距离矩阵"""
        if not self.distance_matrix:
            return None
        return pd.DataFrame(self.distance_matrix).fillna(0)
    
    def get_anomaly_summary(self, result_df, voucher_col='voucher_unique_id'):
        """获取统计摘要"""
        # 按凭证去重
        voucher_df = result_df.groupby(voucher_col).agg({
            '综合得分': 'first',
            '风险等级': 'first',
            '异常类型': 'first'
        }).reset_index()
        
        total = len(voucher_df)
        
        # 跨群统计
        cross_count = len(voucher_df[voucher_df['异常类型'].str.contains('跨模块')])
        
        return {
            'total_vouchers': total,
            'high_risk': len(voucher_df[voucher_df['风险等级'].str.contains('高风险')]),
            'medium_risk': len(voucher_df[voucher_df['风险等级'].str.contains('中风险')]),
            'low_risk': len(voucher_df[voucher_df['风险等级'].str.contains('低风险')]),
            'normal': len(voucher_df[voucher_df['风险等级'].str.contains('正常')]),
            'cross_group': cross_count,
            'avg_score': voucher_df['综合得分'].mean(),
            'max_score': voucher_df['综合得分'].max()
        }
