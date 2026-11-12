# -*- coding: utf-8 -*-
"""现金流量表编写 — 配置与路径。

**本包不含模型、不含清洗逻辑。** 序时账怎么读、列名怎么认、业务桶怎么判，
全部来自 `summary_cleaner`（同仓库的另一个包）。这里只有三样东西：

    ① 本包的目录与版本
    ② 现金流映射表的位置（assets/cf/ 三件套）
    ③ 依赖上游的引导：把仓库根放进 sys.path，让 `import summary_cleaner` 成立

依赖关系（单向，不许反向）：

    序时账 ──► summary_cleaner（读表 + 判定业务桶）──► 本包（桶 + 方向 → 现金流量项目）
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── 版本 ──
__version__ = "1.0.0"
RELEASE_NAME = "现金流量表编写"

# ── 目录 ──
PKG_DIR = Path(__file__).resolve().parent        # .../cfw
ROOT_DIR = PKG_DIR.parent                        # 包根（= skill 根）
ASSETS_DIR = ROOT_DIR / "assets"
CF_DIR = ASSETS_DIR / "cf"                       # 现金流映射表（3 件）
REFERENCE_DIR = ROOT_DIR / "reference"           # 口径文档
REPO_DIR = ROOT_DIR.parent                       # 仓库根（summary_cleaner 在这里）

# 现金流映射表三件套
CF_ARTIFACTS = {
    "items": CF_DIR / "cf_items.json",           # 现金流量项目 + 方向 + 报表分组
    "bucket_map": CF_DIR / "bucket_to_cf.json",  # 业务桶 → 候选项目
    "ruling": CF_DIR / "account_ruling.json",    # 歧义裁判表 + 兜底表
}


class UpstreamMissing(RuntimeError):
    """上游包 summary_cleaner 不可用 —— 这是部署问题，不是代码 bug。"""


def ensure_upstream() -> None:
    """让 `import summary_cleaner` 成立，否则抛 UpstreamMissing。

    两种部署形态都要能跑：
      ① 开发：两个包在同一个仓库根下（本包 = 仓库根/cashflow_writer）
      ② 安装：summary_cleaner 已可被 import（pip 装过 / 在 sys.path 上）
    """
    try:
        import summary_cleaner  # noqa: F401
        return
    except ImportError:
        pass

    if str(REPO_DIR) not in sys.path:
        sys.path.append(str(REPO_DIR))
    try:
        import summary_cleaner  # noqa: F401
    except ImportError as e:
        raise UpstreamMissing(
            "找不到上游包 summary_cleaner（序时账清洗）。本包不含清洗逻辑，"
            "它必须与 summary_cleaner 一起部署：把两个目录放在同一父目录下"
            f"（当前期望位置：{REPO_DIR / 'summary_cleaner'}），"
            "或先把那个包 pip install 上。") from e
