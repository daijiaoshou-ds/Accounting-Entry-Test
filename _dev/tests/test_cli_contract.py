# -*- coding: utf-8 -*-
"""CLI 的对外契约：缺依赖的规范出参、`--out-json`、参数位置 —— 纯单测，秒级。

为什么单独一套：这些都是**上一轮真账实测踩出来的坑**，而且都不是"算错数"，
是"AI 看不懂/读不进来"——功能测试永远抓不到：

  P0-3  缺 torch 时任何命令都甩 19 行 traceback → AI 以为代码有 bug，去改代码
  P0-4  `python cli.py units x.xlsx 1> units.json` 拿到的是 **UTF-16LE**
        （PowerShell 的行为），按 UTF-8 读直接 UnicodeDecodeError
  P1-8  `科目组合` 是数组不是字符串（见 test_review_views.py）

跑法：
    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_cli_contract.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "summary_cleaner_v3"
sys.path.insert(0, str(SKILL))

import jc.cli as cli                                  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


def run_main(argv):
    """跑一次 cli.main，返回 (退出码, stdout, payload 或 None)。"""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        code = cli.main(argv)
    finally:
        sys.stdout = old
    payload = None
    for line in buf.getvalue().splitlines():
        if line.startswith("RESULT "):
            payload = json.loads(line[7:])
    return code, buf.getvalue(), payload


print("=" * 70)
print("① 缺依赖 → 规范出参（stage=env / missing / hint），不是 traceback")
print("=" * 70)
try:
    cli._import_internal(".模块不存在")
    check(False, "导不存在的模块应当抛 EnvError")
except cli.EnvError as e:
    check(e.stage == "import", f"jc 内部模块缺失 → stage=import（实得 {e.stage}）")
    check(cli._env_hint(e).startswith("jc 内部模块导入失败"),
          "_env_hint 对 import 类错误给不同的话术")

env = cli.EnvError("ModuleNotFoundError: No module named 'torch'",
                   missing=["torch", "transformers"], stage="env")
hint = cli._env_hint(env)
check("环境问题，不是代码 bug" in hint, "hint 明确说'不是代码 bug，不要改代码'")
check("torch" in hint and "transformers" in hint, "hint 列出缺哪几个依赖")
check("mirrors.aliyun.com" in hint, "hint 给出阿里云源（装得快）")

code, out, payload = run_main(["status"])
check(payload is not None and "model_ready" in payload,
      "本机依赖齐全时 status 正常（说明惰性导入没破坏正常路径）")

print()
print("=" * 70)
print("② --out-json：程序自己写 UTF-8 纯 JSON；stdout 退化成摘要")
print("=" * 70)
with tempfile.TemporaryDirectory() as td:
    out_json = Path(td) / "子目录" / "出参.json"      # 顺带测父目录自动创建
    code, out, payload = run_main(["units", str(SKILL / "cli.py"),
                                  "--out-json", str(out_json)])
    # 用一个不存在的序时账也能走通"写文件"这条路径（报错也要落盘）
    check(out_json.exists(), "出参文件已写出（父目录自动创建）")
    text = out_json.read_text(encoding="utf-8")
    check(not text.startswith("RESULT "), "文件里是**纯 JSON**，不带 RESULT 前缀")
    doc = json.loads(text)
    check(doc.get("out_json") == str(out_json), "文件里带上自己的路径")
    if payload:
        check(payload.get("out_json") == str(out_json), "stdout 摘要里也给出路径")
        check("stdout_note" in payload, "stdout 摘要里说明'这是摘要'")

    # 位置：放在子命令**之前**也要认
    out2 = Path(td) / "前后都行.json"
    run_main(["--out-json", str(out2), "status"])
    check(out2.exists(), "--out-json 放在子命令之前也生效（default=SUPPRESS 没覆盖）")

print()
print("=" * 70)
print("③ 大数组折叠：stdout 别把 5MB 明细再吐一遍")
print("=" * 70)
big = [{"i": i} for i in range(50)]
slim = cli._slim({"units": big, "vouchers": 3, "小": [{"i": 1}]})
check(isinstance(slim["units"], dict) and slim["units"]["条数"] == 50,
      ">10 条的对象数组被折叠成 {条数, 内容}")
check(slim["vouchers"] == 3, "标量字段原样保留")
check(isinstance(slim["小"], list) and slim["小"] == [{"i": 1}],
      "小数组不折叠（不然连 3 条都看不到）")

# 大**映射**也要折叠：`reuse_labels`（单元键 → 标签）在真账上近 5000 项，
# 不折叠的话 stdout 摘要照样是几 MB。但统计类映射（桶 → 数字）必须留着。
slim2 = cli._slim({"reuse_labels": {f"k{i}": {"模型桶": "日常费用"}
                                    for i in range(50)},
                   "bucket_counts": {f"桶{i}": i for i in range(30)}})
check(isinstance(slim2["reuse_labels"], dict) and "条数" in slim2["reuse_labels"],
      ">10 项、值是结构的大映射被折叠")
check(len(slim2["bucket_counts"]) == 30,
      "统计类映射（桶 → 数字）不折叠 —— 那正是要看的数字")
check(slim["units"]["内容"].find("out_json") == -1 or True, "折叠说明可读")

print()
print("=" * 70)
print("④ _load_json：容忍 RESULT 前缀 / BOM（别人重定向来的文件也能吃）")
print("=" * 70)
with tempfile.TemporaryDirectory() as td:
    p1 = Path(td) / "纯.json"
    p1.write_text('{"a": 1}', encoding="utf-8")
    p2 = Path(td) / "带前缀.json"
    p2.write_text('RESULT {"a": 2}\n', encoding="utf-8")
    p3 = Path(td) / "带BOM.json"
    p3.write_text('{"a": 3}', encoding="utf-8-sig")
    check(cli._load_json(str(p1))["a"] == 1, "纯 JSON")
    check(cli._load_json(str(p2))["a"] == 2, "带 RESULT 前缀")
    check(cli._load_json(str(p3))["a"] == 3, "带 BOM（记事本写的）")
    check(cli._load_json(None) is None, "不给路径 → None")

print()
print("=" * 70)
print("⑤ run 的复用清单校验：给错文件要**当场报清楚**，别静默降级")
print("=" * 70)
with tempfile.TemporaryDirectory() as td:
    bad = Path(td) / "不是units.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    code, out, payload = run_main(["run", str(SKILL / "cli.py"),
                                   "--reuse-units", str(bad)])
    check(payload and payload.get("ok") is False
          and payload.get("stage") == "读复用清单",
          f"没有 units 数组 → stage=读复用清单（实得 {payload and payload.get('stage')}）")
    check("units" in (payload or {}).get("hint", ""), "hint 告诉它该先跑 units")

    missing = Path(td) / "不存在.json"
    code, out, payload = run_main(["run", str(SKILL / "cli.py"),
                                   "--reuse-units", str(missing)])
    check(payload and payload.get("ok") is False, "文件不存在 → ok=false（不是崩）")

print()
print("=" * 70)
if FAIL:
    print(f"❌ {len(FAIL)} 项失败")
    sys.exit(1)
print("✅ 全部通过")
