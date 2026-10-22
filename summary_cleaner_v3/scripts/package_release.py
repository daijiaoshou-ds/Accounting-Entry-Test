# -*- coding: utf-8 -*-
"""打包用户版 skill —— 从开发版生成一个干净的、可分发的用户版。

## 为什么要这个脚本

开发版（`summary_cleaner_v3/`）里混着**只有开发才需要**的东西：

| 只在开发版需要 | 说明 |
|---|---|
| `nn/models/` | BGE 基座缓存 1.6GB，**只有重新训练**才用。已删 |
| `nn/_storage/training_data.json` | 金标准训练数据（17,421 条），训练才用 |
| `nn/_storage/training/`、`backups/` | 训练数据管线产物与备份 |
| `v2/_storage/corrections.json` | ⚠️ **含真实客户的凭证号与摘要**，绝不能分发 |
| `v2/_storage/auto_words/*.json` | 按份序时账切分的自动词，含客户用词 |

而用户只需要「拿自己的序时账跑分类」这一条路径。

## 用法

```cmd
:: 生成到 <仓库根>/summary-cleaner/（默认；**这个目录要入 git**，用户从仓库拉取）
venv\\Scripts\\python.exe -X utf8 scripts\\package_release.py

:: 指定输出目录
venv\\Scripts\\python.exe -X utf8 scripts\\package_release.py --out "D:\\发布\\summary-cleaner"

:: 顺便做一次自检（status + 导入自检 + 接口自检）
venv\\Scripts\\python.exe -X utf8 scripts\\package_release.py --verify
```

> **自检为什么不止跑 `status`**：`status` 只 import `.model` 与 `.cashflow`，
> **碰不到 pipeline / review_views** —— "漏发一个模块"或"拿旧代码打的包"
> 都能骗过它，而用户一跑 `units` 就报错。所以自检还会
> ① import 全部 jc 模块；② 检查 `units`/`run` 的新开关在不在。

> **目录名 = `SKILL.md` 里的 `name`**（`summary-cleaner`）。多数客户端要求
> skill 目录名与 frontmatter 的 name 一致；用户 clone 下来，直接把这个目录
> 拷进自己的 skills 目录即可用。
>
> ⚠️ **自检会用发布版代码跑一次 `status`，那会在发布目录里生成 `__pycache__`。**
> 脚本收尾会自动清掉；但你**别再拿发布目录跑别的命令**（会再生成、然后被提交进去）。
> 要验就验副本，或者重跑一次本脚本。

## 安全设计

脚本带**黑名单断言**：一旦发现客户数据类文件被列入清单，**直接报错退出**，
不会"默默地"打进去。要改清单必须显式修改本文件的 `EXCLUDE_ALWAYS`。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── 目录定位 ──
SCRIPTS_DIR = Path(__file__).resolve().parent          # .../summary_cleaner_v3/scripts
PKG_DIR = SCRIPTS_DIR.parent                            # .../summary_cleaner_v3
REPO_ROOT = PKG_DIR.parent                              # 项目根

DEFAULT_OUT = REPO_ROOT / "summary-cleaner"     # 目录名 = SKILL.md 的 name

# ============================================================================
# 清单
# ============================================================================

# ============================================================================
# 清单
# ============================================================================
#
# 3.0 采用**单一代码库**：开发版和用户版是同一份代码，区别只在发布时剔除什么。
# 下面 EXCLUDE_ALWAYS 就是"开发版有、用户版不给"的东西。

# 代码与文档：整目录复制，但排除下面的模式
INCLUDE_DIRS = [
    "jc",            # 代码（含 jc/training/，由 EXCLUDE_ALWAYS 剔除）
    "assets",        # 业务知识 + AI 积累库 + 模型交付物
    "reference",     # 深度参考资料
    "scripts",       # 运维脚本
]
INCLUDE_FILES = [
    "SKILL.md",      # AI 操作手册（核心）
    "README.md",
    "cli.py",        # 唯一对外接口
    "requirements.txt",
]

# ── 开发版有、用户版不给的东西 ──
EXCLUDE_ALWAYS = [
    # ① 训练代码（用户只做清洗，不训练）
    "jc/training",
    # ② 训练数据与训练产物
    "_dev",                              # 金标准 / 未审 / 备份 / BGE 基座缓存
    "assets/model/training_data.json",   # 训练金标准（万一被拷到 assets 下）
    # ③ 运行期缓存
    "_storage",
    # ④ 开发侧的评审记录（含客户专名与真账细节，**绝不能进公开仓库**）
    "review",
    # ⑤ 打包脚本自己：发布的是 skill，不是"怎么打包 skill"
    "scripts/package_release.py",
]

# 打包时按后缀跳过
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".tmp"}
SKIP_DIRNAME = {"__pycache__", ".pytest_cache", ".git", "_storage_test"}

# 模型交付物（4 件）—— 用户版按设计**不含模型**，需按 SKILL.md 下载
MODEL_ARTIFACTS = [
    "assets/model/fine_tuned/config.json",
    "assets/model/fine_tuned/model.safetensors",
    "assets/model/fine_tuned/tokenizer.json",
    "assets/model/fine_tuned/tokenizer_config.json",
    "assets/model/finance_classifier.pt",
    "assets/model/subject_to_index.json",
    "assets/model/index_to_bucket.json",
]

# 安全性自检：这些文件名一旦出现在待复制清单里，立即中止
FORBIDDEN_NAME_HINTS = [
    "corrections.json",      # 旧版纠错记录（含客户凭证号 + 摘要）
    "training_data.json",    # 训练金标准
    "auto_words_tier1.json", # 旧版自动词（含客户用词）
    "word_data.json",
]


def _rel(p: Path) -> str:
    return p.relative_to(PKG_DIR).as_posix()


def _is_excluded(rel: str) -> bool:
    return any(rel == e or rel.startswith(e + "/") for e in EXCLUDE_ALWAYS)


def _should_skip_file(p: Path) -> bool:
    if p.name in SKIP_DIRNAME or p.suffix in SKIP_SUFFIX:
        return True
    if any(part in SKIP_DIRNAME for part in p.parts):
        return True
    # 点文件默认跳过（缓存/配置），但这几个是**包里要用的**：
    #   .gitattributes / .gitignore —— 仓库卫生
    #   .gitkeep —— assets/model/ 的占位（否则空目录进不了 git，
    #               手动下模型的人找不到该放哪儿）
    if p.name.startswith(".") and p.name not in (".gitattributes", ".gitignore",
                                                 ".gitkeep"):
        return True
    # _storage_test 整个目录
    if any(part.endswith("_storage_test") for part in p.parts):
        return True
    return False


def collect_files():
    """收集待复制文件 → [(源路径, 相对包根的路径)]。"""
    out = []
    for d in INCLUDE_DIRS:
        base = PKG_DIR / d
        if not base.exists():
            print(f"  [跳过] 目录不存在: {d}")
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = _rel(p)
            if _is_excluded(rel) or _should_skip_file(p):
                continue
            out.append((p, rel))
    for f in INCLUDE_FILES:
        p = PKG_DIR / f
        if not p.exists():
            print(f"  [跳过] 文件不存在: {f}")
            continue
        if _is_excluded(f):
            continue
        out.append((p, f))
    return out


def safety_check(files):
    """黑名单断言：客户数据文件绝不能出现在清单里。"""
    bad = []
    for src, rel in files:
        for hint in FORBIDDEN_NAME_HINTS:
            if src.name == hint:
                bad.append(rel)
    if bad:
        print("\n❌ 安全检查失败：以下含客户数据/训练数据的文件出现在待复制清单里：")
        for b in bad:
            print(f"   - {b}")
        print("   请把它们加入 EXCLUDE_ALWAYS 后重跑。已中止，未写入任何文件。")
        return False
    return True


def _copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def write_manifest(out_dir: Path, files, model_ok: dict):
    """写一份 MANIFEST.json，说明这份发布包含什么、缺什么。"""
    total = sum(src.stat().st_size for src, _ in files)
    manifest = {
        "package": "序时账清洗 3.0（用户版）",
        # 不要写绝对路径：这份清单会进公开仓库，没必要暴露打包机器的目录结构
        "generated_from": PKG_DIR.name + "/（开发版）",
        # ⚠️ file_count **含 MANIFEST.json 自身**（以前写 len(files)，于是清单说
        # 23 个、装完实际 24 个 —— 口径不一致，用户/AI 会以为少了一个文件）。
        "file_count": len(files) + 1,
        "file_count_copied": len(files),
        "file_count_note": "file_count 含 MANIFEST.json 自身；file_count_copied 是复制的文件数",
        "total_bytes": total,
        "total_mb": round(total / 1024 / 1024, 1),
        "included_top_level": sorted({rel.split("/")[0] for _, rel in files}),
        "model_artifacts": model_ok,
        "model_ready": all(model_ok.values()),
        "model_dir_hint": "模型放 assets/model/（包里已有 .gitkeep 占位，就是落点）",
        "excluded_intentionally": EXCLUDE_ALWAYS,
        # 这句话以前写死成"本包含 660MB 模型"，默认打包（不带模型）时是错的。
        "note": (("本包已含 4 件模型交付物（约 622MB）。通过 git/网盘分发时注意体积；"
                  "想不带模型，用默认方式重新打包即可。")
                 if all(model_ok.values()) else
                 ("本包**不含**模型交付物——用户需按 SKILL.md 第 1 步自行下载，"
                  "放进 assets/model/（不是 _storage/）后，cli.py status 才会 "
                  "model_ready: true。")),
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _run_in_release(out_dir: Path, py: Path, args: list) -> tuple:
    """在**发布版代码**上下文里跑一条命令（cwd + PYTHONPATH 都指向发布目录）。

    必须显式设置：本脚本自己的 sys.path 里有开发根目录，直接 subprocess 继承环境
    会让 `import jc` 命中开发版——那样验证是假的。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(out_dir)
    env.pop("PYTHONHOME", None)
    r = subprocess.run([str(py), "-X", "utf8", *[str(a) for a in args]],
                       cwd=str(out_dir), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return r


def verify(out_dir: Path, expect_model: bool = True):
    """在发布目录里跑三件事：status 自检 + **导入**自检 + **接口**自检。

    为什么不止 status：`status` 只 import `.model` 与 `.cashflow`，
    **碰不到 pipeline / review_views**。所以"漏发一个模块"或"用旧代码打的包"
    都能骗过只跑 status 的自检 —— 而用户一跑 `units` 就 ImportError。
    接口自检（`--help` 里有没有那几个新开关）防的是第二种：
    发布目录是从开发目录拷的，但拷的是哪一版没人知道。

    expect_model=False（默认的"不带模型"打包）：模型缺失是**预期行为**，
    自检通过判据改为"能启动、桶数对、且代码没串到开发版"。
    """
    out_dir = out_dir.resolve()
    py = REPO_ROOT / "venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)

    cmd = ["-X", "utf8", str(out_dir / "cli.py"), "status"]
    print(f"\n[自检] 用发布版代码运行 status（cwd={out_dir}）")
    r = _run_in_release(out_dir, py, cmd)
    data = None
    for line in (r.stdout or "").splitlines():
        if line.startswith("RESULT "):
            data = json.loads(line[7:])
    if data is None:
        print("  ❌ 自检失败：没有解析到 RESULT 输出")
        print("  stderr:", (r.stderr or "")[-600:])
        return False

    model_ready = bool(data.get("model_ready"))
    buckets = data.get("bucket_count")
    model_dir = str(data.get("model_dir", ""))
    print(f"  model_ready={model_ready}  buckets={buckets}  "
          f"device={data.get('inference_device_will_be')}")
    print(f"  model_dir={model_dir}")

    # ① 代码没串到开发版
    if str(out_dir) not in model_dir:
        print("  ❌ 自检实际用的是开发版目录，验证无效！")
        return False
    # ② 桶数正确（说明配置完整）
    if buckets != 20:
        print(f"  ❌ 桶数为 {buckets}，应为 20")
        return False
    # ③ 训练内容确实被剔除了
    if (out_dir / "jc" / "training").exists():
        print("  ❌ jc/training 未被剔除（用户版不应含训练代码）")
        return False
    if (out_dir / "_dev").exists():
        print("  ❌ _dev 未被剔除（用户版不应含训练数据）")
        return False

    # ④ 导入自检：status 碰不到的模块必须也能 import
    mods = ["jc.cli", "jc.pipeline", "jc.review_views", "jc.units",
            "jc.cashflow", "jc.etl", "jc.hard_rules", "jc.config"]
    r2 = _run_in_release(out_dir, py, ["-c",
                                       "import " + ", ".join(mods) + "; print('IMPORT_OK')"])
    if "IMPORT_OK" not in (r2.stdout or ""):
        print("  ❌ 导入自检失败（status 能跑 ≠ pipeline 能跑）")
        print("     ", (r2.stderr or "").strip().splitlines()[-1:] or "")
        return False
    print(f"  导入自检通过：{len(mods)} 个模块都能 import")

    # ⑤ 接口自检：文档承诺的命令行开关必须真的存在
    wanted = {
        "units": ["--summary", "--families", "--out-json", "--min-family",
                  "--outlier-share", "--reuse-units", "--lookup", "--lookup-row"],
        "run": ["--reuse-units", "--unit", "--cashflow", "--out-json"],
    }
    for sub, flags in wanted.items():
        r3 = _run_in_release(out_dir, py, [str(out_dir / "cli.py"), sub, "--help"])
        text = (r3.stdout or "") + (r3.stderr or "")
        missing = [f for f in flags if f not in text]
        if missing:
            print(f"  ❌ `{sub}` 少了开关 {missing} —— 发布目录像是旧版本代码")
            return False
    print("  接口自检通过：units/run 的新开关都在（不是旧包）")

    if expect_model:
        if not model_ready:
            print("  ❌ 期望带模型，但 model_ready=False")
            return False
        print("  ✅ 通过（带模型版：代码、桶数、模型均正常）")
    else:
        if model_ready:
            print("  ⚠️ 预期不带模型，但检测到模型齐全（可能误打包了）")
        else:
            print("  ✅ 通过（用户版：代码与配置正常，模型待用户按 SKILL.md 下载）")
    return True


def clean_runtime_state(out_dir: Path) -> int:
    """删掉发布目录里"运行时会自动生成"的东西。

    3.0 运行期**不写任何缓存目录**——结果只落到 `--out` 指定的输出目录。
    这里删的是 1.0/2.0 遗留的 `_storage/`（老版本会在这里写纠错记录和自动词），
    以及手工误拷进来的残留；发布包里不该带任何运行痕迹。
    """
    removed = 0
    p = out_dir / "_storage"
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
        removed += 1
    return removed


def clean_bytecode(out_dir: Path) -> int:
    """删掉发布目录里的 __pycache__ / *.pyc。

    为什么需要：打包后如果就地跑过一次 Python（比如 --verify），
    解释器会自动生成 __pycache__。发布包里不该有这些文件。
    """
    removed = 0
    for p in list(out_dir.rglob("__pycache__")):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    for p in list(out_dir.rglob("*.pyc")):
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def main():
    ap = argparse.ArgumentParser(
        description="从开发版打包「用户版」——剔除训练代码、训练数据与模型权重")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("--force", action="store_true", help="输出目录已存在时清空重建")
    ap.add_argument("--verify", action="store_true",
                    help="打包后跑 status 自检（不带模型时预期 model_ready=False）")
    ap.add_argument("--with-model", action="store_true",
                    help="连模型交付物一起打包（约 622MB）。"
                         "默认**不带**，让用户按 SKILL.md 第 1 步自己下载")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if out_dir.exists():
        if not args.force:
            print(f"❌ 输出目录已存在: {out_dir}\n   用 --force 清空重建。")
            return 1
        shutil.rmtree(out_dir)

    print(f"[1/4] 收集文件（源: {PKG_DIR}）")
    files = collect_files()
    excluded_train = [r for _, r in files if r.startswith("jc/training")]
    if excluded_train:
        print(f"  ⚠️ 异常：训练代码未被排除（{len(excluded_train)} 个），请检查清单")
    if not args.with_model:
        before = len(files)
        files = [(s, r) for s, r in files
                 if not any(r == m for m in MODEL_ARTIFACTS)]
        print(f"  默认不带模型：排除 {before - len(files)} 个模型文件")
    print(f"  待复制 {len(files)} 个文件")

    print("[2/4] 安全检查（客户数据 + 训练数据黑名单）")
    if not safety_check(files):
        return 1
    for rel in ("jc/training", "_dev"):
        if any(r == rel or r.startswith(rel + "/") for _, r in files):
            print(f"  ❌ 安全检查失败：{rel} 不应出现在用户版里")
            return 1
    print("  ✅ 无客户数据 / 训练代码 / 训练数据")

    print(f"[3/4] 复制到 {out_dir}")
    for src, rel in files:
        _copy(src, out_dir / rel)

    model_ok = {m: (PKG_DIR / m).exists() for m in MODEL_ARTIFACTS}
    if not args.with_model:
        model_ok = {m: False for m in MODEL_ARTIFACTS}
    manifest = write_manifest(out_dir, files, model_ok)
    print(f"  完成：{manifest['file_count']} 个文件, {manifest['total_mb']} MB")
    print(f"  顶层内容: {manifest['included_top_level']}")
    if not manifest["model_ready"]:
        print("  ⚠️ 模型交付物不全 —— 用户需按 SKILL.md 第 1 步下载")

    print("[4/4] 结果")
    print(f"  发布目录: {out_dir}")
    if args.verify:
        verify(out_dir, expect_model=args.with_model)
    else:
        print("  提示：加 --verify 可在打包后自动跑一次 status 自检")
    n_bc = clean_bytecode(out_dir)
    n_rt = clean_runtime_state(out_dir)
    if n_bc:
        print(f"  已清理 {n_bc} 处 Python 字节码缓存（__pycache__/*.pyc）")
    if n_rt:
        print(f"  已清理 {n_rt} 处运行期状态文件（自动词/纠错/备份等客户数据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
