import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas.marketing_channels import ChannelFollowersOutput
from marketing_agent import channel_followers as cf
from marketing_agent import handlers
from marketing_agent.main import app

client = TestClient(app)
SECRET = "SECRET_TOKEN_VALUE"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    monkeypatch.setenv("MARKETING_FOLLOWER_CHANNELS", "instagram")
    monkeypatch.setenv("MARKETING_SNS_PROVIDER", "instagram")
    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", SECRET)
    monkeypatch.setenv("MARKETING_IG_USER_ID", "1789")
    monkeypatch.setenv("MARKETING_IG_API_VERSION", "v27.0")
    monkeypatch.setenv("MARKETING_IG_API_BASE_URL", "https://graph.example.test")


def _http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(http: httpx.AsyncClient) -> ChannelFollowersOutput:
    return asyncio.run(cf.channel_followers(http))


def _call(requester: str = "planning") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_inc_0001",
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "channel_followers",
        "input": {},
    }
    return client.post("/a2a", json=body).json()


def test_known_reads_followers_count() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"followers_count": 321, "id": "1789"})

    out = _run(_http(handler))
    assert out.status == "known" and out.reason is None
    assert [(c.channel, c.followers) for c in out.channels] == [("instagram", 321)]
    assert out.channels[0].at is not None
    assert seen[0].url.path == "/v27.0/1789"
    assert seen[0].url.params["fields"] == "followers_count"


def test_zero_followers_is_known_not_unknown() -> None:
    out = _run(_http(lambda r: httpx.Response(200, json={"followers_count": 0})))
    assert out.status == "known" and out.channels[0].followers == 0


def test_no_channels_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKETING_FOLLOWER_CHANNELS")
    out = _run(_http(lambda r: pytest.fail("호출하면 안 된다")))
    assert out.status == "unknown" and out.channels == [] and out.reason


def test_unsupported_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_FOLLOWER_CHANNELS", "instagram,youtube")
    out = _run(_http(lambda r: pytest.fail("호출하면 안 된다")))
    assert out.status == "unknown" and "youtube" in (out.reason or "")


@pytest.mark.parametrize("drop", ["MARKETING_IG_ACCESS_TOKEN", "MARKETING_IG_USER_ID"])
def test_no_connection_config(monkeypatch: pytest.MonkeyPatch, drop: str) -> None:
    monkeypatch.delenv(drop)
    out = _run(_http(lambda r: pytest.fail("호출하면 안 된다")))
    assert out.status == "unknown" and drop in (out.reason or "")


def test_provider_mock_is_no_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_SNS_PROVIDER", "mock")
    out = _run(_http(lambda r: pytest.fail("호출하면 안 된다")))
    assert out.status == "unknown" and out.channels == []


def test_api_error_is_unknown_without_secret() -> None:
    body = {"error": {"code": 190, "message": f"bad {SECRET}"}}
    out = _run(_http(lambda r: httpx.Response(400, json=body)))
    assert out.status == "unknown" and "190" in (out.reason or "")
    assert SECRET not in json.dumps(out.model_dump(mode="json"))


def test_network_failure_is_unknown_without_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    out = _run(_http(handler))
    assert out.status == "unknown" and SECRET not in (out.reason or "")


@pytest.mark.parametrize("payload", [{}, {"followers_count": "12"}, {"followers_count": -1}])
def test_bad_payload_is_unknown(payload: dict) -> None:
    out = _run(_http(lambda r: httpx.Response(200, json=payload)))
    assert out.status == "unknown" and out.channels == []


def test_skill_requester_planning_only_and_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(client=None) -> ChannelFollowersOutput:
        return ChannelFollowersOutput(status="unknown", reason="테스트")

    monkeypatch.setattr(handlers, "_channel_followers", fake)
    ok = _call("planning")
    assert ok["status"] == "completed"
    assert ChannelFollowersOutput.model_validate(ok["output"]).status == "unknown"
    for other in ("support", "finance", "data"):
        denied = _call(other)
        assert denied["status"] != "completed", other
