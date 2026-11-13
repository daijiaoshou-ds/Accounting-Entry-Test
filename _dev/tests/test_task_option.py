# -*- coding: utf-8 -*-
"""任务的归属标记（`--option task=…`）不该影响阶段哈希。

为什么值得单独测：`task` 是"这份账属于哪个任务"，只用来把运行记录归到任务下面。
它要是进了哈希，同一个任务里换一次归属、或者同一份账被建到两个任务上，
所有程序阶段都会被判成"输入变了"而全量重跑——症状是页面上一点就卡几分钟，
而产物其实一模一样。

用法：
    venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_task_option.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobflow import Job  # noqa: E402
from jobflow.scan import scan_jobs  # noqa: E402

SOURCE = ROOT / "case" / "序时账1.xlsx"

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


check("task" in Job._HASH_IGNORED_OPTIONS, "task 不在 _HASH_IGNORED_OPTIONS 里，会让阶段哈希白白失配")
check("max_review" in Job._HASH_IGNORED_OPTIONS, "max_review 被从 _HASH_IGNORED_OPTIONS 里弄丢了")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    one = Job.create("cleaning", SOURCE, options={"task": "task_aaa"}, root=root)
    two = Job.create("cleaning", SOURCE, options={"task": "task_bbb"}, root=root)

    # 同一份账 + 不同归属 → 每个程序阶段的哈希都一样
    for stage in one.feature.stages:
        if stage.actor != "program":
            continue
        check(one.stage_hash(stage.id) == two.stage_hash(stage.id),
              f"阶段 {stage.id} 的哈希被 task 影响了")

    # 但归属本身要能读出来
    rows = {row["job_id"]: row for row in scan_jobs(root)}
    check(rows[one.state["job_id"]]["task"] == "task_aaa", "scan 没把 task 读出来")
    check(rows[two.state["job_id"]]["task"] == "task_bbb", "两个任务的归属串了")

    # 换一个会改产物的选项，哈希必须变（否则这个测试什么也没证明）
    three = Job.create("cleaning", SOURCE, options={"task": "task_aaa", "upstream": "j0"}, root=root)
    changed = [s for s in one.feature.stages
               if s.actor == "program" and one.stage_hash(s.id) != three.stage_hash(s.id)]
    check(bool(changed), "改 upstream 居然没让哈希变，说明哈希根本没算选项")

if fails:
    print("\n".join(f"FAIL {x}" for x in fails))
    sys.exit(1)
print("PASS 任务的归属标记不进阶段哈希；upstream 照常影响哈希")
