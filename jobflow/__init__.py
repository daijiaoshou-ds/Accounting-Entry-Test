# -*- coding: utf-8 -*-
"""jobflow —— 程序与 AI 穿插执行的编排层。

**它解决的问题**：以前"跑到哪一步、该谁动、交什么格式"全在对话里。换个会话就丢，
重跑一条补丁要走全链，谁改了哪条没有留痕。

现在一个任务（job）就是磁盘上的一个目录：

    <jobs>/<job_id>/
    ├── state.json                  阶段状态 + 下一步（机器可读）
    ├── source/<原文件>              原件（重跑/复现的根据）
    ├── out/<stage>/…                每个程序阶段的产物
    ├── reviews/<stage>.request.json AI 的待办（带 schema）
    └── decisions/<stage>.json       AI 的结论（幂等键 + 审计）

三条规矩：

1. **`actor="ai"` 的阶段，程序不猜**：只产出 request 然后停下，`next` 里写明
   读什么、交什么、用什么 schema 校验。
2. **AI 不写产出文件**，只写 decision。产物永远由程序重放生成。
3. **哈希短路**：`stage.hash = 输入 + 映射 + 选项 + 上游阶段哈希 + 相关 decision`。
   改一条补丁只重跑下游，清洗不重算。

用法（CLI 见 `jobflow.cli`）：

    journal run 序时账.xlsx --feature cashflow
    journal inbox                         有哪些任务在等 AI
    journal next <job_id>                  下一步 + 待办（直接带条目）
    journal submit <job_id> --file decisions.json
"""

from .config import JOBS_DIR, REPO_ROOT  # noqa: F401
from .stage import Ctx, ReviewRequest, Stage, StageResult  # noqa: F401
from .job import Job, read_json  # noqa: F401
from .scan import scan_jobs  # noqa: F401

__version__ = "0.2.0"

__all__ = ["Job", "read_json", "scan_jobs", "Stage", "StageResult",
           "ReviewRequest", "Ctx", "JOBS_DIR", "REPO_ROOT", "__version__"]
