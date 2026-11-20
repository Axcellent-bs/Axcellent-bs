"""개인 MCP 연결의 전 직원 창구 (`/api/dashboard/me/ai-connection/mcp`, 설계 9장·열린 질문 5 (a)).

- 내 AI 도구가 `axm_` 개인 토큰으로 붙는다. 다른 토큰(세션·공용·에이전트)은 받지 않는다
- 도구는 고른 AI 직원마다 `ask_<직원>` 하나. 화면 AI 대화방이 쓰는 dashboard-api → 부서 대화 skill
  (`<직원>_chat_send`·`<직원>_chat_get`)을 그대로 부른다. 말한 사람은 토큰 주인 계정
  (도구 입력 값은 버린다)
- 고르지 않은 직원 403, 내 몫·회사 상한 429, 모르는 토큰·없는 계정 401 (#1530 검사 그대로)
- 요청 메타: `on_behalf_of` = 토큰 주인 계정, `tenant_id` = 토큰의 회사
- 관문: 창구는 대화 skill 말고 다른 skill을 부르지 못한다. 대화 skill은 화면과 같은 카드 규칙
  (요청자 dashboard-api만, 등급 read·A)이라 상태 변경은 부서 쪽 `execute()`를 그대로 거친다
값은 명백한 더미만 쓴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import human_auth, llm_usage, on_behalf
from ax_agent_template import tenant as tenants
from ax_agent_template.services import route_target
from ax_contracts.data_access.llm_usage import LlmCallRecord
from ax_contracts.schemas import AgentRequest, AgentResult, CompanyAiSettings, TaskStatus
from ax_dashboard_api import (
    accounts,
    auth,
    company_ai,
    data_chat,
    dev_chat,
    finance_chat,
    infra_chat,
    marketing_client,
    mcp_gateway,
    orchestrator_chat,
    planning_chat,
    support_client,
)
from ax_dashboard_api.main import app
from ax_secret_store import resolver

ME = "/api/dashboard/me/ai-connection"
MCP = f"{ME}/mcp"
BACKEND = Path(__file__).resolve().parents[3]

ROWS = [
    {"id": "a-1", "name": "민지", "email": "m@x.test", "role": "admin", "buyer_id": "buy_0077"},
    {"id": "a-3", "name": "회원", "email": "h@x.test", "role": "member"},
]

# 직원 → (dashboard-api 대화 모듈, A2A 받는 곳). 화면 AI 대화방 경로와 같다
CHAT = {
    "orchestrator": (orchestrator_chat, "orchestrator"),
    "support": (support_client, "support"),
    "finance": (finance_chat, "finance"),
    "data": (data_chat, "data"),
    "marketing": (marketing_client, "marketing"),
    "planning": (planning_chat, "planning"),
    "dev": (dev_chat, "dev"),
    "infra": (infra_chat, "incident"),
}


class Spy:
    """모든 대화 모듈의 A2A 보내기 자리. 보낸 요청과 그때 묶인 계정을 적는다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest, tuple[bool, str | None]]] = []
        self.status_after_get = "talking"
        self.fail: tuple[TaskStatus, str] | None = None

    def thread(self, skill: str, status: str) -> dict[str, Any]:
        return {"id": "th-1", "status": status, "who": "x", "messages": [{"text": skill}]}

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request, on_behalf.bound()))
        if self.fail and request.skill.endswith("_chat_send"):
            status, outcome = self.fail
            return AgentResult(
                meta=request.meta, status=status, outcome=outcome, output={"message": "막힘"}
            )
        if request.skill.endswith("_chat_send"):
            th = self.thread(request.skill, "answering")
        elif request.skill.endswith("_chat_get"):
            th = self.thread(request.skill, self.status_after_get)
        else:  # 고객지원 대화가 읽는 집계(문의·클레임·후기 현황)
            return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output={})
        out = th if request.skill.startswith("support_") else {"thread": th}
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=out)

    def skills(self) -> list[str]:
        return [c[1].skill for c in self.calls]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_HUMAN_TOKENS", "공용:shared-test-token")
    monkeypatch.setenv("AX_AGENT_TOKENS", "dashboard-api=dash-agent-token,dev=dev-agent-token")
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
    monkeypatch.delenv(mcp_gateway.ORIGINS_ENV, raising=False)
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    monkeypatch.setenv("AX_SECRET_STORE", "env")
    monkeypatch.setattr(mcp_gateway, "WAIT_SECONDS", 0.05)
    monkeypatch.setattr(mcp_gateway, "POLL_SECONDS", 0.01)
    human_auth.reset_revoked_for_tests()
    resolver.reset_for_tests()
    llm_usage.set_store(llm_usage.SqliteUsageStore(":memory:"))
    (tmp_path / "accounts.json").write_text(json.dumps({"accounts": ROWS}), encoding="utf-8")
    yield tmp_path
    llm_usage.reset_store()
    resolver.reset_for_tests()


