"""호출자 토큰 확인과 허용 경로 (auth.py). DejaFix는 가짜 응답으로 대신한다."""

from __future__ import annotations

import json
from urllib.parse import unquote

import httpx
import pytest

from ax_dejafix_proxy.main import create_app

pytestmark = pytest.mark.asyncio

AGENT_TOKEN = "agent-token-infra"
HUMAN_TOKEN = "human-token-yejin"


class Upstream:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {"token": "jwt"}})
        self.seen.append(request.url.path)
        return httpx.Response(200, json={"data": []})


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEJAFIX_USERNAME", "viewer")
    monkeypatch.setenv("DEJAFIX_PASSWORD", "pw")
    monkeypatch.setenv("AX_AGENT_TOKENS", f"infra={AGENT_TOKEN},dev=data-via-nginx")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", f"yejin:{HUMAN_TOKEN},ghost:data-via-nginx")
    for name in ("AGENT_TEMPLATE_MOCK", "DEJAFIX_PROXY_MOCK"):
        monkeypatch.delenv(name, raising=False)


def _app(up: Upstream):
    upstream = httpx.AsyncClient(base_url="http://dejafix.test", transport=httpx.MockTransport(up))
    return create_app(upstream)


def _client(up: Upstream, token: str | None = AGENT_TOKEN) -> httpx.AsyncClient:
    # 회사 없이 부르면 연결 전이다(11/26). 이 시험들은 경로·토큰을 보므로 회사를 싣는다
    headers = {"Authorization": f"Bearer {token}", "X-AX-Company": "catbooks"} if token else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(up)), base_url="http://proxy", headers=headers
    )


async def _raw_get(up: Upstream, raw_path: bytes, token: str = AGENT_TOKEN) -> tuple[int, dict]:
    """httpx가 `..`·`%2e`를 고치지 않도록 ASGI로 바로 부른다.

    uvicorn처럼 raw_path를 그대로 준다.
    """
    path, _, query = raw_path.partition(b"?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(path.decode("latin-1")),
        "raw_path": path,
        "query_string": query,
        "root_path": "",
        "headers": [
            (b"host", b"proxy"),
            (b"authorization", f"Bearer {token}".encode()),
            (b"x-ax-company", b"catbooks"),  # 회사 없이 부르면 연결 전이다(11/26)
        ],
        "client": ("127.0.0.1", 1),
        "server": ("proxy", 80),
    }
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await _app(up)(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body or b"{}")


async def test_missing_token_401() -> None:
    up = Upstream()
    async with _client(up, token=None) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "AUTH_REQUIRED"
    assert not up.seen


@pytest.mark.parametrize("token", ["unknown-token", "data-via-nginx", "human:yejin", "mock:infra"])
async def test_unknown_token_401(token: str) -> None:
    up = Upstream()
    async with _client(up, token=token) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"
    assert not up.seen


async def test_auth_checked_before_method() -> None:
    up = Upstream()
    async with _client(up, token=None) as c:
        res = await c.post("/api/incidents")
    assert res.status_code == 401


async def test_mock_tokens_only_with_proxy_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEJAFIX_PROXY_MOCK", "1")
    up = Upstream()
    for token in ("human:yejin", "mock:infra"):
        async with _client(up, token=token) as c:
            assert (await c.get("/api/incidents")).status_code == 200


@pytest.mark.parametrize("token", [AGENT_TOKEN, HUMAN_TOKEN])
@pytest.mark.parametrize(
    "url, path",
    [
        ("/api/incidents?status=problem&limit=100", "/api/incidents"),
        ("/api/incidents/42", "/api/incidents/42"),
        ("/api/dashboard/summary", "/api/dashboard/summary"),
        ("/api/hosts", "/api/hosts"),
    ],
)
async def test_allowed_paths_pass(token: str, url: str, path: str) -> None:
    up = Upstream()
    async with _client(up, token=token) as c:
        res = await c.get(url)
    assert res.status_code == 200
    assert up.seen == [path]


@pytest.mark.parametrize(
    "url",
    [
        "/api/hosts/1",
        "/api/hosts/x/metrics",
        "/api/incidents/abc",
        "/api/incidents/1/reports",
        "/api/dashboard",
        "/api/dashboard/summary/x",
        "/api/auth/login",
        "/api/actions",
    ],
)
async def test_paths_outside_allow_list_404(url: str) -> None:
    up = Upstream()
    async with _client(up) as c:
        res = await c.get(url)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert not up.seen


