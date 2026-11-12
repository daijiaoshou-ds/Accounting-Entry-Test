# -*- coding: utf-8 -*-
"""journal_io 自测：列名识别 / 视图 / 折算 / 落盘策略。

这是**数据层的契约**：# 全项目只有这一份列名表，它错一次，四个功能一起错。
所以这里钉的是"识别结果"，不是"实现方式"。

用法：
    venv\Scripts\python.exe -X utf8 _dev\tests\test_journal_io.py
"""
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import journal_io as J  # noqa: E402
from journal_io import FIELDS, detect  # noqa: E402

CASE = ROOT / "case" / "序时账1.xlsx"
fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# ============================================================================
# ① 列名识别：五种真实表结构
# ============================================================================
def check_detect():
    print("\n=== 列名识别 ===")
    cases = {
        # 标准：一级科目 + 明细分开
        "standard": (["凭证编号", "摘要", "一级科目", "科目名称", "借方金额", "贷方金额"],
                     {"voucher_id": "凭证编号", "subject_l1": "一级科目",
                      "subject_l2": "科目名称", "debit": "借方金额",
                      "credit": "贷方金额", "summary": "摘要"}),
        # 只有「科目」两字：必须认成一级科目（精确匹配，不被"科目编码"抢）
        "only_subject": (["凭证号", "摘要", "科目", "借方金额", "贷方金额"],
                         {"subject_l1": "科目"}),
        # 只有「会计科目名称」：**不能**被认成一级科目（否则整本账都是二级名）
        "only_l2": (["凭证号", "摘要", "会计科目名称", "借方金额", "贷方金额"],
                    {"subject_l2": "会计科目名称"}),
        # 科目 + 科目编码：各归各
        "with_code": (["凭证号", "科目编码", "科目", "借方金额", "贷方金额"],
                      {"subject_l1": "科目", "subject_code": "科目编码"}),
        # 单列金额 + 借贷方向
        "amount_flag": (["凭证号", "一级科目", "金额", "借贷方向"],
                        {"amount": "金额", "dc_flag": "借贷方向"}),
        # 「借」「贷」两个单字列
        "short_cols": (["日期", "凭证号", "科目", "借", "贷"],
                       {"debit": "借", "credit": "贷", "date": "日期"}),
    }
    for name, (cols, expect) in cases.items():
        got = detect(cols)
        for k, want in expect.items():
            check(got.get(k) == want, f"{name}: {k} 应为 {want!r}，实得 {got.get(k)!r}")
        print(f"  {name:14s} {got}")

    # 一列只能被一个字段认领：借方金额不能同时喂给 debit 和 amount
    m = detect(["凭证号", "借方金额", "贷方金额"])
    check(m.get("debit") == "借方金额" and "amount" not in m,
          f"「借方金额」不该被 amount 抢走：{m}")

    # 字段字典自身的一致性
    keys = [f.key for f in FIELDS]
    check(len(keys) == len(set(keys)), "FIELDS 里有重复的 key")
    for f in FIELDS:
        check(f.kind in ("text", "money", "date", "period"),
              f"字段 {f.key} 的 kind 非法：{f.kind}")


# ============================================================================
# ② 视图：raw 保真 / money 可算 / keys 稳定
# ============================================================================
def check_views():
    print("\n=== Journal 视图 ===")
    df = pd.DataFrame({
        "凭证编号": ["0001", "0001"],
        "一级科目": ["管理费用", "银行存款"],
        "借方金额": ["1,200.50", ""],
        "贷方金额": ["", "1,200.50"],
        "制单日期": pd.to_datetime(["2025-01-20", "2025-01-20"]),
    })
    j = J.read(df if False else _to_csv(df), requires=("voucher_id", "subject_l1",
                                                       "debit", "credit"))
    check(len(j) == 2, f"行数应为 2，实得 {len(j)}")
    check(list(j.keys("voucher_id")) == ["0001", "0001"],
          f"凭证号必须保真（前导零不能丢）：{list(j.keys('voucher_id'))}")
    check(abs(j.money("debit").sum() - 1200.5) < 1e-6,
          f"金额应能算（去千分位）：{j.money('debit').sum()}")
    check(j.money("credit").sum() == 1200.5, "贷方金额没读到")
    check(list(j.keys("date")) == ["2025-01-20", "2025-01-20"],
          f"日期键应为 YYYY-MM-DD：{list(j.keys('date'))}")
    v = j.vouchers()
    check(len(v) == 1 and int(v.iloc[0]["行数"]) == 2, f"凭证聚合不对：{v.to_dict()}")


def _to_csv(df: pd.DataFrame) -> Path:
    """写成临时 csv 再读，模拟"从文件来"（顺带测 csv 路径）。"""
    p = Path(tempfile.mkdtemp()) / "t.csv"
    # 日期先用字符串落盘，读回来才能验证 keys() 的格式化
    out = df.copy()
    out["制单日期"] = out["制单日期"].dt.strftime("%Y-%m-%d")
    out.to_csv(p, index=False, encoding="utf-8-sig")
    return p


