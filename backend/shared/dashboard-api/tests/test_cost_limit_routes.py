"""비용 상한·정책 경로: 로그인 세션의 운영자·관리자만, 변경은 limit_editor·policy_editor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import accounts, cost_limit_routes, marketing_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard/marketing/cost-limits"
POLICY = "/api/dashboard/cost-policy"


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.status = TaskStatus.COMPLETED
        self.outcome = "state"

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output={"limits": {}}
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Fake:
    f = Fake()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setattr(marketing_client, "SEND", f)
    human_auth.reset_revoked_for_tests()
    return f


@pytest.fixture
def client(fake: Fake) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _seed(tmp_path: Path, rows: list[dict]) -> None:
    import json

    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"accounts": rows}), encoding="utf-8")


@pytest.fixture
def people(fake: Fake, tmp_path: Path) -> dict[str, dict[str, str]]:
    rows = [
        {"id": "a-lim", "name": "민지", "email": "m@x.test", "role": "admin", "limit_editor": True},
        {
            "id": "a-pol",
            "name": "총괄",
            "email": "p@x.test",
            "role": "operator",
            "limit_editor": True,
            "policy_editor": True,
        },
        {"id": "a-adm", "name": "예진", "email": "y@x.test", "role": "admin"},
        {
            "id": "a-mem",
            "name": "회원",
            "email": "h@x.test",
            "role": "member",
            "limit_editor": True,
        },  # 파일에 잘못 들어 있어도 일반회원은 못 쓴다
    ]
    _seed(tmp_path, rows)
    out = {}
    for row in rows:
        token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])[
            0
        ]
        out[row["id"]] = {"Authorization": f"Bearer {token}"}
    return out


def test_read_needs_a_logged_in_operator_or_admin(client: TestClient, people: Any) -> None:
    assert client.get(BASE).status_code == 401
    shared = {"Authorization": "Bearer shared-test-token"}
    assert client.get(BASE, headers=shared).status_code == 403  # 공용 토큰
    assert client.get(BASE, headers=people["a-mem"]).status_code == 403  # 일반회원
    ok = client.get(BASE, headers=people["a-adm"])
    assert ok.status_code == 200
    assert ok.json()["data"]["output"]["viewer"] == {"can_edit": False, "can_edit_policy": False}
    top = client.get(BASE, headers=people["a-pol"]).json()["data"]["output"]["viewer"]
    assert top == {"can_edit": True, "can_edit_policy": True}


def test_writes_need_limit_editor_and_use_the_token_name(
    client: TestClient, fake: Fake, people: Any
) -> None:
    body = {"video_seconds_monthly": 800, "reason": "축소", "confirmed": True, "updated_by": "남"}
    assert client.post(f"{BASE}/lower", json=body, headers=people["a-adm"]).status_code == 403
    assert client.post(f"{BASE}/lower", json=body, headers=people["a-mem"]).status_code == 403
    shared = {"Authorization": "Bearer shared-test-token"}
    assert client.post(f"{BASE}/lower", json=body, headers=shared).status_code == 403
    assert fake.calls == []
    assert client.post(f"{BASE}/lower", json=body, headers=people["a-lim"]).status_code == 200
    res = client.post(
        f"{BASE}/raise",
        json={"image_count_monthly": 900, "reason": "증액"},
        headers=people["a-lim"],
    )
    assert res.status_code == 200
    skills = [(r.skill, r.input["updated_by"]) for _, r in fake.calls]
    assert skills == [("lower_media_cost_limits", "민지"), ("raise_media_cost_limits", "민지")]


def test_skill_failures_pass_through_as_data(client: TestClient, fake: Fake, people: Any) -> None:
    fake.status = TaskStatus.FAILED
    fake.outcome = "over_absolute_max"
    res = client.post(
        f"{BASE}/raise",
        json={"video_seconds_monthly": 9000, "reason": "x"},
        headers=people["a-lim"],
    )
    assert res.status_code == 200
    assert res.json()["data"]["outcome"] == "over_absolute_max"
    fake.outcome = "invalid_input"
    bad = client.post(
        f"{BASE}/raise",
        json={"video_seconds_monthly": 9000, "reason": "x"},
        headers=people["a-lim"],
    )
    assert bad.status_code == 400
    assert (
        client.post(f"{BASE}/raise", json={"reason": "x"}, headers=people["a-lim"]).status_code
        == 400
    )


def test_self_approve_option_is_for_policy_editors_only(
    client: TestClient, fake: Fake, people: Any
) -> None:
    body = {"lower_self_approve": True, "reason": "재무 합의"}
    assert client.post(f"{BASE}/option", json=body, headers=people["a-lim"]).status_code == 403
    assert client.post(f"{BASE}/option", json=body, headers=people["a-pol"]).status_code == 200
    assert fake.calls[-1][1].skill == "set_cost_limit_option"
    assert fake.calls[-1][1].input["updated_by"] == "총괄"


class FakeSafety:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def __call__(self, method: str, url: str, **kw: Any) -> httpx.Response:
        self.calls.append((method, url, kw["headers"].get("Authorization")))
        return httpx.Response(200, json={"data": {"items": [], "history": [], "requests": []}})


def test_policy_window_reads_and_forwards_with_the_session_token(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    safety = FakeSafety()

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
            return safety(method, url, **kw)

    monkeypatch.setattr(cost_limit_routes.httpx, "AsyncClient", Client)
    view = client.get(POLICY, headers=people["a-adm"])
    assert view.status_code == 200 and view.json()["data"]["viewer"] == {"can_edit_policy": False}
    body = {"rule_key": "video_cost_single", "requested_value": 3000, "reason": "상향"}
    assert client.post(f"{POLICY}/requests", json=body, headers=people["a-lim"]).status_code == 403
    assert client.post(f"{POLICY}/requests/rev_1/apply", headers=people["a-lim"]).status_code == 403
    sent = client.post(f"{POLICY}/requests", json=body, headers=people["a-pol"])
    assert sent.status_code == 200
    applied = client.post(f"{POLICY}/requests/rev_1/apply", headers=people["a-pol"])
    assert applied.status_code == 200
    assert [c[0] for c in safety.calls] == ["GET", "POST", "POST"]
    assert safety.calls[1][2] == people["a-pol"]["Authorization"]  # 사람 세션 토큰 그대로


def test_account_flags_are_limited_to_operators_and_admins(
    client: TestClient, people: Any, tmp_path: Path
) -> None:
    url = "/api/dashboard/auth/accounts"
    ops = people["a-pol"]
    row = client.get(url, headers=ops).json()["data"]
    flags = {r["id"]: (r["limit_editor"], r["policy_editor"]) for r in row}
    assert flags["a-lim"] == (True, False) and flags["a-mem"] == (False, False)
    # 일반회원에게는 켤 수 없다
    res = client.post(f"{url}/a-mem/update", json={"limit_editor": True}, headers=ops)
    assert res.status_code == 422
    # 운영자는 관리자에게 켤 수 있다
    ok = client.post(f"{url}/a-adm/update", json={"limit_editor": True}, headers=ops)
    assert ok.status_code == 200 and ok.json()["data"]["limit_editor"] is True
    # 정책 권한은 운영자 또는 정책 담당 관리자만 준다
    denied = client.post(
        f"{url}/a-adm/update", json={"policy_editor": True}, headers=people["a-lim"]
    )
    assert denied.status_code == 403
    granted = client.post(f"{url}/a-adm/update", json={"policy_editor": True}, headers=ops)
    assert granted.json()["data"]["policy_editor"] is True


def test_old_krw_fields_are_still_accepted_and_forwarded_so_the_skill_can_refuse(
    client: TestClient, fake: Fake, people: Any
) -> None:
    """입력 계약은 지우지 않는다. 원화 칸은 그대로 전달되고 마케팅이 거절한다(여기서 400 아님)."""
    fake.status = TaskStatus.FAILED
    fake.outcome = "deprecated_krw_limit"
    res = client.post(
        f"{BASE}/raise", json={"video_per_item_krw": 9000, "reason": "x"}, headers=people["a-lim"]
    )
    assert res.status_code == 200 and res.json()["data"]["outcome"] == "deprecated_krw_limit"
    assert fake.calls[-1][1].input["video_per_item_krw"] == 9000
