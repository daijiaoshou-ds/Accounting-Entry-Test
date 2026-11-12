# -*- coding: utf-8 -*-
"""把 _dev/tests/ 里的测试按顺序跑一遍。

**为什么有 runner**：测试之间有代价差，改一处不必跑全套。

    1. test_journal_io.py     数据层契约（列名识别 / 视图 / 折算 / 落盘）—— 秒级
    2. test_jobflow.py        编排层（状态机 / 决策 / 哈希短路 / 弃权）  —— 秒级
    3. test_cashflow_core.py  现金流判定 30 场景 + 映射表不变量      —— 秒级
    4. test_flow_api.py       接口层契约（demo 功能跑通全流程，不加载模型）—— 秒级
    5. test_service_api.py    服务层 + HTTP 契约（跑 700 张真凭证）  —— 分钟级

改了 journal_io → 跑 1；改了 jobflow → 跑 2；改了现金流映射/判定 → 跑 3；
改了 api/flow_api 或前端调用方式 → 跑 4；改了 service / api / jobs → 跑 5。

用法：
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py --quick
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\run_all.py test_journal_io.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PY = ROOT / "venv" / "Scripts" / "python.exe"

# 需要模型、跑真凭证的那一步（最慢）
SLOW = {"test_service_api.py"}

ORDER = [
    "test_journal_io.py",
    "test_jobflow.py",
    "test_cashflow_core.py",
    "test_flow_api.py",
    "test_service_api.py",
]


def main(argv) -> int:
    quick = "--quick" in argv
    picks = [a for a in argv if a.endswith(".py")]
    todo = picks or [t for t in ORDER if not (quick and t in SLOW)]

    failed = []
    for name in todo:
        path = HERE / name
        if not path.exists():
            print(f"⚠️  找不到 {name}")
            failed.append(name)
            continue
        print(f"\n{'=' * 70}\n▶ {name}\n{'=' * 70}")
        code = subprocess.call([str(PY), "-X", "utf8", str(path)], cwd=str(ROOT))
        if code != 0:
            failed.append(name)

    print(f"\n{'=' * 70}")
    if failed:
        print(f"❌ 失败 {len(failed)} 个：{'、'.join(failed)}")
        return 1
    print(f"✅ 全部通过（{len(todo)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
