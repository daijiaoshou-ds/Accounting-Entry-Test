"""
群聚类引擎模块
基于预设核心群和规则对会计分录特征进行分类
"""
import pandas as pd
import numpy as np
from collections import defaultdict, Counter


# ============ 预设核心群配置 ============

CORE_GROUPS = {
    '采购活动群': [
        '原材料', '半成品', '库存商品', '委托加工物资', 
        '应付账款', '预付账款', '材料采购', '在途物资',
        '应付账款暂估', '材料采购过渡', '周转材料',
        '暂估应付账款', '材料成本差异'
    ],
    '销售活动群': [
        '应收账款', '应收票据', '主营业务收入', '其他业务收入',
        '发出商品', '主营业务成本', '其他业务成本', '其他业务支出',
        '预收账款', '合同负债'
    ],
    '薪酬活动群': [
        '应付职工薪酬', '应付工资', '应付福利费', 
        '应付社会保险费', '应付住房公积金'
    ],
    '生产活动群': [
        '生产成本', '制造费用', '劳务成本', '研发支出',
        '辅助生产成本',
        '基本生产成本', '废品损失', '停工损失'
    ],
    '研发活动群': [
        '研发费用'
    ],
    '长期资产群': [
        '固定资产', '在建工程', '工程物资', '无形资产',
        '长期待摊费用', '累计折旧', '固定资产清理',
        '累计摊销', '无形资产减值准备', '固定资产减值准备',
        '在建工程减值准备', '工程结算',
        '使用权资产', '租赁负债', '长期股权投资', '投资性房地产',
        '使用权资产累计折旧'
    ],
    '资金活动群': [
        '短期借款', '长期借款', '应付利息', '交易性金融资产',
        '投资收益', '应收利息', '应付股利', '应收股利',
        '实收资本', '资本公积', '盈余公积', '未分配利润'
    ],
    '税务活动群': [
        '应交税费', '应交增值税', '应交消费税', '应交所得税',
        '应交城市维护建设税', '应交教育费附加', '应交地方教育费附加',
        '未交增值税', '待抵扣进项税额', '待认证进项税额',
        '进项税额转出', '出口退税',
        '税金及附加'
    ],
    '其他群': []  # 无法分类的归入此处
}

# 特性群 - 只要包含一个核心词就直接判定为该群
CHARACTERISTIC_GROUPS = [
    '薪酬活动群',    # 只要有应付职工薪酬就是薪酬群
    '研发活动群',    # 只要有研发费用就是研发群
    '长期资产群',    # 只要有固定资产/在建工程等就是资产群
    '税务活动群',    # 只要有应交税费等就是税务群
    '生产活动群',    # 只要有生产成本等就是生产群
    '资金活动群'     # 只要有短期借款/长期借款等就是资金群
]

# 非特性群（二元锚定群）- 需要同时踩中2个核心词
ANCHOR_GROUPS = [
    '采购活动群',
    '销售活动群'
]

# 停用词 - 出现在多个群中的科目，聚类时忽略
STOP_WORDS = [
    '银行存款', '库存现金', '其他货币资金',
    '其他应付款', '其他应收款',
    '本年利润', '利润分配', '以前年度损益调整',
    '待处理财产损溢',
]


