# -*- coding: utf-8 -*-
"""把 _dev/tests/ 里的测试按正确顺序跑一遍。

**为什么要有个 runner**：样例序时账（`_dev/smoke/样例序时账.xlsx`）和
它的输出**不进 git**（那是数据，不是代码），靠 `smoke_cli.py` 现造。
所以顺序不能乱：

    1. test_cashflow_core.py     现金流核心 27 场景 + 映射表不变量 —— 纯单测，**秒级**
    2. test_hard_rules.py        硬规则 13 例                      —— 纯单测，**秒级**
    3. test_packaging.py         发布清单                          —— 纯清单，**秒级**
    4. smoke_cli.py              **造样例数据** + 冒烟 status/check/run
    5. test_workbook_and_patch.py  三张 sheet + 补丁往返 + 单元链路（用 6 行小样例）
    6. e2e_cashflow.py           端到端现金流断言（用 **800 张**样例，最慢）

**别每次都跑全套**：改个映射表/口径，跑 1~3 就够（秒级）；
只有动了 pipeline / 导出 / 补丁链路才需要 4~6。
800 张那一步纯属"数据大"：单跑约 10s（GPU）+ 每进程 ~3s 的 torch import，
真正常见的"跑一次好几分钟"是**整套连着跑**，不是某一步慢。

用法：
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py            :: 全套
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py --quick    :: 跳过 800 张那一步
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py test_cashflow_core.py test_packaging.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PY = ROOT / "venv" / "Scripts" / "python.exe"

# 需要 800 张样例的那一步（最慢）；--quick 跳过它
SLOW = {"e2e_cashflow.py"}

ORDER = [
    "test_cashflow_core.py",
    "test_units_dedup.py",           # 切割第二层：摘要归族（纯单测，秒级）
    "test_hard_rules.py",
    "test_packaging.py",             # 只看清单，不需要样例数据
    "smoke_cli.py",                  # 这一步造出后面两步要用的样例数据
    "test_workbook_and_patch.py",
    "e2e_cashflow.py",
]

if not PY.exists():
    print(f"找不到 python：{PY}")
    sys.exit(2)

argv = sys.argv[1:]
quick = any(a in ("--quick", "--fast", "-q") for a in argv)
wanted = [a for a in argv if not a.startswith("-")]
names = [n for n in ORDER
         if not wanted or n in wanted or Path(n).stem in wanted]
if quick:
    names = [n for n in names if n not in SLOW]
unknown = [w for w in wanted
           if w not in ORDER and Path(w).stem not in [Path(n).stem for n in ORDER]]
if unknown:
    print(f"不认识的套件：{unknown}；可选：{ORDER}")
    sys.exit(2)
if not names:
    print("没有要跑的套件")
    sys.exit(2)

print(f"要跑 {len(names)} 个套件：{', '.join(names)}")
if quick and any(n in SLOW for n in ORDER):
    print(f"（--quick：跳过 {', '.join(sorted(SLOW))}）")

results = []
for name in names:
    print("=" * 72)
    print(f"▶ {name}")
    print("=" * 72)
    # stderr 里有第三方库的非 UTF-8 噪音，errors="replace" 兜住
    p = subprocess.run([str(PY), "-X", "utf8", str(HERE / name)],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = [l for l in (p.stdout or "").splitlines() if l.strip()][-3:]
    for l in tail:
        print("   " + l)
    if p.returncode != 0 and (p.stderr or "").strip():
        print("   --- stderr ---")
        for l in p.stderr.strip().splitlines()[-6:]:
            print("   " + l)
    results.append((name, p.returncode))
    print()

print("=" * 72)
bad = [n for n, c in results if c != 0]
for n, c in results:
    print(f"  {'PASS' if c == 0 else 'FAIL'}  {n}")
print("=" * 72)
if bad:
    print(f"❌ {len(bad)}/{len(results)} 个套件失败")
    sys.exit(1)
print(f"✅ {len(results)} 个套件全部通过")
