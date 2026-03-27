"""
数据预处理模块
处理原始会计凭证数据，生成：
1. 凭证唯一值
2. 会计分录特征
3. 过滤无效凭证（如本年利润结转）
"""
import pandas as pd
import numpy as np
from collections import defaultdict, Counter

from .utils import (
    generate_unique_voucher_id, 
    extract_first_level_subject,
    format_amount,
    get_accounting_direction,
    normalize_subject
)


class DataProcessor:
    """
    会计凭证数据处理器
    """
    
    # 标准列名映射（支持用户上传的不同列名格式）
    COLUMN_MAPPINGS = {
        'date': ['制单日期', '记账日期', '日期', 'date', 'voucher_date', '会计日期', '凭证日期'],
        'month': ['月份', 'month', '会计期间', '期间'],
        'voucher_no': ['编号', '凭证编号', '凭证号', 'voucher_no', 'voucher_number', '凭证字号'],
        'summary': ['摘要', '摘要内容', 'summary', 'description', '业务摘要'],
        'subject_code': ['科目编码', '科目代码', 'subject_code', 'account_code', '科目号'],
        'first_level_subject': ['一级科目', 'first_level_subject', '一级科目名称', '总账科目'],
        'subject_name': ['科目名称', '科目', 'subject_name', 'account_name', '会计科目'],
        'counter_subject': ['对方科目', 'counter_subject', '对方科目名称', '对应科目'],
        'currency': ['币种', '货币', 'currency'],
        'original_amount': ['原币金额', '外币金额', 'original_amount'],
        'debit': ['借方金额', '借方', 'debit', 'debit_amount', '借方发生额', '借'],
        'credit': ['贷方金额', '贷方', 'credit', 'credit_amount', '贷方发生额', '贷'],
    }
    
    # 需要过滤掉的科目关键词（结转类、无业务实质的凭证）
    FILTER_SUBJECTS = ['本年利润', '利润分配', '以前年度损益调整']
    
    def __init__(self, df, column_mapping=None):
        """
        初始化处理器
        
        参数:
            df: pandas DataFrame，原始凭证数据
            column_mapping: 用户配置的字段映射，如 {'date': '制单日期', 'voucher_no': '凭证编号', ...}
        """
        self.raw_df = df.copy()
        self.column_mapping = column_mapping or {}  # 用户自定义映射
        self.processed_df = None
        self.voucher_groups = None
        
    def auto_detect_columns(self):
        """
        自动检测列名，返回建议的映射
        """
        df_columns = list(self.raw_df.columns)
        detected = {}
        confidence = {}
        
        for std_name, possible_names in self.COLUMN_MAPPINGS.items():
            for col in df_columns:
                if col in possible_names:
                    detected[std_name] = col
                    confidence[std_name] = 'high'  # 完全匹配
                    break
                # 模糊匹配
                for possible in possible_names:
                    if possible in col or col in possible:
                        if std_name not in detected:
                            detected[std_name] = col
                            confidence[std_name] = 'medium'
                        break
        
        return detected, confidence
    
    def set_column_mapping(self, mapping):
        """
        设置用户自定义的字段映射
        
        参数:
            mapping: dict, 如 {'date': '制单日期', 'voucher_no': '凭证编号', ...}
        """
        self.column_mapping = mapping
    
    def get_column_value(self, row, std_name):
        """
        根据标准列名获取值
        """
        col_name = self.column_mapping.get(std_name)
        if col_name and col_name in row.index:
            return row[col_name]
        return None
    
    def preprocess(self):
        """
        预处理主函数
        """
        # 如果没有配置，先自动检测
        if not self.column_mapping:
            detected, _ = self.auto_detect_columns()
            self.column_mapping = detected
        
        df = self.raw_df.copy()
        
        # 标准化金额列
        debit_col = self.column_mapping.get('debit')
        if debit_col and debit_col in df.columns:
            df['debit'] = df[debit_col].apply(format_amount)
        else:
            df['debit'] = 0.0
            
        credit_col = self.column_mapping.get('credit')
        if credit_col and credit_col in df.columns:
            df['credit'] = df[credit_col].apply(format_amount)
        else:
            df['credit'] = 0.0
        
        # 标准化科目名称
        first_level_col = self.column_mapping.get('first_level_subject')
        subject_name_col = self.column_mapping.get('subject_name')
        
        if first_level_col and first_level_col in df.columns:
            df['first_level_subject'] = df[first_level_col].apply(normalize_subject)
        elif subject_name_col and subject_name_col in df.columns:
            # 从科目名称提取一级科目
            df['first_level_subject'] = df[subject_name_col].apply(
                lambda x: extract_first_level_subject(x, None)
            )
        else:
            df['first_level_subject'] = ''
        
        # 处理对方科目 - 使用用户上传的，如果没有则计算
        counter_col = self.column_mapping.get('counter_subject')
        if counter_col and counter_col in df.columns:
            # 使用用户上传的对方科目
            df['counter_subject'] = df[counter_col].apply(
                lambda x: normalize_subject(x) if pd.notna(x) else ''
            )
        else:
            # 自动计算对方科目
            df = self._calculate_counter_subjects(df)
        
        # 3. 生成凭证唯一值
        date_col = self.column_mapping.get('date')
        voucher_no_col = self.column_mapping.get('voucher_no')
        
        if date_col and voucher_no_col and date_col in df.columns and voucher_no_col in df.columns:
            df['voucher_unique_id'] = df.apply(
                lambda row: generate_unique_voucher_id(
                    row[date_col], 
                    row[voucher_no_col]
                ), 
                axis=1
            )
        elif voucher_no_col and voucher_no_col in df.columns:
            # 如果没有日期，只用凭证号
            df['voucher_unique_id'] = df[voucher_no_col].astype(str)
        else:
            # 如果连凭证号都没有，用行号
            df['voucher_unique_id'] = df.index.astype(str)
        
        # 4. 计算会计分录特征
        df = self._calculate_voucher_features(df)
        
        # 5. 计算凭证的借方绝对值金额（用于重要性水平）
        df = self._calculate_voucher_absolute_amount(df)
        
        # 6. 过滤无效凭证（包含本年利润等的结转凭证）
        df = self._filter_invalid_vouchers(df)
        
        self.processed_df = df
        return df
    
    def _calculate_counter_subjects(self, df):
        """
        计算每一行的对方科目（当用户没有上传时使用）
        
        逻辑：
        - 对于凭证中的每一行，对方科目是该凭证中方向相反的其他科目
        """
        # 为每一行添加方向标记
        df['direction'] = df.apply(lambda row: get_accounting_direction(row['debit'], row['credit']), axis=1)
        
        # 按凭证分组处理
        counter_subjects = []
        
        for idx, row in df.iterrows():
            voucher_id = row['voucher_unique_id']
            current_direction = row['direction']
            current_subject = row['first_level_subject']
            
            # 获取同一凭证的其他行
            same_voucher = df[df['voucher_unique_id'] == voucher_id]
            
            # 对方科目是方向相反的科目
            if current_direction == '借':
                counter = same_voucher[same_voucher['direction'] == '贷']['first_level_subject'].tolist()
            elif current_direction == '贷':
                counter = same_voucher[same_voucher['direction'] == '借']['first_level_subject'].tolist()
            else:
                counter = []
            
            # 去除自己，去重，用顿号连接
            counter = [c for c in counter if c != current_subject and c]
            counter_str = '、'.join(sorted(set(counter))) if counter else ''
            counter_subjects.append(counter_str)
        
        df['counter_subject'] = counter_subjects
        return df
    
    def _calculate_voucher_features(self, df):
        """
        计算每个凭证的会计分录特征
        
        会计分录特征 = 该凭证涉及的所有一级科目，去重后按顿号连接
        """
        # 按凭证分组，聚合科目
        voucher_features = {}
        
        for voucher_id, group in df.groupby('voucher_unique_id'):
            # 获取该凭证涉及的所有一级科目（去重）
            subjects = group['first_level_subject'].dropna().unique()
            # 去除空值，排序后用顿号连接
            subjects = sorted([s for s in subjects if s])
            feature = '、'.join(subjects)
            voucher_features[voucher_id] = feature
        
        # 将特征映射回每一行
        df['voucher_feature'] = df['voucher_unique_id'].map(voucher_features)
        
        return df
    
    def _filter_invalid_vouchers(self, df):
        """
        过滤无效凭证
        - 过滤掉包含本年利润、利润分配等的结转凭证
        
        注意：只过滤完全匹配的结转凭证，不过滤正常业务
        """
        # 按凭证分组检查
        valid_vouchers = []
        filtered_count = 0
        
        for voucher_id, group in df.groupby('voucher_unique_id'):
            feature = group['voucher_feature'].iloc[0]
            subjects_in_feature = set(str(feature).split('、'))
            
            # 检查是否包含过滤关键词（完全匹配科目名称）
            should_filter = False
            for filter_word in self.FILTER_SUBJECTS:
                # 检查是否是独立的科目（作为列表元素或子串）
                if filter_word in str(feature):
                    # 进一步检查：确保是本年利润相关的结转凭证
                    # 特征中主要包含结转类科目
                    if any(word in str(feature) for word in ['本年利润', '利润分配']):
                        should_filter = True
                        break
            
            if not should_filter:
                valid_vouchers.append(voucher_id)
            else:
                filtered_count += 1
        
        # 只保留有效的凭证
        df_filtered = df[df['voucher_unique_id'].isin(valid_vouchers)].copy()
        
        # 记录过滤信息
        if filtered_count > 0:
            print(f"[Filter] 过滤了 {filtered_count} 个结转凭证（本年利润/利润分配）")
        
        return df_filtered
    
    def get_voucher_summary(self):
        """
        获取凭证汇总信息（每个凭证一行）- 按凭证计数，不是按行计数
        """
        if self.processed_df is None:
            self.preprocess()
        
        # 按凭证分组汇总 - 一个凭证一行
        summary = self.processed_df.groupby('voucher_unique_id').agg({
            'first_level_subject': lambda x: '、'.join(sorted(set(x.dropna()))),
            'debit': 'sum',
            'credit': 'sum',
            'voucher_feature': 'first',
            'direction': lambda x: f"借方{sum(x=='借')}条/贷方{sum(x=='贷')}条"
        }).reset_index()
        
        summary.columns = ['凭证唯一值', '涉及科目', '借方合计', '贷方合计', '会计分录特征', '分录构成']
        
        return summary
    
    def get_feature_distribution(self):
        """
        获取会计分录特征的分布统计 - 按凭证计数
        """
        if self.processed_df is None:
            self.preprocess()
        
        # 按凭证去重后统计 - 一个凭证只算一次
        voucher_features = self.processed_df.groupby('voucher_unique_id')['voucher_feature'].first()
        feature_counts = voucher_features.value_counts()
        
        return feature_counts
    
    def get_voucher_count_by_feature(self):
        """
        获取每个会计分录特征对应的凭证数量
        """
        if self.processed_df is None:
            self.preprocess()
        
        # 按凭证去重
        voucher_df = self.processed_df.groupby('voucher_unique_id').agg({
            'voucher_feature': 'first'
        }).reset_index()
        
        # 统计每个特征的凭证数
        feature_counts = voucher_df.groupby('voucher_feature').size().reset_index(name='凭证数量')
        feature_counts = feature_counts.sort_values('凭证数量', ascending=False)
        
        return feature_counts

    def _calculate_voucher_absolute_amount(self, df):
        """
        计算每个凭证的借方绝对值金额（用于重要性水平）
        
        逻辑：
        1. 对每一行取借方金额的绝对值
        2. 按凭证分组，累加所有行的借方绝对值
        3. 将结果映射回每一行
        """
        # 计算每行的借方绝对值
        df['debit_abs'] = df['debit'].abs()
        
        # 按凭证分组，计算该凭证的借方绝对值总和
        voucher_abs_amount = df.groupby('voucher_unique_id')['debit_abs'].sum()
        
        # 映射回每一行
        df['voucher_abs_amount'] = df['voucher_unique_id'].map(voucher_abs_amount)
        
        return df
    
    def get_voucher_amount_stats(self):
        """
        获取凭证金额统计信息（用于重要性水平预览）
        
        返回：
            dict: 包含总凭证数、总金额等统计信息
        """
        if self.processed_df is None:
            self.preprocess()
        
        # 按凭证去重
        voucher_df = self.processed_df.groupby('voucher_unique_id').agg({
            'voucher_abs_amount': 'first',
            'debit': 'sum',  # 原始借方金额（未绝对值化）
        }).reset_index()
        
        total_vouchers = len(voucher_df)
        total_abs_amount = voucher_df['voucher_abs_amount'].sum()
        total_debit = voucher_df['debit'].sum()  # 原始借方总额
        
        return {
            'total_vouchers': total_vouchers,
            'total_abs_amount': total_abs_amount,
            'total_debit': total_debit,
            'avg_abs_amount': total_abs_amount / total_vouchers if total_vouchers > 0 else 0,
            'max_abs_amount': voucher_df['voucher_abs_amount'].max(),
            'min_abs_amount': voucher_df['voucher_abs_amount'].min(),
        }
