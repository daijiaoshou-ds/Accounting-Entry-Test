# -*- coding: utf-8 -*-
"""配账阶段解析一次账 → 各功能载入同一份；中止；AI 阶段炸了要落 failed。

三件事各自都踩过坑：

1. **读表只做一次**。四个功能各读一遍 xlsx，不只是慢（每份账每功能 0.3s，
   全年账 2.5s），更糟的是各认各的列：同一份账在清洗里认对了、在对方科目里
   认错一列，两边结果对不上而没有任何地方报错。所以配账时落
   `prepared/raw.parquet` + `schema.json`，功能只载入。

2. **空单元格**。落 parquet 时字符串列变成 pandas 的 `string`（可空），读回来
   空值就是 `pd.NA`，而 `pd.NA` 在布尔上下文里直接抛
   `TypeError: boolean value of NA is ambiguous`。真账里空科目很常见
   （序时账7 就有一张只有一行、科目为空、金额为 0 的凭证）——曾经让整个复核
   阶段崩掉，任务永远停在「正在跑」。

3. **AI 阶段炸了要落 failed**。程序阶段有 try/except，AI 阶段以前没有：进程退出了，
   state.json 还写着 running，页面上就是永远转圈，用户再点两次「开始处理」就多
   两个假运行。

用法：
    venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_prepared.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import journal_io  # noqa: E402
from features.cleaning import stages as clean_s  # noqa: E402
from jobflow import Job  # noqa: E402
from jobflow.feature import Feature  # noqa: E402
from jobflow.prepare import prepare  # noqa: E402
from jobflow.stage import Ctx, Stage  # noqa: E402

SOURCE = ROOT / "case" / "序时账1.xlsx"

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def ctx_for(job, stage_id):
    return Ctx(job, job.feature.stage(stage_id), lambda *_a, **_k: None)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # ── ① 配账：解析一次，落 raw.parquet + schema.json ──
    j = journal_io.read(SOURCE)
    prepared = tmp / "prepared"
    summary = prepare(j, prepared)
    check((prepared / "raw.parquet").is_file(), "prepare 没落 raw.parquet")
    check((prepared / "schema.json").is_file(), "prepare 没落 schema.json")
    check((prepared / "combo.parquet").is_file(), "prepare 没落 combo.parquet")
    check(summary.get("rows") == len(j), "prepare 汇总里的行数不对")
    check(clean_s.prepared_dir({"prepared": str(prepared)}) == prepared,
          "prepared_dir 认不出已解析的账")
    check(clean_s.prepared_dir({"prepared": str(tmp / "nope")}) is None,
          "prepared_dir 把一个空目录当成已解析")

    # ── ② 功能侧：载入而不是重读原件 ──
    job = Job.create("cleaning", SOURCE, options={"task": "task_t1",
                                                  "prepared": str(prepared)}, root=tmp / "jobs")
    result = clean_s.run_read(ctx_for(job, "read"))
    check(result.facts.get("source") == "task", "read 没走配账产物（facts.source 不是 task）")
    check(result.facts.get("engine") == "parquet", "read 还在用 xlsx 引擎解析")
    check(result.facts.get("rows") == len(j), "载入的行数和配账时不一致")
    check((job.dir / "out" / "read" / "raw.parquet").is_file(),
          "载入后没把 raw.parquet 搬进任务目录（任务就不独立了）")

    # 载入的账和配账那份一致（schema 里的映射要原样带过来）
    check(job.journal.schema.mapping == j.schema.mapping, "载入后字段映射变了")

    # 匿名目录（没有 prepared）→ 老路：当场读原件
    legacy = Job.create("cleaning", SOURCE, root=tmp / "jobs")
    old = clean_s.run_read(ctx_for(legacy, "read"))
    check(old.facts.get("source") == "file", "没配账时应该当场读原件")

    # ── ③ 重新配账要让 read 阶段的哈希变（否则老运行不会重算） ──
    sig_before = clean_s.prepared_signature(ctx_for(job, "read"))
    frame = pd.read_parquet(prepared / "raw.parquet").drop(columns=[j.columns[0]])
    frame.to_parquet(prepared / "raw.parquet", index=False)
    sig_after = clean_s.prepared_signature(ctx_for(job, "read"))
    check(sig_before != sig_after, "换了已解析的账，read 阶段的指纹却没变")

    # ── ④ 空科目（pd.NA）不该让复核阶段崩 ──
    job2 = Job.create("cleaning", SOURCE, root=tmp / "jobs")
    j2 = journal_io.read(SOURCE)
    v_col = j2.schema.mapping["voucher_id"]
    s_col = j2.schema.mapping["subject_l1"]
    df = j2.df.copy()
    df.loc[df.index[0], s_col] = None
    null_voucher = str(df.loc[df.index[0], v_col])
    job2.save_journal(journal_io.Journal(df, j2.schema, source=SOURCE.name))
    # 落盘再读回来：空值这时才变成 pd.NA，也就是线上出问题的那个形态
    clean_dir = job2.dir / "out" / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)
    journal_io.write_table(df, clean_dir / "result.parquet", target="archive")
    reloaded = pd.read_parquet(clean_dir / "result.parquet", columns=[s_col])
    check(str(reloaded[s_col].dtype) == "string" and reloaded[s_col].isna().any(),
          "合成的空科目没变成 pd.NA，这个测试就没测到东西")
    try:
        lines = clean_s.voucher_lines(ctx_for(job2, "review_bucket"), [null_voucher])
        check(any("（空科目）" in line for line in lines.get(null_voucher, [])),
              "空科目没有回退成（空科目）")
    except TypeError as exc:
        check(False, f"空科目把复核阶段带崩了：{exc}")

    # ── ⑤ 中止：状态是 aborted，不是 failed ──
    job3 = Job.create("cleaning", SOURCE, root=tmp / "jobs")
    out = job3.abort()
    check(out["ok"] and job3.status == "aborted", "abort 没把状态改成 aborted")
    nxt = job3.next_action(with_items=False)["next"]
    check(nxt.get("actor") == "operator", "中止后下一步不该让程序继续跑")
    job3.state["status"] = "done"
    check(job3.abort()["ok"] is False, "已经跑完的任务不该被中止")

    # ── ⑥ AI 阶段抛错 → failed（不是永远 running） ──
    def boom(_ctx):
        raise RuntimeError("待办构造失败")

    fake = Feature(id="fake", name="假功能",
                   stages=(Stage(id="rev", title="复核", actor="ai", review=boom),))
    folder = tmp / "jobs" / "fakejob"
    (folder / "source").mkdir(parents=True)
    state = {
        "job_id": "fakejob", "schema_version": 1, "feature": "fake",
        "feature_name": "假功能", "created_at": "x", "updated_at": "x",
        "input": {"file": "source/a.xlsx", "filename": "a.xlsx",
                  "sha256": "0" * 64, "bytes": 0},
        "options": {}, "stages": {}, "status": "created", "error": None,
    }
    broken = Job(folder, state)
    broken._feature = fake
    raised = False
    try:
        broken.advance()
    except RuntimeError:
        raised = True
    check(raised, "AI 阶段的异常没往上抛（调用方看不出失败）")
    check(broken.state["status"] == "failed",
          f"AI 阶段崩了还是 {broken.state['status']}，页面会一直转圈")
    check("待办构造失败" in str(broken.state.get("error")), "failed 里没有原因")
    check(broken.state["stages"]["rev"]["status"] == "failed", "阶段状态没落 failed")

if fails:
    print("\n".join(f"FAIL {x}" for x in fails))
    sys.exit(1)
print("PASS 配账解析一次 + 功能只载入；空科目不崩；中止与 AI 阶段失败都有明确状态")
