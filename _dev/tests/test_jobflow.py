# -*- coding: utf-8 -*-
"""jobflow 自测：状态机 / 决策契约 / 哈希短路 / 弃权。

用一个**假功能**（不碰模型、不碰账）测编排层本身——
真功能的端到端在 `test_service_api.py` 和手工流程里验证。

用法：
    venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_jobflow.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import features  # noqa: E402
from jobflow import Job  # noqa: E402
from jobflow.feature import Feature  # noqa: E402
from jobflow.stage import ReviewRequest, Stage, StageResult  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# ============================================================================
# 假功能：read → review(ai) → apply
# ============================================================================
CALLS = {"read": 0, "apply": 0}


def run_read(ctx):
    CALLS["read"] += 1
    ctx.save_json("data.json", {"n": 2})
    return StageResult(facts={"n": 2})


def review(ctx):
    decided = ctx.decisions("pick")
    items = [{"key": k} for k in ("a", "b") if k not in decided]
    return ReviewRequest(items=items, value_enum=["甲", "乙"], key_name="voucher_id",
                         help="选一个")


def run_apply(ctx):
    CALLS["apply"] += 1
    picked = ctx.decisions("pick")
    return StageResult(facts={"picked": picked, "n": ctx.facts("read")["n"]})


def fake_feature() -> Feature:
    return Feature(
        id="_test", name="测试功能", requires=(),
        stages=(
            Stage(id="read", title="读", actor="program", run=run_read),
            Stage(id="pick", title="选择", actor="ai", depends_on=("read",),
                  review=review),
            Stage(id="apply", title="应用", actor="program",
                  depends_on=("read", "pick"), run=run_apply),
        ),
        exports=({"key": "data", "label": "数据", "stage": "read"},),
    )


_real_get = features.get


def patched_get(feature_id):
    if feature_id == "_test":
        return fake_feature()
    return _real_get(feature_id)


features.get = patched_get


# ============================================================================
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="jobflow_"))
    src = tmp / "小账.csv"
    src.write_text("凭证号,科目,借,贷\n1,库存现金,10,\n", encoding="utf-8-sig")

    print("=== 建任务 + 跑到 AI 阶段 ===")
    job = Job.create("_test", src, root=tmp / "jobs")
    act = job.advance()
    check(act["status"] == "awaiting_review", f"应停在 awaiting_review：{act['status']}")
    check(act["next"]["actor"] == "ai" and act["next"]["stage"] == "pick",
          f"next 应指向 pick：{act['next']}")
    check(act["next"]["count"] == 2, f"待办应 2 条：{act['next']}")
    req = json.loads((job.dir / "reviews" / "pick.request.json").read_text(encoding="utf-8"))
    check([i["key"] for i in req["items"]] == ["a", "b"], f"待办 key 不对：{req['items']}")
    check(req["value_enum"] == ["甲", "乙"], "value_enum 没带出来")
    check(CALLS["read"] == 1 and CALLS["apply"] == 0, f"阶段执行次数不对：{CALLS}")
    print(f"  {act['next']['title']} · {act['next']['count']} 条 · {act['next']['read']}")

    print("\n=== 非法取值 / 未在待办里的 key ===")
    act = job.submit({"stage": "pick", "decisions": [
        {"key": "a", "value": "丙"},          # 不在 value_enum
        {"key": "z", "value": "甲"},          # 不在待办
        {"key": "a", "value": "甲"},          # 合法
    ]})
    doc = json.loads((job.dir / "decisions" / "pick.json").read_text(encoding="utf-8"))
    check(doc["accepted"] == 1, f"应采纳 1 条：{doc['accepted']}")
    check(len(doc["rejected"]) == 2, f"应驳回 2 条：{doc['rejected']}")
    check(act["next"]["count"] == 1, f"还剩 b 一条：{act['next']}")
    print(f"  采纳 {doc['accepted']} / 驳回 {len(doc['rejected'])} → 剩 {act['next']['count']} 条")

    print("\n=== 交完最后一条 → 下游自动跑 ===")
    act = job.submit({"stage": "pick", "decisions": [{"key": "b", "value": "乙"}]})
    check(act["status"] == "done", f"应完成：{act['status']}")
    check(CALLS["apply"] == 1, f"apply 应跑 1 次：{CALLS}")
    facts = job.stage_facts("apply")
    check(facts.get("picked") == {"a": "甲", "b": "乙"},
          f"apply 拿到的决策不对：{facts.get('picked')}")
    print(f"  done · picked={facts.get('picked')}")

    print("\n=== 幂等：再 resume 不应重跑任何阶段 ===")
    before = dict(CALLS)
    act = job.advance()
    check(act["status"] == "done", "仍应 done")
    check(CALLS == before, f"不该重跑：{before} → {CALLS}")
    print(f"  阶段执行次数未变：{CALLS}")

    print("\n=== 弃权：空结论让 AI 阶段直接结束 ===")
    job2 = Job.create("_test", src, root=tmp / "jobs")
    job2.advance()
    act = job2.submit({"stage": "pick", "decisions": [], "waive": True})
    check(act["status"] == "done", f"弃权后应完成：{act['status']}")
    note = job2.stage_state("pick").get("note", "")
    check("弃权" in note, f"阶段状态应记弃权：{note}")
    print(f"  {note}")

    print("\n=== 失败可重试（阶段抛错 → failed → resume）===")
    def boom(ctx):
        raise RuntimeError("故意失败")
    features.get = lambda fid: Feature(
        id="_boom", name="会炸", stages=(Stage(id="x", title="炸", actor="program",
                                              run=boom),))
    job3 = Job.create("_boom", src, root=tmp / "jobs")
    try:
        job3.advance()
        check(False, "应该抛错")
    except RuntimeError:
        pass
    check(job3.status == "failed", f"状态应为 failed：{job3.status}")
    check("故意失败" in (job3.state.get("error") or ""), "error 应写进 state")
    act = job3.next_action()
    check(act["next"]["actor"] == "operator", f"失败后应让人接手：{act['next']}")
    print(f"  failed · {job3.state['error'][:40]}…")

    features.get = patched_get
    print("\n" + "=" * 70)
    if fails:
        print(f"❌ {len(fails)} 项失败：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("✅ jobflow 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