# ============================================================================
# ③ 金额 + 方向 → 折算成借贷两列
# ============================================================================
def check_derive():
    print("\n=== 折算（金额 + 借贷方向）===")
    df = pd.DataFrame({
        "凭证号": ["A1", "A1", "A2"],
        "摘要": ["付款", "付款", "收款"],
        "一级科目": ["管理费用", "银行存款", "银行存款"],
        "金额": [100, 100, 50],
        "借贷方向": ["借", "贷", "借"],
    })
    p = Path(tempfile.mkdtemp()) / "d.csv"
    df.to_csv(p, index=False, encoding="utf-8-sig")
    j = J.read(p, requires=("voucher_id", "subject_l1", "debit", "credit"))
    check(j.money("debit").sum() == 150 and j.money("credit").sum() == 100,
          f"折算不对：借 {j.money('debit').sum()} 贷 {j.money('credit').sum()}")
    back = j.write_back({"业务分类": "x"})
    check("借方金额(折算)" not in back.columns, "折算列不该出现在写回结果里")
    check(list(back.columns)[-1] == "业务分类", "追加列应排在最后")

    # 已经有借/贷列的表**不许**被折算覆盖
    df2 = pd.DataFrame({"凭证号": ["B1"], "一级科目": ["银行存款"],
                        "借方金额": [10], "贷方金额": [0], "金额": [999],
                        "借贷方向": ["贷"]})
    p2 = Path(tempfile.mkdtemp()) / "d2.csv"
    df2.to_csv(p2, index=False, encoding="utf-8-sig")
    j2 = J.read(p2)
    check(j2.money("debit").sum() == 10, "已有借方列时不该折算")


# ============================================================================
# ④ 落盘策略：三档 target
# ============================================================================
def check_writers():
    print("\n=== 落盘策略 ===")
    tmp = Path(tempfile.mkdtemp())
    small = pd.DataFrame({"a": range(10), "b": range(10)})

    r = J.write_table(small, tmp / "s.xlsx", target="download")
    check(r.fmt == "xlsx" and Path(r.paths["Sheet1"]).exists(), "小表应写 xlsx")
    print(f"  小表 → {r.fmt} {r.seconds}s")

    r = J.write_sheets({"甲": small, "乙": small}, tmp / "m.xlsx", target="download")
    check(r.fmt == "xlsx" and len(r.paths) == 2, f"多 sheet 应一个文件：{r.paths}")
    check(len(set(r.paths.values())) == 1, "多 sheet 应落在同一个 xlsx 里")

    big = pd.DataFrame({f"c{i}": range(J.XLSX_MAX_ROWS + 5) for i in range(3)})
    r = J.write_sheets({"大": big}, tmp / "b.xlsx", target="download")
    check(r.fmt == "csv", f"超过 {J.XLSX_MAX_ROWS} 行应写 csv，实得 {r.fmt}")
    check(bool(r.hint), "超阈值应给提示")
    print(f"  {len(big)} 行 → {r.fmt}（提示：{r.hint[:24]}…）")

    r = J.write_table(small, tmp / "a.parquet", target="archive")
    check(r.fmt == "parquet", f"archive 应写 parquet，实得 {r.fmt}")

    try:
        J.write_table(small, tmp / "p.xlsx", target="preview")
        fails.append("preview 不该落盘，应抛错")
    except ValueError:
        pass

    # 估时与提示
    sec = J.estimate_xlsx_seconds(64000, 39)
    check(sec > 5, f"64,255×39 的 xlsx 估时不该这么小：{sec}s")
    print(f"  估时 64,255×39 = {sec}s（单 sheet；三张表实测共 34s）")

    # 字节流（HTTP 导出）
    data, name, mime, fmt, hint = J.serialize_bytes(small, "结果.xlsx")
    check(fmt == "xlsx" and name.endswith(".xlsx") and len(data) > 1000,
          f"serialize_bytes 不对：{name} {fmt} {len(data)}B")


# ============================================================================
# ⑤ 真实账：读表结果与字段映射
# ============================================================================
def check_real_file():
    if not CASE.exists():
        print(f"\n=== 跳过真实账（{CASE.name} 不存在）===")
        return
    print(f"\n=== 真实账 {CASE.name} ===")
    j = J.read(CASE, requires=("voucher_id", "subject_l1", "debit", "credit"))
    m = j.schema.mapping
    check(m.get("voucher_id") == "凭证编号", f"凭证号认错了：{m.get('voucher_id')}")
    check(m.get("subject_l1") == "一级科目", f"一级科目认错了：{m.get('subject_l1')}")
    check(m.get("subject_l2") == "科目名称", f"明细科目认错了：{m.get('subject_l2')}")
    check(j.schema.engine == "calamine", f"引擎应为 calamine，实得 {j.schema.engine}")
    check(len(j) == 11604, f"行数应为 11604，实得 {len(j)}")
    check(j.keys("voucher_id").nunique() == 2216, "凭证数应为 2216")
    print(f"  {j} → {j.keys('voucher_id').nunique()} 张凭证，"
          f"借方合计 {j.money('debit').sum():,.2f}")


def main() -> int:
    check_detect()
    check_views()
    check_derive()
    check_writers()
    check_real_file()
    print("\n" + "=" * 70)
    if fails:
        print(f"❌ {len(fails)} 项失败：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ journal_io 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
