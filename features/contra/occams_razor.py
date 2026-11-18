class OccamsRazor:
    """
    奥卡姆剃刀剪枝器 v5.0 (硬骨头权重 + 孤岛连接惩罚)
    公式：Score = 100 - (连接数) - (分拆惩罚) - (孤岛连接 * 2)
    """

    # === 硬骨头名单 ===
    HARD_BONES = ["应交税费"]

    # 亲缘度屏蔽词（与ExhaustiveSolver保持一致）
    STOP_PREFIXES = {'应付', '应收', '其他', '长期', '短期', '待摊', '预提', '职工'}

    @staticmethod
    def _subject_similarity(subj1, subj2):
        """计算两个科目名的亲缘度 (0.0 ~ 1.0)"""
        if subj1 == subj2:
            return 1.0
        parts1 = subj1.split('-', 1)
        parts2 = subj2.split('-', 1)
        prefix1, suffix1 = parts1[0], parts1[1] if len(parts1) > 1 else ''
        prefix2, suffix2 = parts2[0], parts2[1] if len(parts2) > 1 else ''
        if prefix1 in OccamsRazor.STOP_PREFIXES or prefix2 in OccamsRazor.STOP_PREFIXES:
            return 0.0
        if suffix1 and suffix2 and suffix1 == suffix2:
            return 0.9
        if suffix1 and suffix2:
            if suffix1 in suffix2 or suffix2 in suffix1:
                return 0.7
        if prefix1 == prefix2:
            return 0.3
        return 0.0

    @staticmethod
    def _count_orphan_connections(solution, node_map=None):
        """
        孤岛连接：driver的某条连接去了"没有同族driver去"的bucket。
        情况A：D被拆分 → 非主流且无同族的连接是孤岛
        情况B：D没被拆分 → 但≥2个同族去了别的bucket → D是异类
        情况C：D被拆分且全家族离散 → 所有连接都是孤岛
        """
        if not node_map:
            return 0

        driver_subjs = {}
        driver_buckets = {}
        for d_id in solution:
            node = node_map.get(d_id, {})
            driver_subjs[d_id] = node.get('subject', '')
            valid = [(c, a) for c, a in solution[d_id].items() if abs(a) > 0.001]
            if len(valid) == 1:
                driver_buckets[d_id] = valid[0][0]

        orphans = 0

        for d_id, c_map in solution.items():
            connections = [(c_id, amt) for c_id, amt in c_map.items() if abs(amt) > 0.001]
            d_subj = driver_subjs.get(d_id, '')
            if not d_subj:
                continue

            # 找同族 (亲缘度 > 0.5)
            siblings = [o for o in driver_subjs
                       if o != d_id and OccamsRazor._subject_similarity(d_subj, driver_subjs[o]) > 0.5]
            if not siblings:
                continue

            if len(connections) >= 2:
                # === 情况A/C: D被拆分 ===
                family_count = {}
                for c_id, _ in connections:
                    family_count[c_id] = sum(
                        1 for s in siblings
                        if s in solution and c_id in solution[s]
                    )

                if max(family_count.values()) == 0:
                    # 情况C: 全家族离散 — 同族没有一个人去D连的任何bucket → 全部孤岛
                    orphans += len(connections)
                else:
                    # 情况A: 有主流bucket
                    main_bucket = max(family_count, key=family_count.get)
                    for c_id, _ in connections:
                        if c_id != main_bucket and family_count.get(c_id, 0) == 0:
                            orphans += 1
            else:
                # === 情况B: D没被拆分，但它是家族里的"异类" ===
                my_bucket = driver_buckets.get(d_id)
                if not my_bucket:
                    continue
                # 排除自对冲 (driver和bucket是同一个科目)
                bucket_node = node_map.get(my_bucket, {})
                if bucket_node.get('subject', '') == d_subj:
                    continue
                # 统计同族都去了哪些bucket
                sib_bucket_counts = {}
                for s_id in siblings:
                    s_bucket = driver_buckets.get(s_id)
                    if s_bucket:
                        sib_bucket_counts[s_bucket] = sib_bucket_counts.get(s_bucket, 0) + 1
                if not sib_bucket_counts:
                    continue
                main_bucket = max(sib_bucket_counts, key=sib_bucket_counts.get)
                if my_bucket != main_bucket and sib_bucket_counts.get(main_bucket, 0) >= 2:
                    orphans += 1

        return orphans

    @staticmethod
    def _get_bone_multiplier(subject_raw, node_map=None):
        """
        判断是否为硬骨头，返回惩罚倍率
        subject_raw: 节点ID (v2.0) 或 "应交税费__Pos__D" (v1.x)
        node_map: v2.0节点映射 {node_id: {'subject': str}}
        """
        # v2.0: 从node_map获取科目名
        if node_map and subject_raw in node_map:
            clean_name = node_map[subject_raw]['subject']
        else:
            # v1.x兼容: 清洗后缀
            clean_name = str(subject_raw).split('__')[0]
        
        for bone in OccamsRazor.HARD_BONES:
            if bone in clean_name:
                return 2.0 # 硬骨头惩罚翻倍
        return 1.0

    @staticmethod
    def score_solution(solution, node_map=None):
        """
        solution: {借方Key: {贷方Key: 金额}}
        node_map: {node_id: {'subject': str, ...}} - v2.0节点映射，用于解析科目名
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
                    # 获取硬骨头系数（v2.0支持node_map）
                    multiplier = OccamsRazor._get_bone_multiplier(d_key, node_map)
                    score -= (count - 1) * base_driver_penalty * multiplier
            
            # --- 贷方是 Bucket (轻罚) ---
            for c_key, count in c_split_counts.items():
                if count > 1:
                    multiplier = OccamsRazor._get_bone_multiplier(c_key, node_map)
                    score -= (count - 1) * base_bucket_penalty * multiplier
                    
        else:
            # --- 贷方是 Driver (重罚) ---
            for c_key, count in c_split_counts.items():
                if count > 1:
                    multiplier = OccamsRazor._get_bone_multiplier(c_key, node_map)
                    score -= (count - 1) * base_driver_penalty * multiplier

            # --- 借方是 Bucket (轻罚) ---
            for d_key, count in d_split_counts.items():
                if count > 1:
                    multiplier = OccamsRazor._get_bone_multiplier(d_key, node_map)
                    score -= (count - 1) * base_bucket_penalty * multiplier

        # === 扣分项 3: 孤岛连接惩罚 ===
        orphans = OccamsRazor._count_orphan_connections(solution, node_map)
        score -= orphans * 2.0

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