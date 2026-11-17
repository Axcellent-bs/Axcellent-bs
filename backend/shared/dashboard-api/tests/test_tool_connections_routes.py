"""도구 연결 API: 목록은 비밀 없이, 저장은 확인을 모두 통과해야, 쓰기는 사람 토큰만."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import tool_connections as store
from ax_dashboard_api import tool_connections as routes
from ax_dashboard_api.company_connection import Check
from ax_dashboard_api.main import app

BASE = "/api/dashboard/tool-connections"
SECRET = "jenkins-api-token-xyz"
JENKINS = {
    "url": "http://10.20.30.40:8080",
    "username": "bot",
    "job": "deploy",
    "service": "shop-web",
}


def _body(**over):
    body = {
        "kind": "jenkins",
        "name": "젠킨스",
        "config": dict(JENKINS),
        "secrets": {"api_token": SECRET},
    }
    body.update(over)
    return body


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv(store.FILE_ENV, str(tmp_path / "tool_connections.json"))
    monkeypatch.setattr(store, "_cache", None)
    seen: list[dict] = []
    passing = [True]

    async def fake(kind, config, secrets, *, lookup):  # noqa: ANN001, ANN202
        seen.append({"kind": kind, "config": config, "secrets": secrets})
        return [Check("job", "작업", passing[0], "읽었어요" if passing[0] else "인증 실패")]

    monkeypatch.setattr(routes, "run_checks", fake)
    seen.append({"_passing": passing})
    return seen


def _fail(calls) -> None:
    calls[0]["_passing"][0] = False


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_list_has_fields_and_no_secrets(calls, client) -> None:
    assert client.post(BASE, json=_body()).status_code == 200
    data = client.get(BASE).json()["data"]
    assert set(data["fields"]) == {"github_app", "github_actions", "jenkins", "dejafix"}
    assert data["fields"]["jenkins"][2] == {
        "key": "api_token",
        "label": "API 토큰",
        "secret": True,
        "required": True,
        "help": "",
    }
    assert len(data["connections"]) == 1
    assert SECRET not in client.get(BASE).text
    assert data["connections"][0]["secrets_set"] == {"api_token": "••••"}


def test_create_runs_checks_and_saves(calls, client) -> None:
    res = client.post(BASE, json=_body())
    data = res.json()["data"]
    assert data["connection"]["id"] == "jenkins-1" and data["connection"]["check_ok"] is True
    assert data["checks"][0]["ok"] is True
    assert calls[1]["secrets"] == {"api_token": SECRET}
    assert store.get("jenkins-1") is not None


def test_failed_check_does_not_save(calls, client) -> None:
    _fail(calls)
    res = client.post(BASE, json=_body())
    err = res.json()["error"]
    assert res.status_code == 422 and err["code"] == "CHECK_FAILED"
    assert err["checks"][0]["detail"] == "인증 실패" and "인증 실패" in err["message"]
    assert store.read_all() == []


def test_test_endpoint_does_not_save(calls, client) -> None:
    data = client.post(BASE + "/test", json=_body()).json()["data"]
    assert data["ok"] is True and store.read_all() == []


def test_update_with_blank_secret_uses_saved_secret(calls, client) -> None:
    client.post(BASE, json=_body())
    res = client.put(BASE + "/jenkins-1", json=_body(name="새 이름", secrets={"api_token": ""}))
    assert res.status_code == 200
    assert calls[-1]["secrets"] == {"api_token": SECRET}
    saved = store.get("jenkins-1")
    assert saved is not None and saved.name == "새 이름" and saved.secrets["api_token"] == SECRET
    # 저장 전 확인(/test)도 이전 비밀로
    client.post(BASE + "/test", json=_body(secrets={"api_token": ""}, id="jenkins-1"))
    assert calls[-1]["secrets"] == {"api_token": SECRET}


def test_recheck_records_result(calls, client) -> None:
    client.post(BASE, json=_body())
    _fail(calls)
    data = client.post(BASE + "/jenkins-1/check").json()["data"]
    assert data["ok"] is False and data["connection"]["check_ok"] is False
    rechecked = store.get("jenkins-1")
    assert rechecked is not None and rechecked.check_detail == "작업: 인증 실패"
    assert client.post(BASE + "/nope-1/check").status_code == 404


def _app_body():
    return {
        "kind": "github_app",
        "name": "앱",
        "config": {"app_id": "1", "installation_id": "2"},
        "secrets": {"private_key": "-----BEGIN RSA PRIVATE KEY-----x"},
    }


def _actions_body(service="shop-web"):
    return {
        "kind": "github_actions",
        "name": "액션",
        "config": {"app": "github_app-1", "repo": "a/b", "workflow": "d.yml", "service": service},
        "secrets": {},
    }


def test_delete_app_in_use_is_409(calls, client) -> None:
    assert client.post(BASE, json=_app_body()).status_code == 200
    assert client.post(BASE, json=_actions_body()).status_code == 200
    res = client.delete(BASE + "/github_app-1")
    assert res.status_code == 409 and res.json()["error"]["code"] == "IN_USE"
    assert len(store.read_all()) == 2
    assert client.delete(BASE + "/github_actions-1").status_code == 200
    assert client.delete(BASE + "/github_app-1").status_code == 200
    assert client.delete(BASE + "/github_app-1").status_code == 404


def test_second_deploy_tool_for_same_service_is_409(calls, client) -> None:
    client.post(BASE, json=_body())
    res = client.post(BASE, json=_actions_body("shop-web"))
    assert res.status_code == 409 and res.json()["error"]["code"] == "SERVICE_TAKEN"
    assert client.post(BASE, json=_actions_body("other")).status_code == 200
    # 같은 연결을 고치는 것은 막지 않는다
    assert client.put(BASE + "/jenkins-1", json=_body()).status_code == 200


def test_dejafix_only_one(calls, client) -> None:
    body = {
        "kind": "dejafix",
        "name": "DejaFix",
        "config": {"url": "http://10.0.0.9:18000", "username": "viewer"},
        "secrets": {"password": "pw-secret-1"},
    }
    assert client.post(BASE, json=body).status_code == 200
    res = client.post(BASE, json=body)
    assert res.status_code == 409 and res.json()["error"]["code"] == "ALREADY_CONNECTED"


def test_write_needs_human_token(calls, client) -> None:
    c = TestClient(app)
    c.headers.pop("authorization")
    assert c.post(BASE, json=_body()).status_code == 401
    assert c.delete(BASE + "/jenkins-1").status_code == 401
    assert c.get(BASE).status_code == 200


def test_private_url_policy_applies_to_jenkins(calls, client, monkeypatch) -> None:
    for url in ("http://169.254.169.254/", "https://user:pw@10.0.0.1/", "ftp://10.0.0.1"):
        cfg = {**JENKINS, "url": url}
        res = client.post(BASE, json=_body(config=cfg))
        assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_URL", url
    assert len(calls) == 1  # 확인은 한 번도 부르지 않았다


def test_missing_required_field_is_422(calls, client) -> None:
    res = client.post(BASE, json=_body(secrets={}))
    assert res.status_code == 422 and res.json()["error"]["code"] == "MISSING_FIELD"


def test_broken_store_is_409(calls, client, tmp_path) -> None:
    (tmp_path / "tool_connections.json").write_text("{broken", encoding="utf-8")
    res = client.post(BASE, json=_body())
    assert res.status_code == 409 and res.json()["error"]["code"] == "STORE_UNAVAILABLE"


# 저장된 비밀을 다른 곳으로 보내지 못하게: 주소·계정이 바뀌면 비밀을 다시 받는다
@pytest.fixture
def wire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """진짜 확인을 돌리고 전송 계층에 닿은 요청을 모은다."""
    import httpx

    from ax_dashboard_api import tool_checks

    monkeypatch.setenv(store.FILE_ENV, str(tmp_path / "tool_connections.json"))
    monkeypatch.setattr(store, "_cache", None)
    reached: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        return httpx.Response(404, json={})

    monkeypatch.setattr(tool_checks, "TRANSPORT", httpx.MockTransport(handle))
    return reached


def _seed(kind: str, config: dict, secrets: dict) -> None:
    store.upsert(kind, "저장", config, secrets, conn_id=None, by="t")


CASES = [
    (
        "jenkins",
        JENKINS,
        {"api_token": SECRET},
        {**JENKINS, "url": "http://10.99.99.99:8080"},
    ),
    (
        "jenkins",
        JENKINS,
        {"api_token": SECRET},
        {**JENKINS, "username": "other"},
    ),
    (
        "dejafix",
        {"url": "http://10.0.0.9:18000", "username": "viewer"},
        {"password": "pw-secret-1"},
        {"url": "http://10.9.9.9:18000", "username": "viewer"},
    ),
    (
        "github_app",
        {"app_id": "1", "installation_id": "2"},
        {"private_key": "-----BEGIN RSA PRIVATE KEY-----x"},
        {"app_id": "1", "installation_id": "2", "base_url": "http://10.9.9.9"},
    ),
    (
        "github_app",
        {"app_id": "1", "installation_id": "2"},
        {"private_key": "-----BEGIN RSA PRIVATE KEY-----x"},
        {"app_id": "9", "installation_id": "2"},
    ),
]


@pytest.mark.parametrize(("kind", "saved_config", "saved_secrets", "new_config"), CASES)
def test_test_with_id_and_new_endpoint_requires_secret(
    wire, client, kind, saved_config, saved_secrets, new_config
) -> None:
    _seed(kind, saved_config, saved_secrets)
    body = {"kind": kind, "name": "x", "config": new_config, "secrets": {}, "id": f"{kind}-1"}
    res = client.post(BASE + "/test", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "SECRET_REQUIRED"
    assert wire == []


@pytest.mark.parametrize(("kind", "saved_config", "saved_secrets", "new_config"), CASES)
def test_put_with_new_endpoint_requires_secret(
    wire, client, kind, saved_config, saved_secrets, new_config
) -> None:
    _seed(kind, saved_config, saved_secrets)
    body = {"kind": kind, "name": "x", "config": new_config, "secrets": {}}
    res = client.put(f"{BASE}/{kind}-1", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "SECRET_REQUIRED"
    assert wire == []
    saved = store.get(f"{kind}-1")
    assert saved is not None and saved.config == saved_config


def test_new_endpoint_with_new_secret_is_allowed(wire, client) -> None:
    _seed("jenkins", JENKINS, {"api_token": SECRET})
    body = _body(config={**JENKINS, "url": "http://10.99.99.99:8080"}, secrets={"api_token": "n"})
    res = client.post(BASE + "/test", json={**body, "id": "jenkins-1"})
    assert res.status_code == 200 and len(wire) == 1 and "10.99.99.99" in wire[0]


def test_github_app_default_base_url_is_same_as_missing(calls, client) -> None:
    client.post(BASE, json=_app_body())
    body = _app_body()
    body["config"]["base_url"] = "https://api.github.com/"
    body["secrets"] = {}
    assert client.put(BASE + "/github_app-1", json=body).status_code == 200


def test_recheck_of_stored_link_local_url_makes_no_request(wire, client) -> None:
    _seed("jenkins", {**JENKINS, "url": "http://169.254.169.254"}, {"api_token": SECRET})
    data = client.post(BASE + "/jenkins-1/check").json()["data"]
    assert data["ok"] is False and "INVALID_URL" in data["checks"][0]["detail"]
    assert wire == []


def test_deploy_kinds_have_optional_components_field(calls, client) -> None:
    fields = client.get(BASE).json()["data"]["fields"]
    for kind in ("github_actions", "jenkins"):
        comp = next(f for f in fields[kind] if f["key"] == "components")
        assert comp["required"] is False and comp["secret"] is False
        assert "DejaFix component" in comp["help"] and "서비스 이름" in comp["help"]
        service = next(f for f in fields[kind] if f["key"] == "service")
        assert "롤백" in service["help"]
    base_url = next(f for f in fields["github_app"] if f["key"] == "base_url")
    assert "https://<host>/api/v3" in base_url["help"]


def test_components_are_kept_and_blank_is_dropped(calls, client) -> None:
    body = _body(config={**JENKINS, "components": " payment, api "})
    saved = client.post(BASE, json=body).json()["data"]["connection"]
    assert saved["config"]["components"] == "payment, api"
    blank = routes.ToolConnectionIn(**_body(config={**JENKINS, "components": " "}))
    config, _ = routes._clean(blank)
    assert "components" not in config
