"""인스타 레이더 계정: 화면에서 저장한 목록이 회사 설정 파일보다 먼저다.

data 레이더가 같은 파일을 읽는다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import tenant
from ax_dashboard_api.main import app

BASE = "/api/dashboard/instagram-radar"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(tenant.IG_RADAR_FILE_ENV, raising=False)
    trends = tmp_path / "trends.toml"
    trends.write_text('[instagram]\nradar_accounts = ["@catbooks_a", "catbooks_b"]\n', "utf-8")
    monkeypatch.setenv("DATA_TREND_CONFIG", str(trends))
    return TestClient(app)


def test_without_a_saved_list_the_tenant_file_is_used(client) -> None:
    data = client.get(BASE).json()["data"]
    assert data["source"] == "tenant" and data["accounts"] == ["catbooks_a", "catbooks_b"]
    assert data["tenant_count"] == 2 and data["editable"] is True


def test_a_saved_list_comes_first_and_is_cleaned(client, tmp_path) -> None:
    res = client.post(BASE + "/save", json={"accounts": [" @a.one ", "b_two", "A.ONE", ""]})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["source"] == "screen" and data["accounts"] == ["a.one", "b_two"]
    assert tenant.ig_radar_accounts() == ["a.one", "b_two"]
    assert (tmp_path / tenant.IG_RADAR_FILE).is_file()


def test_clear_goes_back_to_the_tenant_file(client) -> None:
    client.post(BASE + "/save", json={"accounts": ["x"]})
    data = client.post(BASE + "/clear").json()["data"]
    assert data["source"] == "tenant"
    assert tenant.ig_radar_accounts() is None


def test_bad_handles_and_too_many_are_refused(client) -> None:
    res = client.post(BASE + "/save", json={"accounts": ["good", "bad handle!"]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_HANDLE"
    many = [f"acct{i}" for i in range(201)]
    res = client.post(BASE + "/save", json={"accounts": many})
    assert res.status_code == 422 and res.json()["error"]["code"] == "TOO_MANY"


def test_auto_list_shows_without_saved_list_or_tenant_file(client, tmp_path, monkeypatch) -> None:
    # 누비투어처럼 trends.toml이 없는 회사: 경쟁사 찾기가 남긴 자동 목록을 보여 준다
    monkeypatch.delenv(tenant.IG_RADAR_AUTO_FILE_ENV, raising=False)
    monkeypatch.delenv("DATA_TREND_CONFIG")
    monkeypatch.delenv(tenant.TENANT_ENV, raising=False)
    assert client.get(BASE).json()["data"]["source"] == "none"
    auto = tmp_path / tenant.IG_RADAR_AUTO_FILE
    auto.write_text('{"accounts": ["hanatour", "modetour"], "industry": "여행·숙박"}', "utf-8")
    data = client.get(BASE).json()["data"]
    assert data["source"] == "auto" and data["accounts"] == ["hanatour", "modetour"]
    assert data["tenant_count"] == 0 and data["editable"] is True
    # 저장하면 화면 목록이 먼저, 지우면 자동으로 돌아간다
    saved = client.post(BASE + "/save", json={"accounts": ["mine"]}).json()["data"]
    assert saved["source"] == "screen"
    assert client.post(BASE + "/clear").json()["data"]["source"] == "auto"


def test_tenant_file_comes_before_the_auto_list(client, tmp_path) -> None:
    (tmp_path / tenant.IG_RADAR_AUTO_FILE).write_text('{"accounts": ["auto_a"]}', "utf-8")
    data = client.get(BASE).json()["data"]
    assert data["source"] == "tenant" and data["accounts"] == ["catbooks_a", "catbooks_b"]


@pytest.mark.parametrize("path", ["/save", "/clear"])
def test_writes_need_a_human_token(client, path) -> None:
    client.headers.pop("authorization")
    assert client.post(BASE + path, json={"accounts": ["x"]}).status_code == 401
