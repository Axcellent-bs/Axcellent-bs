"""DejaFix 읽기 전용 프록시.

DejaFix는 모든 API에 로그인 토큰(JWT)을 요구한다. 서버 nginx는 로그인·토큰 갱신을 못 하므로
이 서비스가 viewer 계정으로 대신 로그인하고, 받은 토큰을 붙여 GET만 넘긴다.
부르는 쪽도 토큰(사람·에이전트)을 붙여야 하고, 정해 둔 조회 세 가지만 넘긴다 (`auth.py`).
개발 서버 프록시(frontend/dejafix-proxy.ts)와 같은 동작이다.
연결은 부를 때마다 정한다: 공용 보관함의 `dejafix` → 없으면 환경변수(DEJAFIX_PROXY_TARGET·
DEJAFIX_USERNAME·DEJAFIX_PASSWORD) → 없으면 503. 계정은 화면·로그에 싣지 않는다.
`GET /status`는 그 연결의 출처(builtin·custom·none)와 응답 여부만 알려 준다 (화면의 연결 카드용).
`/status`도 호출자 토큰을 확인한다(DejaFix에 실제로 로그인해 보므로). `/healthz`만 토큰 없이 연다.
`/settings/zabbix`(GET·PUT, POST도 저장): 화면에서 Zabbix 주소·읽기 토큰을 바꾼다
(DejaFix 계약 #28·#29). 로그인 세션 운영자·관리자만, 같이 설치된 DejaFix만,
DejaFix 관리자 계정(`DEJAFIX_ADMIN_*`)으로 넘긴다.

회사(11/25, 로드맵 6단계): 부르는 회사의 연결만 쓴다. 회사는 로그인 세션 토큰의 `cid`, 에이전트
토큰이면 `X-AX-Company` 머리글, 둘 다 없으면 회사 없음(연결 전, 11/26 — 캣북스로 떨어지지
않는다). 보관함은 그 회사 것을 읽고
(`tool_connections`가 요청 회사 폴더를 본다). 보관함에 없으면 환경변수 연결(같이 설치된 DejaFix,
이 설치의 것)을 쓴다(11/26: 고객사는 한 회사로 쓰고 그 회사 관리자가 화면에서 Zabbix를 넣는다).
다른 회사 보관함의 DejaFix는 쓰지 않는다. 로그인 토큰(세션)도 회사마다 따로 둔다.
둘 다 없으면 503·"연결 정보가 없어요"다.

회사별 DejaFix(11/26 예진 님 방향): DejaFix는 AXcellent 한 벌에 든 인프라 에이전트라, 하나가
회사마다 그 회사 관리자가 넣은 Zabbix를 보고 장애 기록도 회사별로 나눈다. 그래서 DejaFix로 가는
모든 요청에 부르는 회사(`X-AX-Company`)를 싣고, DejaFix는 거른 회사를 `X-DejaFix-Company`로
돌려준다. 같이 설치된 DejaFix가 그 머리를 돌려주지 않으면(회사별을 모르는 옛 버전) 그 DejaFix의
데이터는 캣북스 것이므로, 캣북스가 아닌 회사에는 장애·현황을 넘기지 않고("Zabbix 연결 전")
Zabbix 저장도 막는다(넣으면 캣북스 Zabbix를 덮는다). 회사 보관함의 DejaFix는 그 회사 것이라 그대로.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from ax_agent_template import (
    company_connection,
    company_scope,
    human_auth,
    pinned_http,
    tool_connections,
)
from ax_agent_template.companies import canonical_company_id
from ax_agent_template.config import refuse_mock_outside_tests
from ax_dejafix_proxy import auth

JSON = "application/json"
# 화면에서 Zabbix 연결을 바꿀 때 DejaFix에 로그인하는 관리자 계정 (viewer 계정과 따로, 서버 .env)
ADMIN_USER_ENV = "DEJAFIX_ADMIN_USERNAME"
ADMIN_PASSWORD_ENV = "DEJAFIX_ADMIN_PASSWORD"
# DejaFix로 보내는 부르는 회사, DejaFix가 돌려주는 "이 회사로 걸렀다" 머리 (회사별 DejaFix)
COMPANY_OUT = company_connection.CALLER_COMPANY_HEADER
COMPANY_ECHO = "X-DejaFix-Company"
NOT_SCOPED = (
    "이 회사의 Zabbix가 아직 연결되지 않았어요. 같이 설치된 DejaFix가 회사별 Zabbix를 지원하도록 "
    "올라가면 회원·서버 관리 › DejaFix 연결에서 넣을 수 있어요"
)


def _error(status: int, code: str, message: str) -> Response:
    body = json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False)
    return Response(content=body, status_code=status, media_type=JSON)


class DejaFixSession:
    """로그인 토큰을 한 번만 받아 두고, 만료(401)되면 다시 받는다."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        username: str | None,
        password: str | None,
        company: str = "",
    ):
        self._client = client
        self._company = company
        self._username = username
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._inflight = 0
        self._retired = False
        self._closing: asyncio.Task[None] | None = None

    @property
    def configured(self) -> bool:
        return bool(self._username and self._password)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _close_later(self) -> None:
        # 작업을 붙잡아 둬야 중간에 가비지 컬렉션되지 않고, 종료 때 기다릴 수 있다
        if self._closing is None:
            self._closing = asyncio.get_running_loop().create_task(self.aclose())

    def retire(self) -> None:
        """더 쓰지 않는 세션. 진행 중인 요청이 끝난 뒤에 클라이언트를 닫는다."""
        self._retired = True
        if self._inflight == 0:
            self._close_later()

    async def shutdown(self) -> None:
        """앱 종료: 닫는 중이면 기다리고, 아직 진행 중이라 안 닫았으면 지금 닫는다."""
        # 닫기 작업을 먼저 붙잡아 둬야 진행 중이던 get()의 finally가 닫기를 또 걸지 않는다
        self._close_later()
        assert self._closing is not None
        await self._closing

    async def _login(self) -> str | None:
        res = await self._client.post(
            "/api/auth/login", json={"username": self._username, "password": self._password}
        )
        if res.status_code != 200:
            return None
        return (res.json().get("data") or {}).get("token")

    def _auth(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        if self._company:
            headers[COMPANY_OUT] = self._company  # DejaFix가 이 회사 것만 돌려주게
        return headers

    async def get(self, path: str, query: str) -> httpx.Response:
        return await self.send("GET", path, query)

    async def send(
        self, method: str, path: str, query: str = "", body: dict | None = None
    ) -> httpx.Response:
        self._inflight += 1
        try:
            return await self._send(method, path, query, body)
        finally:
            self._inflight -= 1
            if self._retired and self._inflight == 0:
                self._close_later()

    async def _send(self, method: str, path: str, query: str, body: dict | None) -> httpx.Response:
        url = f"{path}?{query}" if query else path
        async with self._lock:
            if self._token is None:
                self._token = await self._login()
        res = await self._client.request(method, url, headers=self._auth(), json=body)
        if res.status_code == 401:
            async with self._lock:
                self._token = await self._login()
            res = await self._client.request(method, url, headers=self._auth(), json=body)
        return res


# (대상 주소, 계정, 비밀번호). 비밀번호는 로그·응답에 싣지 않는다
Target = tuple[str, str, str]


def caller_company(request: Request) -> str | None:
    """부르는 회사(정식 회사 ID). 세션 토큰은 `cid`, 그 밖(에이전트·공용 토큰)은 `X-AX-Session`의
    세션 → `X-AX-Company` 머리글. 모두 없으면 None: 어느 회사 연결도 쓰지 않는다(연결 전).

    예전에는 없으면 캣북스였다. 11/26부터 회사 없이 부르는 길은 캣북스로 떨어지지 않는다
    (DejaFix 데이터는 그 회사 회사 연결 설정의 Zabbix를 거쳐야 한다). 소속 회사 없는 계정도 None."""
    token = human_auth.bearer(request.headers.get("authorization")) or ""
    if human_auth.is_session_token(token):
        try:
            human = human_auth.verify_session(token, allow_no_company=True)
        except human_auth.HumanAuthError:
            return None  # check_caller가 먼저 거른다. 여기 오면 회사를 모르는 것으로 본다
        return human.company_id
    # 같은 출처 nginx가 읽기의 Authorization을 공용 토큰으로 바꿔 붙여서
    # X-AX-Session으로도 받는다(11/25).
    # 회사 고르기에만 쓰고, 검증에 실패하면 무시한다(권한은 Authorization)
    extra = human_auth.screen_session_token(None, request.headers.get(human_auth.SESSION_HEADER))
    if extra:
        try:
            return human_auth.verify_session(extra, allow_no_company=True).company_id
        except human_auth.HumanAuthError:
            pass
    header = request.headers.get(company_connection.CALLER_COMPANY_HEADER, "")
    return canonical_company_id(header.strip())


def company_scoped(company: str | None, from_vault: bool, res: httpx.Response) -> bool:
    """이 응답을 그 회사에 넘겨도 되는가.

    회사 보관함의 DejaFix는 그 회사 것, 캣북스는 같이 설치된 DejaFix의 원래 주인이라 그대로 된다.
    그 밖의 회사는 DejaFix가 그 회사로 걸렀다고 `X-DejaFix-Company`로 돌려줄 때만 된다.
    """
    if from_vault or company == company_scope.CATBOOKS:
        return True
    return res.headers.get(COMPANY_ECHO, "").strip() == company


def _target(company: str | None) -> tuple[Target, bool] | None:
    """부를 때마다 정한다: 그 회사 보관함 `dejafix` → 환경 변수(같이 설치된 DejaFix) → 없음(None).
    둘째 값: 보관함에서 왔는가."""
    if company is None:
        return None
    with company_scope.using(company):
        conn = tool_connections.find("dejafix")
    if conn is not None:
        url = str(conn.config.get("url") or "").strip().rstrip("/")
        user = str(conn.config.get("username") or "").strip()
        password = conn.secrets.get("password", "")
        if url and user and password:
            return (url, user, password), True
    # 같이 설치된 DejaFix(환경 변수)는 이 설치의 것이다. 보관함에 자기 DejaFix가 없는 회사는
    # 모두 이걸 쓴다(11/26 예진 님 결정: 고객사는 한 회사로 계속 쓰고, 관리자가 화면에서 넣은
    # Zabbix를 이 DejaFix가 본다. 테스트로 회사를 바꿔 넣으면 나중 것이 앞 것을 덮는다)
    user = os.getenv("DEJAFIX_USERNAME", "")
    password = os.getenv("DEJAFIX_PASSWORD", "")
    if user and password:
        url = os.getenv("DEJAFIX_PROXY_TARGET", "http://127.0.0.1:18000").strip().rstrip("/")
        return (url, user, password), False
    return None


def create_app(client: httpx.AsyncClient | None = None) -> FastAPI:
    """`client`를 주면(테스트) 대상 주소가 어떻든 그 클라이언트로 부른다."""
    state: dict[str, tuple[Target, DejaFixSession]] = {}
    retired: list[DejaFixSession] = []  # 종료 때 끝까지 닫는다

    def session_for(target: Target, guarded: bool = False, company: str = "") -> DejaFixSession:
        """회사마다 세션(로그인 토큰)을 따로 둔다. 대상·계정·비밀번호가 바뀌면 새로 만든다."""
        key = company or company_scope.CATBOOKS
        held = state.get(key)
        if held is not None and held[0] == target:
            return held[1]
        url, user, password = target
        # 화면에서 정한 주소(guarded)는 검사한 IP로만 붙고 리다이렉트를 따라가지 않는다
        transport = pinned_http.guarded_transport() if guarded else None
        http = client or httpx.AsyncClient(
            base_url=url, timeout=10.0, follow_redirects=False, transport=transport
        )
        fresh = DejaFixSession(http, user, password, key)
        if held is not None and client is None:
            held[1].retire()  # 진행 중인 요청이 있으면 끝난 뒤에 닫는다
            # 이미 닫힌 세션(비밀번호를 쥔)은 들고 있지 않는다
            retired[:] = [r for r in retired if r._closing is None or not r._closing.done()]
            retired.append(held[1])
        state[key] = (target, fresh)
        return fresh

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        refuse_mock_outside_tests(auth.MOCK_NAME)  # 목 모드는 토큰 없이 `human:`을 받으므로
        yield
        held = list(state.values()) + list(admin_state.values())
        state.clear()
        admin_state.clear()
        closing = [r.shutdown() for r in retired]
        retired.clear()
        if client is None:
            closing.extend(h[1].aclose() for h in held)
        # 하나가 실패해도 나머지는 끝까지 닫는다
        await asyncio.gather(*closing, return_exceptions=True)

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
        company = caller_company(request)
        found = _target(company)
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
            res = await session_for(target, from_vault, company or "").get(
                "/api/incidents", "status=problem&limit=1"
            )
        except httpx.HTTPError:
            return {"source": source, "reachable": False, "detail": "DejaFix에 연결할 수 없어요"}
        if not company_scoped(company, from_vault, res):
            # 같이 설치된 DejaFix가 회사별을 모른다: 이 회사 Zabbix는 아직 없다
            return {"source": source, "reachable": None, "detail": NOT_SCOPED}
        if res.status_code != 200:
            return {"source": source, "reachable": False, "detail": f"HTTP {res.status_code}"}
        return {"source": source, "reachable": True, "detail": "응답해요"}

    admin_state: dict[str, tuple[Target, DejaFixSession]] = {}

    def admin_session(target: Target, company: str) -> DejaFixSession:
        """DejaFix 관리자 계정 세션(viewer 세션과 따로). 같이 설치된 DejaFix만, 회사마다 따로."""
        held = admin_state.get(company)
        if held is not None and held[0] == target:
            return held[1]
        http = client or httpx.AsyncClient(base_url=target[0], timeout=20.0, follow_redirects=False)
        fresh = DejaFixSession(http, target[1], target[2], company)
        if held is not None and client is None:
            held[1].retire()
            retired.append(held[1])
        admin_state[company] = (target, fresh)
        return fresh

    async def zabbix_settings(request: Request, method: str) -> Response:
        """화면에서 Zabbix 연결 (DejaFix 계약 #28·#29). 운영자·관리자 세션만, 같이 설치된 DejaFix만.

        DejaFix에는 관리자 계정(`DEJAFIX_ADMIN_USERNAME`·`DEJAFIX_ADMIN_PASSWORD`)으로 넘긴다.
        Zabbix 토큰은 요청 본문으로만 넘기고 응답·로그에 싣지 않는다.
        """
        try:
            auth.check_admin_session(request.headers.get("authorization"))
        except auth.AuthFailure as exc:
            status = 403 if exc.code == "ADMIN_REQUIRED" else 401
            return _error(status, exc.code, exc.message)
        company = caller_company(request)
        found = _target(company)
        if found is None:
            return _error(503, "DEJAFIX_NOT_CONNECTED", "DejaFix가 연결되지 않았어요")
        target, from_vault = found
        if from_vault:
            return _error(
                409,
                "DEJAFIX_NOT_BUILTIN",
                "화면에서 Zabbix를 연결하는 건 AXcellent와 같이 설치된 DejaFix에서만 돼요",
            )
        user = os.getenv(ADMIN_USER_ENV, "").strip()
        password = os.getenv(ADMIN_PASSWORD_ENV, "")
        if not (user and password):
            return _error(
                503,
                "DEJAFIX_ADMIN_NOT_SET",
                f"서버 담당이 {ADMIN_USER_ENV}·{ADMIN_PASSWORD_ENV}를 넣어야 해요",
            )
        body: dict | None = None
        if method == "PUT":
            try:
                raw = await request.json()
            except ValueError:
                raw = None
            if not isinstance(raw, dict):
                return _error(422, "VALIDATION_ERROR", "본문은 {url, token, verify_tls}예요")
            url, token, verify = raw.get("url"), raw.get("token", ""), raw.get("verify_tls", True)
            if (
                not isinstance(url, str)
                or not isinstance(token, str)
                or not isinstance(verify, bool)
                or len(url) > 2048
                or len(token) > 512
            ):
                return _error(422, "VALIDATION_ERROR", "Zabbix 주소·토큰 형식이 맞지 않아요")
            body = {"url": url, "token": token, "verify_tls": verify}
        key = company or company_scope.CATBOOKS
        session = admin_session((target[0], user, password), key)
        try:
            upstream = await session.send("GET", "/api/settings/zabbix")
            if upstream.status_code != 404 and not company_scoped(company, False, upstream):
                # 회사별을 모르는 DejaFix: 그 설정은 캣북스 Zabbix다(읽지도 덮지도 않는다)
                return _error(409, "DEJAFIX_NO_COMPANY", NOT_SCOPED)
            if method == "PUT" and upstream.status_code != 404:
                upstream = await session.send(method, "/api/settings/zabbix", "", body)
        except httpx.HTTPError:
            return _error(502, "DEJAFIX_UNREACHABLE", "DejaFix 백엔드에 연결할 수 없습니다")
        if upstream.status_code == 404:
            return _error(
                501, "DEJAFIX_TOO_OLD", "이 DejaFix는 아직 화면에서 Zabbix를 바꾸지 못해요"
            )
        try:
            payload = upstream.json()
        except ValueError:
            return _error(502, "DEJAFIX_UNREACHABLE", "DejaFix 응답을 읽을 수 없습니다")
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            data.pop("token", None)  # 계약상 없지만 혹시 와도 내보내지 않는다
        return Response(
            content=json.dumps(payload, ensure_ascii=False),
            status_code=upstream.status_code,
            media_type=JSON,
        )

    @app.get("/settings/zabbix")
    async def get_zabbix(request: Request) -> Response:
        return await zabbix_settings(request, "GET")

    # 운영 서버 nginx가 PUT을 못 열 수 있어 POST도 같은 저장으로 받는다
    @app.api_route("/settings/zabbix", methods=["PUT", "POST"])
    async def put_zabbix(request: Request) -> Response:
        return await zabbix_settings(request, "PUT")

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
        company = caller_company(request)
        found = _target(company)
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
            upstream = await session_for(target, from_vault, company or "").get(
                full, request.url.query
            )
        except httpx.HTTPError:
            return _error(502, "DEJAFIX_UNREACHABLE", "DejaFix 백엔드에 연결할 수 없습니다")
        if not company_scoped(company, from_vault, upstream):
            # 같이 설치된 DejaFix가 회사별을 모른다: 그 장애는 캣북스 것이라 이 회사에 넘기지 않는다
            return _error(503, "DEJAFIX_NOT_CONNECTED", NOT_SCOPED)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", JSON),
        )

    return app


app = create_app()
