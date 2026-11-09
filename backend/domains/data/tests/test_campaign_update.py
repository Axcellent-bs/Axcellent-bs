"""성과 결과를 기획에 보내기 (`campaign_update`). 실제 기획·safety는 부르지 않는다 (가짜).

- 보낸 요청이 공통 로그에 data → planning A2A(`request_received`, requester data)로 남는다
- 기능 플래그가 꺼져 있으면 기획을 부르지 않는다 (호출 0회)
- 재시도 때 같은 context_id·idempotency_key를 쓴다
- 화면 상태 문장 (보냄·보낼 예정·실패)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_agent_template import common_log
from ax_agent_template.config import agent_name_scope
from ax_agent_template.server import create_agent_app
from ax_contracts.data_access.common_log import CommonLogQuery
from ax_contracts.schemas import AgentCard, AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.data import (
    CampaignResultOutput,
    CampaignSalesEffect,
    CampaignSalesVerdict,
    VideoCheckpoint,
    VideoPerformanceOutput,
)
from ax_contracts.schemas.execute import ExecuteDecision
from data_agent import campaign_update as cu

PUB = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
DONE = PUB + timedelta(days=7, hours=1)
PLANNING_CARD = Path(__file__).resolve().parents[2] / "planning" / "agent-card.json"


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    common_log.set_store(common_log.SqliteLogStore(str(tmp_path / "common.db")))
    yield
    common_log.reset_store()


def _out(proposal: str | None = "PROP-3", verdict: str = "more") -> CampaignResultOutput:
    video = VideoPerformanceOutput(
        video_id="aaaaaaaaaaa", campaign_id="cmp_1", verdict="achieved",
        checkpoints=[VideoCheckpoint(checkpoint="7d", status="known",
                                     target_ratio=1.4)])  # fmt: skip
    sales = CampaignSalesEffect(status="known", verdict=CampaignSalesVerdict(
        status="known", verdict=verdict, label="더 팔림", incremental_gross_krw=1_200_000,
        incremental_orders=40))  # fmt: skip
    return CampaignResultOutput(status="known", campaign_id="cmp_1", campaign_name="책추천",
                                insight_id="ins_1", proposal_id=proposal, window_from=PUB,
                                window_to=PUB + timedelta(days=7), videos=[video],
                                sales=sales)  # fmt: skip


async def _executed(action, params, meta, evidence):  # noqa: ANN001, ANN202
    return ExecuteDecision(execution_id="exe_test", grade="A", status="executed")


def _fake_planning() -> object:
    """기획 카드(receive_campaign_update만)로 만든 가짜 기획 서버. 받으면 applied."""
    card = json.loads(PLANNING_CARD.read_text(encoding="utf-8"))
    card["skills"] = [s for s in card["skills"] if s["id"] == "receive_campaign_update"]
    got: list[AgentRequest] = []

    async def receive(request: AgentRequest) -> AgentResult:
        got.append(request)
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, outcome="applied",
                           output={"outcome": "applied"})  # fmt: skip

    app = create_agent_app(AgentCard.model_validate(card), {"receive_campaign_update": receive})
    app.state.got = got
    return app


def test_a_send_is_logged_as_a_data_to_planning_a2a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_agent_template.client import call_agent

    app = _fake_planning()
    monkeypatch.setenv("AX_PLANNING_URL", "http://planning.test")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    async def send(target: str, req: AgentRequest) -> AgentResult:
        return await call_agent(target, req, http_client=client)

    store = cu.SendStore(tmp_path / "sends.db")
    env = {cu.FLAG_ENV: "1"}
    with agent_name_scope("data"):
        calls = asyncio.run(cu.send_ready([_out()], store, env, DONE, send=send, gate=_executed))
    assert calls == 1
    (req,) = app.state.got
    assert req.meta.idempotency_key == "cmp_1:result"
    assert req.input["direction"] == "늘었음" and req.input["stage"] == "result"
    log = common_log.get_store()
    assert log is not None
    (line,) = log.query(CommonLogQuery(kinds=["request_received"])).records
    assert (line.source, line.requester, line.skill) == ("planning", "data",
                                                         "receive_campaign_update")  # fmt: skip
    assert line.context_id == req.meta.context_id and line.context_id.startswith("ctx_req_data_")
    status = cu.send_status(_out(), store, env, DONE)
    assert (status.state, status.chip) == ("sent", "기획에 보냄")
    assert status.line == 'PROP-3 · "늘었음" · 11월 8일 13:00'
    store.close()


def test_flag_off_calls_nothing(tmp_path: Path) -> None:
    called: list[str] = []

    async def send(target: str, req: AgentRequest) -> AgentResult:
        called.append(target)
        raise AssertionError("부르면 안 됨")

    store = cu.SendStore(tmp_path / "sends.db")
    for env in ({}, {cu.FLAG_ENV: "0"}, {cu.FLAG_ENV: "false"}):
        assert asyncio.run(cu.send_ready([_out()], store, env, DONE, send=send,
                                         gate=_executed)) == 0  # fmt: skip
        assert asyncio.run(cu.tick(env, DONE)) == 0
    assert called == [] and store.get("cmp_1") is None
    store.close()


def test_retries_keep_the_same_context_and_key(tmp_path: Path) -> None:
    seen: list[tuple[str, str | None]] = []

    async def down(target: str, req: AgentRequest) -> AgentResult:
        seen.append((req.meta.context_id, req.meta.idempotency_key))
        return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="http_503")

    store = cu.SendStore(tmp_path / "sends.db")
    env = {cu.FLAG_ENV: "1"}
    for h in range(4):
        asyncio.run(cu.send_ready([_out()], store, env, DONE + timedelta(hours=h), send=down,
                                  gate=_executed))  # fmt: skip
    assert len(seen) == 3 and len(set(seen)) == 1  # 하루 3번, 같은 사건 번호·키
    status = cu.send_status(_out(), store, env, DONE + timedelta(hours=4))
    assert (status.state, status.chip) == ("failed", "보내기 실패 · 다시 시도 중 (3/3)")
    store.close()


def test_pending_and_not_sent_lines(tmp_path: Path) -> None:
    env = {cu.FLAG_ENV: "1"}
    measuring = _out()
    measuring.videos[0].verdict = "measuring"
    wait = cu.send_status(measuring, None, env, PUB + timedelta(days=3))
    assert (wait.state, wait.chip) == ("pending", "기획에 보낼 예정")
    assert wait.line == "7일 측정이 끝나는 11월 8일 12:00 이후 자동으로 보냅니다"
    skip = cu.send_status(_out(proposal=None), None, env, DONE)
    assert (skip.state, skip.chip, skip.line) == ("not_sent", "보내지 않음", "기획 제안 번호 없음")


def test_safety_not_executing_does_not_call_planning(tmp_path: Path) -> None:
    async def send(target: str, req: AgentRequest) -> AgentResult:
        raise AssertionError("safety가 실행하지 않으면 기획을 부르지 않음")

    async def rejected(action, params, meta, evidence):  # noqa: ANN001, ANN202
        assert action == "send_campaign_update" and meta.idempotency_key == "cmp_1:result"
        return ExecuteDecision(execution_id="exe_x", grade="C", status="rejected")

    store = cu.SendStore(tmp_path / "sends.db")
    calls = asyncio.run(cu.send_ready([_out()], store, {cu.FLAG_ENV: "1"}, DONE, send=send,
                                      gate=rejected))  # fmt: skip
    row = store.get("cmp_1")
    assert calls == 0 and row is not None and row.state == "failed"
    assert row.reason == "safety가 실행하지 않음 (rejected)"
    store.close()
