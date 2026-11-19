"""DejaFix 읽기 전용 프록시.

DejaFix는 모든 API에 로그인 토큰(JWT)을 요구한다. 서버 nginx는 로그인·토큰 갱신을 못 하므로
이 서비스가 viewer 계정으로 대신 로그인하고, 받은 토큰을 붙여 GET만 넘긴다.
부르는 쪽도 토큰(사람·에이전트)을 붙여야 하고, 정해 둔 조회 세 가지만 넘긴다 (`auth.py`).
개발 서버 프록시(frontend/dejafix-proxy.ts)와 같은 동작이다.
연결은 부를 때마다 정한다: 공용 보관함의 `dejafix` → 없으면 환경변수(DEJAFIX_PROXY_TARGET·
DEJAFIX_USERNAME·DEJAFIX_PASSWORD) → 없으면 503. 계정은 화면·로그에 싣지 않는다.
`GET /status`는 그 연결의 출처(builtin·custom·none)와 응답 여부만 알려 준다 (화면의 연결 카드용).
`/status`도 호출자 토큰을 확인한다(DejaFix에 실제로 로그인해 보므로). `/healthz`만 토큰 없이 연다.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from ax_agent_template import company_connection, pinned_http, tool_connections
from ax_agent_template.config import refuse_mock_outside_tests
from ax_dejafix_proxy import auth

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
        self._inflight = 0
        self._retired = False

    @property
    def configured(self) -> bool:
        return bool(self._username and self._password)

    async def aclose(self) -> None:
        await self._client.aclose()

    def retire(self) -> None:
        """더 쓰지 않는 세션. 진행 중인 요청이 끝난 뒤에 클라이언트를 닫는다."""
        self._retired = True
        if self._inflight == 0:
            asyncio.get_running_loop().create_task(self.aclose())

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
        self._inflight += 1
        try:
            return await self._get(path, query)
        finally:
            self._inflight -= 1
            if self._retired and self._inflight == 0:
                asyncio.get_running_loop().create_task(self.aclose())

    async def _get(self, path: str, query: str) -> httpx.Response:
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


# (대상 주소, 계정, 비밀번호). 비밀번호는 로그·응답에 싣지 않는다
Target = tuple[str, str, str]


def _target() -> tuple[Target, bool] | None:
    """부를 때마다 정한다: 보관함 `dejafix` → 환경 변수 → 없음(None). 둘째 값: 보관함에서 왔는가."""
    conn = tool_connections.find("dejafix")
    if conn is not None:
        url = str(conn.config.get("url") or "").strip().rstrip("/")
        user = str(conn.config.get("username") or "").strip()
        password = conn.secrets.get("password", "")
        if url and user and password:
            return (url, user, password), True
    user = os.getenv("DEJAFIX_USERNAME", "")
    password = os.getenv("DEJAFIX_PASSWORD", "")
    if user and password:
        url = os.getenv("DEJAFIX_PROXY_TARGET", "http://127.0.0.1:18000").strip().rstrip("/")
        return (url, user, password), False
    return None


def create_app(client: httpx.AsyncClient | None = None) -> FastAPI:
    """`client`를 주면(테스트) 대상 주소가 어떻든 그 클라이언트로 부른다."""
    state: dict[str, tuple[Target, DejaFixSession]] = {}

    def session_for(target: Target, guarded: bool = False) -> DejaFixSession:
        """대상·계정·비밀번호가 바뀌면 세션(토큰)을 새로 만든다."""
        held = state.get("s")
        if held is not None and held[0] == target:
            return held[1]
        url, user, password = target
        # 화면에서 정한 주소(guarded)는 검사한 IP로만 붙고 리다이렉트를 따라가지 않는다
        transport = pinned_http.guarded_transport() if guarded else None
        http = client or httpx.AsyncClient(
            base_url=url, timeout=10.0, follow_redirects=False, transport=transport
        )
        fresh = DejaFixSession(http, user, password)
        if held is not None and client is None:
            held[1].retire()  # 진행 중인 요청이 있으면 끝난 뒤에 닫는다
        state["s"] = (target, fresh)
        return fresh

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        refuse_mock_outside_tests(auth.MOCK_NAME)  # 목 모드는 토큰 없이 `human:`을 받으므로
        yield
        held = state.pop("s", None)
        if held is not None and client is None:
            await held[1].aclose()

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

    @app.get("/status", response_model=None)
    async def status(request: Request) -> dict[str, object] | Response:
        """화면의 DejaFix 연결 카드가 읽는 연결 출처와 실제 응답 여부.

        source: builtin(환경 변수, AXcellent와 같이 설치된 DejaFix) · custom(화면에서 저장한
        다른 DejaFix, 보관함) · none(둘 다 없음).
        reachable: 로그인 뒤 현재 장애 조회가 200이면 True, 아니면 False, 부를 대상이 없으면 None.
        주소·계정·비밀번호·토큰은 싣지 않는다. 토큰이 없거나 모르면 401 (forward와 같다).
        """
        try:
            auth.check_caller(request.headers.get("authorization"))
        except auth.AuthFailure as exc:
            return _error(401, exc.code, exc.message)
        found = _target()
        if found is None:
            return {"source": "none", "reachable": None, "detail": "연결 정보가 없어요"}
        target, from_vault = found
        source = "custom" if from_vault else "builtin"
        if from_vault:
            try:
                await asyncio.to_thread(company_connection.check_host, target[0])
            except ValueError:
                return {"source": source, "reachable": False, "detail": "주소를 쓸 수 없어요"}
        try:
            res = await session_for(target, from_vault).get(
                "/api/incidents", "status=problem&limit=1"
            )
        except httpx.HTTPError:
            return {"source": source, "reachable": False, "detail": "DejaFix에 연결할 수 없어요"}
        if res.status_code != 200:
            return {"source": source, "reachable": False, "detail": f"HTTP {res.status_code}"}
        return {"source": source, "reachable": True, "detail": "응답해요"}

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    async def forward(path: str, request: Request) -> Response:
        # 순서: 호출자 토큰 → GET → 경로 조작 → 허용 목록 (auth.py)
        try:
            auth.check_caller(request.headers.get("authorization"))
        except auth.AuthFailure as exc:
            return _error(401, exc.code, exc.message)
        if request.method != "GET":
            return _error(405, "METHOD_NOT_ALLOWED", "읽기(GET)만 넘깁니다")
        raw = request.scope.get("raw_path") or request.url.path.encode()
        if auth.unsafe_raw_path(raw):
            return _error(400, "INVALID_PATH", "경로를 읽을 수 없습니다")
        full = f"/{path}"
        # 로그인 API 등은 밖으로 열지 않는다. 넘기는 것은 화면·AI가 쓰는 DejaFix 조회 세 가지뿐이다.
        if not auth.allowed_path(full):
            return _error(404, "NOT_FOUND", "넘길 수 없는 경로입니다")
        found = _target()
        if found is None:
            return _error(
                503,
                "DEJAFIX_NOT_CONNECTED",
                "DejaFix가 연결되지 않았습니다 (화면의 도구 연결 또는 DEJAFIX_USERNAME·"
                "DEJAFIX_PASSWORD 설정)",
            )
        target, from_vault = found
        if from_vault:
            try:
                # 화면에서 정한 주소는 사내·허용 목록 정책을 통과해야 부른다 (밖으로 나가는 호출 전)
                # 이름 풀이(DNS)가 막힐 수 있어 이벤트 루프 밖에서 한다
                await asyncio.to_thread(company_connection.check_host, target[0])
            except ValueError as exc:
                return _error(502, "DEJAFIX_UNREACHABLE", f"DejaFix 주소를 쓸 수 없습니다: {exc}")
        try:
            upstream = await session_for(target, from_vault).get(full, request.url.query)
        except httpx.HTTPError:
            return _error(502, "DEJAFIX_UNREACHABLE", "DejaFix 백엔드에 연결할 수 없습니다")
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", JSON),
        )

    return app


app = create_app()
