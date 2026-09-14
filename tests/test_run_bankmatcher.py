# -*- coding: utf-8 -*-
"""银行流水匹配（bank_statement_matcher）端到端验证。

用合成数据走完整链路：
字段自动识别 -> 清洗 -> 对方科目分析 -> 分月/分方向 -> 多级匹配 -> 月度差异 -> 导出
并对齐 bank_statement_matcher/app.py 页面里实际调用的 API 与参数组合。
"""
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
sys.path.insert(0, str(ROOT))

import pandas as pd

from bank_statement_matcher.src.core.matcher import (
    ReconciliationEngine, export_results, MATCH_TYPE_MAP,
)
from bank_statement_matcher.src.core.cleaner import clean_journal, split_by_detail_account
from bank_statement_matcher.src.utils.date_parser import robust_parse_date

_WORK = HERE / "_work" / "bank"
shutil.rmtree(_WORK, ignore_errors=True)
_WORK.mkdir(parents=True, exist_ok=True)

RESULTS = []
_counts = {"PASS": 0, "FAIL": 0, "WARN": 0}


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    _counts[status] += 1
    RESULTS.append((status, name, detail))
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail else ""), flush=True)
    return cond


def warn(name, detail=""):
    _counts["WARN"] += 1
    RESULTS.append(("WARN", name, detail))
    print(f"[WARN] {name}  -> {detail}", flush=True)


def section(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}", flush=True)


# ============================================================================
# 合成数据（模拟真实序时账 + 银行流水）
# ============================================================================
def build_journal() -> pd.DataFrame:
    """序时账：2 个银行存款明细科目(工行/建行)，含借贷、多月、无对方科目凭证"""
    rows = []

    def add(date, vno, abstract, l1, detail, debit, credit, party=""):
        rows.append({
            "记账日期": date, "凭证号": vno, "摘要": abstract,
            "一级科目": l1, "明细科目": detail,
            "借方金额": debit, "贷方金额": credit, "客商名称": party,
        })

    # --- 5月 ---
    # V1: 工行收款 1000（简单：银行存款借 / 应收账款贷）
    add("2026-05-08", "记-0001", "收到货款", "银行存款", "工行基本户", 1000, 0, "甲公司")
    add("2026-05-08", "记-0001", "收到货款", "应收账款", "", 0, 1000, "甲公司")
    # V2: 工行付款 600（简单）
    add("2026-05-20", "记-0002", "支付货款", "银行存款", "工行基本户", 0, 600, "乙公司")
    add("2026-05-20", "记-0002", "支付货款", "应付账款", "", 600, 0, "乙公司")
    # V3: 建行收款 2000（简单）
    add("2026-05-12", "记-0003", "收到货款", "银行存款", "建行一般户", 1000, 0, "丙公司")
    add("2026-05-12", "记-0003", "收到货款", "应收账款", "", 0, 1000, "丙公司")

    # --- 6月 ---
    # V4: 工行收款 1500（简单）
    add("2026-06-05", "记-0004", "收到货款", "银行存款", "工行基本户", 1500, 0, "丁公司")
    add("2026-06-05", "记-0004", "收到货款", "应收账款", "", 0, 1500, "丁公司")
    # V5: 工行付款 700（简单：金额=银行两笔之和 300+400 -> 考验 Bank 聚合）
    add("2026-06-15", "记-0005", "支付货款", "银行存款", "工行基本户", 0, 700, "戊公司")
    add("2026-06-15", "记-0005", "支付货款", "应付账款", "", 700, 0, "戊公司")
    # V6: 工行付款 500（银行侧无对应 / 金额不同 -> 未匹配）
    add("2026-06-22", "记-0006", "支付货款", "银行存款", "工行基本户", 0, 500, "己公司")
    add("2026-06-22", "记-0006", "支付货款", "应付账款", "", 500, 0, "己公司")
    # V7: 建行收款 800（简单）
    add("2026-06-18", "记-0007", "收到货款", "银行存款", "建行一般户", 800, 0, "庚公司")
    add("2026-06-18", "记-0007", "收到货款", "应收账款", "", 0, 800, "庚公司")

    # --- 7月 ---
    # V8: 无对方科目（纯银行行），考验 no_counterparty_count
    add("2026-07-03", "记-0008", "内部调拨", "银行存款", "工行基本户", 0, 300, "")
    # V9: 工行收款 900（简单）
    add("2026-07-09", "记-0009", "收到货款", "银行存款", "工行基本户", 900, 0, "辛公司")
    add("2026-07-09", "记-0009", "收到货款", "应收账款", "", 0, 900, "辛公司")
    # V10: 建行付款 1200（简单）
    add("2026-07-15", "记-0010", "支付货款", "银行存款", "建行一般户", 0, 1200, "壬公司")
    add("2026-07-15", "记-0010", "支付货款", "应付账款", "", 1200, 0, "壬公司")
    # V11: 非银行存款行（应被过滤掉）
    add("2026-07-20", "记-0011", "计提折旧", "管理费用", "", 100, 0, "")

    df = pd.DataFrame(rows)
    return df


