# -*- coding: utf-8 -*-
"""旧桶 → 新桶（v10）桥接器。

用途：不改模型，先用「旧桶 + 科目组合 + 摘要」把现有金标准重标到 33 桶体系，
     验证六分类是否可判、父级回退率多少、哪些样本本质不可判。

输入：summary_cleaner/nn/_storage/training_data.json（17,424 条摘要）
输出：_dev/bridge_report.md（分布、回退率、边界样本、旧新交叉表）

运行：python summary_cleaner/v2/bucket_bridge.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_THIS = Path(__file__).resolve()
ROOT = _THIS.parents[2]
CFG_PATH = _THIS.parent / "assets" / "bucket_taxonomy_v10.json"
TRAIN_PATH = ROOT / "summary_cleaner" / "nn" / "_storage" / "training_data.json"
REPORT_PATH = ROOT / "_dev" / "bridge_report.md"

CASH = {"库存现金", "银行存款", "其他货币资金", "其它货币资金"}

# 费用六类的判定优先级（强信号在前，兜底在后）
EXPENSE_ORDER = [
    "费用·中介与专业服务",
    "费用·市场与销售",
    "费用·能源与场地运营",
    "费用·材料与低值易耗",
    "费用·办公与行政运营",
    "费用·治理与综合",
]

# 权益类 / 投资资产类 / 收益类，用于拆「投资本金」
EQUITY = {"实收资本", "股本", "资本公积"}
INVEST_ASSET = {
    "交易性金融资产", "债权投资", "其他债权投资", "其他权益工具投资",
    "长期股权投资", "持有至到期投资", "可供出售金融资产",
    "定期存款", "大额存单", "结构性存款", "理财产品", "投资性房地产",
}
INVEST_INCOME = {"投资收益", "应收股利", "应收利息"}


# ---------------------------------------------------------------- 解析

def parse_subjects(subjects):
    """['银行存款[借]', '实收资本[贷]'] → (['银行存款','实收资本'], {'银行存款':'借',...})"""
    names, dirs = [], {}
    for s in subjects or []:
        s = str(s).strip()
        if "[" in s and s.endswith("]"):
            name, d = s.rsplit("[", 1)
            name = name.strip()
            dirs[name] = d[:-1]
        else:
            name = s
        if name and name not in names:
            names.append(name)
    return names, dirs


def cash_direction(dirs):
    """货币资金科目的借贷方向 → 收/付；无货币资金 → None"""
    for c in CASH:
        if c in dirs:
            return "收" if dirs[c] == "借" else "付"
    return None


def any_in(names, pool):
    return bool(set(names) & set(pool))


# ---------------------------------------------------------------- 六分类

def classify_expense(names, summary, cfg):
    """日常费用 → 六类之一，判不出回退父级「费用」"""
    s = summary or ""

    # 跨桶纠正 0：备用金/借支 → 员工借支（是资金往来，不是费用）
    if any(k in s for k in ("备用金", "借支", "暂支")):
        return "员工借支", "跨桶纠正:借支词"

    # 跨桶纠正 1：纯薪酬凭证（有应付职工薪酬、且没有任何费用类科目）
    #   —— 发放工资/社保/公积金/生日卡这类，直接借记应付职工薪酬
    #   注意：带"管理费用/销售费用"的凭证是"福利费报销"，要按内容判，
    #   不能因为科目里有应付职工薪酬就一律归薪酬（聚餐≠薪酬）
    EXP_SUBJECTS = {"管理费用", "销售费用", "研发支出", "研发费用", "制造费用", "生产成本"}
    if "应付职工薪酬" in names and not (set(names) & EXP_SUBJECTS):
        return "薪酬", "科目:纯应付职工薪酬"

    # 跨桶纠正 1b：只认"发给员工的钱"，不认"活动/消费"
    #   工资/社保/公积金 → 薪酬；聚餐/团建/食堂 → 走六类（F6 治理与综合）
    if any(k in s for k in ("工资", "薪金", "薪酬", "社保", "公积金", "个税", "代扣代缴")):
        return "薪酬", "跨桶纠正:薪酬词"

    # 跨桶纠正 2：进口环节的报关/代理费 → 采购（计入存货成本）
    if "进口" in s and any(k in s for k in ("报关", "代理", "关税")):
        return "采购", "跨桶纠正:进口关务"

    # 银行手续费单列：财务费用 + 手续费类词
    if "财务费用" in names and any(k in s for k in ("手续费", "网银", "账户管理", "工本费", "结算费")):
        return "银行手续费", "财务费用+手续费词"

    # 六类：按"命中词数量"多数投票；平局（两类各命中 1 个）才归父级。
    # 不能一命中多类就归父级——"差旅费+餐费"是常见组合，多数情况下主语仍明确。
    hits = {}
    for bucket in EXPENSE_ORDER:
        kws = cfg["buckets"][bucket].get("keywords", [])
        h = [k for k in kws if k in s]
        if h:
            hits[bucket] = h

    if hits:
        ranked = sorted(hits.items(), key=lambda x: -len(x[1]))
        top_b, top_h = ranked[0]
        if len(ranked) > 1 and len(ranked[1][1]) == len(top_h):
            return "费用", "多类平局→父级:" + "/".join([ranked[0][0], ranked[1][0]])
        return top_b, "关键词:" + "/".join(top_h[:3])

    return "费用", "无关键词→父级"


# ---------------------------------------------------------------- 旧桶映射

def map_record(old_bucket, names, dirs, summary, cfg):
    """返回 (新桶, 依据)。依据用于抽样核查。"""
    d = cash_direction(dirs)
    has_cash = d is not None
    s = summary or ""

    if old_bucket == "销售收入":
        return "销售", "旧桶"

    if old_bucket == "存货采购":
        return "采购", "旧桶"

    if old_bucket == "职工薪酬":
        return "薪酬", "旧桶"

    if old_bucket == "税费":
        return "税费", "旧桶"

    if old_bucket == "研发":
        return "研发", "旧桶"

    if old_bucket == "长期资产":
        # 桶不含方向：挂账未付的购建（借在建工程/贷应付账款）同样是"长期资产购建"，
        # 只是方向=无、现金流=0。不能因为没出现货币资金就落待复核。
        if any_in(names, {"固定资产清理"}) or d == "收":
            return "长期资产处置", "处置科目/方向收"
        return "长期资产购建", "购建（含挂账未付）"

    if old_bucket == "投资本金":
        if any_in(names, EQUITY):
            return "权益筹资", "锚定:" + "/".join(sorted(set(names) & EQUITY))
        if any_in(names, INVEST_INCOME):
            return "投资收益", "锚定:" + "/".join(sorted(set(names) & INVEST_INCOME))
        if any_in(names, INVEST_ASSET):
            return "投资", "锚定投资资产"
        return "投资", "无锚定→默认投资"

    if old_bucket == "分红股利":
        if d == "收":
            return "投资收益", "方向收"
        if d == "付":
            return "利润分配", "方向付"
        return "利润分配", "无现金→默认分配"

    if old_bucket == "借款筹资":
        return "借款", "旧桶"

    if old_bucket == "利息收支":
        if d == "收":
            return "存款利息", "方向收"
        return "利息支出", "方向付/无现金"

    if old_bucket == "政府补助":
        if any(k in s for k in ("退税", "即征即退", "先征后返", "留抵")):
            return "税费", "退税词"
        return "政府补助", "旧桶"

    if old_bucket == "押金保证金":
        return "押金保证金", "旧桶"

    if old_bucket == "员工借款":
        return "员工借支", "旧桶"

    if old_bucket == "单位往来":
        return "单位往来", "旧桶"

    if old_bucket == "生产制造":
        if not has_cash:
            return "成本结转", "无货币资金"
        return classify_expense(names, s, cfg)

    if old_bucket == "折旧摊销":
        return "折旧摊销", "旧桶"

    if old_bucket == "日常费用":
        return classify_expense(names, s, cfg)

    if old_bucket == "其他业务":
        if any(k in s for k in ("结转", "本年利润", "期间损益")):
            return "期末结转", "结转词"
        return "其他经营收支", "旧桶"

    if old_bucket == "资金内部往来":
        return "资金内部往来", "硬规则"

    if old_bucket == "汇兑损益":
        return "汇兑损益", "硬规则"

    return "待复核", "未知旧桶"


# ---------------------------------------------------------------- 主流程

def main():
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    buckets = cfg["buckets"]
    data = json.loads(TRAIN_PATH.read_text(encoding="utf-8"))
    old_buckets = data["buckets"]

    rows = []  # (旧桶, 新桶, 依据, 摘要, count, subjects)
    for old_b, groups in old_buckets.items():
        for g in groups:
            subjects = g.get("subjects", [])
            names, dirs = parse_subjects(subjects)
            for rec in g.get("records", []):
                summary = str(rec.get("summary", ""))
                cnt = int(rec.get("count", 1) or 1)
                new_b, why = map_record(old_b, names, dirs, summary, cfg)
                rows.append({
                    "old": old_b, "new": new_b, "why": why,
                    "summary": summary, "count": cnt, "subjects": subjects,
                })

    total = len(rows)
    total_count = sum(r["count"] for r in rows)

    # 统计
    by_new = defaultdict(lambda: {"n": 0, "c": 0, "why": Counter()})
    for r in rows:
        by_new[r["new"]]["n"] += 1
        by_new[r["new"]]["c"] += r["count"]
        by_new[r["new"]]["why"][r["why"].split(":")[0]] += 1

    cross = defaultdict(Counter)  # 旧桶 → 新桶 count
    for r in rows:
        cross[r["old"]][r["new"]] += 1

    fallback_n = by_new.get("费用", {"n": 0})["n"]
    pending_n = by_new.get("待复核", {"n": 0})["n"]

    # ---- 报告
    L = []
    L.append("# 桥接报告：旧 18 桶 → 新 33 桶\n")
    L.append(f"- 输入：`{TRAIN_PATH.name}`，{total:,} 条摘要 / count {total_count:,}")
    L.append(f"- 配置：`{CFG_PATH.name}`，{len(buckets)} 桶")
    L.append(f"- 回退父级「费用」：{fallback_n} 条（{fallback_n/total:.1%}）")
    L.append(f"- 落「待复核」：{pending_n} 条（{pending_n/total:.1%}）")
    L.append("")

    L.append("## 一、新桶分布\n")
    L.append("| 新桶 | 域 | 摘要条数 | count | 占摘要 | 判定依据 Top |")
    L.append("|---|---|---:|---:|---:|---|")
    for name, st in sorted(by_new.items(), key=lambda x: -x[1]["n"]):
        dom = buckets.get(name, {}).get("domain", "父级")
        top = "、".join(f"{k}({v})" for k, v in st["why"].most_common(3))
        L.append(f"| {name} | {dom} | {st['n']:,} | {st['c']:,} | {st['n']/total:.1%} | {top} |")
    L.append("")

    L.append("## 二、旧桶 → 新桶 交叉表\n")
    L.append("| 旧桶 | 拆到的新桶（条数） |")
    L.append("|---|---|")
    for old_b in sorted(cross, key=lambda k: -sum(cross[k].values())):
        total_old = sum(cross[old_b].values())
        parts = "、".join(f"{k}({v})" for k, v in cross[old_b].most_common())
        L.append(f"| {old_b}（{total_old:,}） | {parts} |")
    L.append("")

    # 六类内部
    six = [k for k in buckets if buckets[k].get("parent") == "费用"]
    L.append("## 三、费用六类分布\n")
    L.append("| 子类 | 条数 | 占费用类 |")
    L.append("|---|---:|---:|")
    six_total = sum(by_new[k]["n"] for k in six)
    for k in sorted(six, key=lambda x: -by_new[x]["n"]):
        n = by_new[k]["n"]
        L.append(f"| {k} | {n:,} | {n/max(six_total,1):.1%} |")
    L.append(f"| **父级回退（费用）** | **{fallback_n:,}** | **{fallback_n/max(six_total+fallback_n,1):.1%}** |")
    L.append(f"| 合计 | {six_total+fallback_n:,} | |")
    L.append("")

    # 抽样
    L.append("## 四、每桶抽样（各 3 条）\n")
    samples = defaultdict(list)
    for r in rows:
        if len(samples[r["new"]]) < 3:
            samples[r["new"]].append(r)
    for name, st in sorted(by_new.items(), key=lambda x: -x[1]["n"]):
        L.append(f"### {name}")
        for r in samples[name]:
            subj = "、".join(r["subjects"][:4])
            L.append(f"- `{r['summary'][:60]}` · {subj} · ×{r['count']} · {r['why']}")
        L.append("")

    # 边界：回退父级的样本
    fb = [r for r in rows if r["new"] == "费用"]
    if fb:
        L.append(f"## 五、回退父级的样本（共 {len(fb)} 条，抽样 40）\n")
        L.append("> 这些是「确定是费用，但内容说不清」的样本。若比例过高，说明六类关键词覆盖不足。\n")
        for r in sorted(fb, key=lambda x: -x["count"])[:40]:
            subj = "、".join(r["subjects"][:4])
            L.append(f"- `{r['summary'][:70]}` · {subj} · ×{r['count']}")
        L.append("")

    # 待复核
    pd_ = [r for r in rows if r["new"] == "待复核"]
    if pd_:
        L.append(f"## 六、落待复核（共 {len(pd_)} 条）\n")
        for r in sorted(pd_, key=lambda x: -x["count"])[:30]:
            L.append(f"- [{r['old']}] `{r['summary'][:70]}` · {'、'.join(r['subjects'][:4])} · {r['why']}")
        L.append("")

    report = "\n".join(L)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    # 控制台摘要
    print(f"记录 {total:,} / count {total_count:,}")
    print(f"回退父级「费用」{fallback_n} 条（{fallback_n/total:.1%}）")
    print(f"待复核 {pending_n} 条（{pending_n/total:.1%}）")
    print("\nTop 15 新桶：")
    for name, st in sorted(by_new.items(), key=lambda x: -x[1]["n"])[:15]:
        print(f"  {name:<22} {st['n']:>6,} 条  count {st['c']:>7,}")
    print(f"\n报告已写入：{REPORT_PATH}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
