"""레포 연결이 공용 보관함의 GitHub 앱(`app_ref`)을 가리킨다 (도구 연결 설계 PR 6).

- 앱 정보는 보관함에서 읽고, 보관함이 바뀌면 부품을 다시 만든다
- 보관함에서 앱이 지워졌으면 다시 연결하라는 config 오류
- 예전 화면 연결의 앱 정보는 켤 때 한 번 보관함으로 옮긴다 (다시 켜도 같다)

실제 GitHub는 부르지 않는다. 키는 테스트 안에서 새로 만든다.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from ax_agent_template import tool_connections as tc
from dev_agent import vcs
from dev_agent.main import app
from dev_agent.vcs.github import GithubVcsAdapter
from dev_agent.vcs.github_app import GithubAppAuth

pytestmark = pytest.mark.role("dev")


def _pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _pem(KEY)
REPO = "acme/shop"


def _unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "tool_connections.json"
    monkeypatch.setenv(tc.FILE_ENV, str(path))
    monkeypatch.setenv("PLANNING_VCS_STORE", str(tmp_path / "vcs.json"))
    vcs._screen = None
    vcs.set_vcs(None)
    yield path
    vcs._screen = None
    vcs.set_vcs(None)


def _app(app_id: str = "1234", installation_id: str = "42", **config: str) -> tc.ToolConnection:
    return tc.upsert(
        "github_app",
        "회사 앱",
        {"app_id": app_id, "installation_id": installation_id, **config},
        {"private_key": PEM},
        conn_id=None,
        by="test",
    )


def _signed_by(jwt: str, key: rsa.RSAPrivateKey) -> dict:
    head, body, sig = jwt.split(".")
    key.public_key().verify(
        _unb64(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    return json.loads(_unb64(body))


def _gh(connection_id: str) -> tuple[GithubVcsAdapter, GithubAppAuth]:
    adapter = vcs.get_adapter(connection_id)
    assert isinstance(adapter, GithubVcsAdapter) and adapter._app is not None
    return adapter, adapter._app


def _legacy_store(path, **extra) -> None:
    row = {
        "id": "conn-1",
        "kind": "github",
        "repo": REPO,
        "auth": "app",
        "app_id": "1234",
        "installation_id": "42",
        "private_key": PEM,
        **extra,
    }
    path.write_text(json.dumps([row]), encoding="utf-8")


def test_app_ref_builds_adapter_from_vault_and_follows_changes(vault) -> None:
    row = _app(base_url="https://ghe.acme.test/api/v3")
    conn = vcs.upsert_connection(kind="github", repo=REPO, auth="app", app_ref=row.id)
    assert (conn.app_ref, conn.app_id, conn.installation_id, conn.private_key) == (
        row.id,
        "",
        "",
        "",
    )
    adapter, auth = _gh(conn.id)
    assert auth.installation_id == "42"
    assert _signed_by(auth.jwt(), KEY)["iss"] == "1234"
    # 레포 연결에 주소가 없으면 앱의 GitHub Enterprise 주소를 쓴다
    assert adapter.base_url == "https://ghe.acme.test/api/v3"
    assert vcs.get_adapter(conn.id) is adapter  # 그대로면 다시 쓴다
    # 보관함에서 앱 정보를 바꾸면 재시작 없이 새 부품
    tc.upsert(
        "github_app", "회사 앱", {"app_id": "999", "installation_id": "42"}, {},
        conn_id=row.id, by="test",
    )  # fmt: skip
    again, auth = _gh(conn.id)
    assert again is not adapter and auth.app_id == "999"


def test_missing_vault_app_is_clear_config_error(vault) -> None:
    row = _app()
    conn = vcs.upsert_connection(kind="github", repo=REPO, auth="app", app_ref=row.id)
    assert tc.delete(row.id, by="test")
    with pytest.raises(vcs.VcsError) as e:
        vcs.get_adapter(conn.id)
    assert e.value.code == "config" and e.value.message == vcs.APP_MISSING
    assert vcs.connections()[0].public()["app_name"] == ""


@pytest.mark.parametrize("ref", ["github_app-9", "dejafix-1"])
def test_upsert_rejects_unknown_or_other_kind_app_ref(vault, ref) -> None:
    tc.upsert(
        "dejafix", "장애", {"url": "https://dejafix.acme.test"}, {"password": "pw-secret"},
        conn_id=None, by="test",
    )  # fmt: skip
    with pytest.raises(vcs.VcsError) as e:
        vcs.upsert_connection(kind="github", repo=REPO, auth="app", app_ref=ref)
    assert e.value.code == "config" and e.value.message == vcs.APP_MISSING
    assert vcs.connections() == []


def test_api_takes_app_ref_and_returns_app_name_without_secrets(vault) -> None:
    row = _app()
    client = TestClient(app)
    r = client.post(
        "/api/planning/vcs/connections",
        json={"kind": "github", "repo": REPO, "auth": "app", "app_ref": row.id},
    )
    assert r.status_code == 200
    got = r.json()["data"][0]
    assert (got["app_ref"], got["app_name"], got["app_id"], got["installation_id"]) == (
        row.id,
        "회사 앱",
        "1234",
        "42",
    )
    assert got["key_set"] is True
    assert "PRIVATE KEY" not in r.text and "private_key" not in got
    bad = client.post(
        "/api/planning/vcs/connections",
        json={"kind": "github", "repo": REPO, "auth": "app", "app_ref": "nope"},
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_VCS"


def test_startup_moves_inline_app_into_vault_once(vault, tmp_path) -> None:
    store = tmp_path / "vcs.json"
    _legacy_store(store)
    assert vcs.load_saved() == 1
    rows = tc.read_all()
    assert [(r.kind, r.config, r.updated_by) for r in rows] == [
        ("github_app", {"app_id": "1234", "installation_id": "42"}, "migration")
    ]
    conn = vcs.connections()[0]
    assert (conn.app_ref, conn.private_key, conn.app_id) == (rows[0].id, "", "")
    assert "PRIVATE KEY" not in store.read_text(encoding="utf-8")
    _, auth = _gh(conn.id)
    assert _signed_by(auth.jwt(), KEY)["iss"] == "1234"
    # 다시 켜도 같은 상태 (새로 만들지 않는다)
    vcs._screen = None
    assert vcs.load_saved() == 1
    assert len(tc.read_all()) == 1 and vcs.connections()[0].app_ref == rows[0].id


def test_startup_links_existing_vault_app_instead_of_copying(vault, tmp_path) -> None:
    row = _app()
    _legacy_store(tmp_path / "vcs.json")
    vcs.load_saved()
    assert [r.id for r in tc.read_all()] == [row.id]
    assert vcs.connections()[0].app_ref == row.id


def test_startup_skips_move_without_vault(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.delenv(tc.FILE_ENV, raising=False)
    store = tmp_path / "vcs.json"
    monkeypatch.setenv("PLANNING_VCS_STORE", str(store))
    _legacy_store(store)
    vcs._screen = None
    vcs.set_vcs(None)
    try:
        with caplog.at_level(logging.WARNING, logger="dev_agent.vcs"):
            assert vcs.load_saved() == 1
        conn = vcs.connections()[0]
        assert conn.app_ref == "" and conn.private_key == PEM  # 예전 값으로 계속 돈다
        assert "PRIVATE KEY" in store.read_text(encoding="utf-8")
        assert any(tc.FILE_ENV in m for m in caplog.messages)
        assert "PRIVATE KEY" not in caplog.text
        _, auth = _gh(conn.id)
        assert auth.app_id == "1234"
    finally:
        vcs._screen = None
        vcs.set_vcs(None)


def test_startup_skips_move_when_vault_is_broken(vault, tmp_path) -> None:
    vault.write_text("{깨짐", encoding="utf-8")
    _legacy_store(tmp_path / "vcs.json")
    vcs.load_saved()
    conn = vcs.connections()[0]
    assert conn.app_ref == "" and conn.private_key == PEM
    assert vault.read_text(encoding="utf-8") == "{깨짐"  # 덮어쓰지 않는다
