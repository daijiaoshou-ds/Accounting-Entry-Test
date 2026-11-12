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
    # read 可能被跳过（接着上游跑），别假设它的 facts 一定在
    return StageResult(facts={"picked": picked, "n": ctx.facts("read").get("n", 0)})


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


# ── 带"上游"的假功能：link + read + pick + apply ──
# link 只在给了 options.upstream 时跑；给了上游，read/pick 整段跳过
def run_link(ctx):
    CALLS["link"] = CALLS.get("link", 0) + 1
    return StageResult(facts={"upstream": ctx.options.get("upstream")})


def upstream_sig(ctx):
    return ctx.options.get("upstream_sig") or ""


def has_upstream(ctx):
    return bool(ctx.options.get("upstream"))


def no_upstream(ctx):
    return not ctx.options.get("upstream")


def linked_feature() -> Feature:
    return Feature(
        id="_linked", name="接上游的功能", requires=(),
        accepts_upstream=("_test",),
        stages=(
            Stage(id="link", title="接上游", actor="program", run=run_link,
                  enabled=has_upstream, hash_extra=upstream_sig,
                  skip_note="没有上游"),
            Stage(id="read", title="读", actor="program", run=run_read,
                  enabled=no_upstream, skip_note="已从上游接过来"),
            Stage(id="pick", title="选择", actor="ai", depends_on=("read",),
                  review=review, enabled=no_upstream,
                  skip_note="已从上游接过结论"),
            Stage(id="apply", title="应用", actor="program",
                  depends_on=("read", "pick", "link"), run=run_apply),
        ),
        exports=({"key": "data", "label": "数据", "stage": "read"},),
    )


_real_get = features.get


def patched_get(feature_id):
    if feature_id == "_test":
        return fake_feature()
    if feature_id == "_linked":
        return linked_feature()
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

    print("\n=== agent 视角：待办内联 / 分页 / inbox ===")
    nxt = job.next_action()
    check(len(nxt["next"]["items"]) == 2,
          f"next 应内联 2 条：{nxt['next'].get('items')}")
    check(nxt["next"]["items"][0]["key"] == "a", "内联条目要带 key")
    check(nxt["next"]["items_returned"] == 2, "items_returned 要如实")
    check(job.next_action(with_items=False)["next"]["items"] is None,
          "with_items=False 不该带条目（前端轮询用不上的别塞）")
    one = job.next_action(item_limit=1)
    check(one["next"]["items_returned"] == 1 and one["next"]["more"],
          f"item_limit 要生效并给出分页命令：{one['next']}")

    from jobflow import scan_jobs
    row = next((r for r in scan_jobs(tmp / "jobs") if r["job_id"] == job.id), None)
    check(row is not None and row["awaiting"] == "pick"
          and row["awaiting_actor"] == "ai",
          f"inbox 要能看到这个任务停在哪个阶段：{row}")
    print(f"  inbox: {row['status']} / {row['awaiting']} / "
          f"{row['awaiting_count']} 条")

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
    # 回执要跟着往下跑的结果一起回来（不然交完还得去翻 decisions 文件）
    check(act.get("accepted") == 1 and len(act.get("rejected") or []) == 2,
          f"submit 回执不该丢：{ {k: act.get(k) for k in ('accepted', 'rejected')} }")
    check(doc["items"]["a"]["by"] == "ai", f"decision 要记谁交的：{doc['items']['a']}")
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

    features.get = patched_get        # 上面那个"会炸"的功能只影响它自己
    print("\n=== 接着上游跑：整段跳过 + 上游变了要重算 ===")
    jid = "20260101_000000_abcdef"          # 只当选项里的 id 用，不要求真存在
    jl = Job.create("_linked", src, root=tmp / "jobs",
                    options={"upstream": jid, "upstream_sig": "v1"})
    jl.advance()
    st = {s["id"]: s["status"] for s in jl.summary()["stages"]}
    check(st["link"] == "done", f"link 应执行：{st}")
    check(st["read"] == "skipped" and st["pick"] == "skipped",
          f"read/pick 应跳过：{st}")
    check((jl.stage_state("read").get("note") or "") == "已从上游接过来",
          f"跳过要留说明：{jl.stage_state('read')}")
    check(CALLS.get("link") == 1, f"link 应跑 1 次：{CALLS}")

    # 上游没变 → resume 空转
    before = dict(CALLS)
    jl.advance()
    check(CALLS == before, f"上游没变不该重跑：{before} → {CALLS}")

    # 上游变了（签名变）→ link 与下游重跑
    jl.state["options"]["upstream_sig"] = "v2"
    jl.save()
    jl.advance()
    check(CALLS.get("link", 0) >= 2, f"上游变了 link 要重跑：{CALLS}")
    check(CALLS.get("apply", 0) >= 2, f"上游变了下游要重算：{CALLS}")

    # 不带上游 → link 跳过，read 正常跑
    j0 = Job.create("_linked", src, root=tmp / "jobs")
    act = j0.advance()
    st = {s["id"]: s["status"] for s in j0.summary()["stages"]}
    check(st["link"] == "skipped", f"没有上游时 link 跳过：{st}")
    check(st["read"] == "done" and act["next"]["stage"] == "pick",
          f"没有上游时 read 要正常跑：{st} / {act['next']}")

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
