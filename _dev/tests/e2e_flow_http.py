# -*- coding: utf-8 -*-
"""接口层端到端自测：真起 HTTP（不是 TestClient），跑完两轮复核。

    uvicorn api.app:app --port 8502      # 先起服务
    python _dev/tests/e2e_flow_http.py [账文件]

验证的是"前端会怎么用它"：上传 → 轮询 next → 读 review → 交结论 → 下载。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8502"

# 本机有 HTTP(S)_PROXY 时 requests 会把 127.0.0.1 也转给代理（回 502）
S = requests.Session()
S.trust_env = False
BOOK = Path(sys.argv[1] if len(sys.argv) > 1 else "case/序时账1.xlsx")


def wait(jid: str, timeout: float = 600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = S.get(f"{BASE}/api/flow/{jid}/next", timeout=30).json()
        if not n.get("running") and n.get("status") not in ("running", "created"):
            return n
        time.sleep(0.5)
    raise TimeoutError(f"{jid} 超时未收敛")


def main() -> int:
    print(f"账：{BOOK}")
    with open(BOOK, "rb") as f:
        r = S.post(f"{BASE}/api/flow/inspect",
                          files={"file": (BOOK.name, f, "application/octet-stream")},
                          data={"feature": "cashflow"}, timeout=300)
    r.raise_for_status()
    info = r.json()
    print(f"读表：{info['rows']} 行 / {len(info['columns'])} 列 / {info['engine']}"
          f" / 映射完整={info['mapping_complete']}")

    with open(BOOK, "rb") as f:
        r = S.post(f"{BASE}/api/features/cashflow/jobs",
                          files={"file": (BOOK.name, f, "application/octet-stream")},
                          data={"mapping": __import__("json").dumps(info["suggested_mapping"])},
                          timeout=300)
    r.raise_for_status()
    jid = r.json()["job_id"]
    print(f"任务：{jid}")

    n = wait(jid)
    print(f"第一次停靠：{n['status']} / {n['next'].get('stage')} / "
          f"{n['next'].get('count')} 条")
    for s in n["stages"]:
        print(f"  {s['title']:<22} {s['status']:<10} {s.get('elapsed') or ''}")

    bucket = n["next"].get("stage")
    if bucket:
        rv = S.get(f"{BASE}/api/flow/{jid}/review", timeout=60).json()
        print(f"复核面：{rv['total']} 条，列 {[c['key'] for c in rv['columns']]}")
        first = rv["items"][0]
        alt = next((v for v in rv["value_enum"] if v != first["模型桶"]), "")
        r = S.post(f"{BASE}/api/flow/{jid}/decisions", json={
            "stage": bucket,
            "decisions": [{"key": first["key"], "value": alt, "note": "接口自测"}],
        }, timeout=60)
        res = r.json()
        print(f"提交：采纳 {res['accepted']} / 驳回 {len(res['rejected'])}")
        n = wait(jid)
        print(f"→ {n['status']} / {n['next'].get('stage')} / {n['next'].get('count')} 条")

    for stage in ("review_bucket", "review_cf"):
        n = S.get(f"{BASE}/api/flow/{jid}/next", timeout=30).json()
        if n["next"].get("stage") != stage:
            continue
        r = S.post(f"{BASE}/api/flow/{jid}/decisions",
                          json={"stage": stage, "decisions": [], "waive": True},
                          timeout=60)
        print(f"弃权 {stage}：{r.status_code}")
        n = wait(jid)

    print(f"终态：{n['status']}")
    files = S.get(f"{BASE}/api/flow/{jid}/files", timeout=60).json()
    for f in files["files"]:
        d = S.get(f"{BASE}{f['url']}", timeout=300)
        print(f"  下载 {f['filename']}：{d.status_code} / {len(d.content) / 1024:.0f}KB")
    cf = files["cashflow"]
    print(f"现金流：{cf.get('money_rows')} 条 / 待复核 {cf.get('needs_review_rows')}"
          f" / 净现金 {cf.get('net_cash')}")
    print(f"报表 {len(files['statement'])} 行，导出售价 {files['export']}")
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