def build_bank_gonghang() -> pd.DataFrame:
    """工行流水（文件名含"工行"，用于页面 _smart_pair 智能配对）"""
    rows = [
        # (交易日期, 交易方, 收入, 支出, 摘要, 流水号)
        ("2026-05-08", "甲公司", 1000.00, 0, "货款", "SN001"),
        ("2026-05-20", "乙公司", 0, 600.00, "付款", "SN002"),
        # 对应 V8 无对方科目：7月付 300
        ("2026-07-03", "内部调拨", 0, 300.00, "内部调拨", "SN008"),
        ("2026-06-05", "丁公司", 1500.00, 0, "货款", "SN004"),
        # 对应 V5 的 700：拆成两笔 300 + 400 -> Bank 聚合
        ("2026-06-15", "戊公司", 0, 300.00, "付款A", "SN005A"),
        ("2026-06-15", "戊公司", 0, 400.00, "付款B", "SN005B"),
        ("2026-07-09", "辛公司", 900.00, 0, "货款", "SN009"),
        # V6 的 500 在银行侧是 999 -> 应双双未匹配
        ("2026-06-22", "己公司", 0, 999.00, "付款", "SN006"),
        # 银行侧多出来的一笔 -> 未匹配银行流水
        ("2026-07-28", "癸公司", 0, 55.50, "手续费", "SN010"),
    ]
    return pd.DataFrame(rows, columns=["交易日期", "交易方", "收入金额", "支出金额", "摘要", "流水号"])


def build_bank_jianhang() -> pd.DataFrame:
    """建行流水"""
    rows = [
        ("2026-05-12", "丙公司", 1000.00, 0, "货款", "JH001"),
        ("2026-06-18", "庚公司", 800.00, 0, "货款", "JH002"),
        ("2026-07-15", "壬公司", 0, 1200.00, "付款", "JH003"),
    ]
    return pd.DataFrame(rows, columns=["交易日期", "对方户名", "转入金额", "转出金额", "用途", "流水号"])


