# -*- coding: utf-8 -*-
"""journal_io —— 序时账的读写层（全项目唯一一份）。

功能层只跟三样东西打交道：

    from journal_io import read, write_sheets, FIELDS

    j = read("序时账.xlsx", requires=("voucher_id", "subject_l1", "debit", "credit"))
    j.df        # 带类型的原表（写回用它，原列原序不动）
    j.raw       # 全字符串保真视图（凭证号 0001 不丢零）
    j.keys("voucher_id")   # 键视图
    j.money("debit")       # 金额视图（去千分位、NaN→0）

    write_sheets({"结果": df}, "输出.xlsx", target="download")

**这里没有业务判断**：不认桶、不算现金流、不碰模型。只做"文件 ↔ 表"和"列名 ↔ 字段"。

分层与迁移见 `docs/design/architecture_v5.md`。
"""

from .fields import (  # noqa: F401
    ALL_KEYS,
    FEATURE_OPTIONAL,
    FEATURE_REQUIRES,
    FIELD_BY_KEY,
    FIELDS,
    FieldSpec,
    labels,
    requires_of,
    ui_fields_of,
)
from .journal import Journal, read  # noqa: F401
from .readers import Source, read_frame, suffix_of  # noqa: F401
from .schema import (  # noqa: F401
    JournalSchema,
    build_schema,
    detect,
    from_legacy_mapping,
    legacy_key,
    preview,
    to_legacy_mapping,
    validate,
)
from .writers import (  # noqa: F401
    BUDGET_SECONDS,
    CELLS_PER_SEC,
    MIME_CSV,
    MIME_XLSX,
    XLSX_MAX_ROWS,
    WriteResult,
    estimate_xlsx_seconds,
    serialize_bytes,
    to_records,
    too_large_hint,
    will_be_slow,
    write_sheets,
    write_table,
)

__version__ = "1.0.0"

__all__ = [
    "read", "Journal", "JournalSchema", "read_frame", "suffix_of", "Source",
    "FIELDS", "FIELD_BY_KEY", "ALL_KEYS", "FieldSpec", "FEATURE_REQUIRES", "FEATURE_OPTIONAL",
    "requires_of", "ui_fields_of", "labels", "detect", "build_schema", "validate", "preview",
    "to_legacy_mapping", "from_legacy_mapping", "legacy_key",
    "write_table", "write_sheets", "serialize_bytes", "to_records",
    "WriteResult", "estimate_xlsx_seconds", "will_be_slow", "too_large_hint",
    "XLSX_MAX_ROWS", "BUDGET_SECONDS", "CELLS_PER_SEC", "MIME_XLSX", "MIME_CSV",
]
