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
    assert data["connections"][0]["secrets_set"] == {"api_token": "jenk••••"}


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
