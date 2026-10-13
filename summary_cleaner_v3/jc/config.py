# -*- coding: utf-8 -*-
"""序时账清洗 3.0 — 配置与常量

架构（3.0 融合版，不再区分"程序/模型"）：

    序时账 → ETL → 切割(科目组合+摘要) → 模型打分 → 硬规则兜底 → 写回
                                              ↓
                                     中/低置信度 → AI 复核

程序只负责三件事：
  1. **ETL**：读文件、认列名、规范化、把结果写回 Excel
  2. **引用模型**：加载交付物、批量推理、算置信度
  3. **两条硬规则**：写死「资金内部往来」「汇兑损益」

**不负责**：给业务桶打分。所有业务桶的判定都来自模型（+ AI 复核）。
"""

from pathlib import Path

# ── 版本 ──
__version__ = "3.0.0"
RELEASE_NAME = "序时账清洗 3.0"

# ── 目录 ──
PKG_DIR = Path(__file__).resolve().parent          # .../jc
ROOT_DIR = PKG_DIR.parent                          # skill 根目录
ASSETS_DIR = ROOT_DIR / "assets"
REFERENCE_DIR = ROOT_DIR / "reference"
MODEL_DIR = ASSETS_DIR / "model"                   # 4 件模型交付物
CF_DIR = ASSETS_DIR / "cf"                         # 现金流映射表（3 件）

# ── 模型交付物（4 件）──
MODEL_ARTIFACTS = {
    "fine_tuned": MODEL_DIR / "fine_tuned",                    # ① 微调 BGE
    "classifier": MODEL_DIR / "finance_classifier.pt",         # ② 分类头 + 元数据
    "subject_index": MODEL_DIR / "subject_to_index.json",      # ③ 科目索引
    "bucket_index": MODEL_DIR / "index_to_bucket.json",        # ④ 桶索引
}

# ── 推理参数 ──
INFERENCE_DEVICE = "auto"      # auto / cpu / cuda
CHUNK_SIZE = 64                # 单次 forward 的条数
MAX_SUMMARY_CHARS = 200        # 摘要字符级截断（tokenizer 再截 64 token）
MAX_SEQ_LEN = 64
CPU_MIN_FREE_MEM_GB = 2.5      # 显存空闲低于此值就用 CPU

# ── 置信度分档（直接按模型对最终桶的概率）──
# ⚠️ 这两档只是**给用户看的粗标签**，别拿它当质量诊断：模型对熟悉口径会饱和到
# 概率 1.0（实测中位数 1.0000、99.5% 落进「高」），三档几乎恒为全高。
# 真正有判别力的是**概率的排序** —— `cli.py units` 按概率升序输出单元清单，
# 复核就从最可疑的那个单元开始看。
CONF_HIGH = 0.80
CONF_MID = 0.50

# ── 业务桶的来源标记（落到导出列「分类判定」）──
# 为什么要这一列：桶可以由模型判、由硬规则写死、也可以被 AI 复核改判，
# 用户打开 Excel 时必须分得清哪个是哪个，否则"可审计"就是空话。
BUCKET_SRC_MODEL = "模型"
BUCKET_SRC_HARD = "硬规则"
BUCKET_SRC_PATCH = "复核改判"
BUCKET_SRC_FALLBACK = "兜底"        # 无摘要/模型没返回概率时的兜底分桶，别谎称是模型判的
COL_BUCKET_SRC = "分类判定"

# ── 20 个业务桶 ──
# 18 个模型可学习桶 + 2 个程序写死的硬规则桶
MODEL_BUCKETS = [
    "职工薪酬", "长期资产", "存货采购", "销售收入", "借款筹资", "政府补助",
    "折旧摊销", "利息收支", "分红股利", "生产制造", "研发", "投资本金",
    "日常费用", "员工借款", "押金保证金", "税费", "单位往来", "其他业务",
]
HARD_RULE_BUCKETS = ["资金内部往来", "汇兑损益"]
ALL_BUCKETS = MODEL_BUCKETS + HARD_RULE_BUCKETS

# ── 硬规则 ①：资金内部往来 ──
# 一张凭证的**全部**一级科目都属于货币资金 → 纯资金划转，无业务含义
CASH_SUBJECTS = {"库存现金", "银行存款", "其他货币资金", "其它货币资金"}

# ── 硬规则 ②：汇兑损益 ──
HARD_FX_SUBJECT = "财务费用"                    # 必须含财务费用
HARD_FX_OTHER_PL = {                            # 含任一则不是纯汇兑损益
    "管理费用", "销售费用", "研发费用",
    "投资收益", "公允价值变动损益",
    "主营业务收入", "其他业务收入", "营业外收入",
    "主营业务成本", "其他业务成本", "其它业务成本",
    "税金及附加", "营业税金及附加", "所得税费用",
    "营业外支出", "资产减值损失", "信用减值损失",
    "以前年度损益调整",
}
HARD_FX_BLACKLIST = {                           # 不可能出现在汇兑场景
    "应付职工薪酬", "应交税费", "应付利息", "应付股利", "应付债券", "应付票据",
}
HARD_FX_KEYWORDS = ("汇兑", "结汇", "收汇")     # 摘要需含其一

