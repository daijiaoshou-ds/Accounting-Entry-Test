# -*- coding: utf-8 -*-
"""把仓库装配成一份可分发的 DSH 插件：`dist/dsh-auditor/`（外加 zip）。

装的是什么、为什么这么装：

- **插件**（JS）随包：页面、工具、后台跑、环境配置都在里头；
- **引擎**（Python）也随包，放在 `engine/`。用户机器上不用 git clone、不用找
  仓库根——插件第一次运行会把 `engine/` 复制到用户数据目录再跑（venv 和模型
  下载到那里，插件升级不会把环境冲掉，见 `src/settings.js` 的 `engineHome()`）；
- **模型不进包**：`hajishou-V2.0` 约 2GB，走 ModelScope 现下（`core/nn/_storage/`），
  BGE 基座缓存 `core/nn/models/` 同理；
- **venv 不进包**：环境配置页在用户机器上建。

用法：
    python _dev/scripts/build_dsh_auditor.py            # 装配到 dist/dsh-auditor
    python _dev/scripts/build_dsh_auditor.py --zip      # 另打一份 dist/dsh-auditor-<版本>.zip
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "dsh_plugin" / "journal-workbench"
DIST = ROOT / "dist"
OUT = DIST / "dsh-auditor"
VERSION = "0.1.2"

# 运行时引擎：只拷这些（`dsh_plugin` 自己的 JS 不重复拷，`_dev`/`case`/`_archive`
# 与业务无关，`training_case` 是客户真账，绝对不能进包）
ENGINE_DIRS = ("core", "features", "journal_io", "accounting_domain", "jobflow")
ENGINE_FILES = ("journal.py", "requirements.txt", "requirements-nn.txt", "AGENT_PLAYBOOK.md")
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_TOP = {"_storage", "models"}          # 模型/BGE 缓存：运行时下载

PACKAGE_JSON = {
    "name": "dsh-auditor",
    "version": VERSION,
    "private": False,
    "description": "审计工作台：序时账清洗 / 现金流量表编写 / 对方科目分析 / 会计分录测试",
    "repository": {
        "type": "git",
        "url": "git+https://github.com/daijiaoshou-ds/dsh-auditor.git",
    },
    "homepage": "https://github.com/daijiaoshou-ds/dsh-auditor#readme",
    "bugs": {"url": "https://github.com/daijiaoshou-ds/dsh-auditor/issues"},
    "type": "module",
    "main": "index.js",
    "exports": {
        ".": "./index.js",
        "./client": "./client.js",
        "./locale/*.json": "./locale/*.json",
        "./icon": "./icon.svg",
    },
    "files": [
        "index.js", "client.js", "src/*.js", "src/*.py",
        "locale/*.json", "icon.svg", "cordis.patch.yml",
        "README.md", "INSTALL.md", "engine/**",
    ],
    "keywords": ["dsh", "dsh-plugin", "audit", "journal", "accounting"],
    "license": "MIT",
    "dsh": {
        "bundle": {"patch": "./cordis.patch.yml"},
        "client": {
            "platform": "web",
            "immediately": True,
            "inject": ["@deepseek-ai/dsh-client-locale",
                       "@deepseek-ai/dsh-client-ui-conversation"],
        },
    },
}

PATCH_YML = """# 审计工作台（dsh-auditor）—— bundle patch
#
# 插件自带 Python 引擎；第一次运行会复制到用户数据目录，venv 与模型都装在那，
# 升级插件不会把环境冲掉。装好后在页面上点「环境配置」走六步即可。
#
# 可选配置（都不给也能跑）：
#   - insert:
#       - id: dsh-auditor
#         name: dsh-auditor
#         config:
#           repoRoot: D:\\path\\to\\engine        # 想用自己那份引擎时指过去
#           python: D:\\path\\to\\venv\\Scripts\\python.exe
#           jobsDir: D:\\path\\to\\_storage_jobs
#           reviewLimit: 20
#           autoWake: false
#
- insert:
    - id: dsh-auditor
      name: dsh-auditor
"""

INSTALL_MD = """# dsh-auditor 安装

## 一、装插件

在 DSH 里 **插件 → 添加插件**，输入这一行（公开仓库，pnpm 直接拉）：

```
github:daijiaoshou-ds/dsh-auditor
```

本目录装（zip 解压出来的那份）：

```
plugin_manager install_bundle  target=<本目录的绝对路径>
```

装完在 DSH 里打开「审计工作台」页签。

## 二、配环境（第一次，约 10–20 分钟）

页签左上角「环境配置」里六步，从上到下点：

