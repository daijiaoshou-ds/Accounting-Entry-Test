# -*- coding: utf-8 -*-
"""服务层异常：带机器可读 code，供 API 直接映射成 HTTP 状态码。"""

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
