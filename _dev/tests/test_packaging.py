# -*- coding: utf-8 -*-
"""发布包该含什么、不该含什么 —— 不构建发布目录，直接问打包脚本要清单。

为什么要有这个测试：`reference/` 里曾经混进两份**开发报告**（在讲已删除的 v2/nn
架构），文档里还指向过包外不存在的文件。这类问题不会让任何功能测试失败，
但会跟着包发到用户手里。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "summary_cleaner_v3"
sys.path.insert(0, str(SKILL / "scripts"))

import package_release as pkg          # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


print("=" * 70)
print("发布清单（由 scripts/package_release.py 的 collect_files 给出）")
print("=" * 70)

files = pkg.collect_files()
rels = sorted(rel for _src, rel in files)
top = sorted({r.split("/")[0] for r in rels})
print(f"  文件数 {len(rels)}，顶层 {top}\n")

check("_dev" not in top, "不含 _dev/（训练数据、样例、验证脚本）")
check("review" not in top, "不含 review/（开发侧缺陷记录）")
check(not any(r.startswith("jc/training") for r in rels), "不含 jc/training/")
check(not any(r.startswith("_storage") for r in rels), "不含 _storage/")
check(not any(r.startswith("docs") for r in rels), "不含 docs/（设计稿）")

# 映射表只有一份，必须在包根的 assets/cf/ 下。
# scripts/assets/ 曾经留过一份**旧的**同名 JSON（ROOT 改过，写盘的目录跟着变），
# 它被 .gitignore 挡住看不见，但 package_release 是按目录 rglob 收文件的，
# 于是会跟着包发出去 —— 用户/AI 看到两份互相矛盾的 bucket_to_cf.json。
stray = [r for r in rels if r.startswith("scripts/assets/")]
check(not stray, f"scripts/ 下没有第二份映射表（实得 {stray}）")
check(any(r.startswith("assets/cf/") for r in rels), "含包根 assets/cf/（唯一的映射表）")

# assets/model/ 的落点必须**看得见**：空目录进不了 git，用户手动下模型时
# 会找不到该放哪儿（MANIFEST/SETUP 都写着"放进 assets/model/"）。
check("assets/model/.gitkeep" in rels,
      "含 assets/model/.gitkeep（模型落点占位）")
check(pkg._should_skip_file(SKILL / "assets" / "model" / ".gitkeep") is False,
      ".gitkeep 不会被当成缓存点文件跳过")
check(pkg._should_skip_file(SKILL / "jc" / ".hidden_cache") is True,
      "其它点文件仍然跳过（别把缓存打进包）")

refs = sorted(r.split("/", 1)[1] for r in rels if r.startswith("reference/"))
print(f"  reference/ = {refs}")
check(refs == ["AI_REVIEW_GUIDE.md", "CASHFLOW_GUIDE.md", "REVIEW_GUIDE.md", "SETUP.md"],
      "reference/ 只剩 4 个该给用户的文件")

# 只查**指向**包外/不存在文件的引用；"说明某个遗留概念已不存在"的散文是允许的
banned = {
    "docs/design": "指向包外的设计稿",
    "docs\\design": "指向包外的设计稿",
    "speedup_design": "已移出包的开发报告",
    "technical_report2": "已移出包的开发报告",
    "technical_report3": "已移出包的开发报告",
    "FIELD_ENCODING": "从未存在过的文件",
    "--local_dir _storage": "模型下载目录写错（应为 assets/model）",
    "运行期只在 `_storage/`": "假陈述（3.0 运行期不写缓存目录）",
    "lowest_confidence": "已退休的凭证级复核接口",
    "cli.py sample": "已退休的 sample 命令",
    # ↓ 2026-10 真账实测踩出来的：这两条会让用户/执行者直接卡住
    #   （只禁"照抄那行命令"，不禁"说明它已经失效"的警示句）
    "-m modelscope download daijiaoshou": "已失效的模型下载命令（modelscope 改了 CLI 入口）",
    "set PY=..\\venv": "venv 路径假设（skill 装在 .dsh/skills/ 下不成立）",
    "出参被**管道/重定向**时程序已自己用 UTF-8，不用你管": "假陈述（PowerShell 1> 给的是 UTF-16LE）",
}
bad = []
for src, rel in files:
    if src.suffix not in (".md", ".py", ".json", ".txt"):
        continue
    t = src.read_text(encoding="utf-8", errors="replace")
    for needle, why in banned.items():
        if needle in t:
            bad.append(f"{rel} 出现 {needle!r}（{why}）")
if bad:
    for b in bad:
        print("     -", b)
check(not bad, "包内无悬空引用 / 无过时断言（含已退休接口）")

print()
print("=" * 70)
print("MANIFEST 口径：file_count 要含 MANIFEST.json 自身")
print("=" * 70)
# 以前写 len(files)，于是清单说 23 个、装完实际 24 个 —— 用户/AI 会以为少了一个。
mf = pkg.write_manifest.__doc__ or ""
import json as _json                                    # noqa: E402
import tempfile as _tf                                  # noqa: E402
from pathlib import Path as _P                          # noqa: E402
with _tf.TemporaryDirectory() as _td:
    files = pkg.collect_files()
    out = _P(_td)
    for src, rel in files:                     # 真拷一遍，才能跟磁盘对数
        pkg._copy(src, out / rel)
    doc = pkg.write_manifest(out, files, {m: False for m in pkg.MODEL_ARTIFACTS})
    check(doc["file_count"] == doc["file_count_copied"] + 1,
          f"file_count({doc['file_count']}) = 复制数({doc['file_count_copied']}) + 1")
    disk = [p for p in out.rglob("*") if p.is_file()]
    check(len(disk) == doc["file_count"],
          f"清单数字与磁盘实际文件数一致（清单 {doc['file_count']} / 磁盘 {len(disk)}）")
    check(doc["model_dir_hint"].startswith("模型放 assets/model/"),
          "MANIFEST 写明模型落点")
    check(any("excluded_intentionally" == k for k in doc), "MANIFEST 列出剔除了什么")

print()
print("=" * 70)
if FAIL:
    print(f"❌ {len(FAIL)} 项失败")
    sys.exit(1)
print("✅ 全部通过")
