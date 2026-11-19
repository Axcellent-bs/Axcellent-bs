"""키 관리 경로: 권한·담당 고객사 분리·값 유출 없음·승인 흐름. 값은 명백한 더미만 쓴다."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_contracts.schemas import AgentRequest, AgentResult, InputReason, TaskStatus
from ax_dashboard_api import accounts, marketing_client
from ax_dashboard_api.main import app
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key

BASE = "/api/dashboard/secrets"
DUMMY = "DUMMY-NOT-A-REAL-KEY-0001"
DUMMY2 = "DUMMY-NOT-A-REAL-KEY-0002"
NAME = "MARKETING_VIDEO_API_KEY"


class FakeMarketing:
    """마케팅 skill 대역: 요청 번호만 받아 적용한다. change_secret은 승인 전엔 보류."""

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self.approved = False

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        svc = resolver.service()
        pending = svc.pending(request.input["request_id"])
        if request.skill == "change_secret" and not self.approved:
            svc.record_review(pending.id, "rev-1")
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.INPUT_REQUIRED,
                reason_code=InputReason.HUMAN_INPUT,
                outcome="held",
                output={"review_id": "rev-1"},
            )
        done = svc.apply(pending.id, request.input["updated_by"])
        resolver.invalidate()
        return AgentResult(
            meta=request.meta, status=TaskStatus.COMPLETED, outcome="applied", output=done
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeMarketing:
    f = FakeMarketing()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    monkeypatch.setattr(marketing_client, "SEND", f)
    human_auth.reset_revoked_for_tests()
    resolver.reset_for_tests()
    yield f
    resolver.reset_for_tests()


@pytest.fixture
def client(fake: FakeMarketing) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


@pytest.fixture
def people(fake: FakeMarketing, tmp_path: Path) -> dict[str, dict[str, str]]:
    rows = [
        {
            "id": "a-1", "name": "민지", "email": "m@x.test", "role": "admin",
            "secret_editor": True, "secret_tenants": ["acme", "beta"],
        },
        {
            "id": "a-2", "name": "예진", "email": "y@x.test", "role": "operator",
            "secret_editor": True, "secret_tenants": ["acme"],
        },
        {"id": "a-3", "name": "관리", "email": "g@x.test", "role": "admin"},
        {
            "id": "a-4", "name": "회원", "email": "h@x.test", "role": "member",
            "secret_editor": True, "secret_tenants": ["acme"],
        },
        {"id": "a-5", "name": "총괄", "email": "o@x.test", "role": "operator"},
    ]  # fmt: skip
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    out = {}
    for row in rows:
        token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])
        out[row["id"]] = {"Authorization": f"Bearer {token[0]}"}
    return out


def _add(client: TestClient, who: dict, tenant: str = "acme", value: str = DUMMY) -> Any:
    return client.post(f"{BASE}/{tenant}/{NAME}/add", json={"value": value}, headers=who)


def test_only_assigned_editors_can_read_or_write(client: TestClient, people: Any) -> None:
    assert client.get(BASE).status_code == 401
    shared = {"Authorization": "Bearer shared-test-token"}
    assert client.get(BASE, headers=shared).status_code == 403
    assert client.get(BASE, headers=people["a-4"]).status_code == 403  # 일반회원
    assert _add(client, shared).status_code == 403
    assert _add(client, people["a-4"]).status_code == 403
    assert _add(client, people["a-3"]).status_code == 403  # 키 담당 아님
    assert _add(client, people["a-5"]).status_code == 403
    read_only = client.get(BASE, headers=people["a-3"]).json()["data"]
    assert read_only["viewer"]["can_edit"] is False and read_only["tenants"] == []


def test_tenant_assignment_limits_visibility_and_writes(client: TestClient, people: Any) -> None:
    assert _add(client, people["a-1"], "acme").status_code == 200
    assert _add(client, people["a-2"], "beta", DUMMY2).status_code == 403  # 담당 아님
    assert _add(client, people["a-1"], "beta", DUMMY2).status_code == 200
    mine = client.get(BASE, headers=people["a-1"]).json()["data"]
    assert [t["tenant"] for t in mine["tenants"]] == ["acme", "beta"]
    other = client.get(BASE, headers=people["a-2"]).json()["data"]
    assert [t["tenant"] for t in other["tenants"]] == ["acme"]
    assert [r["name"] for r in other["tenants"][0]["secrets"]] == [NAME]
    assert all(h["tenant"] == "acme" for h in other["history"])


def test_no_response_or_log_ever_contains_the_value(
    client: TestClient, people: Any, fake: FakeMarketing, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [_add(client, people["a-1"])]
    responses.append(client.get(BASE, headers=people["a-1"]))
    responses.append(
        client.post(
            f"{BASE}/acme/{NAME}/replace",
            json={"value": DUMMY2, "reason": "교체"},
            headers=people["a-1"],
        )
    )
    responses.append(client.get(BASE, headers=people["a-2"]))
    bad = client.post(f"{BASE}/acme/BAD name/add", json={"value": DUMMY}, headers=people["a-1"])
    short = client.post(f"{BASE}/acme/NEW_KEY_NAME/add", json={"value": "x"}, headers=people["a-1"])
    wrong_type = client.post(
        f"{BASE}/acme/NEW_KEY_NAME/add", json={"value": [DUMMY]}, headers=people["a-1"]
    )
    responses += [bad, short, wrong_type]
    text = "".join(r.text for r in responses) + caplog.text
    assert DUMMY not in text and DUMMY2 not in text
    assert short.status_code == 422 and wrong_type.status_code == 422
    # 마케팅(A2A)에는 값이 가지 않는다
    assert all(DUMMY not in json.dumps(c.input) and DUMMY2 not in json.dumps(c.input)
               for c in fake.calls)  # fmt: skip
    assert all(set(c.input) == {"request_id", "updated_by"} for c in fake.calls)
    status = client.get(BASE, headers=people["a-1"]).json()["data"]
    row = status["tenants"][0]["secrets"][0]
    assert row["configured"] is True and row["hint"] == DUMMY[-4:]
    assert "value" not in row and "ciphertext" not in row


def test_there_is_no_route_that_reads_a_value(client: TestClient, people: Any) -> None:
    _add(client, people["a-1"])
    for path in (f"{BASE}/acme/{NAME}", f"{BASE}/acme/{NAME}/value", f"{BASE}/acme/{NAME}/reveal"):
        assert client.get(path, headers=people["a-1"]).status_code in (404, 405)
    spec = app.openapi()["paths"]
    gets = {p for p, ops in spec.items() if p.startswith(BASE) and "get" in ops}
    assert gets == {BASE}  # 값을 돌려주는 GET은 목록(상태만) 하나뿐


def test_add_then_replace_needs_approval_then_apply(
    client: TestClient, people: Any, fake: FakeMarketing
) -> None:
    assert _add(client, people["a-1"]).status_code == 200
    assert _add(client, people["a-1"]).status_code == 409  # 이미 있음 → 교체로
    res = client.post(
        f"{BASE}/acme/{NAME}/replace",
        json={"value": DUMMY2, "reason": "교체"},
        headers=people["a-1"],
    )
    assert res.status_code == 200
    assert res.json()["data"]["result"]["outcome"] == "held"
    assert resolver.get_secret(NAME, tenant="acme") == DUMMY  # 승인 전에는 그대로
    data = client.get(BASE, headers=people["a-2"]).json()["data"]
    req = data["requests"][0]
    assert req["op"] == "replace" and req["requested_by"] == "민지" and req["review_id"] == "rev-1"
    fake.approved = True
    done = client.post(f"{BASE}/requests/{req['id']}/apply", headers=people["a-2"])
    assert done.status_code == 200 and done.json()["data"]["result"]["outcome"] == "applied"
    assert resolver.get_secret(NAME, tenant="acme") == DUMMY2
    actions = [
        h["action"] for h in client.get(BASE, headers=people["a-1"]).json()["data"]["history"]
    ]
    assert actions[:2] == ["replace", "request_replace"]
    assert [c.skill for c in fake.calls] == ["add_secret", "change_secret", "change_secret"]


def test_apply_checks_the_tenant_of_the_request(
    client: TestClient, people: Any, fake: FakeMarketing
) -> None:
    _add(client, people["a-1"], "beta")
    client.post(f"{BASE}/beta/{NAME}/delete", json={"reason": "폐기"}, headers=people["a-1"])
    req = client.get(BASE, headers=people["a-1"]).json()["data"]["requests"][0]
    fake.approved = True
    assert (
        client.post(f"{BASE}/requests/{req['id']}/apply", headers=people["a-2"]).status_code == 403
    )
    assert client.post(f"{BASE}/requests/sr-none/apply", headers=people["a-1"]).status_code == 404
    assert (
        client.post(f"{BASE}/requests/{req['id']}/apply", headers=people["a-1"]).status_code == 200
    )
    assert resolver.get_secret(NAME, tenant="beta") == ""


def test_env_mode_store_is_off(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    data = client.get(BASE, headers=people["a-1"]).json()["data"]
    assert data["viewer"]["enabled"] is False and data["tenants"] == []
    assert _add(client, people["a-1"]).status_code == 409
    assert not (tmp_path / "secrets.db").exists()


def test_account_profile_grants(client: TestClient, people: Any, tmp_path: Path) -> None:
    update = "/api/dashboard/auth/accounts/{}/update"
    # 운영자는 누구에게나 준다(일반회원 제외)
    ok = client.post(
        update.format("a-3"),
        json={"secret_editor": True, "secret_tenants": ["acme"]},
        headers=people["a-5"],
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["secret_editor"] is True
    assert ok.json()["data"]["secret_tenants"] == ["acme"]
    member = client.post(update.format("a-4"), json={"secret_editor": True}, headers=people["a-5"])
    assert member.status_code == 422
    bad = client.post(
        update.format("a-3"), json={"secret_tenants": ["Bad Tenant"]}, headers=people["a-5"]
    )
    assert bad.status_code == 422
    # 관리자: 자기 자신 금지, 담당 밖 고객사 금지, 담당 안은 가능
    own = client.post(update.format("a-1"), json={"secret_tenants": ["x"]}, headers=people["a-1"])
    assert own.status_code == 409
    outside = client.post(
        update.format("a-3"), json={"secret_tenants": ["zzz"]}, headers=people["a-1"]
    )
    assert outside.status_code == 403
    inside = client.post(
        update.format("a-3"), json={"secret_tenants": ["beta"]}, headers=people["a-1"]
    )
    assert inside.status_code == 200
    plain_admin = client.post(
        update.format("a-1"), json={"secret_editor": False}, headers=people["a-3"]
    )
    assert plain_admin.status_code in (403, 409)