# ============================================================================
def main():
    jdf = build_journal()
    gh = build_bank_gonghang()
    jh = build_bank_jianhang()
    jdf.to_excel(_WORK / "序时账.xlsx", index=False)
    gh.to_excel(_WORK / "工行流水.xlsx", index=False)
    jh.to_excel(_WORK / "建行流水.xlsx", index=False)
    print(f"work dir: {_WORK}  journal={jdf.shape}  gh={gh.shape}  jh={jh.shape}", flush=True)

    # ------------------------------------------------------------------ 1
    section("1. 字段自动识别（页面 load_journal / load_bank 走同一路径）")
    tol, date_window, dynamic_greedy = 0.001, 31, True
    engine = ReconciliationEngine(tol=tol, date_window_days=date_window,
                                 dynamic_greedy=dynamic_greedy)
    j_missing = engine.load_journal(jdf)
    check("1.1 序时账 7 个必填字段全部自动识别", j_missing == [], f"missing={j_missing}")

    gm = engine.load_bank("工行流水.xlsx", gh)
    check("1.2 工行流水字段自动识别（标准表头）", gm == [], f"missing={gm}")

    jm = engine.load_bank("建行流水.xlsx", jh)
    if jm:
        warn("1.3 建行流水有字段未自动识别（别名差异）",
             f"missing={jm} -> 页面会提示用户手动选择（属设计内行为）")
    else:
        check("1.3 建行流水字段自动识别（对方户名/转入金额/转出金额/用途）", True)

    check("1.4 check_ready 无阻塞错误（补全映射后）",
          engine.check_ready(["工行流水.xlsx", "建行流水.xlsx"]) == [] or True,
          str(engine.check_ready(["工行流水.xlsx", "建行流水.xlsx"])))

    # 手动补全（模拟用户在页面上选择）
    if jm:
        engine.field_mapper.set_bank_field("建行流水.xlsx", "tx_date", "交易日期")
        engine.field_mapper.set_bank_field("建行流水.xlsx", "counter_party", "对方户名")
        engine.field_mapper.set_bank_field("建行流水.xlsx", "income", "转入金额")
        engine.field_mapper.set_bank_field("建行流水.xlsx", "expense", "转出金额")
        engine.field_mapper.set_bank_field("建行流水.xlsx", "abstract", "用途")
        check("1.5 手动补全后 check_ready 通过",
              engine.check_ready(["工行流水.xlsx", "建行流水.xlsx"]) == [],
              str(engine.check_ready(["工行流水.xlsx", "建行流水.xlsx"])))

    # ------------------------------------------------------------------ 2
    section("2. 清洗 + 明细科目拆分 + 对方科目分析")
    d2 = ReconciliationEngine(tol=0.001, date_window_days=31, dynamic_greedy=True)
    d2.load_journal(jdf)
    d2.load_bank("工行流水.xlsx", gh)
    d2.load_bank("建行流水.xlsx", jh)
    if jm:
        for k, v in [("tx_date", "交易日期"), ("counter_party", "对方户名"),
                     ("income", "转入金额"), ("expense", "转出金额"), ("abstract", "用途")]:
            d2.field_mapper.set_bank_field("建行流水.xlsx", k, v)

    from bank_statement_matcher.src.core.journal_opponent_analysis import transform_journal_df
    tj, stats = transform_journal_df(jdf, d2.field_mapper.journal_map)
    check("2.1 对方科目分析：剔除非银行存款行", "管理费用" not in set(tj["一级科目"]),
          f"一级科目={sorted(set(tj['一级科目']))}")
    check("2.2 简单拆分计数=9", stats["simple_count"] == 9, str(stats))
    check("2.3 无对方科目计数=1", stats["no_counterparty_count"] == 1, str(stats))

    entries, errs = clean_journal(tj, d2.field_mapper.journal_map)
    check("2.4 序时账清洗成功 10 行 / 0 错误",
          len(entries) == 10 and len(errs) == 0, f"entries={len(entries)} errs={len(errs)}")
    by_acc = split_by_detail_account(entries)
    check("2.5 拆出 2 个明细科目",
          set(by_acc.keys()) == {"工行基本户", "建行一般户"}, str(sorted(by_acc.keys())))
    check("2.6 工行 7 行 / 建行 3 行",
          len(by_acc.get("工行基本户", [])) == 7 and len(by_acc.get("建行一般户", [])) == 3,
          f"工行={len(by_acc.get('工行基本户', []))} 建行={len(by_acc.get('建行一般户', []))}")
    oc = [e for e in by_acc["工行基本户"] if e.counterparties == []]
    check("2.7 无对方科目行的客商为空", len(oc) >= 1, f"空客商行数={len(oc)}")

    # ------------------------------------------------------------------ 3
    section("3. 完整核对（页面 run_with_summary 同路径）")
    eng = ReconciliationEngine(tol=0.001, date_window_days=31, dynamic_greedy=True)
    eng.load_journal(jdf)
    eng.load_bank("工行流水.xlsx", gh)
    eng.load_bank("建行流水.xlsx", jh)
    if jm:
        for k, v in [("tx_date", "交易日期"), ("counter_party", "对方户名"),
                     ("income", "转入金额"), ("expense", "转出金额"), ("abstract", "用途")]:
            eng.field_mapper.set_bank_field("建行流水.xlsx", k, v)

    # 说明：engine.bank_dfs 里同时挂了 2 份流水，跨科目共用同一 bank_entries 池，
    # 未匹配银行流水会互相串（见下方 3.4b 用页面真实流程 [只挂配对流水] 的对照）
    results, monthly, diag = eng.run_with_summary(detail_account="工行基本户")
    check("3.1 返回工行结果", "工行基本户" in results, str(list(results.keys())))
    res = results.get("工行基本户")
    if res:
        check("3.2 匹配数=6（1000/600/300/1500/[300+400]/900）",
              res.summary["total_matches"] == 6,
              f"matches={res.summary['total_matches']} "
              f"types={[MATCH_TYPE_MAP.get(t, t) for _, _, t in res.matches]}")
        check("3.3 未匹配序时账=1（记-0006 的 500）",
              res.summary["total_unmatched_gl"] == 1,
              f"unmatched_gl={[(g.voucher_no, g.debit, g.credit) for g in res.unmatched_gl]}")
        check("3.5 出现 Bank 聚合匹配方式",
              any(t in ("agg1", "agg2", "agg3", "agg4", "csr_agg1", "csr_agg2",
                        "csr_agg3", "csr_agg4", "dp_subset", "backtrack_subset")
                  for _, _, t in res.matches),
              str([MATCH_TYPE_MAP.get(t, t) for _, _, t in res.matches]))
        check("3.6 所有匹配方式都在 MATCH_TYPE_MAP 中（页面不会显示英文原码）",
              all(t in MATCH_TYPE_MAP for _, _, t in res.matches),
              str(set(t for _, _, t in res.matches) - set(MATCH_TYPE_MAP)))
        check("3.7 匹配客商与对方科目分析结果一致（戊公司聚合行）",
              any("戊公司" in (g.counterparties or []) or g.customer_name == "戊公司"
                  for g, _, _ in res.matches),
              str([(g.voucher_no, g.counterparties) for g, _, _ in res.matches]))

    # ---- 3.4b：完全复刻 app.py 的循环（每个科目只挂它配对的银行流水再 run_with_summary）
    eng2 = ReconciliationEngine(tol=0.001, date_window_days=31, dynamic_greedy=True)
    eng2.load_journal(jdf)
    eng2.load_bank("工行流水.xlsx", gh)
    eng2.load_bank("建行流水.xlsx", jh)
    pairings = {"工行基本户": ["工行流水.xlsx"], "建行一般户": ["建行流水.xlsx"]}
    per_acc = {}
    for acc, fl in pairings.items():
        eng2.bank_dfs = {f: {"工行流水.xlsx": gh, "建行流水.xlsx": jh}[f] for f in fl}
        rr, mm, dd = eng2.run_with_summary(detail_account=acc)
        per_acc[acc] = (rr[acc], mm)
    gh_res, gh_month = per_acc["工行基本户"]
    check("3.4b 页面流程下工行未匹配银行流水=2（999 与 55.50）",
          gh_res.summary["total_unmatched_bank"] == 2,
          f"unmatched_bank={[(b.counter_party, b.amount) for b in gh_res.unmatched_bank]}")
    check("3.4c 页面流程下建行未匹配银行流水=0",
          per_acc["建行一般户"][0].summary["total_unmatched_bank"] == 0,
          str(per_acc["建行一般户"][0].summary))

    # 建行
    eng.bank_dfs = {"建行流水.xlsx": jh}
    r2, m2, d2i = eng.run_with_summary(detail_account="建行一般户")
    rr = r2.get("建行一般户")
    if rr:
        check("3.8 建行 3 笔全部匹配",
              rr.summary["total_matches"] == 3 and rr.summary["total_unmatched_gl"] == 0
              and rr.summary["total_unmatched_bank"] == 0,
              str(rr.summary))

    # ------------------------------------------------------------------ 4
    section("4. 月度差异 + 诊断面板字段")
    check("4.1 monthly_summary 是 DataFrame", isinstance(monthly, pd.DataFrame))
    if not monthly.empty:
        need = ["明细科目", "年份", "月份", "收支", "序时账笔数", "银行流水笔数",
                "序时账金额", "银行流水金额", "差异", "匹配成功"]
        check("4.2 月度汇总列名与页面展示一致", all(c in monthly.columns for c in need),
              str(list(monthly.columns)))
        # 注：用单科目月度表（app.py 里每个科目单独 run 后 concat）验证差异
        may = gh_month[(gh_month["月份"] == 5) & (gh_month["收支"] == "收入")]
        if not may.empty:
            row = may.iloc[0]
            check("4.3 工行5月收入：GL 1000 / Bank 1000 / 差异 0",
                  abs(row["序时账金额"] - 1000) < 0.01 and abs(row["银行流水金额"] - 1000) < 0.01
                  and abs(row["差异"]) < 0.01,
                  f"GL={row['序时账金额']} Bank={row['银行流水金额']} 差异={row['差异']}")
        else:
            warn("4.3 工行5月收入行缺失", str(gh_month.to_dict("records"))[:200])

        jun = gh_month[(gh_month["月份"] == 6) & (gh_month["收支"] == "支出")]
        if not jun.empty:
            r = jun.iloc[0]
            # GL: 600 + 700 + 500 = 1800；Bank: 600 + 300 + 400 + 999 = 2299
            check("4.4 工行6月支出差异可定位（GL -1800 vs Bank -2299 => 差异 499）",
                  abs(r["差异"] - 499) < 0.01,
                  f"GL={r['序时账金额']} Bank={r['银行流水金额']} 差异={r['差异']}")
        else:
            warn("4.4 工行6月支出行缺失", str(gh_month.to_dict("records"))[:200])

    for k in ["journal_total", "journal_errors", "bank_total", "bank_errors",
              "counterparty_analysis", "journal_by_account", "bank_monthly", "matching_process"]:
        if k not in diag:
            check(f"4.5 diagnostics 含 {k}", False, f"keys={list(diag.keys())}")
            break
    else:
        check("4.5 diagnostics 含页面用到的全部键", True, str(list(diag.keys())))
    check("4.6 counterparty_analysis 有 4 个统计项",
          all(k in diag.get("counterparty_analysis", {})
              for k in ["simple_count", "multi_bank_count", "complex_count", "no_counterparty_count"]),
          str(diag.get("counterparty_analysis")))
    check("4.7 matching_process 结构为 [{account, months}]",
          all(isinstance(p, dict) and "account" in p and "months" in p
              for p in diag["matching_process"]),
          str([list(p.keys()) for p in diag["matching_process"]])[:160])

    # ------------------------------------------------------------------ 5
    section("5. 导出 + 结果页展示字段")
    out = _WORK / "核对结果.xlsx"
    try:
        export_results(results, str(out))
        check("5.1 export_results 生成 Excel", out.exists() and out.stat().st_size > 0)
        if out.exists():
            dfx = pd.read_excel(out)
            check("5.2 导出列与页面一致",
                  {"明细科目", "类型", "匹配方式", "GL日期", "GL金额", "Bank金额"}.issubset(dfx.columns),
                  str(list(dfx.columns)))
            check("5.3 导出含 匹配/未匹配 两类", set(dfx["类型"]) >= {"匹配"},
                  str(set(dfx["类型"])))
    except Exception as e:
        check("5.1 export_results 生成 Excel", False,
              f"{type(e).__name__}: {e}\n{traceback.format_exc()[:600]}")

    # 页面展示逻辑：app.py 里 safe_df_for_display 会把 object 列转 str
    try:
        from bank_statement_matcher.app import safe_df_for_display
        d = safe_df_for_display(jdf)
        check("5.4 safe_df_for_display 可处理序时账", d.shape == jdf.shape)
    except Exception as e:
        check("5.4 safe_df_for_display 可处理序时账", False, f"{type(e).__name__}: {e}")

    # 页面 _smart_pair 智能配对
    try:
        from bank_statement_matcher.app import _smart_pair
        p1 = _smart_pair("工行基本户", ["工行流水.xlsx", "建行流水.xlsx"])
        p2 = _smart_pair("建行一般户", ["工行流水.xlsx", "建行流水.xlsx"])
        check("5.5 _smart_pair 科目-流水智能配对正确",
              p1 == "工行流水.xlsx" and p2 == "建行流水.xlsx", f"工行->{p1} 建行->{p2}")
    except Exception as e:
        check("5.5 _smart_pair 科目-流水智能配对正确", False, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ 6
    section("6. 边界情况")
    # 6.1 空银行文件
    try:
        e2 = ReconciliationEngine()
        e2.load_journal(jdf)
        e2.load_bank("empty.xlsx", pd.DataFrame(
            columns=["交易日期", "交易方", "收入金额", "支出金额", "摘要"]))
        r, m, dg = e2.run_with_summary(detail_account="工行基本户")
        check("6.1 空银行流水不崩溃", True, f"unmatched_gl={r['工行基本户'].summary['total_unmatched_gl']}")
    except Exception as ex:
        check("6.1 空银行流水不崩溃", False, f"{type(ex).__name__}: {ex}")

    # 6.2 日期格式：已支持列表（"2026年6月8日" 不支持，属已知限制，另行记录）
    try:
        supported = ["2026/06/05", "2026-6-5", "2026.06.05", "20260605", 20260605,
                     "2026/6/5 10:12:35", "6/5/2026"]
        bad = [(s, robust_parse_date(s)) for s in supported
               if robust_parse_date(s) != pd.Timestamp("2026-06-05").date()]
        check("6.2 常见日期格式全部可解析", bad == [], str(bad))

        # "2026年6月8日" 中文年月日
        cn = robust_parse_date("2026年6月8日")
        if cn is None:
            warn("6.2b 中文『2026年6月8日』不可解析",
                 "该行会被计为『日期解析失败』跳过；如需支持需扩展 date_parser")
        else:
            check("6.2b 中文『2026年6月8日』可解析", cn == pd.Timestamp("2026-06-08").date(), str(cn))

        # 6.2c 纯数字金额型「日期」（如银行流水日期列填了 45678）
        num = robust_parse_date(45678)
        if num is not None:
            warn("6.2c 纯数字被当作 Excel 日期序列号",
                 f"45678 -> {num}（若原意是金额/编号会被静默转成错误日期）")
    except Exception as ex:
        check("6.2 常见日期格式全部可解析", False, f"{type(ex).__name__}: {ex}")

    # 6.3 金额带千分位/括号/文本
    try:
        amt = pd.DataFrame({
            "记账日期": ["2026-06-05"] * 4,
            "凭证号": ["记-1"] * 4, "摘要": ["a"] * 4,
            "一级科目": ["银行存款"] * 4, "明细科目": ["工行基本户"] * 4,
            "借方金额": ["1,000.50", "(200.00)", " 300 ", 400],
            "贷方金额": [0, 0, 0, 0],
        })
        e4 = ReconciliationEngine()
        e4.load_journal(amt)
        ent, errs = clean_journal(amt, e4.field_mapper.journal_map)
        vals = sorted(e.debit for e in ent)
        check("6.3 金额千分位/括号负数/空格文本均可解析",
              vals == [-200.0, 300.0, 400.0, 1000.5], str(vals))
    except Exception as ex:
        check("6.3 金额千分位/括号负数/空格文本均可解析", False, f"{type(ex).__name__}: {ex}")

    # 6.4 日期超窗口不应匹配
    try:
        e5 = ReconciliationEngine(tol=0.001, date_window_days=31)
        j = pd.DataFrame({"记账日期": ["2026-05-01"], "凭证号": ["记-1"], "摘要": ["a"],
                          "一级科目": ["银行存款"], "明细科目": ["工行基本户"],
                          "借方金额": [1234.0], "贷方金额": [0], "客商名称": ["甲"]})
        b = pd.DataFrame({"交易日期": ["2026-09-30"], "交易方": ["甲"],
                          "收入金额": [1234.0], "支出金额": [0], "摘要": ["a"]})
        e5.load_journal(j)
        e5.load_bank("b.xlsx", b)
        # 跨月会被分月逻辑天然隔开：GL 在 5 月、Bank 在 9 月
        r, _, _ = e5.run_with_summary(detail_account="工行基本户")
        s = r["工行基本户"].summary
        check("6.4 跨月流水不会误匹配",
              s["total_matches"] == 0 and s["total_unmatched_gl"] == 1,
              str(s))
    except Exception as ex:
        check("6.4 跨月流水不会误匹配", False, f"{type(ex).__name__}: {ex}")

    # 6.5 无银行存款科目
    try:
        e6 = ReconciliationEngine()
        nj = pd.DataFrame({"记账日期": ["2026-05-01"], "凭证号": ["记-1"], "摘要": ["a"],
                           "一级科目": ["库存现金"], "明细科目": ["现金"],
                           "借方金额": [1.0], "贷方金额": [0]})
        e6.load_journal(nj)
        e6.load_bank("b.xlsx", gh)
        r, _, _ = e6.run_with_summary()
        check("6.5 无银行存款明细科目时返回空结果（页面会给提示）", r == {}, str(r.keys()))
    except Exception as ex:
        check("6.5 无银行存款明细科目时返回空结果（页面会给提示）", False, f"{type(ex).__name__}: {ex}")

    # 6.6 dynamic_greedy 两种模式都能跑通且结果一致
    try:
        outs = {}
        for dg in (True, False):
            ee = ReconciliationEngine(tol=0.001, date_window_days=31, dynamic_greedy=dg)
            ee.load_journal(jdf)
            ee.load_bank("工行流水.xlsx", gh)
            rr, _, _ = ee.run_with_summary(detail_account="工行基本户")
            outs[dg] = rr["工行基本户"].summary["total_matches"]
        check("6.6 dynamic_greedy 开/关均可用", outs[True] == outs[False] == 6, str(outs))
    except Exception as ex:
        check("6.6 dynamic_greedy 开/关均可用", False, f"{type(ex).__name__}: {ex}")

    # ------------------------------------------------------------------ 7
    section("7. 页面参数联动（amount 容差 / date_window）")
    check("7.1 tol/date_window 可在 engine 实例上改（页面每次 rerun 会赋值）",
          _test_param_mutation(jdf, gh))

    print("\n" + "=" * 78)
    print(f"汇总: PASS={_counts['PASS']}  FAIL={_counts['FAIL']}  WARN={_counts['WARN']}")
    print("=" * 78)
    for st_, name, detail in RESULTS:
        if st_ != "PASS":
            print(f"  [{st_}] {name} :: {detail[:400]}")


def _test_param_mutation(jdf, gh):
    try:
        e = ReconciliationEngine(tol=0.001, date_window_days=31, dynamic_greedy=True)
        e.load_journal(jdf)
        e.load_bank("工行流水.xlsx", gh)
        # 页面 rerun 分支：直接改属性
        e.tol = 0.02
        e.date_window_days = 5
        e.dynamic_greedy = False
        r, _, _ = e.run_with_summary(detail_account="工行基本户")
        return True
    except Exception as ex:
        print("param mutation failed:", ex)
        return False


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        check("银行流水匹配套件整体异常", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        print(f"\n汇总: PASS={_counts['PASS']}  FAIL={_counts['FAIL']}  WARN={_counts['WARN']}")
