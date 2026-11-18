# -*- coding: utf-8 -*-
"""路径与全局常量。

`sys.path` 的引导放在这里（模块导入阶段执行），因为阶段代码在顶层
`import core` / `import journal_io`——等到 run 时才补路径已经晚了
（踩过一次：模块顶层 import accounting_domain 直接炸）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 仓库根：journal_io / accounting_domain / core / features
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

JOBS_DIR = Path(os.getenv("JOURNAL_JOBS_DIR", str(REPO_ROOT / "_storage_jobs")))

# schema 版本：decision 文件与 state.json 都带，升级时好判断兼容
SCHEMA_VERSION = 1
