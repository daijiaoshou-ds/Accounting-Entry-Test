# -*- coding: utf-8 -*-
"""把打包产物推成公开仓库 `dsh-auditor` 的一版（DSH 里可用 git spec 直接装）。

为什么单独一个仓库：DSH 的「添加插件」输入框接受 pnpm 的 git spec，
`github:daijiaoshou-ds/dsh-auditor` 就指向一个**包在根目录**的仓库。
所以这个仓库里只有装配好的产物（插件 + 随包引擎），没有源码——源码还在
`Accounting-Entry-Test`，改完重跑本脚本再推一版即可。

用法：
    python _dev/scripts/release_dsh_auditor.py            # 只装配 + 同步到 dist/release
    python _dev/scripts/release_dsh_auditor.py --push     # 再提交并推到 GitHub
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"
OUT = DIST / "dsh-auditor"
RELEASE = DIST / "release"
REMOTE = "https://github.com/daijiaoshou-ds/dsh-auditor.git"
BRANCH = "main"


def run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"命令失败：{' '.join(args)}")
    return result.stdout.strip()


def ensure_clone() -> None:
    if (RELEASE / ".git").is_dir():
        return
    if RELEASE.exists():
        shutil.rmtree(RELEASE)
    DIST.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", REMOTE, str(RELEASE)], cwd=DIST)


def sync() -> None:
    """让 dist/release 的内容**完全等于** dist/dsh-auditor（.git 除外）。"""
    for item in RELEASE.iterdir():
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    for item in OUT.rglob("*"):
        target = RELEASE / item.relative_to(OUT)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def make_zip(tag_version: str) -> Path:
    """打一份可直接拷走的 zip：对方网络连 GitHub 不稳时，用它本地安装。"""
    target = DIST / f"dsh-auditor-{tag_version}"
    archive = shutil.make_archive(str(target), "zip", root_dir=str(DIST),
                                  base_dir="dsh-auditor")
    return Path(archive)


def version() -> str:
    for line in (OUT / "package.json").read_text(encoding="utf-8").splitlines():
        if '"version"' in line:
            return line.split('"')[3]
    raise SystemExit("读不出版本号")


def commit_and_push(tag_version: str) -> None:
    run(["git", "add", "-A"], cwd=RELEASE)
    status = run(["git", "status", "--porcelain"], cwd=RELEASE)
    if not status:
        print("· 内容没有变化，不必提交")
    else:
        message = (
            f"发布 {tag_version}：插件 + 随包 Python 引擎\n\n"
            "这个仓库只有装配产物，没有源码；DSH 的添加插件里输入 "
            "github:daijiaoshou-ds/dsh-auditor 即可安装。\n"
        )
        msg_file = RELEASE / ".git" / "COMMIT_MSG.txt"
        msg_file.write_text(message, encoding="utf-8")
        run(["git", "commit", "-F", str(msg_file)], cwd=RELEASE)
        run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=RELEASE)
        print(f"✓ 已推送 {tag_version}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="提交并推到 GitHub")
    ap.add_argument("--skip-build", action="store_true", help="不重新装配，直接用现有 dist/dsh-auditor")
    ap.add_argument("--no-zip", action="store_true", help="不打离线 zip")
    args = ap.parse_args(argv)

    if not args.skip_build:
        run([sys.executable, "-X", "utf8", str(ROOT / "_dev" / "scripts" / "build_dsh_auditor.py")], cwd=ROOT)

    ensure_clone()
    sync()
    v = version()
    print(f"✓ 已同步到 {RELEASE}（版本 {v}）")
    if not args.no_zip:
        archive = make_zip(v)
        print(f"✓ 离线包：{archive}"
              f"（{archive.stat().st_size / 1024 / 1024:.1f}MB，解压后用目录路径安装）")
    if args.push:
        commit_and_push(v)
    else:
        print("  只同步没推；确认无误后加 --push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
