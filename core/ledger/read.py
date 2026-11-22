# -*- coding: utf-8 -*-
"""read 阶段：载入序时账 + 认列名 + 一级科目归一。

读表 + 认列是**账的属性**，一份账只该解析一次。四个功能各解析一遍不只是多花几秒
——更糟的是各认各的列：同一份账在清洗里认对了、在对方科目里认错了一列，两边的
结果对不上，而任何地方都不会报错。

归一化也在这里做一次（`accounting_domain.subject_norm.normalize_journal`）：
它是全流程共用的会计口径，不能各功能各算一遍。原始写法在「原科目」列里，
标准名进「一级科目」——四类硬约束、货币资金判定、对方的模式、科目对都按标准名算。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

import journal_io
from accounting_domain.subject_norm import normalize_journal
from jobflow.stage import Ctx, StageResult

REQUIRES = ("voucher_id", "subject_l1", "debit", "credit")


def prepared_dir(options: Dict[str, Any]) -> Optional[Path]:
    """配账阶段落盘的「已解析账」目录（`raw.parquet` + `schema.json`）。

    为什么读表要放在配账那边：一份账只该解析一次。

    解析出来的是一张 parquet（列有类型、凭证号不会丢前导零、没有编码歧义），
    比 csv 更适合当内部格式：同一张表反复读只要几毫秒，csv 每次都要重新推断
    类型（`0001` 会变成 `1`，`2025-01-01` 会变成字符串或日期，看推断心情）。
    """
    raw = str((options or {}).get("prepared") or "").strip()
    if not raw:
        return None
    folder = Path(raw)
    if not (folder / "raw.parquet").is_file() or not (folder / "schema.json").is_file():
        return None
    return folder


def prepared_signature(ctx: Ctx) -> str:
    """已解析账的内容指纹，进 read 阶段的哈希。

    只在"重新配账"时才会变（换账/改映射后重算 combo 会一并重写 raw.parquet），
    这时下游必须重算；平时它是个常量，不会把历史任务作废。
    """
    folder = prepared_dir(ctx.options or {})
    if folder is None:
        return ""
    digest = hashlib.sha256()
    with open(folder / "raw.parquet", "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def run_read(ctx: Ctx) -> StageResult:
    return read_journal(ctx, REQUIRES)


def read_journal(ctx: Ctx, requires: Any = REQUIRES) -> StageResult:
    """载入账并按 `requires` 校验本功能要用的字段。

    `requires` 是参数而不是常量：对方科目 / 会计分录测试的字段契约比清洗多一个
    「日期」（各功能自己的 `REQUIRES`，见 `features/<id>/` 的 `engine.py` / `stages.py`），但载入逻辑一字不差。
    各功能各抄一份，迟早会有一份认列认错。

    两条路：
      · 配过账（`options.prepared` 指到任务的 prepared/）→ 直接载入，不解析原件；
      · 没配过账（命令行直接跑一份账）→ 当场读原件 + 合并手工映射（老路，保留）。
    """
    folder = prepared_dir(ctx.options or {})
    if folder is not None:
        return _load_prepared(ctx, folder, requires)
    return _read_source(ctx, requires)


def _load_prepared(ctx: Ctx, folder: Path, requires: Any) -> StageResult:
    """载入配账时解析好的表。"""
    meta = json.loads((folder / "schema.json").read_text(encoding="utf-8"))
    j = journal_io.Journal(pd.read_parquet(folder / "raw.parquet"),
                           journal_io.JournalSchema(**meta),
                           source=ctx.job.state["input"]["filename"])
    j.require(requires)
    normalize_journal(j)             # 一级科目归一（有改写才加「原科目」列）
    ctx.job.save_journal(j)          # 搬进本任务：任务能独立重跑、独立打包

    vids = j.keys("voucher_id")
    return StageResult(facts={
        "rows": int(len(j)),
        "vouchers": int(vids[vids != ""].nunique()),
        "engine": "parquet",
        "mapping": j.schema.mapping,
        "unmapped": list(j.schema.unmapped),
        "source": "task",
    })


def _read_source(ctx: Ctx, requires: Any) -> StageResult:
    """老路：当场读原件（命令行/没配账时用）。"""
    override = (ctx.options.get("mapping") or {}) if ctx.options else {}
    j = journal_io.read(ctx.source, requires=())     # 先读，再合并手工映射

    if override:
        for key, col in override.items():
            if col not in j.df.columns:
                raise ValueError(f"mapping 里 {key}={col!r}：表里没有这一列")
            j.schema.mapping[key] = col
    j.require(requires)

    normalize_journal(j)             # 一级科目归一（有改写才加「原科目」列）
    ctx.job.save_journal(j)
    vids = j.keys("voucher_id")
    return StageResult(facts={
        "rows": int(len(j)),
        "vouchers": int(vids[vids != ""].nunique()),
        "engine": j.schema.engine,
        "mapping": j.schema.mapping,
        "unmapped": list(j.schema.unmapped),
        "source": "file",
    })