@pytest.mark.parametrize(
    "raw",
    [
        b"/api/incidents/%2e%2e/auth/login",
        b"/api/incidents%2f1",
        b"/api/incidents/../auth/login",
        b"/api/incidents/./1",
        b"/api//incidents",
        b"/api/incidents\\1",
        b"/api/incidents/1\x00",
    ],
)
async def test_path_tricks_rejected(raw: bytes) -> None:
    up = Upstream()
    status, body = await _raw_get(up, raw)
    assert status == 400
    assert body["error"]["code"] == "INVALID_PATH"
    assert not up.seen


async def test_raw_path_with_query_passes() -> None:
    up = Upstream()
    status, _ = await _raw_get(up, b"/api/incidents?status=problem")
    assert status == 200
    assert up.seen == ["/api/incidents"]


# ---- 로그인 세션 토큰 (axs1., human_auth) ----

SECRET = "s" * 40


@pytest.fixture
def sessions(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from ax_agent_template import human_auth

    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, SECRET)
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.setenv(human_auth.GENERATIONS_FILE_ENV, str(tmp_path / "gen.json"))
    human_auth.reset_revoked_for_tests()
    yield human_auth
    human_auth.reset_revoked_for_tests()


@pytest.mark.parametrize("role", ["operator", "admin", "member"])
@pytest.mark.parametrize(
    "url",
    [
        "/api/incidents?status=problem&limit=100",
        "/api/incidents?limit=100",
        "/api/incidents/42",
        "/api/dashboard/summary",
    ],
)
async def test_session_token_every_role_passes(sessions, role: str, url: str) -> None:
    """화면이 부르는 조회는 관리자·일반회원 세션 모두 통과한다 (읽기라 역할을 가리지 않는다)."""
    token, _ = sessions.issue_session(account_id=f"acc-{role}", name=f"{role}-user", role=role)
    up = Upstream()
    async with _client(up, token=token) as c:
        res = await c.get(url)
    assert res.status_code == 200
    assert len(up.seen) == 1


@pytest.mark.parametrize("role", ["admin", "member"])
async def test_session_token_reads_status(sessions, role: str) -> None:
    token, _ = sessions.issue_session(account_id=f"acc-{role}", name="u", role=role)
    async with _client(Upstream(), token=token) as c:
        res = await c.get("/status")
    assert res.status_code == 200
    assert res.json()["source"] == "builtin"


async def test_status_requires_token() -> None:
    up = Upstream()
    async with _client(up, token=None) as c:
        res = await c.get("/status")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "AUTH_REQUIRED"
    assert not up.seen


async def test_healthz_without_token() -> None:
    async with _client(Upstream(), token=None) as c:
        res = await c.get("/healthz")
    assert res.status_code == 200


async def test_session_token_without_secret_rejected(
    monkeypatch: pytest.MonkeyPatch, sessions
) -> None:
    token, _ = sessions.issue_session(account_id="acc", name="u", role="member")
    monkeypatch.delenv(sessions.SESSION_SECRET_ENV)
    up = Upstream()
    async with _client(up, token=token) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"
    assert not up.seen


async def test_forged_session_token_rejected(sessions) -> None:
    token, _ = sessions.issue_session(account_id="acc", name="u", role="member")
    head, body, _sig = token.split(".")
    up = Upstream()
    async with _client(up, token=f"{head}.{body}.forged") as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"
    assert not up.seen


async def test_expired_session_token_401(sessions) -> None:
    token, _ = sessions.issue_session(account_id="acc", name="u", role="member", now=1, ttl=1)
    up = Upstream()
    async with _client(up, token=token) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "SESSION_EXPIRED"
    assert not up.seen


async def test_logged_out_session_token_401(sessions) -> None:
    token, _ = sessions.issue_session(account_id="acc", name="u", role="admin")
    sessions.revoke_session(sessions.verify_session(token))
    up = Upstream()
    async with _client(up, token=token) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "SESSION_REVOKED"


async def test_shared_human_token_off_when_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_ALLOW_SHARED_HUMAN_TOKEN", "0")
    async with _client(Upstream(), token=HUMAN_TOKEN) as c:
        assert (await c.get("/api/incidents")).status_code == 401
    async with _client(Upstream(), token=AGENT_TOKEN) as c:  # 에이전트 토큰은 그대로
        assert (await c.get("/api/incidents")).status_code == 200
