# -*- coding: utf-8 -*-
"""硬规则单元测试：两条规则必须准确，因为模型永远不会输出这两个桶。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "summary_cleaner_v3"))

from jc.hard_rules import is_internal_transfer, is_fx_gain_loss, preassign  # noqa: E402

CASES = [
    # (描述, subjects_raw, summary, 期望桶 or None)
    ("银行转银行", ["银行存款", "银行存款"], "内部账户调拨", "资金内部往来"),
    ("提现", ["库存现金", "银行存款"], "提取备用金", "资金内部往来"),
    ("三个货币资金科目", ["库存现金", "银行存款", "其他货币资金"], "资金调拨", "资金内部往来"),
    ("其它货币资金（异体字）", ["银行存款", "其它货币资金"], "调拨", "资金内部往来"),
    ("含非货币科目 → 不是", ["银行存款", "应收账款"], "收款", None),
    ("纯结汇", ["财务费用", "银行存款"], "结汇美元", "汇兑损益"),
    ("含汇兑+财务费用", ["财务费用", "银行存款"], "汇兑损益调整", "汇兑损益"),
    ("收汇", ["财务费用", "银行存款"], "收汇结汇", "汇兑损益"),
    ("财务费用但无汇兑词 → 不是", ["财务费用", "银行存款"], "支付手续费", None),
    ("财务费用+管理费用 → 不是", ["财务费用", "管理费用"], "汇兑损失", None),
    ("财务费用+应交税费 → 不是（黑名单）", ["财务费用", "应交税费"], "结汇", None),
    ("财务费用+应付票据 → 不是（黑名单）", ["财务费用", "应付票据"], "汇兑", None),
    ("空科目 → 不是", [], "随便", None),
]


def main():
    ok = True
    for desc, subs, summary, expect in CASES:
        got = None
        if is_internal_transfer(subs):
            got = "资金内部往来"
        elif is_fx_gain_loss(subs, summary):
            got = "汇兑损益"
        flag = "✅" if got == expect else "❌"
        if got != expect:
            ok = False
        print(f"  {flag} {desc:32s} 期望={str(expect):6s} 实得={str(got)}")

    # preassign 批量接口
    items = [
        {"vid": "记-001", "subjects_raw": ["库存现金", "银行存款"], "summary": "提现"},
        {"vid": "记-002", "subjects_raw": ["财务费用", "银行存款"], "summary": "结汇"},
        {"vid": "记-003", "subjects_raw": ["应收账款", "银行存款"], "summary": "收款"},
    ]
    got = preassign(items)
    expect = {"记-001": "资金内部往来", "记-002": "汇兑损益"}
    print(f"\n  preassign 批量: {got}")
    if got != expect:
        print(f"  ❌ 期望 {expect}")
        ok = False
    else:
        print("  ✅ preassign 正确")

    print("\n" + "=" * 54)
    print("硬规则测试:", "全部通过 ✅" if ok else "有失败 ❌")
    print("=" * 54)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
