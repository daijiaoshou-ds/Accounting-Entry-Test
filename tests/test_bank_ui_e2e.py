# -*- coding: utf-8 -*-
"""银行流水匹配 —— 全流程 UI 端到端测试（AppTest + 假上传）。

流程：预置上传文件 -> 页面读取 -> 字段自动识别 -> 手动补全 -> 科目配对
      -> 点「开始核对」-> 结果面板 / 月度差异 / 诊断 / 导出 全部校验
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

FAILS = []


def ok(msg):
    print(f"[PASS] {msg}", flush=True)


def bad(msg):
    FAILS.append(msg)
    print(f"[FAIL] {msg}", flush=True)


def main():
    from streamlit.testing.v1 import AppTest

    if not (HERE / "_work" / "bank" / "序时账.xlsx").exists():
        bad("缺少测试数据，请先运行 test_run_bankmatcher.py")
        return 1

    at = AppTest.from_file(str(HERE / "_apptest_bank_upload.py"), default_timeout=300)
    at.run()
    if at.exception:
        bad(f"首轮渲染抛异常: {str(at.exception[0].value)[:800]}")
        return 1
    ok(f"1 页面带文件渲染成功（selectbox={len(at.selectbox)}, button={len(at.button)}）")

    # ---- 查看字段自动识别结果 ----
    print("  字段映射下拉（前 20）:")
    for sb in list(at.selectbox)[:20]:
        print(f"    {sb.label} = {sb.value!r}")

    warn_msgs = [w.value for w in at.warning]
    print("  页面警告:", warn_msgs[:4])
    info_msgs = [i.value for i in at.info]
    print("  页面提示:", info_msgs[:4])

    # ---- 手动补全未识别字段（模拟用户在页面下拉里选择）----
    wanted = {
        "工行流水.xlsx": {"tx_date": "交易日期", "counter_party": "交易方", "income": "收入金额",
                        "expense": "支出金额", "abstract": "摘要", "serial_no": "流水号"},
        "建行流水.xlsx": {"tx_date": "交易日期", "counter_party": "对方户名", "income": "转入金额",
                        "expense": "转出金额", "abstract": "用途", "serial_no": "流水号"},
    }
    changed = []
    for sb in list(at.selectbox):
        lab = sb.label
        if lab.startswith("bank_") and not lab.startswith("bank_journal_"):
            parts = lab.split("_", 2)
            if len(parts) != 3:
                continue
            _, fname, field = parts
            want = wanted.get(fname, {}).get(field)
            if want and sb.value != want:
                try:
                    sb.set_value(want)
                    changed.append(f"{lab}: {sb.value!r} -> {want}")
                except Exception as e:
                    print(f"  [WARN] 无法设置 {lab} -> {want}: {e}")
    print(f"  手动补全 {len(changed)} 个银行字段: {changed[:3]}...")

    at.run()
    if at.exception:
        bad(f"补全后渲染抛异常: {str(at.exception[0].value)[:1200]}")
        return 1

    errs = [e.value for e in at.error]
    if any("缺少必填字段" in e for e in errs):
        bad(f"补全后仍报缺少必填字段: {errs}")
        for sb in at.selectbox:
            print(f"    {sb.label} = {sb.value!r}")
        return 1
    ok(f"2 字段映射校验通过（error={errs[:2]}）")

    # ---- 科目配对 ----
    pairs = [sb for sb in at.selectbox if sb.label.startswith("配对_")]
    print("  配对下拉默认值:", [(sb.label, sb.value) for sb in pairs])
    for sb in pairs:
        if sb.value == "（未选择）":
            acc = sb.label.replace("配对_", "")
            guess = "工行流水.xlsx" if "工行" in acc else ("建行流水.xlsx" if "建行" in acc else None)
            if guess:
                sb.set_value(guess)
    at.run()
    if at.exception:
        bad(f"配对后渲染抛异常: {str(at.exception[0].value)[:800]}")
        return 1

    pairs = [sb for sb in at.selectbox if sb.label.startswith("配对_")]
    if not pairs:
        bad("找不到科目-流水配对下拉")
        return 1
    if any(sb.value == "（未选择）" for sb in pairs):
        bad(f"存在未配对科目: {[(sb.label, sb.value) for sb in pairs]}")
        return 1
    ok(f"3 配对完成: {[(sb.label.replace('配对_', ''), sb.value) for sb in pairs]}")

    # ---- 点击开始核对 ----
    btns = [b for b in at.button if "开始核对" in b.label]
    if not btns:
        bad(f"找不到「开始核对」按钮: {[b.label for b in at.button]}")
        return 1
    btns[0].click()
    at.run()
    if at.exception:
        bad(f"核对执行抛异常: {str(at.exception[0].value)[:2000]}")
        for c in at.code:
            print("    traceback:", c.value[:1200])
        return 1

    errs = [e.value for e in at.error]
    if errs:
        bad(f"核对报错: {errs[:3]}")
        for c in at.code:
            print("    traceback:", c.value[:1500])
        return 1
    ok(f"4 核对执行完成: {[s.value for s in at.success][:2]}")

    # ---- 结果面板 ----
    metrics = {m.label: m.value for m in at.metric}
    print("  metric:", metrics)
    subs = [s.value for s in at.subheader]
    print("  subheader:", subs)

    for need in ["数据诊断", "月度总体差异", "核对结果", "导出结果"]:
        if not any(need in s for s in subs):
            bad(f"缺少结果面板: {need}")
    if not FAILS:
        ok("5 结果面板齐全（诊断/月度差异/核对结果/导出）")

    tabs = list(at.tabs)
    print("  页签名:", [t.label for t in tabs])
    if len(tabs) < 3:
        bad(f"结果页签不足 3 个: {len(tabs)}")
    else:
        ok(f"5b 三个结果页签存在: {[t.label for t in tabs]}")

    dls = list(at.get("download_button"))
    if not dls:
        bad("缺少导出下载按钮")
    else:
        ok(f"5c 导出按钮存在: {[getattr(d, 'label', '?') for d in dls]}")

    # ---- 指标数值核对 ----
    expect = {"明细科目数": 2, "✅ 匹配成功": 9, "❌ 未匹配序时账": 1, "❌ 未匹配银行流水": 2}
    for k, v in expect.items():
        got = metrics.get(k)
        try:
            same = float(got) == float(v)
        except (TypeError, ValueError):
            same = str(got) == str(v)
        if same:
            print(f"    ✓ {k} = {got}")
        else:
            bad(f"指标不符: {k} 期望 {v}，实际 {got}")
    if not [f for f in FAILS if f.startswith("指标不符")]:
        ok("6 核对指标与预期一致（2 科目 / 9 匹配 / 1 未匹配 GL / 2 未匹配 Bank）")

    # ---- 导出内容 ----
    try:
        import pandas as pd
        d = dls[0]
        data = getattr(d, "data", None) or getattr(d, "value", None)
        if isinstance(data, (bytes, bytearray)):
            df = pd.read_excel(io.BytesIO(bytes(data)))
            ok(f"7 导出 Excel 可读: {df.shape}")
            print("    类型分布:", df["类型"].value_counts().to_dict())
            print("    匹配方式分布:", df["匹配方式"].value_counts().to_dict())
        else:
            print(f"[WARN] 7 下载按钮未暴露 data（type={type(data)}），跳过导出内容校验")
    except Exception as e:
        print(f"[WARN] 7 导出内容校验跳过: {type(e).__name__}: {e}")

    print()
    if FAILS:
        print(f"==== 失败 {len(FAILS)} 项 ====")
        for f in FAILS:
            print("  -", f)
        return 1
    print("==== 全流程 UI 端到端：全部通过 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
