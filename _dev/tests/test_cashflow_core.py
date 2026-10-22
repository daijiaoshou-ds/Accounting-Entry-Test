# -*- coding: utf-8 -*-
"""现金流判定核心场景自测。

口径依据：docs/现金流量表编写理论.md + reference/CASHFLOW_GUIDE.md
    ① 项目范围只由业务桶决定
    ② 方向看货币资金科目在哪一方（借方=收现，贷方=付现），**不看正负号**
    ③ 单向桶（销售收入/存货采购/日常费用…）没有反方向候选：
       方向与桶不符 → 待复核，**不许**顺着方向挑个"支付其他…"糊过去
    ④ 中性项目（汇率变动对现金的影响）不看方向，桶定了就是它
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "summary_cleaner_v3"))
from jc.cashflow import MoneyRow, judge_voucher  # noqa: E402

CASES = []   # (标题, 凭证行, 业务桶, {行号: (方向, 项目, 来源)})


def add(title, rows, bucket, expect):
    CASES.append((title, rows, bucket, expect))


SALES_IN = "销售商品、提供劳务收到的现金"
OTHER_IN = "收到其他与经营活动有关的现金"
OTHER_OUT = "支付其他与经营活动有关的现金"
TAX_OUT = "支付的各项税费"
SALARY_OUT = "支付给职工以及为职工支付的现金"
BUY_OUT = "购买商品、接受劳务支付的现金"
DIVIDEND = "分配股利、利润或偿付利息支付的现金"
ASSET_BUY = "购建固定资产、无形资产和其他长期资产支付的现金"
INVEST_OUT = "投资支付的现金"
INVEST_IN = "收回投资收到的现金"
LOAN_IN = "取得借款收到的现金"
FX = "汇率变动对现金的影响"

# ① theory.md 的原例：借 应交税费 / 贷 银行存款 → 支付的各项税费
add("① 支付1月税费",
    [MoneyRow("应交税费", 100, 0, 0), MoneyRow("银行存款", 0, 100, 1)],
    "税费", {1: ("付", TAX_OUT, "桶+方向")})

# ② 销售收款（销售收入是**单向桶**，只有一个候选 → 桶唯一）
add("② 销售收款",
    [MoneyRow("银行存款", 500, 0, 0), MoneyRow("主营业务收入", 0, 500, 1)],
    "销售收入", {0: ("收", SALES_IN, "桶唯一")})

# ③ 销售退货：借 银行存款 -20（红字）→ 仍是"收"，金额 -20
#    这样与销售收款 100 万抵减后，报表上就是"销售收款 80 万"
add("③ 退货冲收现（借方红字）",
    [MoneyRow("银行存款", -20, 0, 0), MoneyRow("主营业务收入", 0, -20, 1)],
    "销售收入", {0: ("收", SALES_IN, "桶唯一")})

# ④ 销售退款走贷方（借方为 0）→ 付现方向，但销售收入只可能是收现
#    ★ 不许挑出"支付其他…"：那样等于把"桶判错了"洗成一个看着合理的项目。
#      要落待复核，回去改业务桶。
add("④ 销售退款（方向与桶不符 → 待复核）",
    [MoneyRow("银行存款", 0, 800, 0), MoneyRow("主营业务收入", 800, 0, 1)],
    "销售收入", {0: ("付", None, "待复核")})

# ⑤ 资金内部往来：不产生现金流量
add("⑤ 提现（资金内部往来）",
    [MoneyRow("库存现金", 1000, 0, 0), MoneyRow("银行存款", 0, 1000, 1)],
    "资金内部往来",
    {0: ("收", None, "无现金流量"), 1: ("付", None, "无现金流量")})

# ⑥ 计提折旧：非付现，没有货币资金行
add("⑥ 计提折旧",
    [MoneyRow("管理费用", 500, 0, 0), MoneyRow("累计折旧", 0, 500, 1)],
    "折旧摊销", {})

# ⑦ 同一行借贷都有数 → 冲销，无净流量
add("⑦ 同账户冲销分录",
    [MoneyRow("银行存款", 1000, 1000, 0)],
    "资金内部往来", {0: ("无", None, "冲销无净流量")})

# ⑧ 银行结息（利息收入）→ 收到其他与经营活动有关的现金
add("⑧ 银行结息（利息收入）",
    [MoneyRow("银行存款", 1964, 0, 0), MoneyRow("财务费用", 0, 1964, 1)],
    "利息收支", {0: ("收", OTHER_IN, "桶+方向")})

# ⑨ 支付手续费（借 财务费用）：利息收支付现有两个候选
#    （偿付利息 / 支付其他…），财务费用这个一级科目**证不成**是哪一个
#    → 走兜底表默认的"支付其他…"。这是高频小额业务，不该全推人工。
add("⑨ 支付银行手续费（兜底为支付其他）",
    [MoneyRow("财务费用", 500, 0, 0), MoneyRow("银行存款", 0, 500, 1)],
    "利息收支", {1: ("付", OTHER_OUT, "桶兜底")})

# ⑨b 偿付利息：有「应付利息」这个一级科目当裁判 → 偿付利息（筹资活动）
add("⑨b 支付利息（科目裁判为分配股利/利润/偿付利息）",
    [MoneyRow("应付利息", 500, 0, 0), MoneyRow("银行存款", 0, 500, 1)],
    "利息收支", {1: ("付", DIVIDEND, "科目裁判")})

# ⑨c 支付股利：同样靠「应付股利」裁判
add("⑨c 支付股利（科目裁判为分配股利/利润/偿付利息）",
    [MoneyRow("应付股利", 800, 0, 0), MoneyRow("银行存款", 0, 800, 1)],
    "利息收支", {1: ("付", DIVIDEND, "科目裁判")})

# ⑨d 汇兑损益：不是"没有现金流"，而是不归收付两侧
add("⑨d 汇兑收益（中性项目：汇率变动对现金的影响）",
    [MoneyRow("银行存款", 300, 0, 0), MoneyRow("财务费用", 0, 300, 1)],
    "汇兑损益", {0: ("收", FX, "中性项目")})
add("⑨e 汇兑损失（中性项目，付现方向也是同一个项目）",
    [MoneyRow("财务费用", 300, 0, 0), MoneyRow("银行存款", 0, 300, 1)],
    "汇兑损益", {1: ("付", FX, "中性项目")})

# ⑨f 未分类的桶：不能静默当成"不影响现金流"，要复核
add("⑨f 桶不在映射表里（未分类 → 待复核）",
    [MoneyRow("银行存款", 100, 0, 0), MoneyRow("其他应收款", 0, 100, 1)],
    "无此桶", {0: ("收", None, "未分类")})

# ⑩ 职工薪酬
add("⑩ 发放工资",
    [MoneyRow("应付职工薪酬", 900, 0, 0), MoneyRow("银行存款", 0, 900, 1)],
    "职工薪酬", {1: ("付", SALARY_OUT, "桶唯一")})

# ⑪ 采购付款（存货采购也是单向桶 → 桶唯一）
add("⑪ 采购付款",
    [MoneyRow("原材料", 700, 0, 0), MoneyRow("银行存款", 0, 700, 1)],
    "存货采购", {1: ("付", BUY_OUT, "桶唯一")})

# ⑫ 采购退款：存货采购只可能是付现 → 待复核（同 ④ 的道理）
add("⑫ 采购退款（方向与桶不符 → 待复核）",
    [MoneyRow("银行存款", 70, 0, 0), MoneyRow("原材料", 0, 70, 1)],
    "存货采购", {0: ("收", None, "待复核")})

# ⑬ 取得借款：股东投入会归到「投资本金」，所以「借款筹资」收现只有一种可能
add("⑬ 取得借款",
    [MoneyRow("银行存款", 5000, 0, 0), MoneyRow("短期借款", 0, 5000, 1)],
    "借款筹资", {0: ("收", LOAN_IN, "桶+方向")})

# ⑬b 实收资本 → 投资本金 + 收现，是**歧义桶**，靠一级科目当裁判：
#     实收资本 → 吸收投资收到的现金（筹资活动）
add("⑬b 股东实缴出资（科目裁判为吸收投资）",
    [MoneyRow("银行存款", 10000, 0, 0), MoneyRow("实收资本", 0, 10000, 1)],
    "投资本金", {0: ("收", "吸收投资收到的现金", "科目裁判")})

# ⑬c 交易性金融资产赎回 → 同样是投资本金 + 收现，裁判为收回投资（投资活动）
add("⑬c 赎回理财（科目裁判为收回投资）",
    [MoneyRow("银行存款", 2500, 0, 0), MoneyRow("交易性金融资产", 0, 2500, 1)],
    "投资本金", {0: ("收", "收回投资收到的现金", "科目裁判")})

# ⑬d 长期股权投资处置 → 收回投资
add("⑬d 处置长期股权投资（科目裁判为收回投资）",
    [MoneyRow("银行存款", 8000, 0, 0), MoneyRow("长期股权投资", 0, 8000, 1)],
    "投资本金", {0: ("收", "收回投资收到的现金", "科目裁判")})

# ⑬e 一级科目没登记在裁判表里 → 不硬猜，交人工
#     （投资本金收现**没有**兜底表：三个候选都可能是对的，不能瞎默认）
add("⑬e 歧义桶但科目未登记（应待复核）",
    [MoneyRow("银行存款", 600, 0, 0), MoneyRow("其他权益工具", 0, 600, 1)],
    "投资本金", {0: ("收", None, "待复核")})

# ⑬f 收到投资收益（用户口径 2026-10：投资本金收现**加了**"取得投资收益收到的现金"）
#     以前这个项目不在候选里，"收到理财分红收益"从候选里挑哪个都错，
#     只能靠 --cashflow 硬指。现在靠「投资收益」这个一级科目证成。
add("⑬f 收到理财分红收益（科目裁判为取得投资收益）",
    [MoneyRow("银行存款", 300, 0, 0), MoneyRow("投资收益", 0, 300, 1)],
    "投资本金", {0: ("收", "取得投资收益收到的现金", "科目裁判")})

add("⑬f2 收到子公司分红（应收股利）",
    [MoneyRow("银行存款", 500, 0, 0), MoneyRow("应收股利", 0, 500, 1)],
    "投资本金", {0: ("收", "取得投资收益收到的现金", "科目裁判")})

# ⑬g 赎回理财**同时带收益**：本金科目 + 收益科目都在 → 命中 2 个候选
#     → 待复核（这是加"取得投资收益"的**已知代价**，用户确认接受）。
#     一张凭证只有一条货币资金行，只能给一个项目；判不了就交人工，
#     比洗成一个错项目好。
add("⑬g 赎回理财带收益（命中2候选 → 待复核）",
    [MoneyRow("银行存款", 10500, 0, 0), MoneyRow("交易性金融资产", 0, 10000, 1),
     MoneyRow("投资收益", 0, 500, 2)],
    "投资本金", {0: ("收", None, "待复核")})

# ⑭ 买设备
add("⑭ 购建固定资产",
    [MoneyRow("固定资产", 3000, 0, 0), MoneyRow("银行存款", 0, 3000, 1)],
    "长期资产", {1: ("付", ASSET_BUY, "桶+方向")})

# ⑮ 投资理财 / 赎回
add("⑮ 购买理财",
    [MoneyRow("交易性金融资产", 2000, 0, 0), MoneyRow("银行存款", 0, 2000, 1)],
    "投资本金", {1: ("付", INVEST_OUT, "桶+方向")})
add("⑯ 赎回理财",
    [MoneyRow("银行存款", 2500, 0, 0), MoneyRow("交易性金融资产", 0, 2500, 1)],
    "投资本金", {0: ("收", INVEST_IN, "科目裁判")})

# ⑰ 非货币资金行不产生结论
add("⑰ 非货币资金行",
    [MoneyRow("管理费用", 100, 0, 0), MoneyRow("应付账款", 0, 100, 1)],
    "日常费用", {})

# ⑱ 日常费用收款：费用退款是真实业务，但"日常费用"是单向桶
#     → 一样落待复核（回去看是不是该进 单位往来 / 其他业务）
add("⑱ 日常费用收到退款（方向与桶不符 → 待复核）",
    [MoneyRow("银行存款", 300, 0, 0), MoneyRow("管理费用", 0, 300, 1)],
    "日常费用", {0: ("收", None, "待复核")})


# ============================================================================
# 映射表自带的不变量：口径写在表里，别只写在注释里
# ============================================================================
# 这些断言防的是"改表时把口径改回去"——例如又给销售收入挂上"支付其他…"。
ONE_WAY = {          # 桶名自带方向 → 只能有一个方向的候选
    "销售收入": "收", "政府补助": "收",
    "职工薪酬": "付", "存货采购": "付", "生产制造": "付",
    "研发": "付", "日常费用": "付", "分红股利": "付",
}
BOTH_WAY = ["税费", "长期资产", "借款筹资", "投资本金", "利息收支",
            "员工借款", "押金保证金", "单位往来", "其他业务"]
NO_CASH = ["折旧摊销", "资金内部往来"]


def check_mapping(fails):
    from jc import cashflow as c

    def one(bucket, d):
        return c.filter_by_direction(c.bucket_items(bucket), d)

    v = c.validate()
    if v["problem_count"]:
        fails.extend(f"映射表自检问题: {p}" for p in v["problems"])
    if v["bucket_count"] != 20:
        fails.append(f"业务桶数应为 20，实得 {v['bucket_count']}")
    if v["cf_item_count"] != 24:
        fails.append(f"现金流量项目数应为 24，实得 {v['cf_item_count']}")

    for bkt, want in ONE_WAY.items():
        items = c.bucket_items(bkt)
        if not items:
            fails.append(f"单向桶「{bkt}」没有候选")
            continue
        if one(bkt, "收" if want == "付" else "付"):
            fails.append(f"单向桶「{bkt}」不该有反方向候选（{want}向桶）")
        if not one(bkt, want):
            fails.append(f"单向桶「{bkt}」缺 {want} 现候选")

    for bkt in BOTH_WAY:
        if not one(bkt, "收") or not one(bkt, "付"):
            fails.append(f"无方向桶「{bkt}」应收付两侧都有候选")

    for bkt in NO_CASH:
        if c.bucket_items(bkt):
            fails.append(f"「{bkt}」不该有候选（不产生现金流量）")

    # 汇兑损益：不是"没有现金流"，是不归收付两侧
    if c.bucket_items("汇兑损益") != ["汇率变动对现金的影响"]:
        fails.append(f"汇兑损益的候选不对: {c.bucket_items('汇兑损益')}")

    # 兜底只能用在"证不成"的地方：利息收支付现有兜底，投资本金收现**没有**
    if c.fallback_item("利息收支", "付") != "支付其他与经营活动有关的现金":
        fails.append("利息收支/付现 的兜底不对")
    if c.fallback_item("投资本金", "收"):
        fails.append("投资本金/收现 不该有兜底（三个候选都可能是对的，只能交人工）")

    # 投资本金收现的**三个**候选（用户口径 2026-10 加了"取得投资收益"）
    want = ["收回投资收到的现金", "吸收投资收到的现金", "取得投资收益收到的现金"]
    if one("投资本金", "收") != want:
        fails.append(f"投资本金/收现 的候选应为 {want}，实得 {one('投资本金', '收')}")
    # 三个候选都必须在裁判表里登记 —— 漏一个，"证得出来"的那笔业务就永远落待复核
    from jc.cashflow import load_cf_config
    ruling = load_cf_config()["ruling"]
    for item in want:
        if item not in ruling:
            fails.append(f"投资本金/收现 的候选「{item}」没有裁判科目，"
                         f"这条业务就判不出来")
    # 取得投资收益必须能靠科目证成（投资收益/应收股利/应收利息）
    for subj, item in (("投资收益", "取得投资收益收到的现金"),
                       ("应收股利", "取得投资收益收到的现金"),
                       ("实收资本", "吸收投资收到的现金"),
                       ("交易性金融资产", "收回投资收到的现金")):
        picked, why = c.rule_by_account(want, [subj])
        if picked != item:
            fails.append(f"裁判：出现「{subj}」应判「{item}」，实得「{picked or why}」")
    # 赎回带收益：本金科目 + 收益科目同时出现 → 必须落待复核，不许猜
    picked, why = c.rule_by_account(want, ["交易性金融资产", "投资收益"])
    if picked:
        fails.append(f"赎回带收益应落待复核（候选命中 2 个），实得「{picked}」")


def check_counterparty() -> list:
    """对方科目必须**去重 + 计数**。

    真账实测：一条待复核项的「对方科目」列了 **85 个**，`管理费用` 重复 30+ 次、
    `研发费用` 20+ 次 —— 当提示用等于噪声。所以现在给的是
    去重后的有序列表（按出现次数降序）+ `{科目: 次数}`。
    """
    fails = []
    print("\n=== 对方科目：去重 + 计数 ===")
    rows = [MoneyRow("管理费用", 100, 0, 0),
            MoneyRow("管理费用", 50, 0, 1),
            MoneyRow("研发费用", 30, 0, 2),
            MoneyRow("管理费用", 20, 0, 3),
            MoneyRow("银行存款", 0, 200, 4)]
    verdicts = judge_voucher(rows, "日常费用")
    if len(verdicts) != 1:
        return ["对方科目场景：应当只有 1 条货币资金结论"]
    v = verdicts[0][1]
    print(f"  对方科目={v.counterparty}  笔数={v.counterparty_counts}")
    if v.counterparty != ["管理费用", "研发费用"]:
        fails.append(f"对方科目应去重且按次数降序，实得 {v.counterparty}")
    if v.counterparty_counts != {"管理费用": 3, "研发费用": 1}:
        fails.append(f"对方科目笔数不对：{v.counterparty_counts}")
    if "银行存款" in v.counterparty:
        fails.append("货币资金科目不该出现在「对方科目」里")

    # 冲销行（no_cash）也要带上同样的上下文
    wash = [MoneyRow("财务费用", 10, 10, 0), MoneyRow("银行存款", 5, 5, 1)]
    wv = judge_voucher(wash, "利息收支")[0][1]
    if not wv.no_cash or wv.counterparty != ["财务费用"]:
        fails.append(f"冲销行也该带对方科目，实得 {wv.counterparty}")
    return fails


def check_review_payload() -> list:
    """待复核清单：行号 1-based、带金额与方向、对方科目已整理、**能回到单元**。"""
    from jc.cashflow import make_review_payload, summarize_review
    fails = []
    print("\n=== 待复核出参：字段与行号口径 ===")
    rows = [MoneyRow("管理费用", 100, 0, 0), MoneyRow("银行存款", 0, 100, 7)]
    verdicts = judge_voucher(rows, "销售收入")      # 单向桶 + 付现 → 待复核
    cf_by_row = {}
    counterparts, counts = {}, {}
    for row_no, v in verdicts:
        cf_by_row[row_no] = {
            "cf_item": v.cf_item, "direction": v.direction, "amount": v.amount,
            "source": v.source, "needs_review": v.needs_review,
            "no_cash": v.no_cash, "candidates": v.candidates,
            "bucket": v.bucket, "note": v.note,
        }
        counterparts[row_no] = list(v.counterparty)
        counts[row_no] = dict(v.counterparty_counts)
    info = {7: {"凭证号": "记-007", "科目": "银行存款", "摘要": "支付货款",
                "借方": 0.0, "贷方": 100.0}}
    # ★ 凭证号 → 单元：没有它，AI 读完待复核清单也回不到可改判的单元
    unit_of = {"记-007": {"单元号": "U137", "单元键": "库存现金[贷] + 销售费用[借] :: 支付货款",
                          "模型桶": "销售收入"}}
    items = make_review_payload(cf_by_row, info, counterparts, counts, unit_of)
    if len(items) != 1:
        return ["待复核清单应当有 1 条"]
    it = items[0]
    print(f"  {it['凭证号']} 行号={it['行号']} 单元={it['单元号']} "
          f"方向={it['现金流方向']} 对方科目={it['对方科目']} "
          f"笔数={it['对方科目笔数']}")
    if it["行号"] != 8:      # 内部 7（0-based）→ 对外 8（1-based，+1）
        fails.append(f"行号应为 8（1-based，表体第 8 行），实得 {it['行号']}")
    if it["借方金额"] != 0.0 or it["贷方金额"] != 100.0:
        fails.append("应带上借贷金额（判方向够用，不用回源文件查）")
    if it["对方科目"] != ["管理费用"] or it["对方科目笔数"] != {"管理费用": 1}:
        fails.append("待复核清单里的对方科目/笔数不对")
    if it["候选项目"]:
        fails.append("单向桶反方向不该给候选（空才对，免得诱导硬配）")
    # ── A1：单元号 / 单元键 / 模型桶 必须在每条上 ──
    if it["单元号"] != "U137" or not it["单元键"]:
        fails.append("待复核条目必须带 单元号/单元键 —— 那是 run --unit 唯一的把手")
    if it["模型桶"] != "销售收入":
        fails.append("要带模型的**原始**桶：业务分类可能已被改判，模型桶才是线索")

    # 没有 unit_of 时不能崩（旧调用方 / 单元映射缺失）
    legacy = make_review_payload(cf_by_row, info, counterparts, counts)
    if legacy[0]["单元号"] != "":
        fails.append("没有单元映射时应留空，不能编一个")
    print(f"  无单元映射时 单元号={legacy[0]['单元号']!r}（留空，不编）")

    # ── 按单元汇总：N 条 → 几个单元 ──
    many = [dict(it, 行号=i, 单元号=("U137" if i < 4 else "U200"),
                 单元键="k", 原因="方向与桶不符") for i in range(1, 6)]
    s = summarize_review(many)
    print(f"  按单元汇总：{[ (g['单元号'], g['条数']) for g in s ]}")
    if s[0]["单元号"] != "U137" or s[0]["条数"] != 3:
        fails.append(f"汇总应按条数降序聚到单元上：{s}")
    if sum(g["条数"] for g in s) != 5:
        fails.append("汇总不能漏条")
    return fails


def main():
    fails = []
    check_mapping(fails)
    print(f"映射表不变量：{len(ONE_WAY)} 个单向桶 / {len(BOTH_WAY)} 个无方向桶 / "
          f"{len(NO_CASH)} 个不产生现金流 —— "
          + ("通过" if not fails else "有问题"))
    for title, rows, bucket, expect in CASES:
        print(f"\n=== {title} | 业务桶={bucket or '(无)'} ===")
        got = {}
        for row_no, v in judge_voucher(rows, bucket):
            got[row_no] = (v.direction, v.cf_item or None, v.source)
            print(f"  行{row_no}  {v.direction}现  {v.cf_item or '(空)'}  "
                  f"[{v.source}]  金额={v.amount}  复核={v.needs_review}")
            if v.note:
                print(f"        备注: {v.note}")
            # 不变量：来源「待复核 / 未分类」必须真的被标成要复核，反之亦然
            if v.source in ("待复核", "未分类") and not v.needs_review:
                fails.append(f"{title} 行{row_no}: 来源是{v.source}却没标需复核")
            if v.source not in ("待复核", "未分类") and v.needs_review:
                fails.append(f"{title} 行{row_no}: 来源是{v.source}却被标需复核")
        for row_no, want in expect.items():
            if got.get(row_no) != want:
                fails.append(f"{title} 行{row_no}: 期望 {want}，实得 {got.get(row_no)}")
        extra = set(got) - set(expect)
        if extra:
            fails.append(f"{title}: 多出结论行 {sorted(extra)}")

    fails += check_counterparty()
    fails += check_review_payload()

    print("\n" + "=" * 70)
    if fails:
        print(f"❌ {len(fails)} 项失败：")
        for f in fails:
            print("   -", f)
        return 1
    print(f"✅ 全部 {len(CASES)} 个场景通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
