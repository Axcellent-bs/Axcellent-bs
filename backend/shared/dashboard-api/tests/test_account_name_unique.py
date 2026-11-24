"""같은 회사 안 계정 이름 중복 막기 (#1562).

대화 본인 확인이 계정 이름으로 돼 있어서, 같은 회사에 이름이 같은 계정이 둘이면 서로의 AI 대화를
읽을 수 있다. 만들기·이름 바꾸기에서 막고, 예전 파일에 중복이 있으면 시작 때 경고한다.
"""

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_dashboard_api import accounts
from ax_dashboard_api.main import app

BASE = "/api/dashboard/auth"
BOOT_EMAIL = "ops@example.com"
BOOT_PW = "boot-pass-for-tests"  # 테스트용 자리값
AT = "2026-11-20T00:00:00Z"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(company_list.COMPANIES_FILE_ENV, raising=False)
    monkeypatch.delenv(company_list.COMPANIES_DIR_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setenv(accounts.BOOTSTRAP_ENV, f"{BOOT_EMAIL}:{BOOT_PW}")
    monkeypatch.setattr(accounts, "_warned_duplicate_names", False)
    accounts.reset_failures_for_tests()
    human_auth.reset_revoked_for_tests()
    c = TestClient(app)
    c.headers.pop("authorization")  # conftest의 공용 토큰을 빼고 시작
    return c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _op(client: TestClient) -> str:
    res = client.post(BASE + "/login", json={"email": BOOT_EMAIL, "password": BOOT_PW})
    assert res.status_code == 200, res.text
    return res.json()["data"]["token"]


def _create(client: TestClient, op: str, name: str, email: str, role: str = "member"):
    body = {"name": name, "email": email, "role": role, "password": "member-pass-1234"}
    return client.post(BASE + "/accounts", json=body, headers=_bearer(op))


def _rows(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / accounts.ACCOUNTS_FILE).read_text(encoding="utf-8"))["accounts"]


def _id_of(tmp_path: Path, email: str) -> str:
    return next(r["id"] for r in _rows(tmp_path) if r["email"] == email)


def test_same_name_in_the_same_company_is_refused(client) -> None:
    op = _op(client)
    assert _create(client, op, "김준하", "a@example.com").status_code == 200
    for twin in ("김준하", " 김준하 ", "김준하"):
        res = _create(client, op, twin, f"b{len(twin)}@example.com")
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "NAME_TAKEN"


def test_name_compare_ignores_case_and_extra_spaces(client) -> None:
    op = _op(client)
    assert _create(client, op, "Kim  Junha", "a@example.com").status_code == 200
    res = _create(client, op, "kim junha", "b@example.com")
    assert res.status_code == 409 and res.json()["error"]["code"] == "NAME_TAKEN"


def test_rename_to_a_taken_name_is_refused_but_keeping_own_name_is_fine(client, tmp_path) -> None:
    op = _op(client)
    assert _create(client, op, "가나", "a@example.com").status_code == 200
    assert _create(client, op, "다라", "b@example.com").status_code == 200
    b_id = _id_of(tmp_path, "b@example.com")
    res = client.post(f"{BASE}/accounts/{b_id}/update", json={"name": "가나"}, headers=_bearer(op))
    assert res.status_code == 409 and res.json()["error"]["code"] == "NAME_TAKEN"
    assert next(r for r in _rows(tmp_path) if r["id"] == b_id)["name"] == "다라"  # 안 바뀜
    # 자기 이름으로 다시 저장(공백만 다름)은 된다
    res = client.post(
        f"{BASE}/accounts/{b_id}/update", json={"name": " 다라 "}, headers=_bearer(op)
    )
    assert res.status_code == 200, res.text


def test_same_name_in_different_companies_is_allowed(client, tmp_path) -> None:
    (tmp_path / "companies.json").write_text(
        json.dumps(
            {
                "companies": [
                    {"company_id": "catbooks", "name": "캣북스", "created_at": AT},
                    {"company_id": "nubi", "name": "누비투어", "created_at": AT},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    rows = [
        accounts._new_row("홍길동", "a@example.com", "member", "pw-for-tests-123", "catbooks"),
    ]
    nubi = accounts._new_row("홍길동", "b@example.com", "member", "pw-for-tests-123", "nubi")
    assert not accounts._name_taken(rows, nubi["name"], accounts._company_ids(nubi))
    same = accounts._new_row("홍길동", "c@example.com", "member", "pw-for-tests-123", "catbooks")
    assert accounts._name_taken(rows, same["name"], accounts._company_ids(same))


def test_old_file_with_duplicate_names_warns_once_without_the_name(
    client, tmp_path, caplog
) -> None:
    rows = [
        accounts._new_row("같은이름", "a@example.com", "member", "pw-for-tests-123"),
        accounts._new_row("같은이름", "b@example.com", "member", "pw-for-tests-123"),
    ]
    (tmp_path / accounts.ACCOUNTS_FILE).write_text(
        json.dumps({"accounts": rows}, ensure_ascii=False), encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger=accounts.logger.name):
        accounts.load_accounts()
        accounts.load_accounts()
    warned = [r for r in caplog.records if "#1562" in r.getMessage()]
    assert len(warned) == 1  # 한 번만
    assert "같은이름" not in warned[0].getMessage()  # 이름 값은 남기지 않는다
