# -*- coding: utf-8 -*-
"""
静态配置：清晰度系数、桶定义、列名检测规则、数据加载工具
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

# ============================================================================
# 资产文件路径
# ============================================================================
_PACKAGE_DIR = Path(__file__).parent

# ============================================================================
# 存储目录 — 支持测试/生产环境隔离
# ============================================================================
_STORAGE_DIR_NAME = "_storage"

def get_storage_dir() -> Path:
    """返回当前环境的存储根目录。"""
    return _PACKAGE_DIR / _STORAGE_DIR_NAME

def set_test_mode(enabled: bool = True):
    """切换测试/生产环境。

    测试模式: _storage_test/（独立存储，不影响正式数据）
    生产模式: _storage/    （正式数据）

    应在 import 其他模块之前调用，或在模块首次使用存储之前调用。
    """
    global _STORAGE_DIR_NAME
    _STORAGE_DIR_NAME = "_storage_test" if enabled else "_storage"
    # 确保目录存在
    get_storage_dir().mkdir(parents=True, exist_ok=True)

def is_test_mode() -> bool:
    """当前是否为测试模式。"""
    return _STORAGE_DIR_NAME == "_storage_test"
_BUCKETS_JSON_PATH = _PACKAGE_DIR / "assets" / "业务桶与keyword.json"
_SUBJECT_MD_PATH = _PACKAGE_DIR / "assets" / "一级科目明细.md"

# ============================================================================
# 科目清晰度系数
# ============================================================================

# T0 (1.5) — 绝对灵魂：自带强业务属性，出现就是主角
_T0_SUBJECTS = {
    "应付职工薪酬",                               # 薪酬
    "固定资产", "在建工程", "无形资产", "工程物资", # 长期资产
    "主营业务收入",                               # 销售
    "生产成本",                                  # 生产 
    "研发支出", "研发费用",                       # 研发
    "实收资本",                                  # 投资
    "材料采购", "周转材料",                       # 存货采购
    "递延收益"                                    # 政府补助
    # 注意：制造费用不在T0。计提工资/折旧时制造费用借方很常见，
    # 不代表一定是生产制造业务，降为T1让关键词（工资/折旧）主导判断。
}

# T2 (0.3) — 垃圾桶/过渡科目：表达极不清晰，压制权重
_T2_SUBJECTS = {
    "其他应收款", "其它应收款", "其他应付款", "其它应付款",
    "营业外收入", "营业外支出",
    "待处理财产损益", "以前年度损益调整",
    "其他综合收益", "长期待摊费用",
    "应收股利", "应付股利", "应收利息", "应付利息",
    "预计负债"
}

# T3 (0.0) — 纯资金管道：无业务含义，彻底屏蔽
_T3_SUBJECTS = {
    "银行存款", "库存现金", "其它货币资金", "其他货币资金",
}
T3_SUBJECTS = _T3_SUBJECTS  # 公开别名，供其他模块引用

DEFAULT_CLARITY = 0.5  # 仅在_get_clarity()兜底使用：科目不在「一级科目明细.md」时

# 纠错回路 (correct_errors_theory.md)
LAMBDA_RANK = 1.0   # 桶顺位增强系数（纠错初期给足力度）
EMA_ALPHA = 0.1     # 金额 EMA 学习率

# ── 结构分系数 ──
LAMBDA_STRUCT = 1.5 # PMI 结构分放大系数（提升 v·w' 对最终得分的贡献）

# 税费桶衰减——税费桶无强锚定科目，容易靠关键词偏置抢占其他桶
TAX_DECAY = 0.5     # 仅衰减税费桶的关键词 + 自动词偏置分

# 关键词偏置上限——防止关键词过多命中某桶导致偏置压倒结构分
MAX_KEYWORD_BIAS = 2.0    # 手工关键词偏置单桶得分上限
MAX_AUTO_WORD_BIAS = 2.0  # 自动词偏置单桶得分上限

# 金额特征上限——金额在实务中模糊性高，限制其影响力
MAX_AMOUNT_SCORE = 0.1    # 金额特征得分上限（正向/负向均不超过 ±0.1）

# 二级科目关键词信号衰减——科目明细的区分力弱于摘要文本
SUBJECT_DETAIL_KEYWORD_DECAY = 0.6

# ── 制单人（模块会计）偏置 ──
BOOKKEEPER_PREFERRED_BONUS = 0.5   # 专职会计对其负责模块桶的加分
BOOKKEEPER_PENALTY = -0.1          # 专职会计对其他四个模块桶的轻微排斥

# 模块会计岗位 → 偏好业务桶
BOOKKEEPER_ROLE_TO_BUCKET = {
    "应收会计": "销售收入",
    "应付会计": "存货采购",
    "资产会计": "长期资产",
    "工资会计": "职工薪酬",
    "生产会计": "生产制造",
}

# 五个模块桶（排斥范围仅限此五个，不扩散到其他桶）
SPECIALIST_BUCKETS = {"销售收入", "存货采购", "长期资产", "职工薪酬", "生产制造"}


def _parse_subject_md(path: Path = None) -> List[str]:
    """解析 一级科目明细.md，返回所有一级科目名称列表（内联版本，不依赖其他函数）。"""
    if path is None:
        path = _SUBJECT_MD_PATH
    text = path.read_text(encoding="utf-8")
    subjects = []
    for line in text.split("\n"):
        line = line.strip()
        m = re.match(r"^\|\s*\d+\s*\|\s*(.+?)\s*\|", line)
        if m:
            name = m.group(1).strip()
            if name and name not in ("会计科目名称", "---"):
                subjects.append(name)
    return subjects


def build_clarity_dict() -> Dict[str, float]:
    """从 一级科目明细.md 解析所有科目，按规则分配清晰度。

    优先级：T3 > T0 > T2 > 默认T1
    """
    subjects = _parse_subject_md()
    clarity = {}
    for s in subjects:
        s_clean = s.strip()
        if not s_clean:
            continue
        if s_clean in _T3_SUBJECTS:
            clarity[s_clean] = 0.0
        elif s_clean in _T0_SUBJECTS:
            clarity[s_clean] = 1.5
        elif s_clean in _T2_SUBJECTS:
            clarity[s_clean] = 0.3
        else:
            clarity[s_clean] = 1.0
    return clarity


SUBJECT_CLARITY: Dict[str, float] = build_clarity_dict()

# ============================================================================
# 列名自动检测规则
# ============================================================================

COLUMN_NAME_PATTERNS: Dict[str, List[str]] = {
    "date":        ["制单日期", "记账日期", "凭证日期", "日期", "date", "时间"],
    "voucher_no":  ["凭证号", "凭证编号", "凭证字号", "voucher", "单号", "字号的"],
    "voucher_num": ["凭证编号", "凭证号", "凭证字号"],
    "subject":     ["一级科目",  "会计科目"],
    "subject_name":["科目名称", "科目明细", "明细科目", "二级科目", "会计科目名称"],
    "summary":     ["摘要", "说明", "备注", "业务描述", "description"],
    "debit":       ["借方金额", "借方", "借方发生额", "debit", "借"],
    "credit":      ["贷方金额", "贷方", "贷方发生额", "credit", "贷"],
    "currency":    ["币种", "币别", "currency"],
    "original_amt":["原币金额", "原币", "外币金额"],
}

# ============================================================================
# 业务桶统一注册表 — 加桶只改这里一处
# ============================================================================
# 每个桶包含三个维度：
#   clarity  — 桶自身清晰度（影响平局打破）
#   subjects — 偏好向量 w（科目→权重），1.0=决定性，未列出=0.0
#   keywords — 补充关键词（JSON中已有的不需要重复写在这里）
#
# 设计原则：
#   1.0 = 决定性科目（出现即锁定该桶）
#   0.7-0.9 = 强相关（几乎总是该业务）
#   0.4-0.6 = 中等相关（经常出现，但也会在其他业务中出现）
#   0.1-0.3 = 弱相关（偶尔伴随出现）
#
# 注意：权重不需要完美，因为 w'=w×R 会通过PMI矩阵自动把偏好
# 传播到相关科目。真正的分类由 Score=v·w'+b 决定。

BUCKET_REGISTRY: Dict[str, dict] = {
    "职工薪酬": {
        "clarity": 10.0,
        "subjects": {
            "应付职工薪酬": 1.0,
        },
    },
    "税费": {
        "clarity": 1.0,
        "subjects": {
            "应交税费":   1.0,   # 税务场景锚定科目
            "税金及附加": 0.8,
        },
    },
    "存货采购": {
        "clarity": 9.0,
        "subjects": {
            "原材料":               1.0,
            "在途物资":             1.0,
            "材料采购":             1.0,
            "材料采购过渡":         1.0,   # 非标科目，实务中常见
            "委托加工物资":         1.0,
            "应付票据":             0.8,
            "库存商品":             0.5,   # 也可能是自产入库
            "周转材料":             0.4,
            "包装物及低值易耗品":   0.4,
            "材料成本差异":         0.4,
            "消耗性生物资产":       0.3,
        },
    },
    "长期资产": {
        "clarity": 10.0,
        "subjects": {
            "固定资产":     1.0,
            "在建工程":     1.0,
            "工程物资":     0.8,
            "无形资产":     0.7,
            "累计折旧":     0.3,   # 主战场在折旧摊销
            "固定资产清理": 0.5,
            "使用权资产":   0.5,
            "长期待摊费用": 0.3,
            "投资性房地产": 0.3,
            "累计摊销":     0.2,
        },
    },
    "折旧摊销": {
        "clarity": 8.0,
        "subjects": {
            "累计折旧":             0.8,
            "累计摊销":             0.8,
            "使用权资产累计折旧":   0.8,
            "累计折耗":             0.7,
            "长期待摊费用":         0.4,
            "无形资产":             0.3,
        },
        "keywords": [
            "折旧", "计提折旧", "折旧费", "折旧费用",
            "摊销", "计提摊销", "摊销费", "摊销费用",
            "累计折旧", "累计摊销",
            "无形资产摊销", "长期待摊费用摊销",
            "固定资产折旧", "使用权资产折旧",
            "本月折旧", "本期折旧", "当月折旧",
            "本月摊销", "本期摊销", "当月摊销",
        ],
    },
    "销售收入": {
        "clarity": 8.0,
        "subjects": {
            "主营业务收入": 1.0,
            "其他业务收入": 0.8,
            "合同资产":     0.8,
            "应收账款":     0.5,   # 销售回款常伴随应收账款
            "预收账款":     0.3,
            "合同负债":     0.3,
        },
    },
    "借款筹资": {
        "clarity": 8.0,
        "subjects": {
            "短期借款": 1.0,
            "长期借款": 1.0,
            "长期债券": 0.8,
            "应付债券": 0.8,
        },
    },
    "利息收支": {
        "clarity": 7.0,
        "subjects": {
            "财务费用": 1.0,
            "应付利息": 1.0,
            "应收利息": 0.6,
        },
    },
    "费用报销": {
        "clarity": 3.0,
        "subjects": {
            "管理费用": 0.6,   # 低权重：也出现在薪酬/折旧场景
            "销售费用": 0.6,
        },
        "keywords": [
            # 从废弃的往来款桶迁移
            "备用金", "代垫", "代付", "还款",
            "个人借款", "员工借款", "暂借款", "暂支", "暂收", "暂付",
        ],
    },
    "押金保证金": {
        "clarity": 2.0,
        "subjects": {
            "其他应收款": 0.4,
            "其他应付款": 0.3,
        },
    },
    "投资本金": {
        "clarity": 5.0,
        "subjects": {
            "长期股权投资":     1.0,
            "交易性金融资产":   1.0,
            "持有至到期投资":   0.8,
            "可供出售金融资产": 0.8,
            "债权投资":         0.8,
            "其他债权投资":     0.8,
        },
    },
    "分红股利": {
        "clarity": 6.0,
        "subjects": {
            "利润分配": 1.0,
            "应付股利": 1.0,
            "应收股利": 0.7,
        },
    },
    "生产制造": {
        "clarity": 6.0,
        "subjects": {
            "生产成本": 1.0,
            "制造费用": 0.5,   # 可能含非生产性支出，降权
            "工程施工": 0.4,
            "工程结算": 0.4,
            "劳务成本": 0.3,
            "机械作业": 0.4,
            "原材料":   0.3,
            "库存商品": 0.3,
        },
    },
    "研发": {
        "clarity": 6.0,
        "subjects": {
            "研发支出": 1.0,
            "无形资产": 0.4,
        },
        "keywords": [
            "研发", "研究", "开发费", "研发费", "科研",
            "技术开发", "产品开发", "项目研发", "研发项目",
            "委外研发", "委外开发", "中试", "小试",
            "研发领料", "研发用料", "研究开发", "技术研发",
            "研发人员", "研发部门", "研发材料", "研发测试",
        ],
    },
    "政府补助": {
        "clarity": 8.0,
        "subjects": {
            "递延收益": 1.0,     # 政府补助递延确认
            "其他收益": 1.0,     # 政府补助直接确认/摊销
            "营业外收入": 0.4,   # 旧准则下政府补助走营业外收入
        },
        "keywords": [
            "政府补助", "政府补贴", "财政补贴", "财政拨款",
            "专项补贴", "专项补助", "技改补贴", "技改补助",
            "研发补贴", "研发补助", "稳岗补贴", "稳岗返还",
            "税收返还", "即征即退", "先征后返",
            "递延收益", "其他收益",
        ],
    },
    "资金内部往来": {
        "clarity": 10.0,  # 硬规则桶，Step 0 预分配，不参与评分（clarity 仅占位）
    },
    "汇兑损益": {
        "clarity": 10.0,  # 硬规则桶，Step 0 预分配，不参与评分（clarity 仅占位）
    },
    "其他业务": {
        "clarity": 1.0,   # 兜底桶
        "keywords": [
            "营业外", "捐赠", "罚款", "赔款", "盘盈", "盘亏",
            "资产处置", "报废", "非常损失",
        ],
    },
}

# ── 从 BUCKET_REGISTRY 自动派生以下三个配置 ──

BUCKET_SUBJECT_PREFERENCES: Dict[str, Dict[str, float]] = {
    name: info.get("subjects", {})
    for name, info in BUCKET_REGISTRY.items()
}

EXTRA_KEYWORDS: Dict[str, List[str]] = {
    name: info["keywords"]
    for name, info in BUCKET_REGISTRY.items()
    if info.get("keywords")
}

BUCKET_CLARITY: Dict[str, float] = {
    name: info["clarity"]
    for name, info in BUCKET_REGISTRY.items()
}

# ============================================================================
# 显式关键词分数 — 支持负分抑制（理论第5节）
# ============================================================================
# 未出现在此配置中的关键词 → 回退到自动生成评分

KEYWORD_EXPLICIT_SCORES: Dict[str, Dict[str, float]] = {
    # ── 薪酬相关 ──
    "工资":     {"职工薪酬": 1.0},
    "发工资":   {"职工薪酬": 1.0},
    "社保":     {"职工薪酬": 1.0},
    "公积金":   {"职工薪酬": 1.0},
    "年终奖":   {"职工薪酬": 1.0},
    "奖金":     {"职工薪酬": 0.8},
    "绩效":     {"职工薪酬": 0.8},
    "津贴":     {"职工薪酬": 0.7},
    "补贴":     {"职工薪酬": 0.3},
    "离职补偿": {"职工薪酬": 0.8},
    "计提":     {"职工薪酬": 0.7},

    # ── 报销相关 ──
    "报销":     {"费用报销": 0.6, "生产制造": -0.3},
    "差旅":     {"费用报销": 0.6},
    "出差":     {"费用报销": 0.6},
    "招待":     {"费用报销": 0.5},
    "餐饮":     {"费用报销": 0.5},
    "餐费":     {"费用报销": 0.5},
    "办公":     {"费用报销": 0.4},
    "打车":     {"费用报销": 0.5},
    "机票":     {"费用报销": 0.5},
    "酒店":     {"费用报销": 0.5},
    "顺风车":   {"费用报销": 0.4},
    "滴滴":     {"费用报销": 0.4},

    # ── 折旧摊销 ──
    "折旧":     {"折旧摊销": 0.8},
    "摊销":     {"折旧摊销": 0.8},
    "计提折旧": {"折旧摊销": 0.9},
    "计提摊销": {"折旧摊销": 0.9},
    "折旧费":   {"折旧摊销": 0.8},
    "摊销费":   {"折旧摊销": 0.8},

    # ── 生产制造 ──
    "领料":     {"生产制造": 0.6, "费用报销": -0.2},
    "领用":     {"生产制造": 0.6},
    "退料":     {"生产制造": 0.5},
    "补料":     {"生产制造": 0.4},
    "车间":     {"生产制造": 0.4},
    "产线":     {"生产制造": 0.4},
    "入库":     {"生产制造": 0.5},
    "出库":     {"生产制造": 0.4},
    "工单":     {"生产制造": 0.6},
    "报工":     {"生产制造": 0.5},
    "工序":     {"生产制造": 0.4},

    # ── 销售 ──
    "销售款":   {"销售收入": 0.6},
    "回款":     {"销售收入": 0.5},
    "销货款":   {"销售收入": 0.6},

    # ── 借款筹资 ──
    "借款":     {"借款筹资": 0.8},
    "贷款":     {"借款筹资": 0.8},
    "银行贷款": {"借款筹资": 0.9},
    "融资":     {"借款筹资": 0.6},

    # ── 利息 ──
    "利息":     {"利息收支": 0.8},
    "结息":     {"利息收支": 0.8},
    "罚息":     {"利息收支": 0.7},
    "贴息":     {"利息收支": 0.6},

    # ── 采购 ──
    "采购":     {"存货采购": 0.6, "费用报销": -0.2},
    "材料款":   {"存货采购": 0.6},
    "进货":     {"存货采购": 0.5},

    # ── 税费 ──
    "增值税":   {"税费": 0.6},
    "所得税":   {"税费": 0.6},
    "缴税":     {"税费": 0.5},
    "退税":     {"税费": 0.5},
    "交税":     {"税费": 0.5},
    "完税":     {"税费": 0.5},

    # ── 分红股利 ──
    "分红":     {"分红股利": 0.8},
    "股利":     {"分红股利": 0.8},
    "股息":     {"分红股利": 0.7},
    "派息":     {"分红股利": 0.8},

    # ── 投资 ──
    "投资":     {"投资本金": 0.6},
    "理财":     {"投资本金": 0.5},
    "出资":     {"投资本金": 0.6},
    "增资":     {"投资本金": 0.6},

    # ── 研发 ──
    "研发":     {"研发": 0.8},
    "研究":     {"研发": 0.6},
    "委外研发": {"研发": 0.8},
    "技术开发": {"研发": 0.7},

    # ── 政府补助 ──
    "政府补助": {"政府补助": 0.9},
    "政府补贴": {"政府补助": 0.9},
    "财政补贴": {"政府补助": 0.8},
    "财政拨款": {"政府补助": 0.8},
    "专项补贴": {"政府补助": 0.7},
    "技改补贴": {"政府补助": 0.7},
    "稳岗补贴": {"政府补助": 0.8},
    "稳岗返还": {"政府补助": 0.8},
    "税收返还": {"政府补助": 0.7},
    "即征即退": {"政府补助": 0.7},

    # ── 押金保证金 ──
    "押金":     {"押金保证金": 0.7},
    "保证金":   {"押金保证金": 0.7},
    "质保金":   {"押金保证金": 0.8},

    # ── 往来/备用金（往来款桶已废弃）──
    "备用金":   {"费用报销": 0.8},
    "代垫":     {"费用报销": 0.6},
    "代付":     {"费用报销": 0.6},
    "还款":     {"费用报销": 0.5},
    "暂借款":   {"费用报销": 0.6},
    "个人借款": {"费用报销": 0.7},
    "员工借款": {"费用报销": 0.7},

    # ── 歧义修正 ──
    "检测":     {"费用报销": 0.3, "长期资产": 0.2},
    "测试":     {"研发": 0.4, "费用报销": 0.3},
    "工程款":   {"长期资产": 0.5, "销售收入": 0.3},
}


def load_buckets_json(path: Path = None) -> Dict[str, List[str]]:
    """加载业务桶与关键词 JSON，自动合并 EXTRA_KEYWORDS 中的新增桶。

    返回 {桶名: [关键词列表]}。
    """
    path = path or _BUCKETS_JSON_PATH
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for bucket_name, bucket_info in raw.items():
        if bucket_name.startswith("_"):
            continue
        keywords = list(bucket_info.get("keywords", []))
        result[bucket_name] = keywords

    # 合并新增桶的关键词（JSON 中没有的桶）
    for bucket_name, keywords in EXTRA_KEYWORDS.items():
        if bucket_name in result:
            existing = set(result[bucket_name])
            for kw in keywords:
                if kw not in existing:
                    result[bucket_name].append(kw)
        else:
            result[bucket_name] = list(keywords)

    # 补上 BUCKET_REGISTRY 中无关键词的桶（如资金内部往来）
    for name in BUCKET_REGISTRY:
        if name not in result:
            result[name] = []

    return result


def load_subject_list(path: Path = None) -> List[str]:
    """解析 一级科目明细.md，返回所有一级科目名称列表。"""
    return _parse_subject_md(path)


def build_bucket_preferences(subjects: List[str] = None) -> Dict[str, Dict[str, float]]:
    """构建每个桶的初始偏好向量 w。

    使用 BUCKET_SUBJECT_PREFERENCES 的分级权重：
    - 1.0 = 决定性科目
    - 0.5~0.8 = 强相关科目
    - 0.3~0.4 = 中等相关
    - 未列出的科目 = 0.0

    真正的偏好扩散由 w' = w × R 完成。
    """
    if subjects is None:
        subjects = load_subject_list()

    # 合并 JSON 中的桶和新增的桶（研发、其他业务）
    json_buckets = load_buckets_json()
    all_bucket_names = list(json_buckets.keys())
    for name in BUCKET_SUBJECT_PREFERENCES:
        if name not in all_bucket_names:
            all_bucket_names.append(name)

    preferences = {}
    for bucket_name in all_bucket_names:
        bucket_prefs = BUCKET_SUBJECT_PREFERENCES.get(bucket_name, {})
        w = {}
        for s in subjects:
            w[s] = bucket_prefs.get(s, 0.0)
        preferences[bucket_name] = w
    return preferences


# 延迟初始化，避免导入时出错
_BUCKETS_CACHE: Dict[str, List[str]] = None
_BUCKET_NAMES_CACHE: List[str] = None


def _get_buckets() -> Dict[str, List[str]]:
    global _BUCKETS_CACHE
    if _BUCKETS_CACHE is None:
        _BUCKETS_CACHE = load_buckets_json()
    return _BUCKETS_CACHE


def _get_bucket_names() -> List[str]:
    global _BUCKET_NAMES_CACHE
    if _BUCKET_NAMES_CACHE is None:
        _BUCKET_NAMES_CACHE = list(_get_buckets().keys())
    return _BUCKET_NAMES_CACHE


def get_bucket_names() -> List[str]:
    """返回所有业务桶名称列表（含 JSON 中 13 个 + EXTRA_KEYWORDS 新增的）。"""
    return _get_bucket_names()
