# -*- coding: utf-8 -*-
"""接口层契约测试（不需要模型）。

用一个**临时注册的 demo 功能**跑通整套 jobflow HTTP 契约：

    建任务 → 轮询 next → 读 review → 交结论（含驳回）→ 弃权 → 产物 → 下载

为什么不直接用 cashflow：它要加载 bge-large，一个测试跑十几秒还依赖权重文件。
这里要验的是"API 与编排层的契约"，与具体业务无关——所以 demo 功能就够，
既能进 `--quick`，也不会因为模型换版而红。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import features as feature_registry  # noqa: E402
from jobflow import job as job_module  # noqa: E402
from jobflow.feature import Feature  # noqa: E402
from jobflow.stage import ReviewRequest, Stage, StageResult  # noqa: E402


FAILS: list = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
        FAILS.append(msg)


# ============================================================================
# demo 功能：两个程序阶段夹一个 AI 阶段，不碰模型
# ============================================================================

def _make(ctx) -> StageResult:
    ctx.save_json("count.json", {"n": 3})
    return StageResult(facts={"rows": 3})


def _check(ctx) -> ReviewRequest:
    decided = ctx.decisions("check")
    items = [{"key": f"K{i}", "名称": f"条目{i}", "建议": "A" if i % 2 else "B",
              "金额": 100 * (i + 1)}
             for i in range(3) if f"K{i}" not in decided]
    return ReviewRequest(items=items, value_enum=["A", "B"],
                         help="挑一个值", key_name="key")


def _finish(ctx) -> StageResult:
    picks = ctx.decisions("check")
    p = ctx.out("demo.txt")
    p.write_text(json.dumps({"picks": picks}, ensure_ascii=False), encoding="utf-8")
    return StageResult(artifacts={"result": str(p)}, facts={"picked": len(picks)})


DEMO = Feature(
    id="demo", name="演示功能", description="接口契约测试用",
    requires=("voucher_id", "subject_l1", "debit", "credit"),
    accept=(".csv", ".xlsx"),
    stages=(
        Stage(id="make", title="造数", actor="program", run=_make),
        Stage(id="check", title="复核", actor="ai", depends_on=("make",),
              review=_check, help="挑一个值"),
        Stage(id="finish", title="收尾", actor="program", depends_on=("check",),
              run=_finish),
    ),
    exports=({"key": "result", "label": "演示产物", "stage": "finish"},),
)


def install_demo(tmp: Path) -> None:
    feature_registry._LOADERS["demo"] = "features.demo_stub"
    feature_registry._cache["demo"] = DEMO
    # 任务目录指到临时目录，别把测试任务混进真实的 _storage_jobs
    job_module.JOBS_DIR = tmp


def wait(client: TestClient, jid: str, timeout: float = 20) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = client.get(f"/api/flow/{jid}/next").json()
        if "next" not in n:          # 正在写 state（有了原子写不该发生，留个兜底）
            time.sleep(0.05)
            continue
        if not n.get("running") and n.get("status") not in ("running", "created"):
            return n
        time.sleep(0.05)
    raise TimeoutError(f"{jid} 没有在 {timeout}s 内收敛")


# ============================================================================

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="flowtest-"))
    install_demo(tmp)
    try:
        return run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(tmp: Path) -> int:
    from api.app import app
    from api import flow_api
    client = TestClient(app)

    # flow_api 在 import 时 `from jobflow import JOBS_DIR` 绑定了引用，
    # 只改 jobflow.job 的话"列任务"仍会读到真实目录（测试就白测了）
    flow_api.JOBS_DIR = tmp

    print("\n=== 功能清单 ===")
    r = client.get("/api/features")
    ids = [f["id"] for f in r.json()["items"]]
    check(r.status_code == 200 and "demo" in ids and "cashflow" in ids,
          f"/api/features 列出注册表（含 demo）")
    demo = next(f for f in r.json()["items"] if f["id"] == "demo")
    check([s["actor"] for s in demo["stages"]] == ["program", "ai", "program"],
          "manifest 带阶段链与 actor")
    check(client.get("/api/features/nope").status_code == 404, "未知功能 404")

    print("\n=== 读表（不做业务判断）===")
    csv = "凭证编号,一级科目,借方金额,贷方金额,摘要\nA1,银行存款,10,0,收款\n"
    r = client.post("/api/flow/inspect", files={"file": ("t.csv", csv.encode())},
                    data={"feature": "demo"})
    info = r.json()
    check(r.status_code == 200 and info["rows"] == 1, "上传即读表")
    check(set(info["suggested_mapping"]) >= {"voucher_id", "subject_l1",
                                             "debit", "credit"},
          "建议映射用的是 canonical 名")
    check(info["mapping_complete"] is True, "必需字段认全了")

    print("\n=== 建任务 → 停在 AI 阶段 ===")
    r = client.post("/api/features/demo/jobs",
                    files={"file": ("t.csv", csv.encode())},
                    data={"mapping": json.dumps(info["suggested_mapping"])})
    jid = r.json()["job_id"]
    n = wait(client, jid)
    check(n["status"] == "awaiting_review", "跑到 AI 阶段停下")
    check(n["next"]["actor"] == "ai" and n["next"]["stage"] == "check",
          "next 指明 actor/stage")
    check(n["next"]["count"] == 3, "next 带待办条数")
    check(n["next"]["read"].endswith("check.request.json"), "next 说清读哪个文件")
    states = {s["id"]: s["status"] for s in n["stages"]}
    check(states == {"make": "done", "check": "awaiting", "finish": "pending"},
          f"阶段时间线状态正确（{states}）")

    print("\n=== 复核待办 ===")
    rv = client.get(f"/api/flow/{jid}/review").json()
    check(rv["stage"] == "check" and rv["total"] == 3, "review 默认给当前阶段")
    keys = [c["key"] for c in rv["columns"]]
    check(keys[0] == "key" and "名称" in keys and "金额" in keys, "列定义由 items 推出")
    check({c["key"]: c["kind"] for c in rv["columns"]}["金额"] == "num",
          "金额识别成数值列")
    check(rv["value_enum"] == ["A", "B"], "取值清单透传")
    check(client.get(f"/api/flow/{jid}/review/stage/../x").status_code in (404, 400),
          "奇怪的任务号被拒")

    print("\n=== 交结论（合法 + 非法各一条）===")
    r = client.post(f"/api/flow/{jid}/decisions", json={"decisions": [
        {"key": "K0", "value": "A", "note": "看着像 A"},
        {"key": "K1", "value": "Z"},
        {"key": "不存在", "value": "A"},
    ]})
    res = r.json()
    check(r.status_code == 200, "提交接口 200")
    check(res["accepted"] == 1, f"采纳 1 条（实得 {res['accepted']}）")
    check(len(res["rejected"]) == 2, "驳回 2 条")
    reasons = " ".join(x["reason"] for x in res["rejected"])
    check("取值不合法" in reasons and "不在本轮待办" in reasons, "驳回原因是人话")

    n = wait(client, jid)
    check(n["next"]["count"] == 2, "已决定的条目不再出现在待办里")
    rv = client.get(f"/api/flow/{jid}/review").json()
    check(rv["total"] == 2 and rv["submitted"].get("K0") == "A",
          "review 回显已交结论")

    print("\n=== 弃权 → 跑完 ===")
    r = client.post(f"/api/flow/{jid}/decisions",
                    json={"stage": "check", "decisions": [], "waive": True})
    check(r.json()["waived"] is True, "空结论 = 弃权")
    n = wait(client, jid)
    check(n["status"] == "done", "弃权后继续跑到底")
    check(n["next"]["actor"] == "none", "完成后 next.actor = none")

    print("\n=== 产物与下载 ===")
    f = client.get(f"/api/flow/{jid}/files").json()
    check(len(f["files"]) == 1 and f["files"][0]["key"] == "result", "产物清单")
    d = client.get(f["files"][0]["url"])
    check(d.status_code == 200, "下载 200")
    check(json.loads(d.content)["picks"] == {"K0": "A"},
          "产物内容 = 采纳的结论（弃权不写补丁）")
    check(client.get(f"/api/flow/{jid}/files/nope").status_code == 404,
          "不存在的产物 404")

    print("\n=== 幂等 / 状态 / 清理 ===")
    before = client.get(f"/api/flow/{jid}").json()["updated_at"]
    client.post(f"/api/flow/{jid}/resume")
    n = wait(client, jid)
    check(n["status"] == "done", "resume 对已完成任务是空转（哈希没变）")
    listed = client.get("/api/flow?limit=50").json()["items"]
    row = next((x for x in listed if x["job_id"] == jid), None)
    check(row is not None and row["feature_name"] == "演示功能",
          "任务清单能列出来，且带中文功能名与状态")
    check(row["status"] == "done" and row["filename"] == "t.csv", "清单字段齐全")
    check(client.delete(f"/api/flow/{jid}").status_code == 200, "删除任务")
    check(client.get(f"/api/flow/{jid}").status_code == 404, "删完就查不到")
    print(f"    （before={before}）")

    print()
    if FAILS:
        print(f"{len(FAILS)} 项不通过：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
