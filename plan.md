# 会计分录异常检测系统优化计划

## 任务一：输出表格优化

### 1.1 修改 anomaly_detector.py
- [x] `detect_anomalies()` 方法：输出全部凭证，不只是异常部分
- [x] 修改返回的 DataFrame，使用中文列名（除了Score列）
- [x] 避免重复添加用户上传已有的字段

### 1.2 修改 app.py
- [x] `show_anomaly_results()`：显示全部凭证，添加筛选选项
- [x] 调整显示列逻辑，优先使用用户原始列名

## 任务二：群距离计算修改

### 2.1 修改 anomaly_detector.py 中的 `_calculate_distance()`
新公式实现：
```
X_ij = 1 - ∏(k=2 to n) ∏(S in C_k) (1 - (n_S + 1) / (Σ|M_u| + 2))
D = min(1/X_ij - 1, 100)
```

实现步骤：
1. 按阶数 k 分组跨群组合
2. 对每个组合 S 计算 p_S = (n_S + 1) / (Σ|M_u| + 2)
3. 计算 (1 - p_S) 的连乘
4. X_ij = 1 - 连乘结果
5. D = min(1/X_ij - 1, 100)

## 任务三：群内异常得分修改

### 3.1 修改 anomaly_detector.py
- [x] `calculate_inner_group_score()` 使用基于HHI的新公式
- [x] 添加连接频次统计方法
- [x] 实现自适应惩罚函数

新公式：
```
HHI = Σ(f_i)^2
f = n/N
S(f) = (f^-(1-HHI) - 1) / (1-HHI)  [HHI < 1]
S(f) = -ln(f)                       [HHI = 1]
Score_inner = min(100, S(f))
```

## 任务四：科目连接图

### 4.1 修改 app.py 中的 `build_subject_graph()`
- [x] 基于贷方科目和对方科目构建连接
- [x] 按凭证号统计连接频次
- [x] 生成有向图数据

### 4.2 修改 `draw_subject_graph()`
- [x] 使用 NetworkX + Plotly 绘制简洁有向图
- [x] 节点显示科目名
- [x] 边显示凭证计数
- [x] 支持交互式布局

## 文件变更清单

1. `src/accounting_anomaly/anomaly_detector.py` - 核心算法修改
2. `app.py` - UI和展示逻辑修改

## 测试验证项

1. 输出表格包含全部凭证
2. 列名使用中文（除Score列）
3. 群距离计算使用连乘公式
4. 群内得分使用HHI自适应公式
5. 科目连接图正确显示资金流向
