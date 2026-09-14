# -*- coding: utf-8 -*-
"""导航与集成检查：直接从根 app.py 走「首页 -> 各功能 -> 小工具 -> 详情页」全链路。"""
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

    # ---------- 1. 首页（app.py 默认 current_page='home'） ----------
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180)
    at.run()
    if at.exception:
        bad(f"首页渲染异常: {str(at.exception[0].value)[:600]}")
        return 1
    ok("1 首页渲染正常")
    labels = [b.label for b in at.button]
    print("  首页按钮:", labels)
    for need in ["会计分录测试", "对方科目分析", "序时账清洗", "银行流水匹配", "小工具"]:
        if not any(need in l for l in labels):
            bad(f"首页缺少模块入口: {need}")
    if not FAILS:
        ok("1b 五个一级模块入口齐全")

    # ---------- 2. 逐个进入各模块（切 session_state 后 rerun） ----------
    pages = {
        "anomaly": "会计分录测试",
        "contra": "对方科目分析",
        "summary": "序时账清洗",
        "bankmatch": "银行流水匹配",
        "tools": "小工具",
    }
    for page, name in pages.items():
        t = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
        t.session_state["current_page"] = page
        try:
            t.run()
        except Exception as e:
            bad(f"进入「{name}」抛异常: {type(e).__name__}: {e}")
            continue
        if t.exception:
            bad(f"进入「{name}」页面异常: {str(t.exception[0].value)[:500]}")
            continue
        errs = [e.value for e in t.error]
        # 银行流水匹配未上传文件时不应报 error
        if errs:
            bad(f"「{name}」页面出现 error 组件: {errs[:2]}")
        else:
            ok(f"2 进入「{name}」正常（无 error）")

    # ---------- 3. 小工具 -> 5 个详情页 ----------
    for tid, name in [("excel_extract", "Excel数据提取"), ("file_batch", "文件批量整理"),
                      ("xls_convert", "Excel格式互转"), ("pdf_indexer", "PDF索引号生成"),
                      ("pdf_merger", "PDF合并/分拆/转换")]:
        t = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
        t.session_state["current_page"] = "tool_detail"
        t.session_state["current_tool"] = tid
        try:
            t.run()
        except Exception as e:
            bad(f"小工具「{name}」抛异常: {type(e).__name__}: {e}")
            continue
        if t.exception:
            bad(f"小工具「{name}」页面异常: {str(t.exception[0].value)[:600]}")
            continue
        errs = [e.value for e in t.error]
        if errs:
            bad(f"小工具「{name}」出现 error: {errs[:2]}")
        else:
            # 确认确实渲染了工具内容（有 banner / 标题）
            md = " ".join(m.value for m in t.markdown)
            if name not in md:
                bad(f"小工具「{name}」页面未渲染对应标题")
            else:
                ok(f"3 小工具「{name}」渲染正常")

    # ---------- 4. 未知工具 id 的兜底 ----------
    t = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    t.session_state["current_page"] = "tool_detail"
    t.session_state["current_tool"] = "not_exist"
    try:
        t.run()
    except Exception as e:
        bad(f"未知工具 id 未优雅处理: {type(e).__name__}: {e}")
    else:
        if t.exception:
            bad(f"未知工具 id 页面异常: {str(t.exception[0].value)[:400]}")
        else:
            ok(f"4 未知工具 id 优雅兜底（warning={[w.value[:30] for w in t.warning]}）")

    # ---------- 5. 未知页面 ----------
    t = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    t.session_state["current_page"] = "bogus_page"
    try:
        t.run()
        ok("5 未知页面不崩溃（=空白，代码走 else 无分支）")
    except Exception as e:
        bad(f"未知页面崩溃: {type(e).__name__}: {e}")

    print()
    if FAILS:
        print(f"==== 失败 {len(FAILS)} 项 ====")
        for f in FAILS:
            print("  -", f)
        return 1
    print("==== 导航与集成：全部通过 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
