"""총괄이 부른 generate_media는 길어지면 긴 작업(task_id)으로 넘기고, 진행·완료를 알림으로 보낸다.

11/20 wr_82f7c1445b: 영상이 30초를 넘겨 총괄이 실패로 보고 3번 다시 불렀다. 그 재발을 막는 시험이다.
생성 함수는 가짜(asyncio.Event로 끝나는 시점을 정한다), 알림은 가짜 수집기다.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.schemas import ACCEPTED_ASYNC, AgentRequest, AgentResult, TaskStatus
from marketing_agent import handlers
from marketing_agent.media import long_run

CTX = "ctx_long_0001"
INPUT = {"post_draft_id": "drf_1", "media_kind": "video", "scene_template_id": "t", "fields": {}}


def _request(requester: str | None = "orchestrator", **input_over: Any) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "t",
                "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "requester": requester,
            },
            "skill": "generate_media",
            "input": INPUT | input_over,
        }
    )


def _result(req: AgentRequest, outcome: str = "generated") -> AgentResult:
    ok = outcome == "generated"
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.COMPLETED if ok else TaskStatus.FAILED,
        outcome=outcome,
        output={"media_id": "med_1", "asset_ref": "a.mp4"} if ok else {"code": outcome},
    )


class Sent:
    def __init__(self) -> None:
        self.progress: list[str] = []
        self.completed: list[dict[str, Any]] = []

    async def on_progress(self, request, source, task_id, **kw) -> None:  # noqa: ANN001
        assert source == "marketing"
        self.progress.append(task_id)

    async def on_completed(self, request, source, task_id, **kw) -> None:  # noqa: ANN001
        self.completed.append({"task_id": task_id, **kw})


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_MEDIA_QUICK_WAIT_S", "0.05")
    long_run.reset_for_tests()


async def _drain() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_fast_result_is_returned_as_is() -> None:
    """관문 보류·입력 오류처럼 빨리 끝나면 지금과 같은 결과를 그대로 준다(사람 확인 흐름 유지)."""
    req = _request()

    async def run() -> AgentResult:
        return _result(req, "quota_exceeded")

    sent = Sent()
    got = await long_run.run_for_orchestrator(
        req, run, progress=sent.on_progress, completed=sent.on_completed
    )
    assert got.status == TaskStatus.FAILED and got.outcome == "quota_exceeded"
    assert sent.completed == [] and sent.progress == []


@pytest.mark.asyncio
async def test_slow_generation_becomes_long_task_with_progress_and_one_completion() -> None:
    req = _request()
    finish = asyncio.Event()

    async def run() -> AgentResult:
        await finish.wait()
        return _result(req)

    sent = Sent()
    got = await long_run.run_for_orchestrator(
        req, run, progress=sent.on_progress, completed=sent.on_completed, heartbeat_s=0.02
    )
    assert got.status == TaskStatus.WORKING and got.outcome == ACCEPTED_ASYNC
    task_id = got.output["task_id"]
    assert got.output["expected_seconds"] == long_run.EXPECTED_S["video"]

    await _drain()
    assert sent.progress and set(sent.progress) == {task_id}  # 만드는 동안 생존 신호
    finish.set()
    await _drain()
    assert sent.completed == [
        {
            "task_id": task_id,
            "output": {"media_id": "med_1", "asset_ref": "a.mp4"},
            "failed": False,
            "note": None,
        }
    ]


@pytest.mark.asyncio
async def test_repeat_call_while_running_returns_same_task_and_does_not_generate_again() -> None:
    """총괄이 다시 불러도 두 번 만들지 않고 같은 작업 번호를 준다(in_progress 실패 없음)."""
    req = _request()
    finish = asyncio.Event()
    calls = 0

    async def run() -> AgentResult:
        nonlocal calls
        calls += 1
        await finish.wait()
        return _result(req)

    sent = Sent()
    first = await long_run.run_for_orchestrator(
        req, run, progress=sent.on_progress, completed=sent.on_completed
    )
    again = await long_run.run_for_orchestrator(
        _request(), run, progress=sent.on_progress, completed=sent.on_completed
    )
    assert again.status == TaskStatus.WORKING
    assert again.output["task_id"] == first.output["task_id"]
    assert calls == 1
    finish.set()
    await _drain()
    assert len(sent.completed) == 1


@pytest.mark.asyncio
async def test_slow_failure_and_crash_are_reported_as_failed_with_code_only() -> None:
    for outcome, boom in (("provider_error", False), (None, True)):
        long_run.reset_for_tests()
        req = _request(post_draft_id=f"drf_{outcome}")
        finish = asyncio.Event()

        async def run(
            req: AgentRequest = req,
            outcome: str | None = outcome,
            boom: bool = boom,
            finish: asyncio.Event = finish,
        ) -> AgentResult:
            await finish.wait()
            if boom:
                raise RuntimeError("secret prompt text")
            return _result(req, outcome)

        sent = Sent()
        got = await long_run.run_for_orchestrator(
            req, run, progress=sent.on_progress, completed=sent.on_completed
        )
        assert got.status == TaskStatus.WORKING
        finish.set()
        await _drain()
        [done] = sent.completed
        assert done["failed"] is True and done["output"] is None
        assert done["note"] == ("provider_error" if outcome else "internal_error")
        assert "secret" not in repr(done)


@pytest.mark.asyncio
async def test_progress_send_failure_does_not_stop_generation() -> None:
    req = _request()
    finish = asyncio.Event()

    async def run() -> AgentResult:
        await finish.wait()
        return _result(req)

    sent = Sent()

    async def broken(*a, **k) -> None:  # noqa: ANN002, ANN003
        raise ConnectionError("bus down")

    await long_run.run_for_orchestrator(
        req, run, progress=broken, completed=sent.on_completed, heartbeat_s=0.01
    )
    await _drain()
    finish.set()
    await _drain()
    assert len(sent.completed) == 1 and sent.completed[0]["failed"] is False


@pytest.mark.asyncio
async def test_skill_waits_as_before_for_non_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """사람 화면·다른 부서 요청은 지금처럼 끝날 때까지 기다린다."""
    seen: list[str | None] = []

    async def fake_flow(request: AgentRequest, **kw: Any) -> AgentResult:
        await asyncio.sleep(0.1)  # 짧은 대기(0.05초)보다 길다
        seen.append(request.meta.requester)
        return _result(request)

    monkeypatch.setattr(handlers, "generate_media_flow", fake_flow)
    human = await handlers.generate_media_skill(_request(requester="dashboard-api"))
    assert human.status == TaskStatus.COMPLETED and human.outcome == "generated"

    orch = await handlers.generate_media_skill(_request(requester="orchestrator"))
    assert orch.status == TaskStatus.WORKING and orch.outcome == ACCEPTED_ASYNC
    await _drain()
    assert seen == ["dashboard-api", "orchestrator"]