@pytest.fixture
def spy(env: Path, monkeypatch: pytest.MonkeyPatch) -> Spy:
    s = Spy()
    for module, _ in CHAT.values():
        monkeypatch.setattr(module, "SEND", s)
    return s


@pytest.fixture
def client(env: Path) -> TestClient:
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _session(account: str) -> dict[str, str]:
    row = next(r for r in ROWS if r["id"] == account)
    token = human_auth.issue_session(account_id=row["id"], name=row["name"], role=row["role"])
    return {"Authorization": f"Bearer {token[0]}"}


def _connect(client: TestClient, account: str = "a-3", agents: list[str] | None = None,
             share: int | None = None) -> str:  # fmt: skip
    who = _session(account)
    body = {"agents": agents or [], "monthly_share_percent": share}
    assert client.post(f"{ME}/settings", json=body, headers=who).status_code == 200
    res = client.post(f"{ME}/token", json={}, headers=who)
    assert res.status_code == 200, res.text
    return res.json()["data"]["token"]


def _rpc(client: TestClient, token: str | None, method: str, params: dict | None = None,
         headers: dict | None = None) -> Any:  # fmt: skip
    h = {"Authorization": f"Bearer {token}"} if token else {}
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(MCP, json=msg, headers={**h, **(headers or {})})


def _call(client: TestClient, token: str, tool: str, args: dict) -> Any:
    return _rpc(client, token, "tools/call", {"name": tool, "arguments": args})


def _budget(env: Path, tokens: int | None, pct: int | None = None) -> None:
    settings = CompanyAiSettings(model="m", monthly_budget_tokens=tokens, monthly_limit_percent=pct)
    folder = env / "company_ai" / "catbooks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / company_ai.SETTINGS_FILE).write_text(settings.model_dump_json(), encoding="utf-8")


def _usage(account: str | None, tokens: int) -> None:
    store = llm_usage.get_store()
    assert store is not None
    now = datetime.now(UTC)
    store.write(
        LlmCallRecord(
            log_id=f"llm_{account}_{tokens}_{now.timestamp()}",
            tenant_id="catbooks",
            occurred_at=now,
            source="data",
            department="data",
            model="m",
            input_tokens=tokens,
            output_tokens=0,
            success=True,
            account_id=account,
        )
    )


# ---- 경로·인증 ----


def test_path_fits_the_proxy_rule_and_skips_the_human_check() -> None:
    """nginx `me/ai-connection*`(GET·HEAD·POST, 3단계 안). 사람 토큰 대신 개인 토큰을 본다."""
    ops = app.openapi()["paths"][MCP]
    assert set(ops) <= {"get", "head", "post"} and "post" in ops
    assert ("POST", MCP) in auth.MACHINE_ROUTES and ("GET", MCP) in auth.MACHINE_ROUTES


