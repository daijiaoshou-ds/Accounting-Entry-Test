"""
机器学习分类器模块
用于对无法通过规则分类的凭证进行补充分类
使用TF-IDF + KMeans进行词向量和聚类
"""
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from sklearn.metrics.pairwise import cosine_similarity
import re


class MLClassifier:
    """
    基于TF-IDF和KMeans的会计科目分类器
    
    说明:
    - 每次运行时重新训练（一次性学习）
    - 不累计历史数据
    - 用于补充规则无法分类的情况
    """
    
    def __init__(self, n_clusters=5, random_state=42):
        """
        初始化分类器
        
        参数:
            n_clusters: KMeans聚类数，默认5
            random_state: 随机种子
        """
        self.n_clusters = n_clusters
        self.random_state = random_state
        self.vectorizer = None
        self.kmeans = None
        self.is_fitted = False
        
        # 科目特征语料库（用于相似度比较）
        self.subject_corpus = {}
        self.subject_vectors = None
        
    def _tokenize_subjects(self, feature_str):
        """
        对会计分录特征进行分词
        
        参数:
            feature_str: 如"应收账款、主营业务收入"
        返回:
            list: 分词结果
        """
        if pd.isna(feature_str) or not feature_str:
            return []
        
        # 按顿号分割
        subjects = [s.strip() for s in str(feature_str).split('、') if s.strip()]
        
        # 进一步提取关键词（去除常见后缀）
        tokens = []
        for subject in subjects:
            # 去除常见后缀
            subject_clean = re.sub(r'(费|款|金|产|入|本|利)$', '', subject)
            tokens.append(subject_clean)
            tokens.append(subject)  # 保留原词
        
        return tokens
    
    def _preprocess_features(self, features):
        """
        预处理特征列表为文本
        
        参数:
            features: 会计分录特征列表
        返回:
            list: 处理后的文本列表
        """
        texts = []
        for feature in features:
            tokens = self._tokenize_subjects(feature)
            # 用空格连接，适合TF-IDF
            text = ' '.join(tokens)
            texts.append(text)
        return texts
    
    def fit(self, df, feature_col='voucher_feature', group_col='primary_group'):
        """
        基于当前数据训练分类器
        
        说明：
        - 使用无监督学习（KMeans），不需要标注数据
        - 没有"学习轮数"概念，KMeans通过迭代收敛
        - 只在有需要时才训练（当存在"其他群"凭证时）
        
        参数:
            df: DataFrame，包含已分类的数据
            feature_col: 特征列名
            group_col: 群列名
        返回:
            self
        """
        self.training_log = []
        
        # 只使用已成功分类的数据（非"其他群"）
        train_data = df[df[group_col] != '其他群'].copy()
        uncertain_count = len(df[df[group_col] == '其他群'])
        
        self.training_log.append(f"[ML] 开始训练 - 已分类样本: {len(train_data)}, 待分类样本: {uncertain_count}")
        
        if len(train_data) < 10:
            self.training_log.append(f"[ML] 数据不足({len(train_data)}<10)，跳过训练")
            self.is_fitted = False
            return self
        
        features = train_data[feature_col].tolist()
        texts = self._preprocess_features(features)
        
        self.training_log.append(f"[ML] 预处理完成 - 共 {len(features)} 个特征")
        
        # TF-IDF向量化
        self.vectorizer = TfidfVectorizer(
            max_features=100,
            min_df=2,
            ngram_range=(1, 2)
        )
        X = self.vectorizer.fit_transform(texts)
        
        self.training_log.append(f"[ML] TF-IDF向量化完成 - 特征维度: {X.shape}")
        
        # KMeans聚类 - 聚类数不超过数据量
        n_clusters = min(self.n_clusters, len(train_data) // 3)
        n_clusters = max(2, n_clusters)  # 至少2个类
        
        self.training_log.append(f"[ML] KMeans聚类 - 设置聚类数: {n_clusters}")
        
        self.kmeans = KMeans(
            n_clusters=n_clusters,
            random_state=self.random_state,
            n_init=10,
            max_iter=300  # 最大迭代次数
        )
        self.kmeans.fit(X)
        
        self.training_log.append(f"[ML] KMeans收敛 - 实际迭代次数: {self.kmeans.n_iter_}, 惯性: {self.kmeans.inertia_:.2f}")
        
        # 建立每个群的代表向量
        self._build_group_vectors(train_data, feature_col, group_col, X)
        
        self.training_log.append(f"[ML] 训练完成 - 已建立 {len(self.group_vectors)} 个群的代表向量")
        
        self.is_fitted = True
        return self
    
    def _build_group_vectors(self, df, feature_col, group_col, X):
        """
        建立每个群的代表向量（质心）
        
        参数:
            df: 训练数据
            feature_col: 特征列名
            group_col: 群列名
            X: TF-IDF向量
        """
        self.group_vectors = {}
        
        for group in df[group_col].unique():
            if group == '其他群':
                continue
            
            group_mask = df[group_col] == group
            group_indices = df[group_mask].index
            
            if len(group_indices) > 0:
                # 计算该群的平均向量
                group_vectors = X[group_mask.values]
                centroid = np.mean(group_vectors, axis=0)
                self.group_vectors[group] = centroid
    
    def predict(self, feature_str, threshold=0.3):
        """
        预测单个特征的归属群
        
        参数:
            feature_str: 会计分录特征
            threshold: 相似度阈值，低于此值返回None
        返回:
            str or None: 预测的群名，无法预测返回None
        """
        if not self.is_fitted or not self.vectorizer or not self.group_vectors:
            return None
        
        # 向量化
        texts = self._preprocess_features([feature_str])
        X = self.vectorizer.transform(texts)
        
        # 计算与各群代表向量的相似度
        best_group = None
        best_similarity = 0
        
        vector_array = X.toarray()[0]
        
        for group, centroid in self.group_vectors.items():
            centroid_array = np.asarray(centroid).flatten()
            
            # 计算余弦相似度
            similarity = cosine_similarity(
                vector_array.reshape(1, -1),
                centroid_array.reshape(1, -1)
            )[0][0]
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_group = group
        
        # 阈值判断
        if best_similarity >= threshold:
            return best_group
        
        return None
    
    def find_similar_subjects(self, subject, known_subjects, top_k=3):
        """
        查找与给定科目最相似的已知科目
        
        参数:
            subject: 要查找的科目
            known_subjects: 已知科目列表
            top_k: 返回最相似的前k个
        返回:
            list: [(科目, 相似度), ...]
        """
        if not known_subjects:
            return []
        
        # 构建简单的词向量（字符级）
        def subject_vector(s):
            # 基于字符频率的简单向量
            vec = {}
            for char in s:
                vec[char] = vec.get(char, 0) + 1
            return vec
        
        def cosine_sim(v1, v2):
            # 计算余弦相似度
            all_keys = set(v1.keys()) | set(v2.keys())
            dot = sum(v1.get(k, 0) * v2.get(k, 0) for k in all_keys)
            norm1 = sum(v ** 2 for v in v1.values()) ** 0.5
            norm2 = sum(v ** 2 for v in v2.values()) ** 0.5
            if norm1 == 0 or norm2 == 0:
                return 0
            return dot / (norm1 * norm2)
        
        subject_vec = subject_vector(subject)
        similarities = []
        
        for known in known_subjects:
            known_vec = subject_vector(known)
            sim = cosine_sim(subject_vec, known_vec)
            similarities.append((known, sim))
        
        # 排序取前k
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
    
    def batch_predict(self, df, feature_col='voucher_feature', group_col='primary_group'):
        """
        批量预测"其他群"的分类
        
        参数:
            df: DataFrame
            feature_col: 特征列名
            group_col: 群列名
        返回:
            DataFrame: 添加了预测结果的DataFrame
        """
        if not self.is_fitted:
            return df
        
        df = df.copy()
        
        # 找出需要预测的（其他群）
        mask = df[group_col] == '其他群'
        
        predictions = []
        for idx, row in df[mask].iterrows():
            feature = row[feature_col]
            predicted_group = self.predict(feature)
            predictions.append(predicted_group)
        
        # 更新预测结果（仅更新成功预测的）
        pred_idx = 0
        for idx in df[mask].index:
            if predictions[pred_idx] is not None:
                df.at[idx, group_col] = predictions[pred_idx]
                df.at[idx, 'ml_predicted'] = True
            else:
                df.at[idx, 'ml_predicted'] = False
            pred_idx += 1
        
        return df
    
    def get_feature_clusters(self, df, feature_col='voucher_feature'):
        """
        对所有特征进行聚类分析（用于探索性分析）
        
        参数:
            df: DataFrame
            feature_col: 特征列名
        返回:
            dict: 聚类结果
        """
        features = df[feature_col].unique()
        texts = self._preprocess_features(features)
        
        # 向量化
        vectorizer = TfidfVectorizer(max_features=50, min_df=1)
        X = vectorizer.fit_transform(texts)
        
        # 聚类
        n_clusters = min(8, len(features) // 2)
        n_clusters = max(2, n_clusters)
        
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(X)
        
        # 整理结果
        clusters = {}
        for i, feature in enumerate(features):
            label = labels[i]
            if label not in clusters:
                clusters[label] = []
            clusters[label].append(feature)
        
        return clusters
