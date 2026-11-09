import httpx
import pytest

from ax_dejafix_proxy.main import create_app

pytestmark = pytest.mark.asyncio


def _app_client(monkeypatch, handler, *, creds=True) -> httpx.AsyncClient:
    if creds:
        monkeypatch.setenv("DEJAFIX_USERNAME", "viewer")
        monkeypatch.setenv("DEJAFIX_PASSWORD", "pw")
    else:
        monkeypatch.delenv("DEJAFIX_USERNAME", raising=False)
        monkeypatch.delenv("DEJAFIX_PASSWORD", raising=False)
    upstream = httpx.AsyncClient(
        base_url="http://dejafix.test", transport=httpx.MockTransport(handler)
    )
    app = create_app(upstream)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")


class Upstream:
    def __init__(self, valid_token="t1"):
        self.valid_token = valid_token
        self.logins = 0
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            self.logins += 1
            return httpx.Response(200, json={"data": {"token": self.valid_token}})
        auth = request.headers.get("authorization")
        self.seen.append((request.url.path, request.url.query.decode(), auth))
        if auth != f"Bearer {self.valid_token}":
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
        return httpx.Response(200, json={"data": [{"id": 1}]})


async def test_get_logs_in_once_and_forwards_query(monkeypatch):
    up = Upstream()
    async with _app_client(monkeypatch, up) as c:
        for _ in range(2):
            res = await c.get("/api/incidents?status=problem")
            assert res.status_code == 200
            assert res.json() == {"data": [{"id": 1}]}
    assert up.logins == 1
    assert up.seen[0] == ("/api/incidents", "status=problem", "Bearer t1")


async def test_relogin_when_token_expired(monkeypatch):
    up = Upstream()
    async with _app_client(monkeypatch, up) as c:
        await c.get("/api/incidents")
        up.valid_token = "t2"
        res = await c.get("/api/incidents")
    assert res.status_code == 200
    assert up.logins == 2


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_only_get_is_forwarded(monkeypatch, method):
    up = Upstream()
    async with _app_client(monkeypatch, up) as c:
        res = await c.request(method, "/api/incidents")
    assert res.status_code == 405
    assert res.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert up.logins == 0 and not up.seen


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/me", "/docs", "/other"])
async def test_non_api_and_auth_paths_blocked(monkeypatch, path):
    up = Upstream()
    async with _app_client(monkeypatch, up) as c:
        res = await c.get(path)
    assert res.status_code == 404
    assert up.logins == 0 and not up.seen


async def test_unconfigured_credentials_503(monkeypatch):
    up = Upstream()
    async with _app_client(monkeypatch, up, creds=False) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DEJAFIX_NOT_CONFIGURED"
    assert up.logins == 0


async def test_unreachable_upstream_502(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("down")

    async with _app_client(monkeypatch, boom) as c:
        res = await c.get("/api/incidents")
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "DEJAFIX_UNREACHABLE"


async def test_healthz(monkeypatch):
    async with _app_client(monkeypatch, Upstream()) as c:
        assert (await c.get("/healthz")).json() == {"status": "ok"}
