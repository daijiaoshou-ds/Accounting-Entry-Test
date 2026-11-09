# -*- coding: utf-8 -*-
"""API 启动入口：python -m api.run_api [--host 127.0.0.1] [--port 8502]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8502)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()

    import uvicorn

    uvicorn.run("api.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="info")


if __name__ == "__main__":
    main()
