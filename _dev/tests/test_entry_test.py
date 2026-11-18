# -*- coding: utf-8 -*-
"""会计分录测试 2.0 自测。

两段：

    A. 纯逻辑（毫秒级）—— 凭证号切分、缺号、按月借贷（**红字是负数，不能取 abs**）、
       HHI、孤儿冲销的两态（闭环/孤儿）、金额孤立、科目组合的三条规则、
       风险白名单、线索表与汇总表、报告
    B. 真账（约 5s）—— case/序时账1.xlsx 的行表/完整性/画像/三层检验跑得通；
       业务活动用"按科目编码前缀造的假桶"代替上游清洗（这条测试不该依赖 GPU）

为什么 B 不跑整条链：画像必须接上游清洗，而清洗要 GPU 跑几分钟。链路的验收用
命令行做一次（`journal run --feature cleaning` → `journal run --feature entry_test
--option upstream=…`），这里只守住"数字对不对"。

用法：
    venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_entry_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import journal_io  # noqa: E402
from features.entry_test import benford, chains, detail, engine, fingerprint  # noqa: E402
from features.entry_test import activity, integrity, matrix, whitewash  # noqa: E402
from features.entry_test import profile  # noqa: E402
from features.entry_test import leads as leads_mod  # noqa: E402
from features.entry_test import report as report_mod  # noqa: E402  （测试里 report 是个局部变量）

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def frame_of(rows):
    """行表：只给测试关心的列，其余留空（与被测代码的 view() 口径一致）。"""
    frame = pd.DataFrame(rows)
    for col in ("subject", "subject_l2", "subject_code", "summary", "maker", "period"):
        if col not in frame:
            frame[col] = ""
    if "date" not in frame:
        frame["date"] = pd.NaT
    frame["amount"] = frame["debit"] + frame["credit"]
    return frame


# ============================================================================
# A. 纯逻辑
# ============================================================================

cases = {
    "2025年1月-记-0001号": ("2025年1月-记-", 1),
    "GSXSZ-001": ("GSXSZ-", 1),
    "2025年1月-转001号": ("2025年1月-转", 1),
    "40001001": ("4000", 1001),      # 纯数字：末 4 位是序号，前面是类型段
    "10001001": ("1000", 1001),
    "0001": ("", 1),                 # 短号不切，整段就是序号
}
for text, want in cases.items():
    got = integrity.split_series(text)
    check(got == want, f"凭证号切分：{text} → {got}，应为 {want}")
check(integrity.split_series("作废") is None, "没有数字段的凭证号应返回 None")

# 缺号：1、2、4 → 缺 3
gap = frame_of([
    {"vid": "记-0001", "debit": 100.0, "credit": 0.0},
    {"vid": "记-0001", "debit": 0.0, "credit": 100.0},
    {"vid": "记-0002", "debit": 50.0, "credit": 0.0},
    {"vid": "记-0002", "debit": 0.0, "credit": 50.0},
    {"vid": "记-0004", "debit": 10.0, "credit": 0.0},
    {"vid": "记-0004", "debit": 0.0, "credit": 10.0},
])
report = integrity.check(gap)
series = report["series"][0]
check(series["missing"] == [3], f"缺号应为 [3]，实际 {series['missing']}")
check(any(r["结果"] == integrity.WARN and r["检查项"] == "凭证号连续"
          for r in report["rows"]), "缺号没报成警告")

# 红字：借 100、贷 100、再借 -30 → 借合计 70、贷 100，差 -30（取 abs 就看不出来）
red = frame_of([
    {"vid": "记-0001", "debit": 100.0, "credit": 0.0, "period": "2025-01"},
    {"vid": "记-0001", "debit": 0.0, "credit": 100.0, "period": "2025-01"},
    {"vid": "记-0002", "debit": -30.0, "credit": 0.0, "period": "2025-01"},
])
month = integrity.check(red)["months"][0]
check(month["debit"] == 70.0 and month["diff"] == -30.0,
      f"红字行应按符号求和：借合计 {month['debit']}、差 {month['diff']}")
check(any(r["结果"] == integrity.BAD for r in integrity.check(red)["rows"]),
      "月度不平没报成问题")

# 两张凭证号相同但前缀不同 → 两条序列，各自不判缺号
mixed = frame_of([
    {"vid": "2025年1月-记-01", "debit": 1.0, "credit": 0.0},
    {"vid": "2025年1月-转-01", "debit": 1.0, "credit": 0.0},
])
check(len(integrity.check(mixed)["series"]) == 2, "不同凭证类型应分成两条序列")

check(profile.hhi([1]) == 1.0, "只有一家时 HHI 应为 1")
check(profile.hhi([1, 1]) == 0.5, "两家均分时 HHI 应为 0.5")
check(profile.hhi([]) == 0.0, "空集 HHI 应为 0")

buckets = {"A-1": "采购", "A-2": "采购", "A-3": "采购", "B-1": "押金保证金"}
vouchers = pd.DataFrame([
    {"vid": "A-1", "凭证号": "A-1", "期间": "2025-01", "日期": pd.Timestamp("2025-01-01"),
     "摘要": "买料", "制单人": "甲", "行数": 2, "借方合计": 900.0, "贷方合计": 900.0,
     "金额": 900.0, "科目": ["原材料", "应付账款"]},
    {"vid": "A-2", "凭证号": "A-2", "期间": "2025-01", "日期": pd.Timestamp("2025-01-02"),
     "摘要": "买料", "制单人": "乙", "行数": 2, "借方合计": 90.0, "贷方合计": 90.0,
     "金额": 90.0, "科目": ["原材料", "应付账款"]},
    {"vid": "A-3", "凭证号": "A-3", "期间": "2025-01", "日期": pd.Timestamp("2025-01-03"),
     "摘要": "买料", "制单人": "乙", "行数": 2, "借方合计": 10.0, "贷方合计": 10.0,
     "金额": 10.0, "科目": ["原材料", "银行存款"]},
    {"vid": "B-1", "凭证号": "B-1", "期间": "2025-01", "日期": pd.Timestamp("2025-01-04"),
     "摘要": "付押金", "制单人": "丙", "行数": 2, "借方合计": 500.0, "贷方合计": 500.0,
     "金额": 500.0, "科目": ["其他应收款", "银行存款"]},
])
rows = pd.DataFrame([
    {"vid": "A-1", "subject": "原材料", "debit": 900.0, "credit": 0.0},
    {"vid": "A-1", "subject": "应付账款", "debit": 0.0, "credit": 900.0},
    {"vid": "B-1", "subject": "其他应收款", "debit": 500.0, "credit": 0.0},
    {"vid": "B-1", "subject": "银行存款", "debit": 0.0, "credit": 500.0},
])
doc = profile.build(frame_of(rows.to_dict("records")), vouchers, buckets)
check([a["业务活动"] for a in doc["activities"]] == ["采购", "押金保证金"],
      f"活动应按金额降序：{[a['业务活动'] for a in doc['activities']]}")
buy = doc["activities"][0]
check(buy["凭证"] == 3 and buy["金额"] == 1000.0, f"采购的笔数/金额不对：{buy}")
# 采购的金额是 900/1000，HHI = 0.81 + 0.0081 + 0.0001 = 0.8182 → 高度集中
check(buy["金额HHI"] > 0.8, f"金额 HHI 应该在 0.8 以上：{buy['金额HHI']}")
check(buy["判读"].startswith("金额高度集中"), f"判读文字不对：{buy['判读']}")
check("picks" not in doc, "抽凭建议按决定砍掉了，不该再出现在模型里")
check("前3张占比" not in buy, "前3张占比与 HHI 说的是同一件事，已删")

# 报告：三节齐、自包含
model = report_mod.build({"feature_name": "会计分录测试", "input": {"filename": "小账.xlsx"}},
                         integrity.check(gap), doc, {"leads": [], "stats": {}})
html_text = report_mod.to_html(model)
for name, label in (("integrity", "完整性"), ("profile", "画像"),
                    ("leads", "线索总述"), ("layer_benford", "本福特"),
                    ("layer_amount", "金额特征孤立"),
                    ("layer_activity", "活动里的科目"),
                    ("layer_pair", "科目对孤立"),
                    ("layer_abnormal", "核算异常")):
    check(re.search(rf"id=['\"]{name}['\"]", html_text), f"报告缺{label}一节")
# 完整性只留四行汇总：逐月逐序列铺开就是"一片通过"，异常那行反而看不见
check(len(integrity.check(gap)["summary"]) == 4,
      f"完整性汇总应只有 4 行：{len(integrity.check(gap)['summary'])}")
check("<link" not in html_text and "src=" not in html_text and "<script" not in html_text,
      "报告应该是自包含的：不能有外链或脚本")
check("<th>综合得分</th>" not in html_text, "报告里不该出现「综合得分」这一列")
check("都没有发现值得单列的东西" in html_text, "没有线索时该有一句明确的说明")

# 有线索时：三层各自成节、本福特画图、报告里不放凭证明细（明细走 Excel）
chart_lead = leads_mod.lead(layer="总体层", kind="本福特·借方次位0", subject="费用",
                            evidence="e", vouchers=["记-1"], count=3,
                            amount=100.0, priority="中")
benford_stats = {"sides": {"借方": {
    "samples": 1200,
    "first": {"mad": 0.009, "verdict": "可接受",
              "obs": {str(d): 1200 // 9 for d in range(1, 10)}},
    "second": {"mad": 0.011, "verdict": "边缘",
               "obs": {str(d): 120 // 10 for d in range(0, 10)}}}},
    "skipped": []}
rich_model = report_mod.build(
    {"feature_name": "会计分录测试", "input": {"filename": "小账.xlsx"}},
    integrity.check(gap), doc,
    {"leads": [chart_lead], "stats": {"本福特": benford_stats},
     "counts": {"总体层": 1}})
rich_html = report_mod.to_html(rich_model)
check(rich_html.count("<svg") == 2, f"本福特该有首位/次位两张图：{rich_html.count('<svg')}")
check("<rect" in rich_html, "图里没有柱子")
check("实际" in rich_html and "本福特期望" in rich_html,
      "图缺图例，读的人不知道两根柱哪个是实际")
check("命中的线索" not in rich_html, "凭证明细不该进报告（它归 Excel）")
check(all(f"3.{n}" in rich_html for n in (1, 2, 3, 4)), "四块线索没有各自的编号")
check("secno" in rich_html, "分节编号没有渲染")

# ⑤ Excel 标记表：四列按凭证号铺到行上（报告里不出现明细，这里才是动手那张）
keys = pd.Series(["记-1", "记-2", "记-3"])
mk = detail.marks({
    "leads": [
        {"类型": "金额孤立", "凭证": ["记-1"], "优先级": "中"},
        {"类型": "孤儿冲销", "凭证": ["记-1"], "优先级": "高"},
        {"类型": "科目组合孤立·私人专线", "凭证": ["记-2"], "优先级": "低"},
    ],
    "whitewash": {"vouchers": [{"凭证号": "记-3", "放行原因": "汇兑损益"}]},
}, keys)
check(list(mk["是否异常"]) == ["是", "是", ""], f"是否异常列不对：{list(mk['是否异常'])}")
check(list(mk["异常原因"]) == ["金额孤立；孤儿冲销", "科目组合孤立·私人专线", ""],
      f"异常原因该合并多条：{list(mk['异常原因'])}")
check(list(mk["最高优先级"]) == ["高", "低", ""], f"优先级该取最高：{list(mk['最高优先级'])}")
check(list(mk["白名单放行"]) == ["", "", "汇兑损益"], "白名单没标到行上")

# ⑦ 活动 × 科目：某个活动里只出现一两次、金额却很大的科目
act_rows = []
for index in range(1, 201):           # 采购：200 张，全是原材料
    act_rows.append((f"采-{index:03d}", "原材料", 1000.0, 0.0, "采购", "2025-01"))
act_rows.append(("采-900", "生产成本", 5_000_000.0, 0.0, "采购", "2025-01"))
for index in range(1, 11):            # 生产：生产成本是常客
    act_rows.append((f"产-{index:03d}", "生产成本", 1000.0, 0.0, "生产", "2025-01"))
act_frame = frame_of([{"vid": v, "subject": s, "debit": d, "credit": c,
                       "summary": m, "period": p} for v, s, d, c, m, p in act_rows])
buckets = {row[0]: row[4] for row in act_rows}
act_doc = activity.detect(act_frame, buckets)
check(len(act_doc["leads"]) == 1, f"该只报采购里那笔生产成本：{len(act_doc['leads'])}")
hit = act_doc["leads"][0]
check(hit["主体"] == "采购 · 生产成本", f"主体不对：{hit['主体']}")
check(hit["凭证"] == ["采-900"], f"凭证不对：{hit['凭证']}")
check("0.50%" in hit["证据"], f"证据里该写清活动内占比：{hit['证据']}")


# ============================================================================
# B. 真账
# ============================================================================

BOOK = ROOT / "case" / "序时账1.xlsx"
if not BOOK.is_file():
    fails.append(f"测试账不存在：{BOOK}")
else:
    journal = journal_io.read(BOOK, requires=engine.REQUIRES)
    frame = engine.view(journal)
    vouchers = engine.vouchers(frame)

    check(len(frame) == 11604, f"行数不对：{len(frame)}")
    check(len(vouchers) == 2216, f"凭证数不对：{len(vouchers)}")
    check(str(frame["period"].iloc[0]).startswith("2025-"),
          f"期间应从日期推成 YYYY-MM：{frame['period'].iloc[0]}")

    report = integrity.check(frame)
    check(len(report["months"]) == 2, f"这份账只有两个月：{len(report['months'])}")
    check(all(m["diff"] == 0.0 for m in report["months"]),
          f"两个月的借贷都应该平：{[m['diff'] for m in report['months']]}")
    check(all(s["missing"] == [] for s in report["series"]),
          f"这份账的凭证号应该是连续的：{[s['missing'] for s in report['series']]}")
    check(len(report["series"]) == 2, f"应有两条序列（1 月/2 月）：{len(report['series'])}")
    check(report["ok"], "这份账的完整性应该通过")

    # 假桶：按科目编码前缀分，只为验证"数字守恒"，不代表真实业务分类
    code = frame["subject_code"].astype(str)
    fake = np.where(code.str.startswith("1"), "资金往来",
                    np.where(code.str.startswith("2"), "采购",
                             np.where(code.str.startswith("5"), "费用", "其他")))
    by_row = pd.Series(fake, index=frame.index)
    bucket_of = {}
    for vid, group in by_row.groupby(frame["vid"], sort=False):
        bucket_of[str(vid)] = str(group.mode().iloc[0])

    doc = profile.build(frame, vouchers, bucket_of)
    acts = doc["activities"]
    check(len(acts) == len(set(bucket_of.values())),
          f"活动数应等于桶数：{len(acts)} vs {len(set(bucket_of.values()))}")
    shares = sum(float(a["金额占比"].rstrip("%")) for a in acts)
    check(abs(shares - 100.0) < 0.5, f"金额占比应合计 100%：{shares}")
    check(sum(a["凭证"] for a in acts) == len(vouchers), "各活动凭证数应合计等于总凭证数")
    for act in acts:
        for key in ("业务活动", "凭证", "金额", "金额HHI", "对手方HHI", "判读"):
            check(key in act, f"画像行缺「{key}」")

    # 标记表：原表列序一个不动，只在末尾加四列；行数与账一致
    marked = detail.build(journal, {"leads": [], "whitewash": {}})["序时账"]
    baseline = journal.write_back()
    check(list(marked.columns) == list(baseline.columns)
          + ["是否异常", "异常原因", "最高优先级", "白名单放行"],
          f"标记表列不对：{list(marked.columns)}")
    check(len(marked) == len(baseline) == 11604, f"行数该与原账一致：{len(marked)}")
    check((marked["是否异常"] == "").all(), "没有线索时不该有任何行被标异常")


# ============================================================================
if fails:
    print(f"FAIL {len(fails)}")
    for msg in fails:
        print("  ✗", msg)
    sys.exit(1)
print("PASS 会计分录测试 2.0：纯逻辑 + 报告 + 真账")
