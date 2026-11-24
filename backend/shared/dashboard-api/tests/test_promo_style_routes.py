"""회사 홍보 영상 방향 API (11/24 "재미 먼저, 브랜드는 끝에").

- 보기는 사람 토큰이면 누구나, 저장·되돌리기는 고객사 관리자만 (회원·운영자 403)
- 회사 이름으로 나눠 저장한다: 다른 회사 값은 읽지도 덮지도 않는다
- 저장한 값을 data 트렌드 보고서가 읽는 `tenant.promo()`가 그대로 본다
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_agent_template import tenant as tenants
from ax_dashboard_api import accounts
from ax_dashboard_api.main import app

URL = "/api/dashboard/promo-style"
ROWS = [
    {"id": "a-1", "name": "민지", "email": "m@x.test", "role": "admin"},
    {"id": "a-3", "name": "회원", "email": "h@x.test", "role": "member"},
    {"id": "a-4", "name": "총괄", "email": "o@x.test", "role": "operator"},
]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(tenants.TENANT_ENV, "catbooks")
    monkeypatch.delenv(tenants.TENANTS_DIR_ENV, raising=False)
    monkeypatch.delenv(tenants.PROMO_FILE_ENV, raising=False)
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    human_auth.reset_revoked_for_tests()
    return tmp_path


@pytest.fixture
def people(env: Path) -> dict[str, dict[str, str]]:
    (env / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    out = {}
    for row in ROWS:
        token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])
        out[row["id"]] = {"Authorization": f"Bearer {token[0]}"}
    return out


@pytest.fixture
def client(env: Path) -> TestClient:
    return TestClient(app)


def _data(res: Any) -> dict[str, Any]:
    assert res.status_code == 200, res.text
    return res.json()["data"]


def test_read_shows_tenant_file_values_and_sources(
    client: TestClient, people: dict[str, dict[str, str]]
) -> None:
    got = _data(client.get(URL, headers=people["a-3"]))  # 회원도 본다
    assert (got["promo_style"], got["brand_exposure"]) == ("fun_first", "end_card")
    assert got["source"] == {"promo_style": "tenant", "brand_exposure": "tenant"}
    assert got["editable"] is True and got["defaults"] == tenants.PROMO_DEFAULTS


def test_admin_saves_and_data_reads_the_same_value(
    client: TestClient, env: Path, people: dict[str, dict[str, str]]
) -> None:
    body = {"promo_style": "product", "brand_exposure": "caption"}
    got = _data(client.post(f"{URL}/save", json=body, headers=people["a-1"]))
    assert (got["promo_style"], got["brand_exposure"]) == ("product", "caption")
    assert got["source"] == {"promo_style": "screen", "brand_exposure": "screen"}
    assert tenants.promo() == body  # data 트렌드 보고서가 읽는 값
    saved = json.loads((env / "promo_style.json").read_text(encoding="utf-8"))
    assert saved == {"companies": {"catbooks": body}}
    got = _data(client.post(f"{URL}/clear", headers=people["a-1"]))
    assert got["source"]["promo_style"] == "tenant"
    assert tenants.promo()["promo_style"] == "fun_first"


@pytest.mark.parametrize("who", ["a-3", "a-4"])
def test_member_and_operator_cannot_save(client: TestClient, people: dict[str, dict[str, str]],
                                         who: str) -> None:  # fmt: skip
    body = {"promo_style": "product", "brand_exposure": "caption"}
    assert client.post(f"{URL}/save", json=body, headers=people[who]).status_code == 403
    assert client.post(f"{URL}/clear", headers=people[who]).status_code == 403
    assert tenants.promo()["promo_style"] == "fun_first"


@pytest.mark.parametrize("body", [
    {"promo_style": "funny", "brand_exposure": "end_card"},
    {"promo_style": "fun_first", "brand_exposure": "logo"},
    {"promo_style": "fun_first", "brand_exposure": "end_card", "extra": 1},
])  # fmt: skip
def test_unknown_values_are_rejected(client: TestClient, people: dict[str, dict[str, str]],
                                     body: dict[str, Any]) -> None:  # fmt: skip
    assert client.post(f"{URL}/save", json=body, headers=people["a-1"]).status_code == 422


def test_other_company_values_are_kept_and_not_read(
    client: TestClient, env: Path, people: dict[str, dict[str, str]]
) -> None:
    nubi = {"promo_style": "product", "brand_exposure": "prop"}
    (env / "promo_style.json").write_text(json.dumps({"companies": {"nubi": nubi}}), "utf-8")
    got = _data(client.get(URL, headers=people["a-1"]))
    assert got["promo_style"] == "fun_first"  # 누비투어 값은 캣북스에 안 보인다
    body = {"promo_style": "fun_first", "brand_exposure": "caption"}
    _data(client.post(f"{URL}/save", json=body, headers=people["a-1"]))
    saved = json.loads((env / "promo_style.json").read_text(encoding="utf-8"))
    assert saved["companies"] == {"nubi": nubi, "catbooks": body}  # 다른 회사 칸은 그대로
    _data(client.post(f"{URL}/clear", headers=people["a-1"]))
    saved = json.loads((env / "promo_style.json").read_text(encoding="utf-8"))
    assert saved["companies"] == {"nubi": nubi}
