# -*- coding: utf-8 -*-
"""把 _dev/tests/ 里的测试按顺序跑一遍。

**为什么有 runner**：测试之间有代价差，改一处不必跑全套。

    1. test_layering.py       分层：运行时不 import tools、功能之间不互相 import —— 秒级
    2. test_journal_io.py     数据层契约（列名识别 / 视图 / 折算 / 落盘）—— 秒级
    3. test_jobflow.py        编排层（状态机 / 决策 / 哈希短路 / 弃权）  —— 秒级
    4. test_task_option.py    任务的归属标记不进阶段哈希              —— 秒级
    5. test_prepared.py       配账解析一次 / 空科目 / 中止 / AI 阶段失败 —— 秒级
    6. test_cashflow_core.py  现金流判定 30 场景 + 映射表不变量      —— 秒级
    7. test_contra.py         对方科目分析：候选筛选 + 决策翻译 + 真账导出一轮 —— 十几秒
    8. test_entry_test.py     会计分录测试 2.0：口径 + 四层检验 + 白名单 + 报告 —— 七秒

    改了分层/挪了目录 → 跑 1；改了 journal_io → 跑 2；改了 jobflow → 跑 3；
    改了任务的归属/配账 → 跑 4、5；改了现金流映射/判定 → 跑 6；
    改了对方科目分析 → 跑 7；改了分录测试 → 跑 8。
（接口层的 test_flow_api 与真 HTTP 的 e2e_flow_http 依赖旧 api/，已随旧界面归档。）

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

# --quick 要跳过的慢测试；旧的真 HTTP 端到端已归档，暂时没有
SLOW = set()

ORDER = [
    # 分层规则（运行时不 import tools、功能之间不互相 import）—— 秒级
    "test_layering.py",
    "test_journal_io.py",
    "test_jobflow.py",
    # 任务的归属标记（--option task=…）不该影响阶段哈希 —— 秒级
    "test_task_option.py",
    # 配账解析一次 + 功能只载入 / 空科目不崩 / 中止 / AI 阶段失败 —— 秒级
    "test_prepared.py",
    # 复核粒度：一条待办 = 一组业务等价的凭证，交一条整组生效 —— 秒级
    "test_group_review.py",
    "test_cashflow_core.py",
    # 对方科目分析：逻辑 + 真账跑一轮（读表 2s、穷举 4s、导出 7s）
    "test_contra.py",
    # 会计分录测试：逻辑 + 真账跑一轮（打分 2s、导出 2.5s）
    "test_entry_test.py",
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
