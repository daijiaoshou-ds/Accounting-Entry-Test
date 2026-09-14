# -*- coding: utf-8 -*-
"""一键跑完 tests/ 下的回归测试（按「先底层、后页面」的顺序）

用法：
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py          # 全部
    venv\\Scripts\\python.exe -X utf8 tests\\run_all.py 页面 小工具   # 只跑标题含关键词的

每个脚本都是独立的（不依赖 pytest），退出码 0 = 通过。
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SUITES = [
    ("小工具纯逻辑", "test_run_smalltools.py"),
    ("银行核对纯逻辑", "test_run_bankmatcher.py"),
    ("页面级入口（真实类型）", "test_page_entrypoints.py"),
    ("小工具真实 UI 交互（文件/文件夹）", "test_tools_ui_interaction.py"),
    ("上传文件夹模式 & 下载式输出", "test_folder_upload.py"),
    ("银行核对全流程 UI", "test_bank_ui_e2e.py"),
    ("导航与集成", "test_navigation.py"),
    ("导航按钮流转", "test_nav_clicks.py"),
]


def tail_summary(text: str) -> str:
    """摘出脚本最后一行「汇总: ...」"""
    for line in reversed(text.splitlines()):
        if line.startswith("汇总:"):
            return line.strip()
    return ""


def main() -> int:
    keywords = sys.argv[1:]
    results = []
    for title, script in SUITES:
        if keywords and not any(k in title for k in keywords):
            continue
        path = HERE / script
        print(f"\n{'=' * 70}\n>>> {title}  ({script})\n{'=' * 70}", flush=True)
        proc = subprocess.run([sys.executable, "-X", "utf8", str(path)],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        out = (proc.stdout or "") + (proc.stderr or "")
        # 只回显关键行，避免刷屏；完整输出在失败时给出
        for line in out.splitlines():
            if line.startswith(("[PASS]", "[FAIL]", "[WARN]", "===", ">>>", "汇总")):
                print("   ", line, flush=True)
        if proc.returncode != 0:
            print("\n---- 失败详情（尾部 60 行）----")
            print("\n".join(out.splitlines()[-60:]))
        results.append((title, script, proc.returncode, tail_summary(out)))

    print(f"\n{'=' * 70}\n总计\n{'=' * 70}")
    bad = 0
    for title, script, code, summary in results:
        flag = "✅" if code == 0 else "❌"
        bad += (code != 0)
        print(f"  {flag} {title:<32} {summary or '(无汇总行)'}")
    print(f"\n{len(results) - bad}/{len(results)} 个测试脚本通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