class ClusterEngine:
    """
    群聚类引擎
    """
    
    def __init__(self, core_groups=None, stop_words=None):
        """
        初始化聚类引擎
        
        参数:
            core_groups: 自定义核心群配置，默认使用内置配置
            stop_words: 自定义停用词，默认使用内置配置
        """
        self.core_groups = core_groups or CORE_GROUPS
        self.stop_words = set(stop_words or STOP_WORDS)
        
        # 建立科目到群的反向映射
        self.subject_to_group = {}
        self._build_subject_mapping()
        
        # 聚类结果缓存
        self.classification_cache = {}
        
    def _build_subject_mapping(self):
        """
        建立科目到群的反向映射表
        """
        for group_name, subjects in self.core_groups.items():
            if group_name == '其他群':
                continue
            for subject in subjects:
                # 一个科目可能属于多个群，这里记录主要归属
                if subject not in self.subject_to_group:
                    self.subject_to_group[subject] = []
                self.subject_to_group[subject].append(group_name)
    
    def extract_subjects_from_feature(self, feature_str):
        """
        从会计分录特征字符串中提取科目列表
        
        参数:
            feature_str: 如"应收账款、主营业务收入"
        返回:
            list: 科目列表
        """
        if pd.isna(feature_str) or not feature_str:
            return []
        return [s.strip() for s in str(feature_str).split('、') if s.strip()]
    
    def classify_voucher(self, feature_str, directions=None):
        """
        对单个凭证的会计分录特征进行分类
        
        新逻辑：
        1. 过滤停用词
        2. 如果过滤后还有有效科目，按有效科目判断
        3. 如果全部停用词，归入其他群
        
        参数:
            feature_str: 会计分录特征
            directions: 借贷方向列表，如['借', '贷']
        返回:
            tuple: (归属群, 涉及群列表, 分类类型)
        """
        # 检查缓存
        if feature_str in self.classification_cache:
            return self.classification_cache[feature_str]
        
        subjects = self.extract_subjects_from_feature(feature_str)
        
        if not subjects:
            result = ('其他群', ['其他群'], '其他群')
            self.classification_cache[feature_str] = result
            return result
        
        # 过滤停用词
        valid_subjects = [s for s in subjects if s not in self.stop_words]
        
        # 如果过滤后没有有效科目（全是停用词），归入其他群
        if not valid_subjects:
            result = ('其他群', ['其他群'], '停用词主导')
            self.classification_cache[feature_str] = result
            return result
        
        # 获取每个有效科目所属的群
        subject_to_groups = {}
        for subject in valid_subjects:
            subject_to_groups[subject] = self.subject_to_group.get(subject, [])
        
        # 统计每个群的匹配科目数
        group_scores = defaultdict(int)
        for subject, groups in subject_to_groups.items():
            for group in groups:
                group_scores[group] += 1
        
        # 判断分录类型
        subject_count = len(valid_subjects)
        is_simple = subject_count == 2
        
        # 第1步：检查特性群（单字判定）
        characteristic_matches = []
        for group in CHARACTERISTIC_GROUPS:
            if group_scores[group] >= 1:
                characteristic_matches.append(group)
        
        # 第2步：检查非特性群（二元锚定群）
        anchor_matches = []
        
        # 根据有效科目数量采用不同的判定策略
        if len(valid_subjects) == 1:
            # 只有一个有效科目的情况：只要该科目属于某个二元锚定群，就判定入群
            subject = valid_subjects[0]
            subject_groups = subject_to_groups.get(subject, [])
            for group in subject_groups:
                if group in ANCHOR_GROUPS:
                    anchor_matches.append(group)
        
        elif len(valid_subjects) == 2:
            # 两个有效科目的情况：需要两个科目都匹配同一个群（严格二元锚定）
            sub1, sub2 = valid_subjects[0], valid_subjects[1]
            groups1 = set(subject_to_groups.get(sub1, [])) & set(ANCHOR_GROUPS)
            groups2 = set(subject_to_groups.get(sub2, [])) & set(ANCHOR_GROUPS)
            
            # 两个科目都在同一个群
            common_groups = groups1 & groups2
            for g in common_groups:
                anchor_matches.append(g)
            
            # 各自在不同群（跨群情况）
            if not common_groups and groups1 and groups2:
                for g in (groups1 | groups2):
                    anchor_matches.append(g)
            
            # 只有一个在群
            if not common_groups and (groups1 or groups2):
                for g in (groups1 | groups2):
                    if g not in anchor_matches:
                        anchor_matches.append(g)
        
        else:
            # 三个及以上有效科目的情况：至少需要2个科目匹配同一个群
            anchor_group_subject_counts = defaultdict(set)
            for subject in valid_subjects:
                subject_groups = subject_to_groups.get(subject, [])
                for group in subject_groups:
                    if group in ANCHOR_GROUPS:
                        anchor_group_subject_counts[group].add(subject)
            
            for group, matched_subjects in anchor_group_subject_counts.items():
                if len(matched_subjects) >= 2:
                    anchor_matches.append(group)
        
        # 合并涉及群：特性群 + 满足二元锚定的非特性群（去重）
        all_involved_groups = list(dict.fromkeys(characteristic_matches + anchor_matches))
        
        # 确定主要归属群
        if characteristic_matches:
            # 优先使用特性群（取第一个）
            primary_group = characteristic_matches[0]
            classify_type = '特性群判定'
        elif anchor_matches:
            # 否则使用满足二元锚定的非特性群
            primary_group = anchor_matches[0]
            classify_type = '二元锚定'
        else:
            # 都不满足
            primary_group = '其他群'
            all_involved_groups = ['其他群']
            classify_type = '其他群'
        
        result = (primary_group, all_involved_groups, classify_type)
        self.classification_cache[feature_str] = result
        return result
    
    def classify_all(self, df, feature_col='voucher_feature', voucher_col='voucher_unique_id'):
        """
        对DataFrame中所有凭证进行分类
        
        重要：按凭证分组处理，确保跨群判断正确
        """
        # 先按凭证分组，获取每个凭证的会计分录特征
        voucher_features = {}
        for voucher_id, group in df.groupby(voucher_col):
            # 取该凭证的第一行特征（所有行应该相同）
            feature = group[feature_col].iloc[0]
            voucher_features[voucher_id] = feature
        
        # 对每个凭证进行分类
        voucher_classification = {}
        for voucher_id, feature in voucher_features.items():
            primary_group, all_groups, classify_type = self.classify_voucher(feature)
            voucher_classification[voucher_id] = {
                'primary_group': primary_group,
                'involved_groups': all_groups,
                'group_count': len(all_groups),
                'classify_type': classify_type,
                'is_cross_group': len(all_groups) > 1
            }
        
        # 将分类结果映射回原始DataFrame的每一行
        results = []
        for idx, row in df.iterrows():
            voucher_id = row[voucher_col]
            classification = voucher_classification.get(voucher_id, {})
            
            results.append({
                'primary_group': classification.get('primary_group', '其他群'),
                'involved_groups': '、'.join(classification.get('involved_groups', ['其他群'])),
                'group_count': classification.get('group_count', 1),
                'classify_type': classification.get('classify_type', '其他群'),
                'is_cross_group': classification.get('is_cross_group', False)
            })
        
        # 合并结果
        result_df = pd.DataFrame(results)
        df = pd.concat([df.reset_index(drop=True), result_df], axis=1)
        
        return df
    
    def get_group_distribution(self, df):
        """
        获取群分布统计 - 按凭证计数，不是按行计数
        
        参数:
            df: 已分类的DataFrame
        返回:
            DataFrame: 各群统计
        """
        if 'primary_group' not in df.columns:
            raise ValueError("请先调用classify_all进行分类")
        
        # 首先按凭证去重 - 一个凭证只算一次
        voucher_df = df.groupby('voucher_unique_id').agg({
            'primary_group': 'first',
            'voucher_feature': 'first'
        }).reset_index()
        
        # 再按群统计
        distribution = voucher_df.groupby('primary_group').agg({
            'voucher_unique_id': 'count',
            'voucher_feature': 'nunique'
        }).reset_index()
        
        distribution.columns = ['业务群', '凭证数量', '特征种类数']
        distribution = distribution.sort_values('凭证数量', ascending=False)
        
        return distribution
    
    def get_cross_group_vouchers(self, df):
        """
        获取跨模块凭证
        
        参数:
            df: 已分类的DataFrame
        返回:
            DataFrame: 跨模块凭证
        """
        if 'is_cross_group' not in df.columns:
            raise ValueError("请先调用classify_all进行分类")
        
        return df[df['is_cross_group'] == True].copy()
    
    def get_uncertain_classifications(self, df, ml_classifier=None):
        """
        获取需要ML辅助分类的凭证
        
        参数:
            df: 已分类的DataFrame
            ml_classifier: 可选的ML分类器
        返回:
            DataFrame: 归入"其他群"的凭证
        """
        uncertain = df[df['primary_group'] == '其他群'].copy()
        return uncertain