def test_only_a_personal_token_gets_in(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning"])
    assert _rpc(client, None, "tools/list").status_code == 401
    for other in ("shared-test-token", "dash-agent-token", "dev-agent-token"):
        res = _rpc(client, other, "tools/list")
        assert res.status_code == 401 and res.json()["error"]["code"] == "PERSONAL_TOKEN_REQUIRED"
    session = _session("a-3")["Authorization"].split(" ", 1)[1]
    assert _rpc(client, session, "tools/list").status_code == 401
    res = _rpc(client, "axm_unknown", "tools/list")
    assert res.status_code == 401 and res.json()["error"]["code"] == "TOKEN_UNKNOWN"
    assert _rpc(client, token, "tools/list").status_code == 200
    assert spy.calls == []


def test_account_gone_stops_the_token(client: TestClient, env: Path, spy: Spy) -> None:
    token = _connect(client, agents=["planning"])
    rows = [r for r in ROWS if r["id"] != "a-3"]
    (env / "accounts.json").write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    res = _rpc(client, token, "tools/list")
    assert res.status_code == 401 and res.json()["error"]["code"] == "ACCOUNT_GONE"


def test_browser_origin_is_refused_and_get_has_no_stream(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning"])
    res = _rpc(client, token, "tools/list", headers={"Origin": "http://evil.test"})
    assert res.status_code == 403
    got = client.get(MCP, headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 405


# ---- MCP 기본 ----


def test_initialize_and_ping(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning"])
    res = _rpc(client, token, "initialize", {"protocolVersion": "2025-03-26"})
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "axcellent"
    assert "데이터" in result["instructions"]  # 답 속 글은 데이터(지시가 아니다)
    assert _rpc(client, token, "ping").json()["result"] == {}
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    sent = client.post(MCP, json=note, headers={"Authorization": f"Bearer {token}"})
    assert sent.status_code == 202
    assert _rpc(client, token, "nope").json()["error"]["code"] == -32601


def test_tools_are_only_the_chosen_ai_employees(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning", "dev", "hr", "archive"])
    tools = _rpc(client, token, "tools/list").json()["result"]["tools"]
    # 인사·아카이브는 대화 창구가 없다
    assert [t["name"] for t in tools] == ["ask_planning", "ask_dev"]
    for t in tools:
        schema = t["inputSchema"]
        assert set(schema["properties"]) == {"message", "thread_id"}
        assert t["description"]
    dev = next(t for t in tools if t["name"] == "ask_dev")
    assert "작업 대기열" in dev["description"]  # 코딩 작업은 개발 서비스 MCP(axcellent-dev)
    every = _connect(client, "a-1", agents=list(mcp_gateway.AGENTS) + ["hr", "archive"])
    names = [t["name"] for t in _rpc(client, every, "tools/list").json()["result"]["tools"]]
    assert names == [f"ask_{a}" for a in CHAT]
    instructions = _rpc(client, token, "initialize").json()["result"]["instructions"]
    assert "인사" in instructions and "아카이브" in instructions  # 준비 중이라고 알린다


# ---- 막기 (#1530 검사 그대로) ----


def test_unchosen_employee_is_403(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning"])
    res = _call(client, token, "ask_finance", {"message": "손익"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "AGENT_NOT_CHOSEN"
    res = _call(client, token, "ask_nobody", {"message": "x"})
    assert res.json()["error"]["code"] == -32602
    assert spy.calls == []


def test_over_my_share_is_429(client: TestClient, env: Path, spy: Spy) -> None:
    _budget(env, 10_000)
    token = _connect(client, agents=["planning"], share=10)
    _usage("a-3", 1000)
    res = _call(client, token, "ask_planning", {"message": "요청"})
    assert res.status_code == 429 and res.json()["error"]["code"] == "LIMIT_REACHED"
    assert spy.calls == []


def test_company_cap_is_429(client: TestClient, env: Path, spy: Spy) -> None:
    _budget(env, 10_000, pct=50)
    token = _connect(client, agents=["planning"])
    _usage(None, 5000)
    res = _call(client, token, "ask_planning", {"message": "요청"})
    assert res.status_code == 429 and res.json()["error"]["code"] == "COMPANY_LIMIT_REACHED"
    assert spy.calls == []


# ---- 화면과 같은 대화 경로 ----


@pytest.mark.parametrize("agent", list(CHAT))
def test_ask_uses_the_screen_chat_path_as_the_token_owner(
    client: TestClient, spy: Spy, agent: str
) -> None:
    token = _connect(client, "a-1", agents=[agent])
    args = {
        "message": "이번 주 상황 알려 줘",
        "thread_id": "th-1",
        # 아래는 모두 버린다: 말한 사람·계정·skill은 토큰과 창구가 정한다
        "who": "someone-else",
        "account_id": "a-3",
        "on_behalf_of": "a-3",
        "skill": "issue_coupon",
    }
    res = _call(client, token, f"ask_{agent}", args)
    assert res.status_code == 200, res.text
    result = res.json()["result"]
    assert result["isError"] is False
    data = result["structuredContent"]
    assert data["agent"] == agent and data["thread_id"] == "th-1" and data["status"] == "talking"
    chat = [c for c in spy.calls if "_chat_" in c[1].skill]
    assert [c[1].skill for c in chat] == [f"{agent}_chat_send", f"{agent}_chat_get"]
    _, target = CHAT[agent]
    for got_target, request, bound in spy.calls:  # 고객지원 집계 읽기도 같은 계정으로
        assert got_target == target
        assert bound == (True, "a-1")  # on_behalf_of = 토큰 주인 계정
        assert request.meta.token == "dash-agent-token"  # 화면 경로와 같은 dashboard-api 요청
    for _, request, _ in chat:
        assert request.meta.tenant_id == "catbooks"  # 토큰의 회사
    send = next(r for _, r, _ in spy.calls if r.skill == f"{agent}_chat_send")
    who_key = "who"
    assert send.input[who_key] == "민지"
    assert "someone-else" not in json.dumps(send.model_dump(mode="json"), ensure_ascii=False)
    if agent == "support":  # 사람의 말은 untrusted로만, 대화가 읽는 집계는 숫자만
        assert send.untrusted == {"message": "이번 주 상황 알려 줘"}
        assert send.input["thread_id"] == "th-1" and "context" in send.input
    else:
        expect = {"who": "민지", "message": "이번 주 상황 알려 줘", "thread_id": "th-1"}
        if agent == "orchestrator":
            expect["buyer_id"] = "buy_0077"  # 화면과 같이 계정의 구매자 연결
        assert send.input == expect
    get = next(r for _, r, _ in spy.calls if r.skill == f"{agent}_chat_get")
    if agent == "support":
        assert get.input == {"thread_id": "th-1", "viewer": "민지", "role": "member"}
    else:
        assert get.input == {"who": "민지", "thread_id": "th-1"}
    assert on_behalf.bound() == (False, None)  # 요청이 끝나면 풀린다


def test_reply_still_coming_says_so_and_can_be_read_again(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["finance"])
    spy.status_after_get = "answering"
    first = _call(client, token, "ask_finance", {"message": "손익"}).json()["result"]
    data = first["structuredContent"]
    assert data["status"] == "answering" and data["thread_id"] == "th-1"
    assert "thread_id" in data["note"]
    spy.calls.clear()
    spy.status_after_get = "talking"
    again = _call(client, token, "ask_finance", {"thread_id": "th-1"}).json()["result"]
    assert again["structuredContent"]["status"] == "talking"
    assert spy.skills() == ["finance_chat_get"]  # 말 없이 다시 부르면 읽기만
    empty = _call(client, token, "ask_finance", {}).json()["result"]
    assert empty["isError"] is True


def test_department_refusal_comes_back_as_a_tool_error(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning"])
    spy.fail = (TaskStatus.FAILED, "forbidden")  # 남의 대화를 이어 쓰려 하면 부서가 막는다
    res = _call(client, token, "ask_planning", {"message": "x", "thread_id": "th-9"})
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["code"] == "FORBIDDEN"


def test_message_length_follows_the_screen(client: TestClient, spy: Spy) -> None:
    token = _connect(client, agents=["planning", "dev"])
    long = "가" * 2001
    assert _call(client, token, "ask_planning", {"message": long}).json()["result"]["isError"]
    assert spy.calls == []
    ok = _call(client, token, "ask_dev", {"message": long}).json()["result"]  # 개발은 4000자
    assert ok["isError"] is False


# ---- 관문 ----


def test_mcp_has_no_path_around_the_gate(client: TestClient, spy: Spy) -> None:
    """창구가 부를 수 있는 것은 대화 skill뿐이고, 그 skill은 화면과 같은 카드 규칙이다.

    - 창구의 skill 표는 직원마다 `<직원>_chat_send`·`<직원>_chat_get` 둘(+ 고객지원 집계 읽기)뿐
    - 그 skill은 받는 서비스 카드에서 요청자가 dashboard-api 하나, 등급 read·A다. 사람 승인이 필요한
      일(B·C)은 부서가 대화 중에 `execute()`로 올린다. 창구에는 그 일을 바로 부르는 도구가 없다
    - 도구 입력에 skill을 적어도 대화 skill만 나간다(위 경로 시험)
    """
    allowed_reads = {"inquiry_overview", "claim_stats", "feedback_overview"}
    for agent in CHAT:
        assert mcp_gateway.skills_of(agent) == {f"{agent}_chat_send", f"{agent}_chat_get"}
    every = _connect(client, "a-1", agents=list(CHAT))
    for agent in CHAT:
        _call(client, every, f"ask_{agent}", {"message": "환불해 줘", "skill": "refund_order"})
    sent = set(spy.skills())
    chat = {s for a in CHAT for s in mcp_gateway.skills_of(a)}
    assert sent <= chat | allowed_reads, sent - chat
    for agent, (_, target) in CHAT.items():
        for skill in mcp_gateway.skills_of(agent):
            service = route_target(target, skill)
            card = json.loads(
                (BACKEND / "services" / service / "agent-card.json").read_text(encoding="utf-8")
            )
            row = next(s for s in card["skills"] if s["id"] == skill)
            assert row["allowed_requesters"] == ["dashboard-api"], skill
            assert row["grade"] in ("read", "A"), skill
