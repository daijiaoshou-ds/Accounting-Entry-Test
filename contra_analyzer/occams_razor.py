class OccamsRazor:
    """
    奥卡姆剃刀剪枝器 v4.0 (硬骨头权重版)
    公式：Score = 100 - (行数 * 1) - (分拆惩罚 * 硬骨头系数)
    """
    
    # === 硬骨头名单 ===
    # 这些科目通常业务逻辑简单，不应被随意拆分
    HARD_BONES = ["应交税费"] 

    @staticmethod
    def _get_bone_multiplier(subject_raw):
        """
        判断是否为硬骨头，返回惩罚倍率
        subject_raw: 可能带后缀，如 "应交税费__Pos__D"
        """
        # 清洗科目名
        clean_name = str(subject_raw).split('__')[0]
        
        for bone in OccamsRazor.HARD_BONES:
            if bone in clean_name:
                return 2.0 # 硬骨头惩罚翻倍
        return 1.0

    @staticmethod
    def score_solution(solution):
        """
        solution: {借方Key: {贷方Key: 金额}}
        """
        score = 100.0
        
        # 1. 统计借方数量(n) 和 贷方数量(m)
        d_split_counts = {} 
        c_split_counts = {}
        
        all_d = set(solution.keys())
        all_c = set()
        
        # 计算总行数 (连接数)
        total_lines = 0
        
        for d, c_map in solution.items():
            valid_c_links = [c for c, amt in c_map.items() if abs(amt) > 0.001]
            d_split_counts[d] = len(valid_c_links)
            total_lines += len(valid_c_links)
            
            for c in valid_c_links:
                all_c.add(c)
                c_split_counts[c] = c_split_counts.get(c, 0) + 1
                
        n = len(all_d)
        m = len(all_c)
        
        # === 扣分项 1: 行数惩罚 (固定扣1分) ===
        score -= total_lines * 1.0
        
        # === 扣分项 2: 分拆惩罚 (含硬骨头加成) ===
        # 规则：数量多的一方做 Driver (遍历方)，数量少的是 Bucket
        
        debit_is_driver = (n >= m)
        
        base_driver_penalty = 5.0
        base_bucket_penalty = 1.0
        
        if debit_is_driver:
            # --- 借方是 Driver (重罚) ---
            for d_key, count in d_split_counts.items():
                if count > 1:
                    # 获取硬骨头系数
                    multiplier = OccamsRazor._get_bone_multiplier(d_key)
                    # 扣分 = (分拆次数) * 基础分 * 系数
                    score -= (count - 1) * base_driver_penalty * multiplier
            
            # --- 贷方是 Bucket (轻罚) ---
            for c_key, count in c_split_counts.items():
                if count > 1:
                    multiplier = OccamsRazor._get_bone_multiplier(c_key)
                    score -= (count - 1) * base_bucket_penalty * multiplier
                    
        else:
            # --- 贷方是 Driver (重罚) ---
            for c_key, count in c_split_counts.items():
                if count > 1:
                    multiplier = OccamsRazor._get_bone_multiplier(c_key)
                    score -= (count - 1) * base_driver_penalty * multiplier

            # --- 借方是 Bucket (轻罚) ---
            for d_key, count in d_split_counts.items():
                if count > 1:
                    multiplier = OccamsRazor._get_bone_multiplier(d_key)
                    score -= (count - 1) * base_bucket_penalty * multiplier
        
        return round(score, 2)

    @staticmethod
    def rank_solutions(solutions):
        """仅按奥卡姆得分排序"""
        if not solutions: return [], []
        scored_items = []
        for sol in solutions:
            score = OccamsRazor.score_solution(sol)
            scored_items.append((sol, score))
        
        # 分数高到低
        scored_items.sort(key=lambda x: x[1], reverse=True)
        
        return [x[0] for x in scored_items], [x[1] for x in scored_items]