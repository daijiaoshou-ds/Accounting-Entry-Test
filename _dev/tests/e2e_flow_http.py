# -*- coding: utf-8 -*-
"""接口层端到端自测：真起 HTTP（不是 TestClient），跑完整条链。

    uvicorn api.app:app --port 8502      # 先起服务
    python _dev/tests/e2e_flow_http.py [账文件]

验的正是"用户会怎么用它"：

    ① 序时账清洗：上传 → 停在中/低置信复核 → 改一条桶 → 弃权剩下的 → 导出
    ② 现金流量表：**从①的任务起跑**（不重复分类与复核）→ 停 → 导出
    ③ 回①再改一条桶 → resume② → 现金流自动重算（哈希接力）

注意：本机若配了 HTTP(S)_PROXY，requests 会把 127.0.0.1 也转给代理（回 502），
所以这里 `trust_env = False`。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8502"

S = requests.Session()
S.trust_env = False

BOOK = Path(sys.argv[1] if len(sys.argv) > 1 else "case/序时账1.xlsx")


def wait(jid: str, timeout: float = 900) -> dict:
    """等到"不忙了"（停在 AI 阶段 / 完成 / 失败）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = S.get(f"{BASE}/api/flow/{jid}/next", timeout=30).json()
        if "next" not in n:
            time.sleep(0.2)
            continue
        if not n.get("running") and n.get("status") not in ("running", "created"):
            return n
        time.sleep(0.5)
    raise TimeoutError(f"{jid} 超时未收敛")


def drain(jid: str) -> dict:
    """把剩下的 AI 阶段都弃权掉，直到跑完（最多 4 个停靠点）。"""
    n = wait(jid)
    for _ in range(4):
        stage = n["next"].get("stage")
        if not stage:
            break
        S.post(f"{BASE}/api/flow/{jid}/decisions",
               json={"stage": stage, "decisions": [], "waive": True}, timeout=60)
        n = wait(jid)
    return n


def stages_of(jid: str) -> dict:
    return {s["id"]: s["status"]
            for s in S.get(f"{BASE}/api/flow/{jid}").json()["stages"]}


def cashflow_of(jid: str) -> dict:
    f = S.get(f"{BASE}/api/flow/{jid}/files", timeout=60).json()
    return {k: f["cashflow"].get(k) for k in
            ("money_rows", "needs_review_rows", "net_cash")}


def main() -> int:
    print(f"账：{BOOK}")
    with open(BOOK, "rb") as f:
        info = S.post(f"{BASE}/api/flow/inspect",
                      files={"file": (BOOK.name, f, "application/octet-stream")},
                      data={"feature": "cleaning"}, timeout=300).json()
    print(f"读表：{info['rows']} 行 / {len(info['columns'])} 列 / {info['engine']}"
          f" / 映射完整={info['mapping_complete']}")

    # ── ① 序时账清洗 ──
    with open(BOOK, "rb") as f:
        r = S.post(f"{BASE}/api/features/cleaning/jobs",
                   files={"file": (BOOK.name, f, "application/octet-stream")},
                   data={"mapping": json.dumps(info["suggested_mapping"])},
                   timeout=300)
    r.raise_for_status()
    clean_job = r.json()["job_id"]
    print(f"\n① 清洗任务 {clean_job}")
    n = wait(clean_job)
    print(f"   停靠：{n['status']} / {n['next'].get('stage')} / "
          f"{n['next'].get('count')} 条")
    for s in n["stages"]:
        print(f"   {s['title']:<22} {s['status']:<10} {s.get('elapsed') or ''}")

    rv = S.get(f"{BASE}/api/flow/{clean_job}/review", timeout=60).json()
    first = rv["items"][0]
    alt = next((v for v in rv["value_enum"] if v != first["模型桶"]), "")
    S.post(f"{BASE}/api/flow/{clean_job}/decisions", json={
        "stage": "review_bucket",
        "decisions": [{"key": first["key"], "value": alt, "note": "接口自测"}],
    }, timeout=60)
    wait(clean_job)
    S.post(f"{BASE}/api/flow/{clean_job}/decisions",
           json={"stage": "review_bucket", "decisions": [], "waive": True},
           timeout=60)
    wait(clean_job)
    files = S.get(f"{BASE}/api/flow/{clean_job}/files", timeout=60).json()
    print(f"   导出：{[f['filename'] for f in files['files']]}"
          f" / {files['export'].get('format')}")

    # ── ② 从清洗任务起跑现金流 ──
    r = S.post(f"{BASE}/api/features/cashflow/jobs",
               data={"upstream": clean_job}, timeout=120)
    r.raise_for_status()
    cf_job = r.json()["job_id"]
    print(f"\n② 现金流任务 {cf_job}（接着 {clean_job}）")
    n = wait(cf_job)
    st = stages_of(cf_job)
    print(f"   阶段：{st}")
    if st.get("read") != "skipped" or st.get("clean") != "skipped":
        print("   ✗ 从上游起跑时 read/clean 应当跳过")
        return 1
    print(f"   停靠：{n['status']} / {n['next'].get('stage')} / "
          f"{n['next'].get('count')} 条")
    drain(cf_job)
    before = cashflow_of(cf_job)
    print(f"   现金流：{before}")

    # ── ③ 回上游改一条桶，resume 现金流应当自动重算 ──
    print("\n③ 回上游再改一条桶 → resume 现金流")
    rv = S.get(f"{BASE}/api/flow/{clean_job}/review", timeout=60).json()
    if rv.get("items"):
        it = rv["items"][0]
        alt = next((v for v in rv["value_enum"] if v != it["模型桶"]), "")
        S.post(f"{BASE}/api/flow/{clean_job}/decisions", json={
            "stage": "review_bucket",
            "decisions": [{"key": it["key"], "value": alt, "note": "改桶试链"}],
        }, timeout=60)
        wait(clean_job)
    S.post(f"{BASE}/api/flow/{cf_job}/resume", timeout=60)
    drain(cf_job)
    after = cashflow_of(cf_job)
    overrides = 0
    for s in S.get(f"{BASE}/api/flow/{cf_job}").json()["stages"]:
        if s["id"] == "judge":
            overrides = (s.get("facts") or {}).get("bucket_overrides", 0)
    print(f"   现金流：{after}（改桶 {overrides} 条）")
    if overrides < 1:
        print("   ✗ 上游的改桶结论没有传到现金流")
        return 1

    # ── 产物下载 ──
    print("\n④ 产物")
    for jid, tag in ((clean_job, "清洗"), (cf_job, "现金流")):
        for f in S.get(f"{BASE}/api/flow/{jid}/files", timeout=60).json()["files"]:
            d = S.get(f"{BASE}{f['url']}", timeout=300)
            print(f"   {tag} {f['filename']}：{d.status_code} / "
                  f"{len(d.content) / 1024:.0f}KB")

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