# ── 列名检测 ──
# 匹配语义：
#   含「明细」的模式 → 只做**精确相等**匹配（否则「科目明细」会抢走「科目」）
#   其余 → 子串匹配（容忍「借方金额(元)」这类后缀）
COLUMN_PATTERNS = {
    "date":         ["制单日期", "记账日期", "凭证日期", "日期", "date", "时间"],
    "voucher_no":   ["凭证号", "凭证编号", "凭证字号", "voucher", "单号", "字号"],
    "subject":      ["一级科目", "会计科目"],
    "subject_name": ["科目名称", "科目明细", "明细科目", "二级科目",
                     "会计科目名称", "一级科目名称"],
    "summary":      ["摘要", "说明", "备注", "业务描述", "description"],
    "debit":        ["借方金额", "借方", "借方发生额", "debit", "借"],
    "credit":       ["贷方金额", "贷方", "贷方发生额", "credit", "贷"],
    "bookkeeper":   ["制单人", "制表人", "记账人", "录入人"],
}

# 这些列名只允许对应字段使用（解决"序号/科目/科目名称"互相抢列）
FIELD_EXACT_ALIASES = {
    "subject": {"科目", "一级科目", "会计科目", "科目编码", "科目代码",
                "科目编号", "总账科目"},
}
# 该字段绝不接受这些列名
FIELD_EXCLUDE = {
    "subject": {"科目名称", "科目明细", "明细科目", "二级科目",
                "会计科目名称", "一级科目名称"},
}

REQUIRED_FIELDS = ("voucher_no", "subject", "debit", "credit")
FIELD_LABELS = {
    "voucher_no": "凭证号",
    "subject": "一级科目",
    "subject_name": "科目明细/二级科目",
    "summary": "摘要",
    "debit": "借方金额",
    "credit": "贷方金额",
    "bookkeeper": "制单人",
    "date": "日期",
}

SUPPORTED_EXT = {".xlsx", ".xls", ".xlsm", ".csv"}

# ============================================================================
# 训练相关（只有开发者版用得到，用户版发布时由打包脚本剔除）
# ============================================================================
# 设计：**训练直接产出到推理读取的位置**，训完不需要手动拷贝。
#       训练数据与备份放在 _dev/（发布时整个剔除）。
#
#   assets/model/             ← 推理读这里；训练也写这里（best 落盘）
#   _dev/training/            ← 训练数据（未审/金标准/备份）→ 发布剔除
#   _dev/models/              ← BGE 基座缓存（约 1.6GB，只有重训才用）→ 发布剔除
#   _dev/backups/             ← 上一版微调权重备份 → 发布剔除
DEV_DIR = ROOT_DIR / "_dev"
TRAIN_DIR = DEV_DIR / "training"            # 训练数据：金标准 + 未审 + 备份
BACKUP_DIR = DEV_DIR / "backups"            # 训练落盘前的上一版权重备份
MODEL_CACHE_DIR = DEV_DIR / "models"        # BGE 基座模型缓存（训练用）

# 训练超参（沿用技术报告 3.0 定稿值，改动前先读报告 §3.4）
TRAIN_MAX_LENGTH = 64          # 摘要 token 截断（压显存）
TRAIN_ENCODER_LR = 1e-5        # 编码器学习率（2e-5 会在第 1-2 轮后过拟合）
TRAIN_HEAD_LR = 1e-3           # 分类头学习率（随机初始化，需快收敛）
TRAIN_EPOCHS = 8               # 默认最大轮数（预训练模型 1-3 轮即收敛）
TRAIN_BATCH_SIZE = 4           # 8GB 显存压显存；3 线程并发会 OOM
TRAIN_EARLY_STOP = 5           # 连续 5 轮无提升则停
TRAIN_SUBJECT_DIM = 64         # 科目开关压缩维度
TRAIN_HIDDEN_DIM = 256         # 分类头隐层

# 训练数据格式版本（buckets_v2：按桶聚合，便于 AI 按桶审核）
RECORDS_FORMAT = "buckets_v2"
MAX_COUNT_EXPAND = 20          # 训练时单条记录最多展开成多少样本（防权重膨胀）

# 基座模型候选（训练时可换小的省显存；bge-base 已在缓存里）
MODEL_CHOICES = {
    "BAAI/bge-large-zh-v1.5": {"hidden_size": 1024, "params": "326M"},
    "BAAI/bge-base-zh-v1.5": {"hidden_size": 768, "params": "109M"},
}


def load_keywords_json() -> dict:
    """兼容旧训练数据模块的接口：返回 {桶名: [关键词]}。

    3.0 已删除程序关键词匹配（打分全在模型），此函数只为让训练数据模块
    能继续生成「桶含义说明」（AI 审核指南用）。文件不存在就返回空表。
    """
    import json
    p = ASSETS_DIR / "model" / "bucket_keywords.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return {b: [] for b in MODEL_BUCKETS}
