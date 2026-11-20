"""개인 MCP 연결 토큰으로 개발 서비스 MCP 서버(`/api/planning/mcp`)에 붙기 (내 연결, 11/20).

- 토큰은 개발 서비스가 직접 풀지 않고 dashboard-api 검사 API(`/me/ai-connection/mcp/check`)에 묻는다
- 고르지 않은 AI 직원·한도 초과는 막고 이유를 돌려준다. 모르는 토큰은 401
- 들어온 요청은 토큰 주인 계정으로 `RequestMeta.on_behalf_of`·LLM 사용 기록 계정을 채운다
  (도구 입력에 적힌 값은 무시)
실제 dashboard-api·GitHub·LLM은 부르지 않는다. 토큰은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import llm_usage, on_behalf
from ax_contracts.schemas import PersonalMcpCheckInput, PersonalMcpCheckOutput
from dev_agent import coder, personal_mcp, repo_routing, vcs, work_api
from dev_agent.main import app
from dev_agent.runtime import set_service
from dev_agent.service import DevService
from dev_agent.vcs import MockVcsAdapter

pytestmark = pytest.mark.role("dev")

TOKEN = "axm_DUMMY-NOT-A-REAL-TOKEN-0001"
ME = {"Authorization": f"Bearer {TOKEN}"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _out(**kw) -> PersonalMcpCheckOutput:
    base = {
        "allowed": True, "code": "OK", "account_id": "a-3", "name": "회원",
        "company_id": "catbooks", "agents": ["dev"],
    }
    return PersonalMcpCheckOutput.model_validate({**base, **kw})


class Dash:
    """dashboard-api 검사 API 대신. 받은 질문을 남긴다."""

    def __init__(self, out: PersonalMcpCheckOutput) -> None:
        self.out = out
        self.asked: list[PersonalMcpCheckInput] = []

    async def __call__(self, body: PersonalMcpCheckInput) -> PersonalMcpCheckOutput:
        self.asked.append(body)
        return self.out


@pytest.fixture
def svc():
    return set_service(DevService())


@pytest.fixture
def adapter():
    vcs._screen = None
    a = MockVcsAdapter()
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    coder.set_llm(None)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    repo_routing.reset_llm()


@pytest.fixture
def dash(monkeypatch: pytest.MonkeyPatch) -> Dash:
    d = Dash(_out())
    monkeypatch.setattr(personal_mcp, "CHECK", d)
    return d


def test_personal_token_is_checked_with_dashboard_api(svc, adapter, dash: Dash) -> None:
    client = TestClient(app)
    res = client.post("/api/planning/mcp", json=LIST, headers=ME)
    assert res.status_code == 200, res.text
    assert [t["name"] for t in res.json()["result"]["tools"]][0] == "list_tasks"
    assert dash.asked == [PersonalMcpCheckInput(token=TOKEN, agent="dev")]


def test_refusals_carry_the_reason(svc, adapter, dash: Dash) -> None:
    client = TestClient(app)
    cases = [
        ("TOKEN_UNKNOWN", 401),
        ("ACCOUNT_GONE", 401),
        ("AGENT_NOT_CHOSEN", 403),
        ("LIMIT_REACHED", 429),
        ("COMPANY_LIMIT_REACHED", 429),
    ]
    for code, status in cases:
        dash.out = _out(allowed=False, code=code, message=f"이유 {code}")
        res = client.post("/api/planning/mcp", json=LIST, headers=ME)
        assert res.status_code == status, (code, res.text)
        err = res.json()["error"]
        assert err["code"] == code and err["message"] == f"이유 {code}"
        rest = client.get("/api/planning/work/tasks", headers=ME)
        assert rest.status_code == status


def test_dashboard_unreachable_is_503_not_a_pass(svc, adapter, monkeypatch) -> None:
    async def down(body: PersonalMcpCheckInput) -> PersonalMcpCheckOutput:
        raise personal_mcp.CheckUnavailable("연결 실패")

    monkeypatch.setattr(personal_mcp, "CHECK", down)
    res = TestClient(app).post("/api/planning/mcp", json=LIST, headers=ME)
    assert res.status_code == 503 and res.json()["error"]["code"] == "MCP_CHECK_UNAVAILABLE"


def test_work_and_human_tokens_still_work_without_asking(svc, adapter, dash, monkeypatch) -> None:
    monkeypatch.setenv("DEV_WORK_TOKENS", "kim:work-test-token")
    client = TestClient(app)
    for who in ("work-test-token", "human:tester"):
        auth = {"Authorization": f"Bearer {who}"}
        res = client.post("/api/planning/mcp", json=LIST, headers=auth)
        assert res.status_code == 200
    assert dash.asked == []


def test_tool_call_runs_on_behalf_of_the_token_owner(svc, adapter, dash, monkeypatch) -> None:
    """도구 처리 중 on_behalf_of·LLM 사용 기록 계정이 토큰 주인이다. 입력에 적은 값은 무시."""
    seen: list[tuple] = []
    real = work_api._run

    async def spy(svc_, who, name, args):
        seen.append((who, on_behalf.bound(), llm_usage._account.get()))
        return await real(svc_, who, name, args)

    monkeypatch.setattr(work_api, "_run", spy)
    client = TestClient(app)
    msg = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "list_tasks", "arguments": {"on_behalf_of": "a-1"}},
    }
    res = client.post("/api/planning/mcp", json=msg, headers=ME)
    assert res.status_code == 200 and res.json()["result"]["isError"] is False
    assert seen == [("회원", (True, "a-3"), "a-3")]
    seen.clear()
    client.post("/api/planning/mcp", json=msg, headers={"Authorization": "Bearer human:tester"})
    assert seen == [("tester", (False, None), None)]  # 사람·작업 토큰은 계정을 싣지 않는다


def test_check_calls_the_dashboard_with_the_dev_agent_token(monkeypatch) -> None:
    """실제 검사 함수: dashboard-api 주소·경로, 개발 서비스 에이전트 토큰, 계약 모양."""
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash.test")
    monkeypatch.setenv("DEV_TOKEN", "dev-agent-token")
    got: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        got.append(request)
        return httpx.Response(200, json={"data": _out().model_dump(mode="json")})

    async def go() -> PersonalMcpCheckOutput:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            body = PersonalMcpCheckInput(token=TOKEN, agent="dev")
            return await personal_mcp.ask_dashboard(body, http_client=http)

    out = asyncio.run(go())
    assert out.allowed and out.account_id == "a-3"
    [req] = got
    assert str(req.url) == "http://dash.test/api/dashboard/me/ai-connection/mcp/check"
    assert req.headers["authorization"] == "Bearer dev-agent-token"
    assert json.loads(req.content) == {"token": TOKEN, "agent": "dev"}

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def go_broken() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
            await personal_mcp.ask_dashboard(
                PersonalMcpCheckInput(token=TOKEN, agent="dev"), http_client=http
            )

    with pytest.raises(personal_mcp.CheckUnavailable):
        asyncio.run(go_broken())
