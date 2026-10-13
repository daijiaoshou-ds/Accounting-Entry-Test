# -*- coding: utf-8 -*-
"""序时账清洗 3.0 — skill 入口（薄壳）

真正的实现在 `jc/` 包内。本文件只做两件事：
  1. 把 skill 根目录放进 sys.path（让 `jc` 可被导入，无需 pip install）
  2. 转发到 `jc.cli.main()`

所以两种调用方式等价：
    python cli.py run "序时账.xlsx"
    python -m jc.cli run "序时账.xlsx"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jc.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
