# -*- coding: utf-8 -*-
"""导航交互检查：点击「进入XX」与「返回首页 / 返回小工具」按钮后的页面流转。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # 项目根
HERE = Path(__file__).resolve().parent       # tests/
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
FAILS = []


def ok(m):
    print(f"[PASS] {m}", flush=True)


def bad(m):
    FAILS.append(m)
    print(f"[FAIL] {m}", flush=True)


def main():
    from streamlit.testing.v1 import AppTest

    # 1. 首页左侧导航点「💱 银行流水匹配」-> 右侧面板切换 -> 再点「🚀 进入」
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    nav = [b for b in at.button if "银行流水匹配" in b.label and b.label.strip().startswith("💱")]
    if not nav:
        bad(f"首页找不到银行流水匹配导航按钮: {[b.label for b in at.button]}")
        return 1
    nav[0].click()
    at.run()
    if at.exception:
        bad(f"首页导航切换异常: {str(at.exception[0].value)[:600]}")
        return 1
    md = " ".join(m.value for m in at.markdown)
    if "GL/Bank多级匹配引擎" in md:
        ok("1 首页左侧导航切换到「银行流水匹配」详情面板")
    else:
        bad(f"1 切换后详情面板内容不符: {md[-200:]}")

    btn = [b for b in at.button if b.label.startswith("🚀 进入")]
    if not btn:
        bad(f"切换后找不到「进入」按钮: {[b.label for b in at.button]}")
        return 1
    btn[0].click()
    at.run()
    if at.exception:
        bad(f"进入银行流水匹配后异常: {str(at.exception[0].value)[:600]}")
        return 1
    titles = [t.value for t in at.title]
    infos = [i.value for i in at.info]
    if any("银行流水" in t for t in titles) or any("上传" in i for i in infos):
        ok(f"1b 首页按钮 -> 银行流水匹配成功（title={titles}, info={infos[:1]}）")
    else:
        bad(f"1b 首页按钮跳转后未见银行流水页面内容: title={titles} info={infos[:2]}")

    # 2. 在该页面点「← 返回首页」
    back = [b for b in at.sidebar.button if "返回首页" in b.label]
    if not back:
        bad(f"银行流水页侧边栏缺少返回首页按钮: {[b.label for b in at.sidebar.button]}")
    else:
        back[0].click()
        at.run()
        if at.exception:
            bad(f"返回首页异常: {str(at.exception[0].value)[:400]}")
        else:
            md = " ".join(m.value for m in at.markdown)
            if "会计分析工具箱" in md:
                ok("2 「← 返回首页」回到首页成功")
            else:
                bad("2 返回首页后未见首页内容")

    # 3. 小工具目录 -> 点「打开Excel数据提取」-> 点「← 返回小工具」
    t = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    t.session_state["current_page"] = "tools"
    t.run()
    if t.exception:
        bad(f"小工具目录异常: {str(t.exception[0].value)[:400]}")
        return 1
    open_btns = [b for b in t.button if b.label.startswith("打开Excel数据提取")]
    if not open_btns:
        bad(f"小工具目录缺少打开按钮: {[b.label for b in t.button]}")
    else:
        open_btns[0].click()
        t.run()
        if t.exception:
            bad(f"进入工具详情异常: {str(t.exception[0].value)[:600]}")
        else:
            md = " ".join(m.value for m in t.markdown)
            if "Excel数据提取" in md:
                ok("3 「打开Excel数据提取」进入详情页成功")
            else:
                bad("3 进入详情页后未见工具标题")
            back2 = [b for b in t.sidebar.button if "返回小工具" in b.label]
            if not back2:
                bad(f"详情页侧边栏缺少返回小工具按钮: {[b.label for b in t.sidebar.button]}")
            else:
                back2[0].click()
                t.run()
                if t.exception:
                    bad(f"返回小工具异常: {str(t.exception[0].value)[:400]}")
                else:
                    md = " ".join(m.value for m in t.markdown)
                    if "更多工具打磨中" in md or "小工具" in md:
                        ok("3b 「← 返回小工具」回到目录页成功")
                    else:
                        bad("3b 返回小工具后未见目录内容")

    # 4. 未知工具 id 兜底内容
    t = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    t.session_state["current_page"] = "tool_detail"
    t.session_state["current_tool"] = "not_exist"
    t.run()
    md = " ".join(m.value for m in t.markdown)
    warn = [w.value for w in t.warning]
    info = [i.value for i in t.info]
    print(f"    debug: warning={warn} info={info} md_has_小工具={'小工具' in md}")
    if "更多工具打磨中" in md or any("小工具" in w for w in warn):
        ok("4 未知工具 id 正确回落到小工具目录页")
    else:
        bad(f"4 未知工具 id 兜底异常: warning={warn} md={md[:150]}")

    print()
    if FAILS:
        print(f"==== 失败 {len(FAILS)} 项 ====")
        for f in FAILS:
            print("  -", f)
        return 1
    print("==== 导航交互：全部通过 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
