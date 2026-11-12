# -*- coding: utf-8 -*-
"""路径与全局常量。

`sys.path` 的引导放在这里（模块导入阶段执行），因为阶段代码在顶层
`import summary_cleaner` / `import cfw` / `import journal_io`——等到 run 时才补
路径已经晚了（踩过一次：`cfw/judge.py` 顶层 import accounting_domain 直接炸）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 仓库根：journal_io / accounting_domain / summary_cleaner / features
# cashflow_writer：`cfw`（现金流判定与导出的实现）
for _p in (REPO_ROOT, REPO_ROOT / "cashflow_writer"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

JOBS_DIR = Path(os.getenv("JOURNAL_JOBS_DIR", str(REPO_ROOT / "_storage_jobs")))

# schema 版本：decision 文件与 state.json 都带，升级时好判断兼容
SCHEMA_VERSION = 1
