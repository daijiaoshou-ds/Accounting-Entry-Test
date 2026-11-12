# -*- coding: utf-8 -*-
"""对方科目分析（features/contra）自测。

两段：

    A. 纯逻辑（秒级）—— 哪些模式该问人、决策怎么翻译成引擎要的 `"<p>-<rank>"`、
       方案序号越界怎么办、产物里"没定案"的行怎么数
    B. 端到端（真账，约 15s）—— 建任务 → 停在复核位 → 交结论 → 导出，
       并把导出的表读回来看列和行数

为什么 B 一定要跑真账：`finalize_report` 是引擎内部一整套遍历，任何一处 mapping
对不上（日期/摘要/明细科目）都只在真数据上炸。

用法：
    venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_contra.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import features  # noqa: E402
from features.contra import engine  # noqa: E402
from jobflow import Job  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# ============================================================================
# A. 纯逻辑
# ============================================================================

def pattern(key, count, scores, name=None):
    return {
        "key": key, "name": name or f"组合{key}", "count": count,
        "voucher": "记-0001", "summary": "", "entries": [],
        "options": [{"rank": i + 1, "score": s, "pairs": []} for i, s in enumerate(scores)],
        "solutions": len(scores),
        "timeout": False,
        "gap": round(scores[0] - scores[1], 2) if len(scores) > 1 else None,
    }


# 只有一个方案 → 没什么可选的
check(not engine.needs_review(pattern("a", 5, [90])), "单方案的模式不该问人")
# 两个方案但分数差很远、只出现一次 → 不值得问
check(not engine.needs_review(pattern("b", 1, [98, 85])), "只出现一次且差距大的不该问")
# 分数并列 → 算法分不出来，必须问
check(engine.needs_review(pattern("c", 1, [98, 98])), "并列的模式要问")
# 出现多次 → 选错一次错一批，要问
check(engine.needs_review(pattern("d", 3, [98, 85])), "高频模式要问")

pats = [pattern("one", 1, [98]), pattern("tie", 1, [98, 98]),
        pattern("hot", 5, [98, 85]), pattern("big", 9, [98, 98])]
picked = [p["key"] for p in engine.candidates(pats)]
check(picked[:2] == ["big", "hot"], f"排序应按影响面：先高频后低频，实际 {picked}")
check("one" not in picked, "单方案的模式不该进候选")
check(set(picked) == {"big", "hot", "tie"}, f"候选集合不对：{picked}")

check(engine._selection(2) == "0-2", "决策序号要翻成引擎认的 '<任意>-<rank>'")
check(engine._selection(1) == "0-1", "第 1 案")


class StubProcessor:
    """只记下 finalize 收到了什么，不真算。"""

    def __init__(self, df):
        self.seen = None
        self._df = df

    def finalize_report(self, kb, log, user_selections=None, custom_solutions=None):
        self.seen = user_selections
        return self._df


df_stub = pd.DataFrame({"对方科目-一级": ["应付账款", "需人工分析(无解)", "无效分录"]})
stub = StubProcessor(df_stub)
df, info = engine.finalize(stub, [pattern("p1", 2, [98, 98]), pattern("p2", 1, [90])],
                           {"p1": "2", "p2": "9", "nope": "1"})
check(stub.seen == {"组合p1": "0-2", "组合p2": "0-1"},
      f"决策翻译不对：{stub.seen}")
check(info["applied"] == 2, f"applied 应为 2，实际 {info['applied']}")
check(info["ignored"] == ["nope"], f"对不上模式的 key 要记进 ignored：{info['ignored']}")
check(len(info["clamped"]) == 1 and "只有" in info["clamped"][0],
      f"越界的序号要记进 clamped：{info['clamped']}")
check(info["unresolved_rows"] == 2,
      f"没定案的行数不对（需人工分析 + 无效分录 = 2）：{info['unresolved_rows']}")


# ============================================================================
# B. 端到端（真账）
# ============================================================================

BOOK = ROOT / "case" / "序时账1.xlsx"
if not BOOK.is_file():
    fails.append(f"测试账不存在：{BOOK}")
else:
    tmp = Path(tempfile.mkdtemp(prefix="contra_test_"))
    job = Job.create("contra", BOOK, root=tmp / "jobs")

    out = job.advance(lambda msg: None)
    check(out.get("status") == "awaiting_review",
          f"应停在复核位，实际 {out.get('status')}（{job.state.get('error')}）")
    check(out.get("stage") == "review_pattern", f"停的阶段不对：{out.get('stage')}")

    req = json.loads((job.dir / "reviews" / "review_pattern.request.json")
                     .read_text(encoding="utf-8"))
    items = req.get("items") or []
    check(len(items) > 0, "复核请求里没有待办")
    check(req.get("value_enum") == ["1", "2", "3"],
          f"value_enum 应为 1/2/3：{req.get('value_enum')}")
    check(req.get("truncated") is True, "候选 91 个、只问 25 个，应当标 truncated")

    first = items[0]
    for field in ("为什么要看", "分录", "候选方案"):
        check(field in first, f"待办缺字段「{field}」")
    check(len(first.get("候选方案") or []) >= 2, "第一条待办应该给不止一个方案")

    # 交一条改判 + 一条保持默认，剩下的弃权
    decisions = [{"key": first["key"], "value": "2", "note": "测试：换第 2 案"}]
    if len(items) > 1:
        decisions.append({"key": items[1]["key"], "value": "1", "note": "测试：保持第 1 案"})
    job.submit({"schema_version": 1, "stage": "review_pattern", "decisions": decisions},
               lambda msg: None)

    # 还有没问过的模式 → 会再停一次；弃权把这段走完
    if job.status == "awaiting_review":
        job.submit({"schema_version": 1, "stage": "review_pattern",
                    "decisions": [], "waive": True}, lambda msg: None)

    check(job.status == "done", f"最终应 done，实际 {job.status}（{job.state.get('error')}）")

    facts = job.stage_facts("export")
    check(facts.get("decisions_applied") == len(decisions),
          f"应用的结论数不对：{facts.get('decisions_applied')} != {len(decisions)}")
    check(facts.get("unresolved_rows") == 0,
          f"不该有没定案的行：{facts.get('unresolved_rows')}")
    check(isinstance(facts.get("summary"), list) and facts["summary"],
          "结果摘要（summary）缺失，报告与页面会没东西可显示")

    files = list((job.dir / "out" / "export").glob("*.xlsx"))
    check(len(files) == 1, f"导出文件数不对：{files}")
    if files:
        df = pd.read_excel(files[0])
        original = pd.read_excel(BOOK)
        check(len(df) > 0, "导出表是空的")
        check(abs(len(df) - len(original)) < len(original) * 0.02,
              f"导出行数 {len(df)} 与原表 {len(original)} 差得太多")
        for col in ("对方科目-一级", "对方科目-二级"):
            check(col in df.columns, f"导出表缺列「{col}」")
        # 1v1 的凭证：两个方向都该有对方科目
        first_voucher = df[df["凭证编号"].astype(str) == str(df["凭证编号"].iloc[0])]
        check(first_voucher["对方科目-一级"].astype(str).str.len().gt(0).all(),
              "第一张凭证有行没填对方科目")


# ============================================================================
if fails:
    print(f"FAIL {len(fails)}")
    for msg in fails:
        print("  ✗", msg)
    sys.exit(1)
print("PASS 对方科目分析：逻辑 12 项 + 端到端 1 轮")
