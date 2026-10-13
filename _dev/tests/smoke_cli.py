# -*- coding: utf-8 -*-
"""冒烟测试：用金标准造一份样例序时账，跑通 CLI 的 status / check / run。

产物写到 _dev/smoke/（不碰生产存储）。
"""
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "summary_cleaner_v3"
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

GOLD = SKILL / "_dev" / "training" / "training_data.json"
WORK = ROOT / "_dev" / "smoke"
PY = str(ROOT / "venv" / "Scripts" / "python.exe")


def make_sample(n_vouchers=800, seed=42):
    """按金标准分布造样例序时账（含重复摘要，模拟真实按月重复）。

    金标准结构（buckets_v2）：
        {"buckets": {桶: [{"subjects": [...], "records": [{"summary", "count"}]}]}}
    """
    d = json.loads(GOLD.read_text(encoding="utf-8"))
    pool = []
    for groups in d["buckets"].values():
        for g in groups:
            for r in g["records"]:
                pool.append((g["subjects"], r["summary"],
                             int(r.get("count", 1))))
    rng = random.Random(seed)
    rng.shuffle(pool)

    rows, vid = [], 0
    for subjects, summary, count in pool:
        for _ in range(min(count, 20)):
            vid += 1
            if vid > n_vouchers:
                break
            amt = round(rng.uniform(500, 2_000_000), 2)
            bk = rng.choice(["张三", "李四", "王五"])
            for s in subjects:
                is_debit = s.endswith("[借]")
                rows.append({
                    "凭证号": f"记-{vid:05d}",
                    "科目": s[:-3],
                    "科目名称": s[:-3] + "-明细",
                    "摘要": summary,
                    "借方金额": amt if is_debit else 0.0,
                    "贷方金额": 0.0 if is_debit else amt,
                    "制单人": bk,
                })
        if vid > n_vouchers:
            break
    return pd.DataFrame(rows)


def run_cli(*args, expect_ok=True):
    """调 CLI 并解析 RESULT JSON。"""
    cmd = [PY, "-X", "utf8", str(SKILL / "cli.py"), *args]
    print(f"\n$ {' '.join(str(a) for a in args)}", flush=True)
    r = subprocess.run(cmd, cwd=str(SKILL), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    payload = None
    for line in out.splitlines():
        if line.startswith("RESULT "):
            payload = json.loads(line[7:])
    if payload is None:
        print("  [FAIL] 没有解析到 RESULT 行")
        print("  stdout:", out[:600])
        print("  stderr:", (r.stderr or "")[-800:])
        return None
    if expect_ok is True and not payload.get("ok"):
        print("  [FAIL] ok=False:", json.dumps(payload, ensure_ascii=False)[:600])
        return None
    return payload


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    xlsx = WORK / "样例序时账.xlsx"
    if not xlsx.exists():
        if not GOLD.exists():
            print(f"[跳过造数据] 金标准不存在: {GOLD}")
        else:
            df = make_sample()
            with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="序时账")
            print(f"[造数据] {xlsx.name}: {len(df)} 行, "
                  f"{df['凭证号'].nunique()} 张凭证")
    else:
        print(f"[复用] {xlsx.name}")

    ok = True

    print("\n" + "=" * 60 + "\n1) status\n" + "=" * 60)
    st = run_cli("status", expect_ok=None)
    if st:
        print(f"  模型就绪={st['model_ready']} 设备={st['inference_device_will_be']} "
              f"桶数={st['bucket_count']}")
        cf = st.get("cashflow") or {}
        print(f"  现金流映射: 项目={cf.get('cf_item_count')} "
              f"桶={cf.get('bucket_count')} 问题={cf.get('problem_count')} "
              f"待复核桶={cf.get('needs_review_buckets')}")
        if cf.get("problem_count"):
            ok = False
    else:
        ok = False

    print("\n" + "=" * 60 + "\n2) check\n" + "=" * 60)
    ck = run_cli("check", str(xlsx))
    if ck:
        print(f"  行数={ck['rows']} 缺字段={ck['missing_required_cn']} ok={ck['ok']}")
        for k, v in ck["detected_mapping"].items():
            print(f"    {k:14s} -> {v}")
    else:
        ok = False

    print("\n" + "=" * 60 + "\n3) run\n" + "=" * 60)
    rn = run_cli("run", str(xlsx), "--out", str(WORK / "输出"))
    if rn:
        cf = rn.get("cashflow") or {}
        print(f"  凭证={rn['vouchers']} 已分类={rn['classified']} "
              f"覆盖={rn['coverage']}")
        print(f"  设备={rn['device']}/{rn['precision']} "
              f"耗时={rn['elapsed_seconds']}s")
        print(f"  置信度={rn['confidence_dist']}")
        print(f"  现金流: 相关凭证={cf.get('money_vouchers')} "
              f"分录={cf.get('money_rows')} 冲销={cf.get('wash_rows')} "
              f"待复核={cf.get('needs_review_rows')} 净额={cf.get('net_cash')}")
        print(f"  分活动: {cf.get('amount_by_section')}")
        print(f"  待复核出参条数: {rn.get('cashflow_review', {}).get('count')}")
        print("  输出文件（应只有 1 个）:")
        for k, v in rn["output_files"].items():
            exists = Path(v).exists()
            print(f"    {'OK  ' if exists else 'MISS'} {k}: {v}")
            if not exists:
                ok = False
        if len(rn["output_files"]) != 1:
            print(f"    ❌ 预期只有 1 个输出文件，实得 {len(rn['output_files'])}")
            ok = False
        print(f"  sheet 行数: {rn.get('sheet_rows')}")
        print("  桶分布（前 6）:")
        bs = sorted(rn["bucket_counts"].items(), key=lambda kv: -kv[1])[:6]
        for b, n in bs:
            print(f"    {b:10s} {n:5d} 张")
    else:
        ok = False

    print("\n" + "=" * 60)
    print("冒烟结果:", "全部通过 ✅" if ok else "有失败 ❌")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
