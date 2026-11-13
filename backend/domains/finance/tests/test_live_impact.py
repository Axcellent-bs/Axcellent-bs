"""실제 모드에서 data `impact_analysis`를 부르는 모양: 계약(`ImpactAnalysisInput`)대로 보내는지.

목 모드 시드만으로는 data가 받는 입력 형식이 틀려도 통과해서,
실제 호출을 가짜 `call_agent`로 받아 확인한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    ImpactAnalysisInput,
    RequestMeta,
    TaskStatus,
)
from finance_agent import sources
from finance_agent.sources import LiveSources, impact_input

META = RequestMeta(
    context_id="ctx_inc_0001", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
)
WINDOW = {"window_from": "2026-10-22T05:00:00Z", "window_to": "2026-10-22T05:40:00Z"}
DATA_OUT = {
    "failed_order_count": 128,
    "affected_customer_count": 2,
    "affected_customer_ids": ["buyer_anon_1", "buyer_anon_2"],
    "damage_amount": 5_430_000,
    "summary": "결제 실패 128건",
}


@pytest.fixture
def data_calls(monkeypatch: pytest.MonkeyPatch) -> list[AgentRequest]:
    """data 호출을 가로채 계약 출력을 돌려준다. 받은 요청을 쌓아 둔다."""
    calls: list[AgentRequest] = []

    async def fake_call_agent(name: str, request: AgentRequest) -> AgentResult:
        assert name == "data"
        calls.append(request)
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=dict(DATA_OUT))

    monkeypatch.setattr(sources, "call_agent", fake_call_agent)
    return calls


@pytest.mark.asyncio
async def test_impact_request_matches_data_contract(data_calls: list[AgentRequest]) -> None:
    impact = await LiveSources().impact(META, "ctx_inc_0001", {**WINDOW, "category": "book"})
    assert impact is not None
    assert (impact.failed_orders, impact.affected_customers, impact.lost_amount) == (
        128,
        2,
        5_430_000,
    )
    sent = data_calls[0]
    assert sent.skill == "impact_analysis"
    ImpactAnalysisInput.model_validate(sent.input)  # data가 그대로 받을 수 있는 입력
    assert set(sent.input) == {"window_from", "window_to", "category"}
    assert sent.meta.context_id == "ctx_inc_0001"  # 사건 번호는 입력이 아니라 meta로


@pytest.mark.asyncio
async def test_without_window_finance_does_not_ask_data(data_calls: list[AgentRequest]) -> None:
    assert await LiveSources().impact(META, "ctx_inc_0001", None) is None
    assert impact_input({"from": WINDOW["window_from"], "to": WINDOW["window_to"]}) is None
    assert data_calls == []


@pytest.mark.asyncio
async def test_data_output_outside_contract_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def wrong_output(name: str, request: AgentRequest) -> AgentResult:
        out: dict[str, Any] = {**DATA_OUT, "affected_customer_count": 5}  # id 수와 다름
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=out)

    monkeypatch.setattr(sources, "call_agent", wrong_output)
    assert await LiveSources().impact(META, "ctx_inc_0001", WINDOW) is None


# --- 영향 캐시: 해결로 닫힌 사건은 한 번만 묻는다 ---
# 재무 화면이 그릴 때마다 사건 8개를 다시 물어 대화방에 ×16이 쌓이던 문제 (2026-11-12, 251)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(sources.time, "monotonic", c)
    return c


@pytest.mark.asyncio
async def test_closed_incident_asks_data_only_once(
    data_calls: list[AgentRequest], clock: _Clock
) -> None:
    live = LiveSources()
    first = await live.impact(META, "ctx_inc_0001", WINDOW, final=True)
    clock.now += sources.IMPACT_CACHE_SECONDS * 10  # 50분 뒤 다시 그려도
    again = await live.impact(META, "ctx_inc_0001", WINDOW, final=True)
    assert len(data_calls) == 1
    assert again == first


@pytest.mark.asyncio
async def test_open_incident_asks_again_after_the_cache_time(
    data_calls: list[AgentRequest], clock: _Clock
) -> None:
    live = LiveSources()
    await live.impact(META, "ctx_inc_0001", WINDOW)
    clock.now += sources.IMPACT_CACHE_SECONDS - 1
    await live.impact(META, "ctx_inc_0001", WINDOW)
    assert len(data_calls) == 1  # 5분 안은 기억한 값
    clock.now += 2
    await live.impact(META, "ctx_inc_0001", WINDOW)
    assert len(data_calls) == 2  # 300초가 지나면 다시 묻는다


def _incident_row(status: str) -> dict[str, Any]:
    return {
        "context_id": "ctx_inc_0001",
        "component": "payment",
        "status": status,
        "opened_at": "2026-10-22T05:00:00Z",
        "closed_at": "2026-10-22T05:40:00Z",
        "impact_from": WINDOW["window_from"],
        "impact_to": WINDOW["window_to"],
        "recovered_at": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "final"), [("resolved", True), ("degraded", False)])
async def test_only_resolved_incident_is_final(
    monkeypatch: pytest.MonkeyPatch, status: str, final: bool
) -> None:
    """degraded는 나중에 복구 신호로 영향 구간이 바뀔 수 있어 계속 기억하지 않는다."""
    live = LiveSources()

    async def read_json(url: str, timeout: float) -> Any:
        return {"data": [_incident_row(status)]}

    monkeypatch.setattr(live, "_read_json", read_json)
    found = await live.incident_window("ctx_inc_0001")
    assert found is not None and found["final"] is final


# --- 진행 중 요청 합치기: 같은 사건을 동시에 여러 번 물어도 데이터분석에는 한 번 ---


class _SlowData:
    """data 호출을 문이 열릴 때까지 붙잡는다. `fail`이면 예외로 끝낸다."""

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self.gate = asyncio.Event()
        self.fail: Exception | None = None

    async def __call__(self, name: str, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        out = {**DATA_OUT, "started_at": "2026-10-22T05:41:00Z"}
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=out)


@pytest.fixture
def slow_data(monkeypatch: pytest.MonkeyPatch) -> _SlowData:
    fake = _SlowData()
    monkeypatch.setattr(sources, "call_agent", fake)
    return fake


async def _three_at_once(live: LiveSources) -> list[Any]:
    tasks = [asyncio.create_task(live.impact(META, "ctx_inc_0001", WINDOW)) for _ in range(3)]
    await asyncio.sleep(0)  # 셋 다 data 앞에서 기다리게
    return tasks


@pytest.mark.asyncio
async def test_three_calls_at_once_ask_data_once(slow_data: _SlowData) -> None:
    live = LiveSources()
    tasks = await _three_at_once(live)
    slow_data.gate.set()
    results = await asyncio.gather(*tasks)
    assert len(slow_data.calls) == 1
    assert results[0] is not None
    assert results[0] == results[1] == results[2]


@pytest.mark.asyncio
async def test_failed_inflight_call_fails_everyone_and_is_not_remembered(
    slow_data: _SlowData,
) -> None:
    live = LiveSources()
    tasks = await _three_at_once(live)
    slow_data.fail = TimeoutError("data 응답 없음")
    slow_data.gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert len(slow_data.calls) == 1
    assert all(isinstance(r, TimeoutError) for r in results)  # 기다리던 쪽도 같은 오류
    slow_data.fail = None
    assert await live.impact(META, "ctx_inc_0001", WINDOW) is not None
    assert len(slow_data.calls) == 2  # 실패는 기억하지 않아 다시 묻는다


@pytest.mark.asyncio
async def test_unanswered_call_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[AgentRequest] = []

    async def failed(name: str, request: AgentRequest) -> AgentResult:
        calls.append(request)
        return AgentResult(meta=request.meta, status=TaskStatus.FAILED)

    monkeypatch.setattr(sources, "call_agent", failed)
    live = LiveSources()
    assert await live.impact(META, "ctx_inc_0001", WINDOW) is None
    assert await live.impact(META, "ctx_inc_0001", WINDOW) is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_impact_log_has_queue_process_and_cache(
    slow_data: _SlowData, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="finance.impact")
    live = LiveSources()
    tasks = await _three_at_once(live)
    slow_data.gate.set()
    await asyncio.gather(*tasks)
    await live.impact(META, "ctx_inc_0001", WINDOW)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "finance.impact"]
    # miss는 응답을 받은 뒤에 남겨서 같이 기다린 줄(inflight)보다 늦게 찍힌다
    assert sorted(x["cache"] for x in lines) == ["hit", "inflight", "inflight", "miss"]
    assert lines[-1]["cache"] == "hit"
    miss = next(x for x in lines if x["cache"] == "miss")
    assert miss["context_id"] == "ctx_inc_0001" and miss["ok"] is True
    assert {"queue_ms", "process_ms", "total_ms"} <= set(miss)
    assert isinstance(miss["total_ms"], int)
    # 응답의 started_at(2026-10-22)은 지금보다 한참 전이라 queue_ms는 음수, process_ms는 큰 양수다:
    # 둘 다 응답의 started_at으로 나눴는지만 본다
    assert miss["queue_ms"] is not None and miss["process_ms"] is not None
    assert miss["queue_ms"] + miss["process_ms"] == miss["total_ms"]


def test_started_at_is_read_only_when_it_is_a_utc_time() -> None:
    assert sources._started_at(None) is None
    assert sources._started_at({"started_at": "not a time"}) is None
    assert sources._started_at({"started_at": "2026-10-22T05:41:00"}) is None  # 시간대 없음
    at = sources._started_at({"started_at": "2026-10-22T05:41:00Z"})
    assert at == datetime(2026, 10, 22, 5, 41, tzinfo=UTC)
