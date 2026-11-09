# -*- coding: utf-8 -*-
"""服务层：序时账清洗的业务内核，与界面无关。

对外只有四组能力：
- `cleaner`：读表识别字段、建任务、跑清洗、分页、导出
- `jobs`：任务目录与状态机
- `errors`：带 code 的业务异常

约定：这一层禁止 import streamlit / fastapi，方便被脚本、CLI、API 共用。
"""

from . import cleaner, errors, jobs  # noqa: F401
from .errors import ServiceError  # noqa: F401

__all__ = ["cleaner", "jobs", "errors", "ServiceError"]
