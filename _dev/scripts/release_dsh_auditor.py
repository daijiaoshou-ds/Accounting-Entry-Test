# -*- coding: utf-8 -*-
"""把打包产物发一版出去：npm 包 + 公开仓库 `dsh-auditor` + 离线 zip。

三条通道给的是同一份装配产物（插件 + 随包引擎），源码仍在本仓库：
- **npm**（首选，DSH 里填 `dsh-auditor` 就能装）；
- **git**（`github:daijiaoshou-ds/dsh-auditor`，npm 装不上时退这条）；
- **zip**（网络都不通时拷过去，解压后按目录装）。

为什么要 git 仓库那一份：DSH 的「添加插件」接受 pnpm 的 git spec，而这个包必须在
**仓库根目录**才装得上，所以单独一个仓库放装配产物。

用法：
    python _dev/scripts/release_dsh_auditor.py                 # 只装配 + 同步 + 打 zip
    python _dev/scripts/release_dsh_auditor.py --push          # 再提交并推到 GitHub
    python _dev/scripts/release_dsh_auditor.py --push --npm    # 同时发 npm（要 token）
"""
from __future__ import annotations

import argparse
import os
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
# 发布必须走官方源：本机 npm/pnpm 配的是国内镜像（registry.npmmirror.com），只能读
NPM_REGISTRY = "https://registry.npmjs.org/"


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


def publish_npm() -> None:
    """发到 npm。

    两个坑写在前面：
    - **必须官方源**：本机 npm/pnpm 配的是 `registry.npmmirror.com`（只读镜像），
      往那儿发会 404/403，所以 `--registry` 写死；
    - **版本号先改**：npm 不许覆盖已发布的版本，重发会被 409 挡回来，
      版本号在 `build_dsh_auditor.py` 顶部的 `VERSION`。

    发布是异步的：命令返回成功后再等几分钟 registry 上才查得到；这期间只会看到一个
    `0.0.0-stage` 占位版本，那不是失败，别重发。
    """
    cmd = ["npm", "publish", "--registry", NPM_REGISTRY]
    if os.name == "nt":
        cmd = ["cmd", "/c"] + cmd
    print(f"$ {' '.join(cmd)}   (cwd={OUT})")
    result = subprocess.run(cmd, cwd=str(OUT), text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit("npm publish 失败：看上面的报错（token / 2FA / 版本号已存在）")
    print("✓ 已发到 npm（异步处理，几分钟后在 registry 上可见）")


def version() -> str:
    for line in (OUT / "package.json").read_text(encoding="utf-8").splitlines():
        if '"version"' in line:
            return line.split('"')[3]
    raise SystemExit("读不出版本号")


def ahead() -> int:
    """本地比 origin 多几个提交（拉不到 origin 就按"有东西要推"处理）。"""
    try:
        return int(run(["git", "rev-list", "--count", f"origin/{BRANCH}..HEAD"], cwd=RELEASE) or 0)
    except (SystemExit, ValueError):
        return 1


def commit_and_push(tag_version: str) -> None:
    run(["git", "add", "-A"], cwd=RELEASE)
    status = run(["git", "status", "--porcelain"], cwd=RELEASE)
    if not status:
        print("· 内容没有变化，不必提交")
    else:
        message = (
            f"发布 {tag_version}：插件 + 随包 Python 引擎\n\n"
            "这个仓库只有装配产物，没有源码；DSH 的添加插件里输入 "
            "dsh-auditor（npm）或 github:daijiaoshou-ds/dsh-auditor 都能装。\n"
        )
        msg_file = RELEASE / ".git" / "COMMIT_MSG.txt"
        msg_file.write_text(message, encoding="utf-8")
        run(["git", "commit", "-F", str(msg_file)], cwd=RELEASE)
    # 上一次推失败时提交会留在本地：这次内容没变也得把它推上去，
    # 否则"没变化 → 跳过"会把一版永远卡在本地（网络抖一下就中）。
    if ahead() == 0:
        print("· 已经是最新，不必推")
        return
    run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=RELEASE)
    print(f"✓ 已推送 {tag_version}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="提交并推到 GitHub")
    ap.add_argument("--skip-build", action="store_true", help="不重新装配，直接用现有 dist/dsh-auditor")
    ap.add_argument("--no-zip", action="store_true", help="不打离线 zip")
    ap.add_argument("--npm", action="store_true", help="发到 npm（要官方源与 npm token）")
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
    if args.npm:
        publish_npm()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