| 步骤 | 装什么 | 国内网络 |
|---|---|---|
| Python 解释器 | 3.13.5（约 28MB） | python.org 慢就走华为云镜像（已内置） |
| 虚拟环境 | 引擎目录下的 `venv/` | 本地，几秒 |
| 基础依赖 | pandas / numpy / pyarrow / calamine / openpyxl | 默认阿里云源 |
| 模型依赖（CPU） | torch CPU 轮子（约 122MB）+ transformers / modelscope | torch 官方源 + 交大镜像兜底 |
| 模型文件 | hajishou-V2.0（约 2GB） | ModelScope 国内直下 |
| 基座模型（BGE） | bge-large-zh（约 1.6GB） | ModelScope 优先（不预下的话，第一本账要静默等十几分钟） |

GPU 版不装也能用全部功能（推理是 CPU 跑得动的规模）；要显卡再在「模型依赖」里
切成 CUDA 版（约 2.4GB）。

## 三、用

1. 右栏「＋」新建任务：选层 → 上传序时账 → 确认字段映射；
2. 如果出现「先对一下科目」：给认不出的写法挑标准科目（写进全局别名表），
   或者「先不管，直接开始」；
3. 主区点功能开跑；停到复核位可以交给这个任务的 AI 复核会话，也可以不复核。
"""


def copy_plugin() -> None:
    (OUT / "src").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN / "index.js", OUT / "index.js")
    shutil.copy2(PLUGIN / "client.js", OUT / "client.js")
    shutil.copy2(PLUGIN / "icon.svg", OUT / "icon.svg")
    for item in (PLUGIN / "src").iterdir():
        if item.is_file():
            shutil.copy2(item, OUT / "src" / item.name)
    if (PLUGIN / "locale").is_dir():
        shutil.copytree(PLUGIN / "locale", OUT / "locale", dirs_exist_ok=True)

    # 插件 id 换成包名：装两个同名 id 的插件会打架
    index = (OUT / "index.js").read_text(encoding="utf-8")
    index = index.replace("export const name = 'dsh-journal-workbench'",
                          "export const name = 'dsh-auditor'")
    (OUT / "index.js").write_text(index, encoding="utf-8")

    # 浏览器半边更要换：client-modules 按**包名**核对 `__ModuleLoader__.load({id})`。
    # 留成旧 id 的话 Host 能起来、页面却没反应，报 "loaded without registering"。
    client = (OUT / "client.js").read_text(encoding="utf-8")
    client = client.replace("  id: 'dsh-journal-workbench',", "  id: 'dsh-auditor',")
    client = client.replace("const NS = 'dsh-journal-workbench'", "const NS = 'dsh-auditor'")
    (OUT / "client.js").write_text(client, encoding="utf-8")
    assert "id: 'dsh-auditor'," in client and "NS = 'dsh-auditor'" in client


def copy_engine() -> None:
    engine = OUT / "engine"
    engine.mkdir(parents=True, exist_ok=True)

    def ignore(directory, names):
        dropped = set()
        for name in names:
            if name in SKIP_DIRS:
                dropped.add(name)
            # 模型/缓存目录只在顶层排除；其余目录里的同名文件夹不当特殊处理
            if Path(directory).name in ("nn",) and name in SKIP_TOP:
                dropped.add(name)
        return dropped

    for folder in ENGINE_DIRS:
        shutil.copytree(ROOT / folder, engine / folder, ignore=ignore, dirs_exist_ok=True)
    for name in ENGINE_FILES:
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, engine / name)

    # 模型目录先占个位：环境页下载模型时直接往这里写
    (engine / "core" / "nn" / "_storage").mkdir(parents=True, exist_ok=True)
    # 引擎版本戳：插件升级时据此判断要不要把新的 engine/ 同步过去
    stamp = f"{VERSION}-{time.strftime('%Y%m%d%H%M%S')}"
    (engine / ".engine-version").write_text(stamp, encoding="utf-8")


def write_meta() -> None:
    (OUT / "package.json").write_text(
        json.dumps(PACKAGE_JSON, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "cordis.patch.yml").write_text(PATCH_YML, encoding="utf-8")
    (OUT / "INSTALL.md").write_text(INSTALL_MD, encoding="utf-8")
    readme = ROOT / "README.md"
    if readme.exists():
        shutil.copy2(readme, OUT / "README.md")


def size_of(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="另打一份 zip")
    args = ap.parse_args(argv)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    copy_plugin()
    copy_engine()
    write_meta()

    print(f"✓ 装配完成：{OUT}")
    print(f"  插件：{(OUT / 'client.js').stat().st_size / 1024:.0f}KB client + src/")
    print(f"  引擎：{size_of(OUT / 'engine'):.1f}MB（{sum(1 for _ in (OUT / 'engine').rglob('*.py'))} 个 .py）")
    print(f"  合计：{size_of(OUT):.1f}MB")

    if args.zip:
        target = DIST / f"dsh-auditor-{VERSION}"
        archive = shutil.make_archive(str(target), "zip", root_dir=DIST,
                                      base_dir="dsh-auditor")
        print(f"✓ zip：{archive}（{Path(archive).stat().st_size / 1024 / 1024:.1f}MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
