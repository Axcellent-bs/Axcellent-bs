"""응답 형식: 성공 `{data, meta}`, 실패 `{error: {code, message}}` (CLAUDE.md 표기·형식)."""

from __future__ import annotations

import os
from collections.abc import Awaitable
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ax_contracts.schemas.common import TENANT_ID_ALIASES, TENANT_ID_CATBOOKS, TENANT_ID_DEFAULT


class ApiError(Exception):
    """`extra`는 `error` 안에 더 싣는 칸이다(예: 사이트 변환기 `site_code`). 없으면 예전 모양."""

    def __init__(
        self, status_code: int, code: str, message: str, extra: dict[str, Any] | None = None
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = dict(extra or {})


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


# 이 요청의 로그인 세션 회사 (요청 하나 = 작업 하나라 다른 요청에 새지 않는다). 세션이 아니면 None
_SESSION_COMPANY: ContextVar[str | None] = ContextVar("dashboard_session_company", default=None)


def session_company() -> str | None:
    return _SESSION_COMPANY.get()


def _company_from_authorization(authorization: str | None) -> str | None:
    """로그인 세션 토큰이면 그 회사. 그 밖이거나 검증이 안 되면 None(거절은 각 라우트 몫)."""
    from ax_agent_template import human_auth

    token = human_auth.bearer(authorization)
    if not token or not human_auth.is_session_token(token):
        return None
    try:
        return human_auth.verify_session(token).company_id
    except Exception:  # noqa: BLE001 - 여기서는 회사만 읽는다. 오류 응답은 인증 의존성이 낸다
        return None


class SessionCompanyMiddleware:
    """요청의 세션 회사를 contextvar에 담는다 (응답 `meta.tenant_id`가 읽는다)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        authorization = None
        for key, value in scope.get("headers", ()):
            if key == b"authorization":
                authorization = value.decode("latin-1")
                break
        token = _SESSION_COMPANY.set(_company_from_authorization(authorization))
        try:
            await self.app(scope, receive, send)
        finally:
            _SESSION_COMPANY.reset(token)


def tenant_meta(explicit: str | None = None) -> dict[str, str]:
    """에이전트를 부를 때 `RequestMeta`에 더할 회사 칸. 명시한 값이 먼저, 없으면 세션 회사.

    캣북스 세션과 세션 없는 호출(백그라운드·공용 토큰)은 칸을 비워 지금 기본값(shop-demo)을
    그대로 둔다(받는 쪽의 로그 조회가 그 값을 본다). 다른 회사 세션이면 그 회사 id를 싣는다.
    """
    if explicit:
        return {"tenant_id": explicit}
    company = _SESSION_COMPANY.get()
    if company and TENANT_ID_ALIASES.get(company, company) != TENANT_ID_CATBOOKS:
        return {"tenant_id": company}
    return {}


def meta_tenant_id() -> str:
    """응답 meta의 회사. 세션이 캣북스거나 없으면 지금 값(`TENANT_ID`, 기본 shop-demo)을 둔다
    (공통 로그 조회가 그 값을 본다). 다른 회사 세션이면 그 회사 id."""
    company = _SESSION_COMPANY.get()
    if company and TENANT_ID_ALIASES.get(company, company) != TENANT_ID_CATBOOKS:
        return company
    return os.getenv("TENANT_ID", TENANT_ID_DEFAULT)


def ok(data: Any, total: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"tenant_id": meta_tenant_id()}
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


def _error(
    status_code: int, code: str, message: str, extra: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {**(extra or {}), "code": code, "message": message}},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, getattr(exc, "extra", None))

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return _error(exc.status_code, "HTTP_ERROR", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return _error(422, "INVALID_REQUEST", f"{loc}: {first.get('msg', '잘못된 요청')}")
