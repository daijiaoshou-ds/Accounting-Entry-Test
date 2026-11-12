# -*- coding: utf-8 -*-
"""阶段（Stage）：一个 job 由若干个阶段串起来。

两种 actor：

    actor="program"  确定性函数，同一输入必得同一输出；可跳过（哈希没变）
    actor="ai"       程序不实现它，只产出**待办**（ReviewRequest）；等 decision 交回

阶段的依赖用 `depends_on` 声明（可以依赖 program 阶段的产物，也可以依赖 AI 阶段的
结论——后者的"哈希"就是 decision 文件的哈希）。这样 `改一条补丁只重跑下游` 是
自然结果，不需要谁去手工判断"哪些要重算"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

if TYPE_CHECKING:                       # 避免循环 import（job → stage → job）
    from .job import Job


# ============================================================================
# 阶段产出
# ============================================================================

@dataclass
class StageResult:
    """一个 program 阶段跑完之后交回的东西。

    Attributes:
        artifacts: {名字: 路径} —— 只做登记，文件由阶段自己写到 `ctx.out(...)`
        facts: 进 state 的轻量事实（给下游、给前端、给人看），别塞大表
        review: 非空表示"跑完还需要 AI 看一眼"（program 阶段也能产生待办）
    """
    artifacts: Dict[str, str] = field(default_factory=dict)
    facts: Dict[str, Any] = field(default_factory=dict)
    review: Optional["ReviewRequest"] = None


@dataclass
class ReviewRequest:
    """给 AI 的待办。

    Attributes:
        items: 待确认条目（每条至少要有 `key`，补丁就用它当幂等键）
        value_enum: 合法取值（业务桶 / 现金流量项目）；空表示不校验
        help: 一句话说清"要你做什么"
        key_name: 键的含义（voucher_id / row_no），只用于文案与前端
        truncated: 是否被截断（超过 max_review）
    """
    items: List[Dict[str, Any]] = field(default_factory=list)
    value_enum: List[str] = field(default_factory=list)
    help: str = ""
    key_name: str = "key"
    truncated: bool = False

    def __bool__(self) -> bool:
        return bool(self.items)

    def payload(self, stage_id: str) -> Dict[str, Any]:
        missing = [i for i, it in enumerate(self.items) if not it.get("key")]
        if missing:
            # 没有 key 就交不回来结论 —— 这是阶段实现的 bug，别写进 request
            raise ValueError(f"阶段 {stage_id} 的待办缺少 key（第 {missing[:3]} 条）")
        return {
            "stage": stage_id,
            "help": self.help,
            "key_name": self.key_name,
            "value_enum": self.value_enum,
            "count": len(self.items),
            "truncated": self.truncated,
            "how_to_submit": {
                "format": {"schema_version": 1, "stage": stage_id,
                           "decisions": [{"key": "<上面的 key>",
                                          "value": "<value_enum 里的一个>",
                                          "note": "依据（可选）"}]},
                "command": f"journal submit <job_id> --file decisions.json",
            },
            "items": self.items,
        }


# ============================================================================
# 阶段定义
# ============================================================================

@dataclass
class Stage:
    """一个阶段。

    Attributes:
        id: 阶段标识（也是目录名，别用中文/空格）
        title: 给人看的标题
        actor: program / ai
        depends_on: 依赖的阶段 id（决定重跑范围与哈希）
        run: program 阶段的实现 `run(ctx) -> StageResult`
        review: ai 阶段的实现 `review(ctx) -> ReviewRequest`
        help: ai 阶段的一句话说清要做什么
    """
    id: str
    title: str
    actor: str = "program"
    depends_on: Tuple[str, ...] = ()
    run: Optional[Callable[["Ctx"], StageResult]] = None
    review: Optional[Callable[["Ctx"], ReviewRequest]] = None
    help: str = ""

    def __post_init__(self) -> None:
        if self.actor not in ("program", "ai"):
            raise ValueError(f"阶段 {self.id} 的 actor 只能是 program/ai，收到 {self.actor}")
        if self.actor == "program" and self.run is None:
            raise ValueError(f"program 阶段 {self.id} 缺 run()")
        if self.actor == "ai" and self.review is None:
            raise ValueError(f"ai 阶段 {self.id} 缺 review()")


# ============================================================================
# 上下文
# ============================================================================

class Ctx:
    """阶段拿到的上下文：读写产物、取上游事实、取 AI 结论。

    阶段的代码只应该碰这个对象 + `ctx.journal`，不要去摸 job 目录结构。
    """

    def __init__(self, job: "Job", stage: Stage, log: Callable[[str], None]):
        self.job = job
        self.stage = stage
        self.log = log

    # ── 元信息 ──
    @property
    def options(self) -> Dict[str, Any]:
        return self.job.state.get("options") or {}

    @property
    def job_id(self) -> str:
        return self.job.id

    @property
    def source(self) -> Path:
        """原件路径（job/source/<原文件名>）。"""
        return self.job.source

    # ── 产物读写 ──
    def out(self, name: str) -> Path:
        """本阶段的产物路径（目录自动建）。"""
        p = self.job.dir / "out" / self.stage.id / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def path(self, stage_id: str, name: str) -> Path:
        return self.job.dir / "out" / stage_id / name

    def save(self, name: str, frame: pd.DataFrame) -> Path:
        """存表（parquet：小、快、保前导零）。"""
        import journal_io
        p = self.out(name)
        journal_io.write_table(frame, p, target="archive")
        return p.with_suffix(".parquet")

    def load(self, name: str, stage_id: Optional[str] = None) -> pd.DataFrame:
        p = self.path(stage_id or self.stage.id, name)
        if p.suffix != ".parquet":
            p = p.with_suffix(".parquet")
        if not p.exists():
            raise FileNotFoundError(f"上游产物不存在：{p}（是不是 depends_on 没写全？）")
        return pd.read_parquet(p)

    def save_json(self, name: str, obj: Any) -> Path:
        p = self.out(name)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                     encoding="utf-8")
        return p

    def load_json(self, name: str, stage_id: Optional[str] = None) -> Any:
        p = self.path(stage_id or self.stage.id, name)
        if not p.exists():
            raise FileNotFoundError(f"上游产物不存在：{p}")
        return json.loads(p.read_text(encoding="utf-8"))

    # ── 上游事实 / 结论 ──
    def facts(self, stage_id: Optional[str] = None) -> Dict[str, Any]:
        return self.job.stage_facts(stage_id or self.stage.id)

    def decisions(self, stage_id: Optional[str] = None) -> Dict[str, Any]:
        """某个 AI 阶段交回的结论：{key: value}。"""
        return self.job.decisions(stage_id or self.stage.id)

    # ── 账 ──
    @property
    def journal(self):
        return self.job.journal

    # ── 便捷 ──
    def max_review(self, default: int = 100) -> int:
        try:
            return int(self.options.get("max_review") or default)
        except (TypeError, ValueError):
            return default
