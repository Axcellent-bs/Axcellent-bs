"""업무 요청 라우터: 켠 서비스면 A2A, 안 켠 서비스면 사람 할 일함, 조직도에도 없으면 no_assignee."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_agent_template import common_log, config, routing
from ax_agent_template.client import call_agent
from ax_contracts.data_access.common_log import CommonLogQuery
from ax_contracts.schemas import AgentRequest, RequestMeta

ORG = """
assignments:
  finance: {unit: 재무/정산}
  closing_report: {unit: 재무/정산, assignee: 정산 담당}
  claims: {unit: 고객지원}
"""


def _request(skill: str, **extra) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_req_route",
            token="ignored",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key=extra.pop("idempotency_key", None),
        ),
        skill=skill,
        input={"seller_id": "sel_1"},
        untrusted={"memo": "고객이 적은 글"},
        **extra,
    )


class Recorder:
    """보낸 요청을 모으고, A2A는 그대로 완료로, 할 일함은 받은 본문으로 만든 일로 답한다."""

    def __init__(self, inbox_status: int = 201) -> None:
        self.requests: list[httpx.Request] = []
        self.inbox_status = inbox_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if request.url.path == "/a2a":
            return httpx.Response(
                200, json={"meta": body["meta"], "status": "completed", "output": {"ok": True}}
            )
        if request.url.path == "/api/dashboard/handoffs":
            if self.inbox_status != 201:
                return httpx.Response(self.inbox_status)
            task = {
                **{k: v for k, v in body.items() if k != "idempotency_key"},
                "task_id": "hof_test1",
                "status": "open",
                "created_at": "2026-10-28T00:00:00Z",
            }
            return httpx.Response(201, json={"data": task, "meta": {}})
        return httpx.Response(404)


@pytest.fixture
def org(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "org.yaml"
    path.write_text(ORG, encoding="utf-8")
    monkeypatch.setenv(routing.ORG_ENV, str(path))
    return path


@pytest.fixture
def log_store():
    store = common_log.SqliteLogStore(":memory:")
    common_log.set_store(store)
    return store


@pytest.fixture(autouse=True)
def caller() -> None:
    config.set_agent_name("inquiry")


async def _call(target: str, req: AgentRequest, recorder: Recorder):
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as http_client:
        return await call_agent(target, req, http_client=http_client)


@pytest.mark.asyncio
async def test_an_enabled_service_is_called_over_a2a(monkeypatch, org) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry, claims")
    recorder = Recorder()
    result = await _call("support", _request("request_compensation"), recorder)
    assert result.status == "completed"
    [sent] = recorder.requests
    assert (sent.url.host, sent.url.path) == ("claims", "/a2a")  # 부서 이름 → 맡은 서비스


@pytest.mark.asyncio
async def test_with_no_enabled_list_every_service_is_on(monkeypatch) -> None:
    monkeypatch.delenv(routing.ENABLED_ENV, raising=False)
    recorder = Recorder()
    result = await _call("finance", _request("closing_report"), recorder)
    assert result.status == "completed"
    assert recorder.requests[0].url.path == "/a2a"


@pytest.mark.asyncio
async def test_a_disabled_service_becomes_a_human_task(monkeypatch, org, log_store) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    recorder = Recorder()
    req = _request("closing_report", idempotency_key="ctx_req_route:closing_report:sel_1")
    result = await _call("finance", req, recorder)

    assert (result.status, result.outcome, result.reason_code) == (
        "input-required",
        "handed_to_human",
        "dept_input",
    )
    assert result.output == {
        "task_id": "hof_test1",
        "to_service": "finance",
        "assignee_unit": "재무/정산",
        "assignee": "정산 담당",  # skill 줄이 서비스 줄보다 먼저다
    }
    [sent] = recorder.requests
    assert (sent.url.host, sent.url.path) == ("dashboard-api", "/api/dashboard/handoffs")
    assert sent.headers["authorization"] == "Bearer mock:inquiry"
    body = json.loads(sent.content)
    assert body["untrusted"] == {"memo": "고객이 적은 글"}  # 외부 입력 구획 유지
    assert body["input"] == {"seller_id": "sel_1"}
    assert body["idempotency_key"] == "ctx_req_route:closing_report:sel_1"

    [line] = log_store.query(CommonLogQuery(kinds=["task_status"])).records
    assert line.task_status == "input-required"
    assert line.payload["outcome"] == "handed_to_human"
    assert line.payload["task_id"] == "hof_test1"


@pytest.mark.asyncio
async def test_the_service_line_is_used_when_the_skill_has_none(monkeypatch, org) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    result = await _call("finance", _request("audit_report"), Recorder())
    assert result.output["assignee_unit"] == "재무/정산"
    assert result.output["assignee"] is None


@pytest.mark.asyncio
async def test_a_disabled_service_missing_from_the_org_chart_is_no_assignee(
    monkeypatch, org, log_store
) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    recorder = Recorder()
    # impact_analysis는 read지만 사람이 할 수 있는 일이라 넘기는 쪽이다
    result = await _call("data", _request("impact_analysis"), recorder)
    assert (result.status, result.outcome) == ("failed", "no_assignee")
    assert result.output["to_service"] == "data"
    assert recorder.requests == []  # 할 일함에도 A2A에도 보내지 않는다
    [line] = log_store.query(CommonLogQuery(kinds=["task_status"])).records
    assert line.payload["outcome"] == "no_assignee"


@pytest.mark.asyncio
async def test_without_an_org_chart_nothing_is_made_up(monkeypatch) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    monkeypatch.delenv(routing.ORG_ENV, raising=False)
    result = await _call("finance", _request("closing_report"), Recorder())
    assert result.outcome == "no_assignee"


@pytest.mark.asyncio
async def test_a_broken_org_chart_is_treated_as_empty(monkeypatch, tmp_path) -> None:
    path = tmp_path / "org.yaml"
    path.write_text("assignments:\n  finance: {assignee: 누군가}\n", encoding="utf-8")
    monkeypatch.setenv(routing.ORG_ENV, str(path))
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    result = await _call("finance", _request("closing_report"), Recorder())
    assert result.outcome == "no_assignee"


@pytest.mark.asyncio
async def test_an_unreachable_inbox_fails_instead_of_dropping_the_work(monkeypatch, org) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    result = await _call("finance", _request("closing_report"), Recorder(inbox_status=503))
    assert (result.status, result.outcome) == ("failed", "inbox_unreachable")


@pytest.mark.asyncio
async def test_shared_servers_and_addresses_are_never_handed_off(monkeypatch, org) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    recorder = Recorder()
    await _call("memory", _request("search"), recorder)
    await _call("http://localhost:9999", _request("echo"), recorder)
    assert [r.url.path for r in recorder.requests] == ["/a2a", "/a2a"]


def test_org_chart_keeps_unit_names_only_in_the_file(org) -> None:
    table = routing.load_org()
    assert table["claims"] == routing.Assignment("고객지원", None)
    assert routing.assignment_for("claims", "decide_claim") == routing.Assignment("고객지원", None)


@pytest.mark.asyncio
async def test_a_lookup_to_a_disabled_service_is_not_handed_off(
    monkeypatch, org, log_store
) -> None:
    """카드에서 read + handoff:false인 목록·조회는 받은 요청을 만들지 않는다."""
    monkeypatch.setenv(routing.ENABLED_ENV, "finance")
    recorder = Recorder()
    result = await _call("claims", _request("list_claims"), recorder)
    assert (result.status, result.outcome) == ("rejected", "service_disabled")
    assert result.output["to_service"] == "claims"
    assert recorder.requests == []  # 할 일함에도 A2A에도 보내지 않는다
    [line] = log_store.query(CommonLogQuery(kinds=["task_status"])).records
    assert (line.task_status, line.payload["outcome"]) == ("rejected", "service_disabled")


@pytest.mark.asyncio
async def test_read_work_like_a_notice_draft_is_still_handed_off(monkeypatch, tmp_path) -> None:
    """read 등급이어도 공지 초안처럼 사람이 대신할 수 있는 일은 받은 요청 1건이 된다."""
    org = tmp_path / "org.yaml"
    org.write_text("assignments:\n  coupon: {unit: 마케팅}\n", encoding="utf-8")
    monkeypatch.setenv(routing.ORG_ENV, str(org))
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    recorder = Recorder()
    result = await _call("marketing", _request("draft_notice"), recorder)
    assert result.outcome == "handed_to_human"
    assert [r.url.path for r in recorder.requests] == ["/api/dashboard/handoffs"]


@pytest.mark.asyncio
async def test_an_action_skill_to_a_disabled_service_is_one_human_task(monkeypatch, org) -> None:
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    recorder = Recorder()
    result = await _call("finance", _request("closing_report"), recorder)
    assert result.outcome == "handed_to_human"
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_without_a_readable_card_the_skill_is_handed_off(monkeypatch, org, tmp_path) -> None:
    """카드를 못 읽으면 조회인지 알 수 없어 지금처럼 넘긴다 (지어내서 막지 않는다)."""
    monkeypatch.setenv(routing.CARDS_ENV, str(tmp_path / "no-cards"))
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    recorder = Recorder()
    await _call("finance", _request("closing_report"), recorder)
    assert [r.url.path for r in recorder.requests] == ["/api/dashboard/handoffs"]


def test_lookup_flags_come_from_the_service_cards() -> None:
    assert routing.is_lookup("coupon", "list_active_notices") is True
    assert routing.is_lookup("coupon", "draft_notice") is False  # read지만 일
    assert routing.is_lookup("coupon", "issue_coupon") is False  # 실행
    assert routing.is_lookup("coupon", "no_such_skill") is False
