# -*- coding: utf-8 -*-
"""现金流量表编写 —— 映射表位置。

只回答一个问题：现金流映射表三件套（项目 / 桶→项目 / 歧义裁判）在哪。

这是一个曾经独立分发的包留下的最小化产物：那时代码要能找到同仓库的判定内核，
于是带着"把仓库根 append 进 sys.path"的引导。收进 `features/cashflow/` 之后不需要了
——仓库根本来就在 `sys.path` 上，跨目录 import 与同目录一样是普通导入。
"""

from __future__ import annotations

from pathlib import Path

# ── 版本 ──
__version__ = "1.0.0"
RELEASE_NAME = "现金流量表编写"

# ── 映射表 ──
PKG_DIR = Path(__file__).resolve().parent        # features/cashflow
CF_DIR = PKG_DIR / "assets" / "cf"

# 现金流映射表三件套
CF_ARTIFACTS = {
    "items": CF_DIR / "cf_items.json",           # 现金流量项目 + 方向 + 报表分组
    "bucket_map": CF_DIR / "bucket_to_cf.json",  # 业务桶 → 候选项目
    "ruling": CF_DIR / "account_ruling.json",    # 歧义裁判表 + 兜底表
}
