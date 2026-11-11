"""root_cause_hypotheses: LLM에는 요청 마감까지 남은 시간만 준다.

LLM 대기(60초·재시도 1번)가 총괄 단계 마감(30초)보다 길어 결과가 아예 남지 않던 것을 막는다.
마감 = meta.deadline − DATA_ROOT_CAUSE_MARGIN_S(기본 5초). 남은 시간이 3초 미만이면 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ax_agent_template.llm import LLMClient, LLMSettings
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import RootCauseInput
from data_agent.skills import root_cause
from data_agent.skills.root_cause import _Candidate, _Candidates, estimate_root_cause, llm_deadline

CTX = "ctx_inc_deadline"
START = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=20)
NOW = END + timedelta(minutes=5)  # 사실을 모을 때 쓰는 시연 시계
DATA = RootCauseInput(
    context_id=CTX,
    window_from=START,
    window_to=END,
    metric="payment_fail_rate",
    component="payment",
)
PR_BEFORE = {
    "pr_number": 412,
    "title": "결제 재시도 설정 변경",
    "merged_at": (START - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
}


def _meta(seconds: float) -> RequestMeta:
    return RequestMeta(
        context_id=CTX,
        token="mock:orchestrator",
        path=["orchestrator"],
        deadline=datetime.now(UTC) + timedelta(seconds=seconds),
    )


async def _send(target: str, req: AgentRequest) -> AgentResult:
    output: dict[str, Any] = {
        "find_related_prs": {"candidates": [PR_BEFORE]},
        "list_active_notices": {"items": [], "total": 0},
        "detect_inquiry_surge": {"evaluated_at": NOW.isoformat(), "surges": []},
        "card_search": {"hits": []},
    }[req.skill]
    return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=output)


class SlowLLM:
    """답하기까지 `delay`초 걸리는 가짜 LLM. 부른 횟수를 센다."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls = 0

    async def structured(self, prompt, schema, untrusted=None, **_):  # noqa: ANN001
        self.calls += 1
        await asyncio.sleep(self.delay)
        pr = next(f for f in untrusted["facts"] if f["kind"] == "deploy")
        return _Candidates(
            hypotheses=[
                _Candidate(
                    explanation="결제 재시도 설정 변경 배포가 원인일 수 있음",
                    fact_ids=[pr["fact_id"]],
                    confidence="high",
                )
            ]
        )


def test_deadline_thirty_seconds_ahead_leaves_twenty_five_for_the_llm(monkeypatch) -> None:
    monkeypatch.delenv(root_cause.MARGIN_ENV, raising=False)
    now = datetime.now(UTC)
    meta = SimpleNamespace(deadline=now + timedelta(seconds=30))
    assert (llm_deadline(meta, now) - now).total_seconds() == pytest.approx(25)


@pytest.mark.asyncio
async def test_slow_llm_is_cut_at_the_deadline_and_rules_take_over(monkeypatch) -> None:
    monkeypatch.delenv(root_cause.MARGIN_ENV, raising=False)
    llm = SlowLLM(delay=40)
    started = time.monotonic()
    result = await estimate_root_cause(DATA, _meta(8.5), llm, send=_send, now=NOW)  # LLM 3.5초
    took = time.monotonic() - started

    assert took < 6  # 40초를 기다리지 않는다
    assert llm.calls == 1
    assert result.fallback is True
    assert result.output.hypotheses  # 규칙 기반 후보가 남는다
    assert "LLM 응답 지연으로 시간 순서 규칙으로 고름" in result.output.summary
    assert "답하지 않아 끊음" in result.evidence.data_summary


@pytest.mark.asyncio
async def test_fast_llm_keeps_the_llm_answer(monkeypatch) -> None:
    monkeypatch.delenv(root_cause.MARGIN_ENV, raising=False)
    llm = SlowLLM(delay=0)
    result = await estimate_root_cause(DATA, _meta(30), llm, send=_send, now=NOW)
    assert result.fallback is False
    assert result.output.hypotheses[0].confidence == "high"
    assert "시간 순서 규칙" not in result.output.summary
    assert "끊음" not in result.evidence.data_summary
    assert "부르지 않음" not in result.evidence.data_summary


@pytest.mark.asyncio
async def test_too_little_time_left_skips_the_llm(monkeypatch) -> None:
    monkeypatch.delenv(root_cause.MARGIN_ENV, raising=False)
    llm = SlowLLM(delay=0)
    result = await estimate_root_cause(DATA, _meta(7), llm, send=_send, now=NOW)  # 남은 2초
    assert llm.calls == 0
    assert result.fallback is True
    assert "LLM 응답 지연으로" in result.output.summary
    assert "부르지 않음" in result.evidence.data_summary


def test_without_a_request_deadline_the_setting_is_used() -> None:
    now = datetime.now(UTC)
    no_deadline = SimpleNamespace()
    got = llm_deadline(no_deadline, now, {root_cause.DEADLINE_ENV: "12"})
    assert (got - now).total_seconds() == 12
    assert (llm_deadline(no_deadline, now, {}) - now).total_seconds() == 25


@pytest.mark.parametrize("bad", ["abc", "-1", "0", "nan", "inf", ""])
def test_bad_settings_fall_back_to_the_defaults(bad: str) -> None:
    now = datetime.now(UTC)
    no_deadline = SimpleNamespace()
    waited = llm_deadline(no_deadline, now, {root_cause.DEADLINE_ENV: bad}) - now
    assert waited.total_seconds() == 25
    meta = SimpleNamespace(deadline=now + timedelta(seconds=30))
    margin = (meta.deadline - llm_deadline(meta, now, {root_cause.MARGIN_ENV: bad})).total_seconds()
    assert margin == (0 if bad == "0" else 5)  # 여유 0초는 허용한다


def test_margin_setting_is_used() -> None:
    now = datetime.now(UTC)
    meta = SimpleNamespace(deadline=now + timedelta(seconds=30))
    got = llm_deadline(meta, now, {root_cause.MARGIN_ENV: "8"})
    assert (meta.deadline - got).total_seconds() == 8


class HangingTransport(httpx.AsyncBaseTransport):
    """답하지 않는 LLM 서버. 닫혔는지 기록한다."""

    def __init__(self) -> None:
        self.closed = False
        self.requests = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        await asyncio.sleep(40)
        return httpx.Response(500)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_cut_llm_call_closes_its_http_connection(monkeypatch) -> None:
    """끊긴 LLM 호출은 HTTP 클라이언트·연결을 남기지 않는다(실제 LLM 클라이언트)."""
    monkeypatch.delenv(root_cause.MARGIN_ENV, raising=False)
    transport = HangingTransport()
    llm = LLMClient(
        "data", LLMSettings(model="test-model", base_url="http://llm.test"), transport=transport
    )
    started = time.monotonic()
    result = await estimate_root_cause(DATA, _meta(8.5), llm, send=_send, now=NOW)
    assert time.monotonic() - started < 6
    assert transport.requests == 1
    assert transport.closed is True
    assert result.fallback is True
