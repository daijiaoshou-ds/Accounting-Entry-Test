# -*- coding: utf-8 -*-
"""API 层的业务异常：带机器可读 code，直接映射成 HTTP 状态码。

以前这个类在 `summary_cleaner/service/errors.py`——那层服务（老的一次性清洗任务）
在编排层落地后退场了。异常映射本来就是 HTTP 层的事，搬到这里。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ServiceError(Exception):
    """业务异常。

    code 是给前端做分支判断的稳定标识（不要用 message 做判断），
    status 是建议的 HTTP 状态码。
    """

    def __init__(self, code: str, message: str, status: int = 400,
                 detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": {"code": self.code, "message": self.message,
                      "detail": self.detail},
        }


def not_found(what: str, ident: Any) -> ServiceError:
    return ServiceError("not_found", f"{what}不存在：{ident}", 404)


def invalid(code: str, message: str, **detail: Any) -> ServiceError:
    return ServiceError(code, message, 400, detail)
