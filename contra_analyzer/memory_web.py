"""
对方科目分析 - Web版简化记忆模块
移除本地存储功能，仅保留内存临时存储
"""
from .occams_razor import OccamsRazor


class KnowledgeBase:
    """
    Web版知识库 - 无持久化存储
    
    原版的本地JSON存储功能已移除，改为：
    1. 仅内存临时存储（会话级）
    2. 仅使用奥卡姆得分进行方案排序
    3. 无EMA学习和记忆泛化功能
    """
    
    def __init__(self):
        # 仅内存存储，每次会话重新开始
        self.memory = {}
        self.learning_rate = 0.6
        self.beta_factor = 0.5
    
    def rank_solutions(self, solutions, pattern_name=""):
        """
        对方案进行排序（仅使用奥卡姆得分）
        
        Args:
            solutions: 方案列表
            pattern_name: 模式名称（Web版不使用，仅保持接口兼容）
        
        Returns:
            排序后的方案列表（按得分降序）
        """
        if not solutions:
            return []
        
        scored = []
        for sol in solutions:
            # 仅计算奥卡姆得分
            r = OccamsRazor.score_solution(sol)
            # 记忆得分固定为0.5（无偏好）
            m = 0.5
            # 合计得分
            total = r * (1 + self.beta_factor * m)
            scored.append((total, sol))
        
        # 按得分降序排序
        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored]
    
    def get_memory_score(self, pattern_name, solution):
        """
        获取记忆得分（Web版固定返回0.5）
        
        Args:
            pattern_name: 模式名称
            solution: 方案
        
        Returns:
            float: 固定返回0.5
        """
        # Web版无记忆功能，返回默认值
        return 0.5
    
    def calculate_total_score(self, razor_score, memory_score):
        """
        计算合计得分
        
        Args:
            razor_score: 奥卡姆得分
            memory_score: 记忆得分
        
        Returns:
            float: 合计得分
        """
        return round(razor_score * (1 + self.beta_factor * memory_score), 2)
    
    def clear_memory(self):
        """清空内存（Web版仅清空内存，不涉及文件操作）"""
        self.memory = {}
