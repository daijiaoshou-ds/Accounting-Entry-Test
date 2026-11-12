# -*- coding: utf-8 -*-
"""`journal` 入口（薄壳）—— 让 `python journal.py …` 与 `python -m jobflow.cli …` 等价。

真正的实现在 `jobflow/cli.py`。这里只把仓库根放进 sys.path。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobflow.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
