# -*- coding: utf-8 -*-
"""把 hajishou 的 4 件交付物发布到魔搭社区（ModelScope）。

只上传白名单内的文件——训练数据、embed_cache.db、backups 一律不带。

用法::

    # 先看要传什么、仓库状态，不实际写入
    python _dev/scripts/upload_modelscope.py --dir <发布目录> --dry-run

    # 真正上传（仓库不存在会自动创建为公开仓库）
    python _dev/scripts/upload_modelscope.py --dir <发布目录>

    # 自定义仓库 id / 提交信息
    python _dev/scripts/upload_modelscope.py --dir <dir> \\
        --repo daijiaoshou/hajishou-V2.0 --message "V2.0: 24 桶 / 187 科目"

token 来源（按优先级）:
    1. 环境变量 MODELSCOPE_API_TOKEN
    2. ``~/.modelscope/credentials/session``（``modelscope login`` 写入）

获取 token: https://www.modelscope.cn/my/myaccesstoken
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_REPO = "daijiaoshou/hajishou-V2.0"

# 发布白名单（相对路径）。缺一件就会让下游加载失败，所以这里硬校验。
REQUIRED = (
    "fine_tuned/model.safetensors",
    "fine_tuned/config.json",
    "fine_tuned/tokenizer.json",
    "fine_tuned/tokenizer_config.json",
    "finance_classifier.pt",
    "subject_to_index.json",
    "index_to_bucket.json",
)
OPTIONAL = ("README.md",)


def _read_token() -> str | None:
    tok = os.getenv("MODELSCOPE_API_TOKEN", "").strip()
    if tok:
        return tok
    p = Path.home() / ".modelscope" / "credentials" / "session"
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    return None


def _check_dir(root: Path) -> list[str]:
    """校验发布目录：返回错误列表（空 = 通过）。"""
    errs: list[str] = []
    missing = [f for f in REQUIRED if not (root / f).exists()]
    if missing:
        errs.append("缺少必需文件: " + ", ".join(missing))

    allowed = set(REQUIRED) | set(OPTIONAL)
    extra = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for p in root.rglob("*")
        if p.is_file() and str(p.relative_to(root)).replace("\\", "/") not in allowed
    )
    if extra:
        errs.append("发现白名单外文件（会被一并上传，请先移走）: " + ", ".join(extra))
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="发布目录（只含 4 件交付物 + README）")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"仓库 id，默认 {DEFAULT_REPO}")
    ap.add_argument("--message", default="Upload V2.0: 24 buckets / 187 subject switches",
                    help="commit message")
    ap.add_argument("--dry-run", action="store_true", help="只校验与打印，不写入")
    args = ap.parse_args()

    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        print(f"[FAIL] 目录不存在: {root}")
        return 1

    print(f"发布目录: {root}")
    total = 0
    for f in REQUIRED + OPTIONAL:
        p = root / f
        if p.exists():
            size = p.stat().st_size
            total += size
            print(f"  {f:<40} {size:>13,} bytes")
    print(f"  合计 {total / 1024 / 1024:.1f} MB")

    errs = _check_dir(root)
    if errs:
        print("\n[FAIL] 目录校验未通过:")
        for e in errs:
            print("  -", e)
        return 2
    print("[OK] 文件白名单校验通过")

    token = _read_token()
    if not token:
        print("\n[FAIL] 未找到 token。先执行: modelscope login --token <你的token>")
        print("       或设置环境变量 MODELSCOPE_API_TOKEN")
        return 3
    print(f"[OK] 已读取 token（长度 {len(token)}，{token[:2]}***{token[-2:]}）")

    if args.dry_run:
        print(f"\n[DRY-RUN] 将上传到 {args.repo}，未执行任何写入。")
        return 0

    from modelscope.hub.api import HubApi

    api = HubApi()
    try:
        api.login(token)
        print("[OK] 登录成功")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 登录失败: {exc}")
        print("       token 可能已失效，请到 https://www.modelscope.cn/my/myaccesstoken 重新生成")
        return 4

    # 仓库不存在则创建（公开）。不做 get_model 预检——不同 SDK 版本方法名有出入，
    # 直接创建并识别「已存在」的报错更稳。
    try:
        api.create_model(
            model_id=args.repo,
            visibility=5,                     # 5 = 公开
            license="mit",
            chinese_name=args.repo.split("/")[-1],
            description="序时账业务分类模型（BGE 微调，24 桶 / 187 科目开关）",
        )
        print(f"[OK] 已创建公开仓库: {args.repo}")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        low = msg.lower()
        if "exist" in low or "already" in low or "已存在" in msg or "重复" in msg:
            print(f"[OK] 仓库已存在，追加提交: {args.repo}")
        else:
            print(f"[WARN] 创建仓库返回: {msg[:200]}")
            print("       若仓库确实不存在，请先到网页手动创建后再重跑")

    print(f"[INF] 上传中（约 {total / 1024 / 1024:.0f} MB，视网速 3–15 分钟）…")
    api.upload_folder(
        model_id=args.repo,
        folder_path=str(root),
        commit_message=args.message,
    )
    print(f"[OK] 上传完成: https://www.modelscope.cn/models/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
