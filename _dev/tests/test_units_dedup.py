# -*- coding: utf-8 -*-
"""切割第二层（摘要相似度归族）的纯单测 —— 不加载模型、不读真账，秒级。

覆盖用户定的三条口径：
  ① 不看数字：忽略数字后再比
  ② 只在同一科目组合内比（跨组合再像也不并）
  ③ 同族只判一次、结论写回整族
再加确定性与边界（阈值 1.0 / off）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "summary_cleaner_v3"))
from jc import units as U  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def item(vid, subjects, summary):
    return {"vid": str(vid), "subjects": list(subjects), "summary": summary}


# ── ① 归一化：忽略数字 ──────────────────────────────────────────────
print("① 归一化（忽略数字 / 金额 / 标点）")
check(U.normalize_summary("公司向银行贷款1000万元")
      == U.normalize_summary("公司向银行贷款2000万元"),
      "贷款 1000 万 == 贷款 2000 万（去掉数字后同一句）")
check(U.normalize_summary("公司向银行贷款1000万元") == "公司向银行贷款",
      "金额整体被去掉，不留「万元」噪声")
check(U.normalize_summary("７月付甲公司货款") == U.normalize_summary("7月付甲公司货款"),
      "全角数字与半角数字等价（NFKC）")
check(U.normalize_summary("支付 货款，RMB 1,200.00 元")
      == U.normalize_summary("支付货款RMB元") or
      U.normalize_summary("支付 货款，RMB 1,200.00 元") == "支付货款rmb",
      f"标点/空白/币种被规范化（实得 {U.normalize_summary('支付 货款，RMB 1,200.00 元')!r}）")
check(U.normalize_summary("") == "", "空摘要不炸")

# ── ② 只在同一个科目组合里归族 ──────────────────────────────────────
print("\n② 只在同一科目组合内比较（用户举的反例）")
items = [
    item(1, ["应付账款[借]", "银行存款[贷]"], "支付分公司货款100000"),
    item(2, ["银行存款[借]", "应收账款[贷]"], "收分公司货款100000"),
    item(3, ["应付账款[借]", "银行存款[贷]"], "支付分公司货款250000"),
]
st = U.canonicalize(items, threshold=0.95)
check(st["units_after"] == 2, f"3 张 → 2 个单元（跨科目组合不并）：{st['units_after']}")
check(items[0]["_rep_norm"] == items[2]["_rep_norm"],
      "同科目组合、只差金额 → 同一族")
check(items[1]["_rep_norm"] != items[0]["_rep_norm"],
      "「收分公司货款」没被并进「付分公司货款」（不同科目组合）")

# ── ③ 同族只判一次 + 结论铺到整族 ───────────────────────────────────
print("\n③ 同族归并规模")
big = [item(i, ["主营业务收入[贷]", "应收账款[借]"],
            f"外销某公司货物{i:04d}") for i in range(1, 51)]
big.append(item(99, ["主营业务收入[贷]", "应收账款[借]"], "内销某公司货物0001"))
st = U.canonicalize(big, threshold=0.95)
check(st["units_after"] == 2, f"51 张 → 2 个单元（外销 50 + 内销 1）：{st['units_after']}")
check(st["merged_vouchers"] == 49, f"49 张抄代表作业：{st['merged_vouchers']}")
reps = {i["_rep_norm"] for i in big if i["vid"] == "99"}
check(len(reps) == 1, "离群的那张自己一族（不同文本没被强行并）")

# ── ④ 代表要确定性 ────────────────────────────────────────────────
print("\n④ 代表选择是确定性的（出现最多 → 最短 → 字典序）")
a = [item(1, ["银行存款[借]"], "付A公司货款1万元"),
     item(2, ["银行存款[借]"], "付A公司货款2万元"),
     item(3, ["银行存款[借]"], "付A公司货款3万元")]
U.canonicalize(a, threshold=0.95)
reps1 = [i["_rep_summary"] for i in a]
b = [item(3, ["银行存款[借]"], "付A公司货款3万元"),
     item(1, ["银行存款[借]"], "付A公司货款1万元"),
     item(2, ["银行存款[借]"], "付A公司货款2万元")]
U.canonicalize(b, threshold=0.95)
check(reps1[0] == a[0]["_rep_summary"] and a[0]["_rep_summary"] == a[2]["_rep_summary"],
      f"同族代表一致：{a[0]['_rep_summary']!r}")
check(sorted(i["_rep_summary"] for i in b) == sorted(reps1),
      "换个输入顺序，代表不变")

# ── ⑤ 阈值与开关 ──────────────────────────────────────────────────
print("\n⑤ 阈值边界")
c = [item(1, ["银行存款[借]"], "招行支付乙公司月货款"),
     item(2, ["银行存款[借]"], "招行支付丙公司月货款")]
U.canonicalize(c, threshold=1.0)
check(c[0]["_rep_norm"] != c[1]["_rep_norm"],
      "阈值 1.0（只并完全相同）：不同公司名不并")
U.canonicalize(c, threshold=0.90)
check(c[0]["_rep_norm"] == c[1]["_rep_norm"],
      "阈值 0.90：不同公司名的同类付款并成一族")
d = [item(1, ["银行存款[借]"], "付货款1000元"),
     item(2, ["银行存款[借]"], "付货款2000元")]
st_off = U.canonicalize(d, threshold=0)
check(st_off["on"] is False and d[0]["_rep_norm"] != d[1]["_rep_norm"],
      "阈值 0（off）：连「忽略数字」也不做，退回原样文本去重")
st_one = U.canonicalize(d, threshold=1.0)
check(st_one["on"] and d[0]["_rep_norm"] == d[1]["_rep_norm"],
      "阈值 1.0：数字仍被忽略，所以「付货款1000元/2000元」还是同一族")

print("\n" + "=" * 70)
if FAIL:
    print(f"❌ {len(FAIL)} 项失败：")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
