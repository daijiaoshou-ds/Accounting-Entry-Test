# -*- coding: utf-8 -*-
"""复核交互的三条硬约束：分页只读、note 校验、任务号能当 job 用。

这里用**合成任务**（不跑模型）：建一个 cleaning 任务，把 reviews/<stage>.request.json
写成 25 条待办，然后把 state 标成"停在复核位"。这样几秒内就能验证接口行为，
不必等一本真账跑十分钟。

任务目录用环境变量指到临时目录——`jobflow.config.JOBS_DIR` 在 import 时读它，
所以必须在 import jobflow 之前设好。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="journal-review-test-"))
os.environ["JOURNAL_JOBS_DIR"] = str(TMP / "jobs")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobflow import Job  # noqa: E402 - 必须在设好环境变量之后导入

ENUM = ["采购", "费用", "销售", "薪酬"]


def make_job(count: int = 25) -> Job:
    src = TMP / "序时账.xlsx"
    src.write_bytes(b"fake")
    job = Job.create("cleaning", src, root=TMP / "jobs")
    items = [{"key": f"V{i:03d}", "张数": 1, "模型桶": "费用", "置信度": "中",
              "摘要": f"摘要 {i}"} for i in range(count)]
    (job.path / "reviews").mkdir(exist_ok=True)
    (job.path / "reviews" / "review_bucket.request.json").write_text(json.dumps({
        "schema_version": 1, "stage": "review_bucket", "help": "测试",
        "value_enum": ENUM, "key_name": "voucher_id", "truncated": False,
        "items": items,
    }, ensure_ascii=False), encoding="utf-8")
    job.state["status"] = "awaiting_review"
    job.state["stages"] = {
        "review_bucket": {"status": "awaiting", "title": "复核业务分类",
                          "facts": {"count": count, "truncated": False},
                          "request": "reviews/review_bucket.request.json"},
    }
    job.save()
    return job


def check_paging(job: Job) -> None:
    page = job.next_action(item_limit=10, item_offset=5)["next"]
    keys = [item["key"] for item in page["items"]]
    assert len(keys) == 10, f"limit 没生效：拿到 {len(keys)} 条"
    assert keys[0] == "V005", f"offset 没生效：第一条是 {keys[0]}"
    assert page["count"] == 25 and page["item_offset"] == 5
    assert page["more"] and "--offset 15" in page["more"], page["more"]
    tail = job.next_action(item_limit=10, item_offset=20)["next"]
    assert [i["key"] for i in tail["items"]] == [f"V{i:03d}" for i in range(20, 25)]
    print("✅ 分页：limit / offset / more 都对（读不用先提交）")


def check_notes(job: Job) -> None:
    ok = job.submit({"stage": "review_bucket",
                     "decisions": [{"key": "V000", "value": "费用", "note": "按对方科目判"}]},
                    advance=False)
    assert ok["accepted"] == 1, ok

    bad = job.submit({"stage": "review_bucket", "decisions": [
        {"key": "V001", "value": "费用", "note": "很长的依据" * 80},
        {"key": "V002", "value": "费用", "note": "第一行\n第二行"},
        {"key": "V003", "value": "费用", "note": '{"key": "V003"}'},
        {"key": "V004", "value": "费用", "note": "正常的一句依据"},
    ]}, advance=False)
    reasons = {item["key"]: item["reason"] for item in bad["rejected"]}
    assert "V001" in reasons and "300" in reasons["V001"], reasons
    assert "V002" in reasons and "控制字符" in reasons["V002"], reasons
    assert "V003" in reasons and "JSON" in reasons["V003"], reasons
    assert bad["accepted"] == 1 and "V004" not in reasons, bad
    print("✅ note：超长 / 换行 / 塞 JSON 都被驳回，正常的一句放行")


def check_task_id(job: Job) -> None:
    from jobflow.cli import _open_job
    job.state["options"] = {"task": "task_demo_0001"}
    job.save()
    assert _open_job("task_demo_0001").id == job.id
    try:
        _open_job("task_nope_9999")
    except SystemExit as exc:
        assert "job_id" in str(exc), exc
    else:
        raise AssertionError("不存在的任务号应当报错")
    print("✅ 任务号：能解析到最近一次运行，解析不到时说清该看 job_id")


def main() -> int:
    try:
        job = make_job()
        check_paging(job)
        check_notes(job)
        check_task_id(job)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
