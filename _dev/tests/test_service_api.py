# -*- coding: utf-8 -*-
"""服务层 + API 契约的端到端冒烟（离线可跑，不依赖 Streamlit）。

覆盖：
  1. inspect（读表 + 建议映射）
  2. create_job → run_job（同步跑，样本取前 3,000 行，别和训练抢 GPU）
  3. review_rows 分页 / export 导出 / unreviewed 读取
  4. 纠错表导出 → 改一行 → 回传解析（dry_run，不落库）
  5. HTTP：health / buckets / inspect / jobs / rows / export 头

用法：python _dev/tests/test_service_api.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ["SUMMARY_CLEANER_JOBS_DIR"] = tempfile.mkdtemp(prefix="jobs_")

from fastapi.testclient import TestClient  # noqa: E402

from api.app import store  # noqa: E402
from summary_cleaner.service import cleaner  # noqa: E402
from summary_cleaner.v2.io_utils import read_table  # noqa: E402

BOOK = ROOT / "summary_cleaner_v3/_dev/序时账1.xlsx"
SAMPLE_ROWS = 3000

fails = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


print("=== 1. inspect ===")
df_all = read_table(BOOK)
sample = df_all.head(SAMPLE_ROWS)
tmp_xlsx = Path(tempfile.mkdtemp()) / "样本.xlsx"
sample.to_excel(tmp_xlsx, index=False)
raw = tmp_xlsx.read_bytes()
info = cleaner.inspect(raw, filename="样本.xlsx")
check("识别出行数", info["rows"] == SAMPLE_ROWS, str(info["rows"]))
check("映射完整", info["mapping_complete"], str(info["missing_fields"]))
check("有预览", len(info["preview"]) > 0)
print("     mapping:", info["suggested_mapping"])

print("=== 2. 建任务 + 跑清洗 ===")
t0 = time.time()
job = cleaner.create_job(store, raw, "样本.xlsx", info["suggested_mapping"])
summary = cleaner.run_job(job)
elapsed = time.time() - t0
check("状态 done", job.status["state"] == "done", job.status["message"])
check("有桶分布", bool(summary["bucket_counts"]), f"{len(summary['bucket_counts'])} 桶")
check("凭证数 > 0", summary["vouchers"] > 0, str(summary["vouchers"]))
check("明细已落盘", job.file("detail.parquet").exists())
print(f"     {elapsed:.1f}s / {summary['vouchers']} 凭证 / 置信度 {summary['confidence_dist']}")

print("=== 3. 分页 / 导出 / 未审数据 ===")
page = cleaner.review_rows(job, level="all", offset=0, limit=5)
check("分页返回 5 条", len(page["items"]) == 5, f"total={page['total']}")
check("列齐", all(c in page["items"][0] for c in ("凭证号", "业务分类", "置信度")))
data, name, mime, _ = cleaner.export(job, "result")
check("导出 xlsx", name.endswith(".xlsx") and len(data) > 5000, f"{len(data)//1024}KB")
try:
    un = cleaner.unreviewed(job)
    check("未审数据可读", bool(un.get("buckets")), f"{len(un.get('buckets') or {})} 桶")
except Exception as e:
    check("未审数据可读", False, repr(e))

print("=== 4. 4.0 引擎字段 ===")
result_df = job.load_frame("result.parquet")
detail_df = job.load_frame("detail.parquet")
check("结果含判定来源", "判定来源" in result_df.columns)
check("引擎信息完整", bool((job.meta.get("summary") or {}).get("engine", {}).get("mode")),
      str((job.meta.get("summary") or {}).get("engine")))
check("硬约束统计存在", isinstance((job.meta.get("summary") or {}).get("hard_rule_counts"), dict))
check("明细是凭证级 14 列", len(detail_df.columns) == 14, str(list(detail_df.columns)))
check("明细含组内张数", "组内张数" in detail_df.columns)
check("去重统计存在", isinstance((job.meta.get("summary") or {}).get("dedup"), dict),
      str((job.meta.get("summary") or {}).get("dedup")))

print("=== 5. HTTP 契约 ===")
client = TestClient(store_app := __import__("api.app", fromlist=["app"]).app)
r = client.get("/api/health")
check("health 200", r.status_code == 200 and r.json()["status"] == "ok")
check("health 带模型信息", "model" in r.json())
r = client.get("/api/buckets")
check("buckets 33 项", r.status_code == 200 and r.json()["count"] >= 30,
      str(r.json().get("count")))
r = client.post("/api/inspect", files={"file": ("样本.xlsx", raw)})
check("POST /api/inspect", r.status_code == 200 and r.json()["rows"] == SAMPLE_ROWS)
r = client.post("/api/jobs/local", json={"path": str(tmp_xlsx)})
check("POST /api/jobs/local", r.status_code == 200, str(r.json())[:120])
job_id = r.json()["job_id"]
for _ in range(120):
    st = client.get(f"/api/jobs/{job_id}").json()["status"]
    if st["state"] in ("done", "failed"):
        break
    time.sleep(1)
check("HTTP 任务完成", st["state"] == "done", f"{st['state']} {st.get('message')}")
r = client.get(f"/api/jobs/{job_id}/rows", params={"level": "all", "limit": 3})
check("GET rows", r.status_code == 200 and len(r.json()["items"]) == 3)
r = client.get(f"/api/jobs/{job_id}/export", params={"kind": "vouchers"})
check("GET export", r.status_code == 200 and len(r.content) > 1000)
r = client.get(f"/api/jobs/{job_id}")
check("summary.engine 存在", bool((r.json().get("summary") or {}).get("engine")),
      str((r.json().get("summary") or {}).get("engine")))
r = client.post(f"/api/jobs/{job_id}/corrections", files={"file": ("x.xlsx", b"x")})
check("纠错接口已下线（404/405）", r.status_code in (404, 405), str(r.status_code))
r = client.get("/api/jobs/不存在的ID")
check("非法 job_id → 404", r.status_code == 404)

print()
if fails:
    print(f"❌ {len(fails)} 项失败：" + "、".join(fails))
    sys.exit(1)
print("✅ 服务层 + API 契约 全部通过")
