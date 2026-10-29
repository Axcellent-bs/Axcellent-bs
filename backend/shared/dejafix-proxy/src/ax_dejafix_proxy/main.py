"""DejaFix 읽기 전용 프록시.

DejaFix는 모든 API에 로그인 토큰(JWT)을 요구한다. 서버 nginx는 로그인·토큰 갱신을 못 하므로
이 서비스가 viewer 계정으로 대신 로그인하고, 받은 토큰을 붙여 GET만 넘긴다.
개발 서버 프록시(frontend/dejafix-proxy.ts)와 같은 동작이다.
계정은 환경변수(DEJAFIX_USERNAME·DEJAFIX_PASSWORD)로만 받고 화면·로그에 싣지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

JSON = "application/json"


def _error(status: int, code: str, message: str) -> Response:
    body = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)
    return Response(content=body, status_code=status, media_type=JSON)


class DejaFixSession:
    """로그인 토큰을 한 번만 받아 두고, 만료(401)되면 다시 받는다."""

    def __init__(self, client: httpx.AsyncClient, username: str | None, password: str | None):
        self._client = client
        self._username = username
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._username and self._password)

    async def _login(self) -> str | None:
        res = await self._client.post(
            "/api/auth/login", json={"username": self._username, "password": self._password}
        )
        if res.status_code != 200:
            return None
        return (res.json().get("data") or {}).get("token")

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def get(self, path: str, query: str) -> httpx.Response:
        url = f"{path}?{query}" if query else path
        async with self._lock:
            if self._token is None:
                self._token = await self._login()
        res = await self._client.get(url, headers=self._auth())
        if res.status_code == 401:
            async with self._lock:
                self._token = await self._login()
            res = await self._client.get(url, headers=self._auth())
        return res


def create_app(client: httpx.AsyncClient | None = None) -> FastAPI:
    target = os.getenv("DEJAFIX_PROXY_TARGET", "http://127.0.0.1:18000").rstrip("/")
    owned = client is None
    http = client or httpx.AsyncClient(base_url=target, timeout=10.0)
    session = DejaFixSession(http, os.getenv("DEJAFIX_USERNAME"), os.getenv("DEJAFIX_PASSWORD"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owned:
            await http.aclose()

    app = FastAPI(
        title="dejafix-proxy",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    async def forward(path: str, request: Request) -> Response:
        if request.method != "GET":
            return _error(405, "METHOD_NOT_ALLOWED", "읽기(GET)만 넘깁니다")
        full = f"/{path}"
        # 로그인 API를 밖으로 열지 않는다. 넘기는 것은 DejaFix의 /api/* 조회뿐이다.
        if not full.startswith("/api/") or full.startswith("/api/auth"):
            return _error(404, "NOT_FOUND", "넘길 수 없는 경로입니다")
        if not session.configured:
            return _error(
                503,
                "DEJAFIX_NOT_CONFIGURED",
                "DejaFix 계정(DEJAFIX_USERNAME·DEJAFIX_PASSWORD)이 설정되지 않았습니다",
            )
        try:
            upstream = await session.get(full, request.url.query)
        except httpx.HTTPError:
            return _error(
                502, "DEJAFIX_UNREACHABLE", f"DejaFix 백엔드({target})에 연결할 수 없습니다"
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", JSON),
        )

    return app


app = create_app()
