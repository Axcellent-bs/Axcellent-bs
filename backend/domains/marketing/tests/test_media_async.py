"""generate_media 긴 작업: 총괄 호출은 접수 즉시 WORKING, 진행·완료는 알림, 같은 열쇠는 한 번만."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_agent_template import async_task, events
from ax_contracts.schemas import (
    ACCEPTED_ASYNC,
    AgentRequest,
    EventType,
    ExecuteDecision,
    TaskStatus,
)
from marketing_agent import handlers
from marketing_agent.media import async_generate, skill
from marketing_agent.media.ledger import MediaLedger

CTX = "ctx_media_async_1"
FIELDS = {"season": "spring", "color_theme": "soft pastel", "category": "그림책"}


class SlowGate:
    """안전 관문 자리. `release`를 부르기 전까지 공급자 호출로 넘어가지 못한다(긴 생성 흉내)."""

    def __init__(self) -> None:
        self.calls = 0
        self.event = asyncio.Event()
        self.error: Exception | None = None

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.calls += 1
        await self.event.wait()
        if self.error is not None:
            raise self.error
        return ExecuteDecision(execution_id="exe_a1", grade="B", status="executed")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    for name in (
        "MARKETING_IMAGE_PROVIDER",
        "MARKETING_VIDEO_PROVIDER",
        "MARKETING_MEDIA_LEDGER_DB",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "media"))
    handlers.reset_state()


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> SlowGate:
    fake = SlowGate()
    monkeypatch.setattr(skill, "execute", fake)
    return fake


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    got: list[Any] = []

    async def publish(envelope: Any) -> None:
        got.append(envelope)

    monkeypatch.setattr(async_task, "publish", publish)
    return got


def _request(requester: str | None = "orchestrator", **over: Any) -> AgentRequest:
    data = {
        "post_draft_id": "DRAFT-1",
        "media_kind": "image",
        "scene_template_id": "cat_bookstore_image",
        "fields": FIELDS,
    } | over
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "requester": requester,
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": data,
        }
    )


async def _drain() -> None:
    while async_generate._TASKS:  # noqa: SLF001
        await asyncio.gather(*list(async_generate._TASKS))  # noqa: SLF001


def _of(sent: list[Any], kind: EventType) -> list[Any]:
    return [e for e in sent if e.type == kind]


@pytest.mark.asyncio
async def test_orchestrator_call_returns_working_immediately(
    gate: SlowGate, sent: list[Any]
) -> None:
    result = await handlers.HANDLERS["generate_media"](_request())
    assert result.status == TaskStatus.WORKING and result.outcome == ACCEPTED_ASYNC
    assert result.output["task_id"].startswith("tsk_") and result.output["expected_seconds"] > 0
    assert not _of(sent, EventType.TASK_COMPLETED)  # 아직 생성 중
    gate.event.set()
    await _drain()


@pytest.mark.asyncio
async def test_completed_event_has_result_id_and_short_facts(
    gate: SlowGate, sent: list[Any]
) -> None:
    result = await handlers.HANDLERS["generate_media"](_request())
    gate.event.set()
    await _drain()
    done = _of(sent, EventType.TASK_COMPLETED)
    assert len(done) == 1
    data = done[0].data
    assert data["task_id"] == result.output["task_id"] and data["status"] == "done"
    assert data["output"]["media_id"].startswith("med_") and data["output"]["ai_generated"] is True
    assert "prompt" not in data["output"] and "fields" not in data["output"]
    assert done[0].idempotency_key == f"task.completed:{data['task_id']}"


@pytest.mark.asyncio
async def test_same_key_while_running_returns_same_task_without_second_charge(
    gate: SlowGate, sent: list[Any]
) -> None:
    first = await handlers.HANDLERS["generate_media"](_request())
    await asyncio.sleep(0)  # 첫 일이 관문 앞까지 간다 (장부 예약 완료)
    retry = await handlers.HANDLERS["generate_media"](_request())  # 총괄의 재전송
    assert retry.status == TaskStatus.WORKING
    assert retry.output["task_id"] == first.output["task_id"]
    gate.event.set()
    await _drain()
    assert gate.calls == 1  # 공급자 호출·과금은 한 번
    assert len(_of(sent, EventType.TASK_COMPLETED)) == 1
    month = datetime.now(UTC).strftime("%Y-%m")
    assert handlers.STATE.media_ledger.month_usage(month, "image") == 1


@pytest.mark.asyncio
async def test_different_input_is_a_different_task(gate: SlowGate, sent: list[Any]) -> None:
    a = await handlers.HANDLERS["generate_media"](_request())
    b = await handlers.HANDLERS["generate_media"](_request(post_draft_id="DRAFT-2"))
    assert a.output["task_id"] != b.output["task_id"]
    gate.event.set()
    await _drain()
    assert len(_of(sent, EventType.TASK_COMPLETED)) == 2


@pytest.mark.asyncio
async def test_failure_is_reported_as_failed_completion(gate: SlowGate, sent: list[Any]) -> None:
    gate.error = RuntimeError("boom")
    await handlers.HANDLERS["generate_media"](_request())
    gate.event.set()
    await _drain()
    data = _of(sent, EventType.TASK_COMPLETED)[0].data
    assert data["status"] == "failed" and data["note"] == "provider_failed"
    assert "boom" not in str(data)  # 예외 내용은 싣지 않는다
    assert not async_generate._RUNNING  # noqa: SLF001  (끝났으니 같은 요청을 다시 받을 수 있다)


@pytest.mark.asyncio
async def test_progress_heartbeat_while_running(
    gate: SlowGate, sent: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(async_generate, "HEARTBEAT_SECONDS", 0.01)
    await handlers.HANDLERS["generate_media"](_request())
    await asyncio.sleep(0.08)
    assert _of(sent, EventType.TASK_PROGRESS)
    gate.event.set()
    await _drain()


@pytest.mark.asyncio
async def test_non_orchestrator_call_stays_synchronous(gate: SlowGate, sent: list[Any]) -> None:
    gate.event.set()
    result = await handlers.HANDLERS["generate_media"](_request(requester="dashboard-api"))
    assert result.status == TaskStatus.COMPLETED and result.outcome == "generated"
    assert not sent


@pytest.mark.asyncio
async def test_in_progress_reply_says_not_retryable(gate: SlowGate) -> None:
    """프로세스 밖(옛 reserved 행)에서 막힌 경우: 다시 불러도 같은 답이라 재시도 금지."""
    ledger: MediaLedger = handlers.STATE.media_ledger
    sync = skill.generate_media(
        _request(requester=None),
        ledger=ledger,
        from_decision=handlers._from_decision,  # noqa: SLF001
    )
    first = asyncio.ensure_future(sync)
    await asyncio.sleep(0)
    second = await skill.generate_media(
        _request(requester=None),
        ledger=ledger,
        from_decision=handlers._from_decision,  # noqa: SLF001
    )
    assert second.outcome == "in_progress" and second.output["retryable"] is False
    gate.event.set()
    assert (await first).outcome == "generated"
