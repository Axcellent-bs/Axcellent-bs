"""총괄이 부른 `generate_media`를 긴 작업으로 받는다 (긴 작업 규약 `ax_agent_template.async_task`).

왜: 영상 생성은 1~2분 걸린다. 총괄은 30초 안에 답이 없으면 실패로 보고 다시 불렀고, 다시 부른
요청은 장부가 "이미 만드는 중"(`in_progress`)으로 돌려줘 의뢰가 사람 확인으로 넘어갔다
(11/20 wr_82f7c1445b).

동작 (요청자가 총괄일 때만. 사람 화면·다른 부서는 지금처럼 끝날 때까지 기다린다):

1. 생성을 백그라운드로 시작하고 `MARKETING_MEDIA_QUICK_WAIT_S`(기본 20초)까지 기다린다. 그 안에
   끝나면(입력 오류·예산 초과·관문 보류·저장본 재사용 등) 결과를 그대로 돌려준다. 관문 보류
   (`held`)는 지금처럼 사람 확인으로 간다.
2. 그때까지 안 끝나면(공급자가 만드는 중) `WORKING + task_id + expected_seconds`로 접수만 알린다.
   만드는 동안 `HEARTBEAT_S`(60초)마다 `task.progress`, 끝나면 `task.completed`를 한 번 보낸다.
   `generated`가 아니면 실패 완료이고 사유는 결과 코드만 싣는다(문장·프롬프트는 싣지 않는다).
3. 같은 사건에서 같은 입력으로 다시 부르면 새로 만들지 않고 진행 중인 작업 번호를 돌려준다.

상태를 바꾸는 일(돈이 드는 공급자 호출)은 여전히 `generate_media` 안의 `execute()` 관문을 거친다.
이 모듈은 기다리는 방식만 바꾼다. 서버가 재시작되면 진행 중 작업은 사라지고, 총괄은 진행 알림이
끊긴 것으로 단계를 끝낸다(같은 키로 다시 부르면 장부가 끝난 결과를 돌려준다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable

from ax_agent_template import async_task
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus

log = logging.getLogger(__name__)

SOURCE = "marketing"
ORCHESTRATOR = "orchestrator"
HEARTBEAT_S = 60.0  # 총괄 기준(기본 300초)보다 자주
EXPECTED_S = {"video": 240.0, "audio": 120.0, "image": 90.0}
NOTE_MAX = 120

Run = Callable[[], Awaitable[AgentResult]]
Progress = Callable[..., Awaitable[None]]
Completed = Callable[..., Awaitable[None]]

# 같은 사건·같은 입력의 진행 중 작업: 키 → (task_id, 작업)
_RUNNING: dict[str, tuple[str, asyncio.Task[AgentResult]]] = {}
_WATCHERS: set[asyncio.Task[None]] = set()  # 백그라운드 작업이 GC로 사라지지 않게 잡아 둔다


def quick_wait_s() -> float:
    raw = os.environ.get("MARKETING_MEDIA_QUICK_WAIT_S", "")
    try:
        value = float(raw) if raw else 20.0
    except ValueError:
        return 20.0
    return min(max(value, 0.0), 25.0)  # 총괄 30초 제한 안에서 답해야 한다


def is_orchestrator(request: AgentRequest) -> bool:
    return request.meta.requester == ORCHESTRATOR


def _key(request: AgentRequest) -> str:
    body = json.dumps(request.input, sort_keys=True, ensure_ascii=False, default=str)
    raw = f"{request.meta.tenant_id}|{request.meta.context_id}|{body}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _expected(request: AgentRequest) -> float:
    kind = request.input.get("media_kind") if isinstance(request.input, dict) else None
    return EXPECTED_S.get(str(kind), EXPECTED_S["video"])


def _crashed(request: AgentRequest, error: BaseException) -> AgentResult:
    log.warning("generate_media 백그라운드 실패 (%s)", type(error).__name__)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="internal_error",
        output={"error": type(error).__name__},
    )


def _result_of(request: AgentRequest, task: asyncio.Task[AgentResult]) -> AgentResult:
    if task.cancelled():
        return _crashed(request, asyncio.CancelledError())
    error = task.exception()
    return _crashed(request, error) if error is not None else task.result()


async def run_for_orchestrator(
    request: AgentRequest,
    run: Run,
    *,
    progress: Progress = async_task.report_progress,
    completed: Completed = async_task.report_completed,
    heartbeat_s: float = HEARTBEAT_S,
) -> AgentResult:
    """총괄 요청을 짧게 기다려 보고, 길어지면 긴 작업으로 넘긴다."""
    key = _key(request)
    running = _RUNNING.get(key)
    if running is not None and not running[1].done():
        return async_task.accepted(request, running[0], expected_seconds=_expected(request))

    work = asyncio.create_task(run())
    done, _ = await asyncio.wait({work}, timeout=quick_wait_s())
    if done:
        return _result_of(request, work)

    task_id = async_task.new_task_id()
    _RUNNING[key] = (task_id, work)
    watcher = asyncio.create_task(
        _watch(request, key, task_id, work, progress, completed, heartbeat_s)
    )
    _WATCHERS.add(watcher)
    watcher.add_done_callback(_WATCHERS.discard)
    return async_task.accepted(request, task_id, expected_seconds=_expected(request))


async def _watch(
    request: AgentRequest,
    key: str,
    task_id: str,
    work: asyncio.Task[AgentResult],
    progress: Progress,
    completed: Completed,
    heartbeat_s: float,
) -> None:
    try:
        while True:
            done, _ = await asyncio.wait({work}, timeout=heartbeat_s)
            if done:
                break
            try:
                await progress(request, SOURCE, task_id, note="생성 중")
            except Exception as error:  # noqa: BLE001 - 알림을 못 보내도 생성은 계속한다
                name = type(error).__name__
                log.warning("generate_media %s 진행 알림 못 보냄 (%s)", task_id, name)
        result = _result_of(request, work)
        ok = result.status == TaskStatus.COMPLETED and result.outcome == "generated"
        try:
            await completed(
                request,
                SOURCE,
                task_id,
                output=(result.output or {}) if ok else None,
                failed=not ok,
                note=None if ok else (result.outcome or result.status.value)[:NOTE_MAX],
            )
        except Exception as error:  # noqa: BLE001 - 총괄은 진행 알림이 끊긴 것으로 끝낸다
            log.warning("generate_media %s 완료 알림 못 보냄 (%s)", task_id, type(error).__name__)
    finally:
        if _RUNNING.get(key, (None,))[0] == task_id:
            _RUNNING.pop(key, None)


def reset_for_tests() -> None:
    _RUNNING.clear()
