"""응답 형식: 성공 `{data, meta}`, 실패 `{error: {code, message}}` (CLAUDE.md 표기·형식)."""

from __future__ import annotations

import os
from collections.abc import Awaitable
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ax_contracts.schemas.common import TENANT_ID_DEFAULT


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class ServiceDisabled(ApiError):
    """받는 서비스가 꺼져 있어 조회가 막혔다(`service_disabled`, 업무 요청 라우터).

    목록·집계 화면은 `or_unavailable`로 받아 "없음"으로 계속 진행하고
    `meta.unavailable`에 이유를 남긴다.
    단건 조회처럼 보여 줄 것이 없는 곳은 이 오류(503)가 그대로 나간다.
    """

    def __init__(self, service: str) -> None:
        self.service = service
        super().__init__(503, "SERVICE_DISABLED", unavailable_text(service))


def unavailable_text(service: str) -> str:
    return f"확인할 수 없음 ({service} 서비스가 꺼져 있음)"


# 이 요청에서 "확인할 수 없음"으로 남길 이유 (요청 하나 = 작업 하나라 다른 요청에 새지 않는다)
_UNAVAILABLE: ContextVar[tuple[str, ...]] = ContextVar("dashboard_unavailable", default=())


def note_unavailable(message: str) -> None:
    current = _UNAVAILABLE.get()
    if message not in current:
        _UNAVAILABLE.set((*current, message))


async def or_unavailable[T](call: Awaitable[T], empty: T) -> T:
    """꺼진 서비스의 조회면 `empty`(없음)로 계속 진행하고 이유를 응답 meta에 남긴다.

    `empty`는 빈 목록처럼 "없음"인 값만 준다. 숫자를 지어내지 않는다.
    """
    try:
        return await call
    except ServiceDisabled as disabled:
        note_unavailable(disabled.message)
        return empty


def ok(data: Any, total: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"tenant_id": os.getenv("TENANT_ID", TENANT_ID_DEFAULT)}
    if total is not None:
        meta["total"] = total
    unavailable = _UNAVAILABLE.get()
    if unavailable:
        meta["unavailable"] = list(unavailable)
    return {"data": data, "meta": meta}


def is_mock() -> bool:
    return os.getenv("DASHBOARD_MOCK") == "1" or os.getenv("AGENT_TEMPLATE_MOCK") == "1"


def require_mock() -> None:
    """실제 데이터 연결은 3단계 이후다. 그 전에는 목 모드에서만 응답한다."""
    if not is_mock():
        raise ApiError(
            503,
            "UPSTREAM_NOT_CONNECTED",
            "데이터 출처가 아직 연결되지 않았다 (DASHBOARD_MOCK=1로 목 모드 사용)",
        )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _error(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return _error(422, "INVALID_REQUEST", f"{loc}: {first.get('msg', '잘못된 요청')}")
