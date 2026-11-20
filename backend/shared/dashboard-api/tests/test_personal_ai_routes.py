"""개인 AI 연결 API (도구 연결 › 내 연결, 예진 결정 11/20).

- 입력은 키·모델·주소·월 한도(토큰)와 고른 AI 직원. 나만 보고 나만 바꾼다(관리자도 남의 것을 못 봄)
- 키는 암호화 키 저장소에 회사 ID + 계정 ID 범위로. 응답·기록에는 끝 4자리만
- 로그인 세션만. 에이전트 토큰·공용 토큰은 403. 계정을 고르는 입력(경로·본문)은 없다
- 사람 세션 요청이면 A2A 메타 `on_behalf_of`에 세션 계정이 실린다(본문 값은 무시)
값은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth, llm_usage
from ax_agent_template import tenant as tenants
from ax_agent_template.client import call_agent
from ax_contracts.data_access.llm_usage import LlmCallRecord
from ax_contracts.schemas import PERSONAL_AI_KEY_NAME, AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import accounts, company_ai, orchestrator_chat, personal_ai
from ax_dashboard_api import on_behalf as on_behalf_mw
from ax_dashboard_api.main import app
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key

ME = "/api/dashboard/me/ai-connection"
ADMIN = "/api/dashboard/company-ai"
VIEW = "/api/dashboard/ai-connection"
DUMMY = "DUMMY-NOT-A-REAL-KEY-0001"
DUMMY2 = "DUMMY-NOT-A-REAL-KEY-0002"

ROWS = [
    {"id": "a-1", "name": "민지", "email": "m@x.test", "role": "admin"},
    {"id": "a-2", "name": "태윤", "email": "t@x.test", "role": "admin"},
    {"id": "a-3", "name": "회원", "email": "h@x.test", "role": "member"},
    {"id": "a-4", "name": "총괄", "email": "o@x.test", "role": "operator"},
]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv("AX_AGENT_TOKENS", "orchestrator=orch-agent-token")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(tenants.TENANT_ENV, "catbooks")
    monkeypatch.delenv(tenants.TENANTS_DIR_ENV, raising=False)
    monkeypatch.delenv(company_ai.DIR_ENV, raising=False)
    monkeypatch.delenv(company_ai.TRIAL_KEY_ENV, raising=False)
    monkeypatch.delenv(company_ai.TRIAL_TOKENS_ENV, raising=False)
    monkeypatch.delenv("DATA_TENANT_TIMEZONE", raising=False)
    monkeypatch.delenv(accounts.ACCOUNTS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.REVOKED_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.GENERATIONS_FILE_ENV, raising=False)
    monkeypatch.delenv(human_auth.ALLOW_SHARED_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    human_auth.reset_revoked_for_tests()
    resolver.reset_for_tests()
    llm_usage.set_store(llm_usage.SqliteUsageStore(":memory:"))
    yield tmp_path
    llm_usage.reset_store()
    resolver.reset_for_tests()


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
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _put_key(client: TestClient, who: dict, value: str = DUMMY) -> Any:
    return client.post(f"{ME}/key", json={"value": value}, headers=who)


def _settings(client: TestClient, who: dict, **body: Any) -> Any:
    return client.post(f"{ME}/settings", json=body, headers=who)


def _usage(account: str | None, tokens: int, tenant: str = "catbooks") -> None:
    store = llm_usage.get_store()
    assert store is not None
    now = datetime.now(UTC)
    store.write(
        LlmCallRecord(
            log_id=f"llm_{account}_{tokens}_{now.timestamp()}",
            tenant_id=tenant,
            occurred_at=now,
            source="planning",
            department="planning",
            model="m",
            input_tokens=tokens,
            output_tokens=0,
            success=True,
            account_id=account,
        )
    )


# ---- 권한 ----


def test_needs_a_member_or_admin_login_session(client: TestClient, people: Any) -> None:
    assert client.get(ME).status_code == 401
    for token in ("shared-test-token", "orch-agent-token"):
        who = {"Authorization": f"Bearer {token}"}
        assert client.get(ME, headers=who).status_code == 403
        assert _put_key(client, who).status_code == 403
        assert _settings(client, who, model="m").status_code == 403
        assert client.post(f"{ME}/key/delete", json={}, headers=who).status_code == 403
    assert client.get(ME, headers=people["a-4"]).status_code == 403  # 운영자는 고객사 회원이 아니다
    for who in ("a-1", "a-3"):  # 관리자·일반회원 모두
        assert client.get(ME, headers=people[who]).status_code == 200


def test_there_is_no_way_to_name_another_account(client: TestClient, people: Any) -> None:
    """계정은 세션에서만 꺼낸다. 본문·쿼리에 계정을 적어도 내 것만 바뀐다."""
    res = _settings(client, people["a-3"], model="m", account_id="a-1")
    assert res.status_code == 422
    res = client.post(
        f"{ME}/key", json={"value": DUMMY, "account_id": "a-1"}, headers=people["a-3"]
    )
    assert res.status_code in (200, 422)
    assert client.get(f"{ME}?account_id=a-3", headers=people["a-1"]).json()["data"]["key"][
        "has_key"
    ] is False  # 쿼리 값은 무시하고 a-1 자기 것
    paths = {p: ops for p, ops in app.openapi()["paths"].items() if p.startswith(ME)}
    assert sorted(paths) == [ME, f"{ME}/key", f"{ME}/key/delete", f"{ME}/settings"]
    assert all("{" not in p for p in paths)  # 경로에 계정·회사 자리가 없다
    # nginx는 GET·HEAD·POST만 연다(PUT·DELETE 없음)
    assert {m for ops in paths.values() for m in ops} <= {"get", "head", "post"}


# ---- 키 ----


def test_key_is_mine_only_and_only_the_hint_comes_back(
    client: TestClient, people: Any, env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    res = _put_key(client, people["a-3"])
    assert res.status_code == 200, res.text
    assert res.json()["data"] == {"has_key": True, "hint": "0001"}
    mine = client.get(ME, headers=people["a-3"]).json()["data"]
    assert mine["key"]["has_key"] is True and mine["key"]["hint"] == "0001"
    assert mine["state"] == "connected"
    tenant = personal_ai.secret_tenant("catbooks", "a-3")
    assert resolver.get_secret(PERSONAL_AI_KEY_NAME, tenant=tenant) == DUMMY
    # 회사 키 자리·다른 사람 자리에는 없다
    assert resolver.get_secret("LLM_API_KEY", tenant="catbooks") in (None, "")
    assert resolver.get_secret(PERSONAL_AI_KEY_NAME, tenant="catbooks") in (None, "")
    other = client.get(ME, headers=people["a-1"]).json()["data"]
    assert other["key"] == {"has_key": False, "hint": "", "updated_at": None}
    # 관리자 화면(회사 AI·키 관리)에도 개인 키는 보이지 않는다
    seen = [
        client.get(ADMIN, headers=people["a-1"]).text,
        client.get(f"{VIEW}/status", headers=people["a-1"]).text,
        client.get("/api/dashboard/secrets", headers=people["a-1"]).text,
    ]
    assert all("0001" not in t and PERSONAL_AI_KEY_NAME not in t for t in seen)
    # 원문은 응답·파일·로그 어디에도 없다
    seen.append(_put_key(client, people["a-3"], DUMMY2).text)
    seen.append(client.get(ME, headers=people["a-3"]).text)
    seen.append(client.post(f"{ME}/key/delete", json={}, headers=people["a-3"]).text)
    files = [p.read_text(encoding="utf-8") for p in (env / "company_ai").rglob("*") if p.is_file()]
    text = "\n".join(seen + files) + caplog.text
    assert DUMMY not in text and DUMMY2 not in text


def test_replace_and_delete_take_effect_at_once(client: TestClient, people: Any) -> None:
    _put_key(client, people["a-3"])
    assert _put_key(client, people["a-3"], DUMMY2).json()["data"]["hint"] == "0002"
    tenant = personal_ai.secret_tenant("catbooks", "a-3")
    assert resolver.get_secret(PERSONAL_AI_KEY_NAME, tenant=tenant) == DUMMY2
    res = client.post(f"{ME}/key/delete", json={}, headers=people["a-3"])
    assert res.status_code == 200 and res.json()["data"] == {"has_key": False, "hint": ""}
    assert resolver.get_secret(PERSONAL_AI_KEY_NAME, tenant=tenant) in (None, "")
    assert client.get(ME, headers=people["a-3"]).json()["data"]["state"] == "no_key"
    assert client.post(f"{ME}/key/delete", json={}, headers=people["a-3"]).status_code == 404


def test_one_persons_delete_never_touches_anothers_key(client: TestClient, people: Any) -> None:
    _put_key(client, people["a-1"])
    _put_key(client, people["a-3"], DUMMY2)
    client.post(f"{ME}/key/delete", json={}, headers=people["a-3"])
    t1 = personal_ai.secret_tenant("catbooks", "a-1")
    assert resolver.get_secret(PERSONAL_AI_KEY_NAME, tenant=t1) == DUMMY


def test_same_account_in_another_company_is_a_different_slot(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put_key(client, people["a-3"])
    assert personal_ai.secret_tenant("catbooks", "a-3") != personal_ai.secret_tenant("x", "a-3")
    assert personal_ai.secret_tenant("catbooks", "a-3") != personal_ai.secret_tenant(
        "catbooks", "a-1"
    )
    monkeypatch.setenv(tenants.TENANT_ENV, "other-co")
    assert client.get(ME, headers=people["a-3"]).json()["data"]["key"]["has_key"] is False


def test_secret_tenant_fits_the_store_rule() -> None:
    from ax_secret_store.service import TENANT_RE

    for cid, acc in (("catbooks", "a-1"), ("c" * 40, "x" * 200), ("co", "한글-계정")):
        t = personal_ai.secret_tenant(cid, acc)
        assert TENANT_RE.match(t) and t.startswith("p-") and len(t) == 40


def test_store_off_blocks_saving_with_a_reason(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    resolver.reset_for_tests()
    got = client.get(ME, headers=people["a-3"]).json()["data"]
    assert got["secret_store"]["enabled"] is False and got["secret_store"]["reason"]
    assert got["can_save"] is False
    res = _put_key(client, people["a-3"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "SECRET_STORE_DISABLED"
    assert _settings(client, people["a-3"], model="m").status_code == 409


# ---- 설정 ----


def test_settings_are_mine_and_validated(client: TestClient, people: Any, env: Path) -> None:
    res = _settings(
        client, people["a-3"], model="any-model-name", monthly_limit_tokens=1_000_000,
        agents=["planning", "dev", "planning"],
    )
    assert res.status_code == 200, res.text
    s = res.json()["data"]["settings"]
    assert s["model"] == "any-model-name" and s["provider"] == "auto"
    assert s["agents"] == ["planning", "dev"] and s["monthly_limit_tokens"] == 1_000_000
    res = _settings(client, people["a-3"], model="m", base_url="https://llm.example.test/v1")
    assert res.json()["data"]["settings"]["provider"] == "openai_compatible"
    assert client.get(ME, headers=people["a-1"]).json()["data"]["settings"]["model"] == ""
    assert _settings(client, people["a-3"], agents=["nope"]).status_code == 422
    assert _settings(client, people["a-3"], base_url="ftp://x").status_code == 422
    assert _settings(client, people["a-3"], monthly_limit_tokens=-1).status_code == 422
    assert _settings(client, people["a-3"], api_key=DUMMY).status_code == 422
    files = list((env / "company_ai" / "catbooks" / "personal").glob("*.json"))
    assert len(files) == 1 and "a-3" not in files[0].name  # 파일 이름은 계정 해시


def test_choosable_agents_are_the_ten_ai_employees(client: TestClient, people: Any) -> None:
    got = client.get(ME, headers=people["a-3"]).json()["data"]
    assert got["agents_available"] == [
        "orchestrator", "support", "finance", "data", "hr",
        "marketing", "planning", "dev", "infra", "archive",
    ]


def test_no_company_means_nothing_to_save(
    client: TestClient, people: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tenants, "current", lambda: None)
    got = client.get(ME, headers=people["a-3"]).json()["data"]
    assert got["state"] == "no_company" and got["can_save"] is False
    assert _put_key(client, people["a-3"]).status_code == 409


# ---- 사용량 ----


def test_my_usage_counts_only_my_personal_key_calls(client: TestClient, people: Any) -> None:
    _settings(client, people["a-3"], model="m", monthly_limit_tokens=1000)
    _usage("a-3", 250)
    _usage("a-1", 900)  # 다른 사람
    _usage(None, 5000)  # 회사 키 호출
    _usage("a-3", 100, tenant="other-co")  # 다른 회사
    u = client.get(ME, headers=people["a-3"]).json()["data"]["usage"]
    assert u["recording"] is True
    assert (u["used_tokens"], u["limit_tokens"], u["percent"]) == (250, 1000, 25.0)


def test_personal_calls_are_not_counted_in_the_company_budget(
    client: TestClient, people: Any
) -> None:
    client.post(f"{ADMIN}/settings", json={"model": "m", "monthly_budget_tokens": 10000},
                headers=people["a-1"])
    _usage(None, 1000)
    _usage("a-3", 4000)
    st = client.get(f"{VIEW}/status", headers=people["a-3"]).json()["data"]["usage"]
    assert (st["used_tokens"], st["percent"]) == (1000, 10.0)
    us = client.get(f"{VIEW}/usage", headers=people["a-3"]).json()["data"]
    assert us["total_tokens"] == 1000


def test_usage_recording_off_is_said_plainly(client: TestClient, people: Any) -> None:
    llm_usage.set_store(None)
    llm_usage.reset_store()
    u = client.get(ME, headers=people["a-3"]).json()["data"]["usage"]
    assert u["recording"] is False and u["used_tokens"] == 0 and u["percent"] is None


# ---- A2A 메타 on_behalf_of ----


class Wire:
    """dashboard-api가 실제 `call_agent`로 보낸 JSON 메타를 잡는다."""

    def __init__(self) -> None:
        self.metas: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.metas.append(body["meta"])
        thread = {"id": "orchchat-1", "status": "answering", "messages": [], "who": "x"}
        result = AgentResult(
            meta=AgentRequest.model_validate(body).meta,
            status=TaskStatus.COMPLETED,
            output={"thread": thread, "threads": [thread]},
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    w = Wire()
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")

    async def send(target: str, request: AgentRequest) -> AgentResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(w.handler)) as http:
            return await call_agent("http://orchestrator.test", request, http_client=http)

    monkeypatch.setattr(orchestrator_chat, "SEND", send)
    return w


def test_session_request_carries_the_session_account_on_the_wire(
    client: TestClient, people: Any, wire: Wire
) -> None:
    body = {"message": "안녕", "on_behalf_of": "a-1", "meta": {"on_behalf_of": "a-1"}}
    res = client.post("/api/dashboard/orchestrator-chat", json=body, headers=people["a-3"])
    assert res.status_code == 200, res.text
    assert wire.metas[-1]["on_behalf_of"] == "a-3"  # 본문 값은 무시, 세션 계정


def test_non_session_requests_send_no_person(
    client: TestClient, people: Any, wire: Wire
) -> None:
    for token in ("shared-test-token",):
        res = client.post(
            "/api/dashboard/orchestrator-chat", json={"message": "안녕"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, (token, res.text)
        assert "on_behalf_of" not in wire.metas[-1]
    assert len(wire.metas) == 1


def test_only_a_verified_login_session_names_a_person(people: Any) -> None:
    """에이전트 토큰·공용 토큰·토큰 없음·위조 세션이면 사람이 없다(None)."""
    assert on_behalf_mw.session_account(people["a-3"]["Authorization"]) == "a-3"
    forged = people["a-3"]["Authorization"][:-4] + "AAAA"
    for auth in (None, "", "Bearer orch-agent-token", "Bearer shared-test-token", forged):
        assert on_behalf_mw.session_account(auth) is None
    assert any(m.cls is on_behalf_mw.OnBehalfMiddleware for m in app.user_middleware)
