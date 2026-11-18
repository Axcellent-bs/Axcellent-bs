"""연결 키: 운영팀이 발급하고 고객사가 넣어 회사를 연결한다. 서버에 저장해 어느 PC에서든 같다.

전체 키는 발급할 때 한 번만 준다. 목록에는 가린 키만 나온다. 쓰기는 사람 토큰.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_dashboard_api import connection_keys as ck
from ax_dashboard_api.main import app

BASE = "/api/dashboard/connection-keys"
INFO = {"name": "캣북스", "industry": "온라인 쇼핑몰", "network": "192.168.10.0/24"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(ck.KEYS_FILE_ENV, raising=False)
    return TestClient(app)


def _issue(client: TestClient, memo: str = "캣북스 IT") -> dict:
    res = client.post(BASE + "/issue", json={"memo": memo})
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_issue_gives_the_full_key_once_and_lists_it_masked(client, tmp_path) -> None:
    fresh = _issue(client)
    key = fresh["key"]
    assert key.startswith("AXC-") and len(key) == 18
    state = client.get(BASE).json()["data"]
    assert state["company"] is None and state["editable"] is True
    [row] = state["keys"]
    assert row["status"] == "issued" and row["memo"] == "캣북스 IT"
    assert key not in json.dumps(state)
    assert row["key"].startswith(key[:8]) and row["id"] == fresh["id"]
    assert (tmp_path / ck.KEYS_FILE).is_file()


def test_connect_with_a_key_saves_the_company_for_every_pc(client) -> None:
    key = _issue(client)["key"]
    res = client.post(BASE + "/connect", json={"key": key.lower(), **INFO})
    assert res.status_code == 200, res.text
    state = TestClient(app).get(BASE).json()["data"]  # 다른 PC
    assert state["company"]["name"] == "캣북스" and state["company"]["network"] == INFO["network"]
    assert state["keys"][0]["status"] == "connected"
    assert state["keys"][0]["company"]["name"] == "캣북스"


def test_check_says_why_a_key_cannot_be_used(client) -> None:
    res = client.post(BASE + "/check", json={"key": "AXC-AAAA-BBBB-CCCC"})
    assert res.json()["data"]["ok"] is False
    key = _issue(client)["key"]
    assert client.post(BASE + "/check", json={"key": key}).json()["data"]["ok"] is True
    client.post(BASE + "/revoke", json={"id": client.get(BASE).json()["data"]["keys"][0]["id"]})
    data = client.post(BASE + "/check", json={"key": key}).json()["data"]
    assert data["ok"] is False and "폐기" in data["reason"]


def test_a_revoked_key_cannot_connect_and_revoking_disconnects(client) -> None:
    fresh = _issue(client)
    client.post(BASE + "/connect", json={"key": fresh["key"], **INFO})
    client.post(BASE + "/revoke", json={"id": fresh["id"]})
    state = client.get(BASE).json()["data"]
    assert state["company"] is None and state["keys"][0]["status"] == "revoked"
    res = client.post(BASE + "/connect", json={"key": fresh["key"], **INFO})
    assert res.status_code == 422 and res.json()["error"]["code"] == "KEY_REVOKED"


def test_reconnecting_with_another_key_frees_the_old_one(client) -> None:
    a = _issue(client, "a")["key"]
    b = _issue(client, "b")["key"]
    client.post(BASE + "/connect", json={"key": a, **INFO})
    client.post(BASE + "/connect", json={"key": b, **INFO})
    rows = {r["memo"]: r["status"] for r in client.get(BASE).json()["data"]["keys"]}
    assert rows == {"a": "issued", "b": "connected"}


def test_update_info_and_disconnect(client) -> None:
    key = _issue(client)["key"]
    client.post(BASE + "/connect", json={"key": key, **INFO})
    res = client.post(BASE + "/company", json={**INFO, "name": "캣북스 본사"})
    assert res.json()["data"]["company"]["name"] == "캣북스 본사"
    state = client.post(BASE + "/disconnect").json()["data"]
    assert state["company"] is None and state["keys"][0]["status"] == "issued"


def test_company_info_needs_a_name(client) -> None:
    key = _issue(client)["key"]
    res = client.post(BASE + "/connect", json={"key": key, **{**INFO, "name": " "}})
    assert res.status_code == 422


@pytest.mark.parametrize(
    "path", ["/issue", "/check", "/connect", "/revoke", "/company", "/disconnect"]
)
def test_writes_need_a_human_token(client, path) -> None:
    client.headers.pop("authorization")
    assert client.post(BASE + path, json={}).status_code == 401


def test_without_the_connection_file_env_it_is_read_only(client, monkeypatch) -> None:
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV)
    assert client.get(BASE).json()["data"] == {"keys": [], "company": None, "editable": False}
    res = client.post(BASE + "/issue", json={})
    assert res.status_code == 409
