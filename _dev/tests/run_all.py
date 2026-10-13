# -*- coding: utf-8 -*-
"""把 _dev/tests/ 里的测试按正确顺序跑一遍。

**为什么要有个 runner**：样例序时账（`_dev/smoke/样例序时账.xlsx`）和
它的输出**不进 git**（那是数据，不是代码），靠 `smoke_cli.py` 现造。
所以顺序不能乱：

    1. test_cashflow_core.py     现金流核心 21 场景 —— 纯单测，不依赖数据
    2. test_hard_rules.py        硬规则 13 例      —— 纯单测
    3. smoke_cli.py              **造样例数据** + 冒烟 status/check/run
    4. test_workbook_and_patch.py  三张 sheet + 补丁往返（依赖 3 的产物）
    5. e2e_cashflow.py           端到端现金流断言（依赖 3 的产物）

用法：
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PY = ROOT / "venv" / "Scripts" / "python.exe"

ORDER = [
    "test_cashflow_core.py",
    "test_hard_rules.py",
    "smoke_cli.py",                  # 这一步造出后面两步要用的样例数据
    "test_workbook_and_patch.py",
    "e2e_cashflow.py",
]

if not PY.exists():
    print(f"找不到 python：{PY}")
    sys.exit(2)

results = []
for name in ORDER:
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
