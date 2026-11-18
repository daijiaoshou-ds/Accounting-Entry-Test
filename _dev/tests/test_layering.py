# -*- coding: utf-8 -*-
"""分层规则：只靠约定守不住，就让测试盯着。

三条（见 `docs/reference/STRUCTURE.md` §二）：

1. 运行时（`core/` `features/` `jobflow/` `journal_io/` `accounting_domain/`）
   **不许 import `tools/`**——训练代码只在重训时跑，跑账的机器上不该依赖它。
   破过一次：`core/classify/pipeline.py` 运行时 `from tools.train.training_data import …`。
2. 功能之间**不许互相 import**（`features/a` 不许 `import features.b`）——
   共用段要么在 `core/`，要么说明该下沉了。
3. `core/` **不许 import `features/`**——内核不知道有哪些功能。

反过来是允许的：`tools/` 可以 import `core/`（训练脚本读内核的模块）。

    ..\\venv\\Scripts\\python.exe -X utf8 _dev\\tests\\test_layering.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ("core", "features", "jobflow", "journal_io", "accounting_domain")


def python_files(base: Path):
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def absolute_imports(path: Path):
    """(行号, 模块名) —— 相对导入跳过（那是同一个包内部的事）。

    读文件用 utf-8-sig：仓库里有带 BOM 的文件，`ast.parse` 不认 BOM，
    会把整个文件当成解析失败——那样这条检查就等于没查。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError as exc:
        raise RuntimeError(f"解析不了 {path}：{exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                yield node.lineno, node.module


def module_of(base: Path, path: Path) -> str:
    rel = path.relative_to(base).with_suffix("")
    return ".".join(rel.parts)


def main() -> int:
    bad = []

    def imports_or_flag(path: Path):
        """解析失败也算一条失败：悄悄跳过等于这条检查没跑。"""
        try:
            return list(absolute_imports(path))
        except RuntimeError as exc:
            bad.append(str(exc))
            return []

    # ① 运行时不 import tools
    for pkg in RUNTIME:
        for path in python_files(ROOT / pkg):
            for line, mod in imports_or_flag(path):
                if mod == "tools" or mod.startswith("tools."):
                    bad.append(f"{path.relative_to(ROOT)}:{line} import {mod}"
                               f"（运行时不许依赖 tools/）")

    # ② 功能之间不互相 import；③ core 不 import features
    for path in python_files(ROOT / "features"):
        owner = path.relative_to(ROOT / "features").parts[0]
        for line, mod in imports_or_flag(path):
            if not mod.startswith("features."):
                continue
            other = mod.split(".", 1)[1].split(".")[0]
            if other and other != owner:
                bad.append(f"{path.relative_to(ROOT)}:{line} import {mod}"
                           f"（features/{owner} 不许 import features/{other}）")

    for path in python_files(ROOT / "core"):
        for line, mod in imports_or_flag(path):
            if mod == "features" or mod.startswith("features."):
                bad.append(f"{path.relative_to(ROOT)}:{line} import {mod}"
                           f"（core/ 不知道有哪些功能）")

    if bad:
        print("\n".join(f"FAIL {item}" for item in bad))
        return 1
    print("✅ 分层：运行时不依赖 tools/，功能之间不互相 import，core 不认识 features")
    return 0


if __name__ == "__main__":
    sys.exit(main())
