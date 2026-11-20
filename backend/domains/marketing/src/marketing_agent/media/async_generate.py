"""총괄이 부르는 `generate_media`를 긴 작업으로 받는다 (`ax_agent_template.async_task` 규약).

영상 생성은 총괄의 한 호출 마감(30초)보다 오래 걸린다. 동기로 답하면 첫 호출이 마감에 걸려
FAILED가 되고, 총괄이 같은 요청을 다시 보내면 장부(`ledger.reserve`)가 같은 열쇠의 `reserved`
행을 보고 `in_progress`로 거절한다(트렌드 보고서 `trend_report_create`와 같은 문제·같은 해법).

- 요청자가 총괄(`meta.requester == "orchestrator"`)이면 접수 즉시 `WORKING` + `task_id`
  (+`expected_seconds`)로 답하고, 일은 백그라운드에서 한다. 진행은 `task.progress`(생존 신호),
  끝나면 `task.completed` 한 번. `output`은 결과 번호(`media_id`)와 짧은 사실만 담는다.
- 같은 열쇠(테넌트·context·skill·입력 해시)로 이미 도는 일이 있으면 새 일을 만들지 않고 그 일의
  `task_id`로 `WORKING`을 다시 돌려준다. 공급자 호출·과금은 한 번뿐이다.
- 총괄이 아닌 호출(화면·같은 부서 안의 흐름)은 예전처럼 동기로 한다.
- 실제 생성(`generate_media_flow`)은 그대로라 안전 관문·월 사용량 예산·장부는 달라지지 않는다.
- 프로세스가 죽으면 이 표는 사라진다. 그때 장부에 남은 `reserved` 행은 `generate_media`가
  `in_progress` + `retryable: false`로 답한다(다시 부르면 막히니 재시도하지 않는다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ax_agent_template import async_task
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus

log = logging.getLogger("marketing_agent.media.async_generate")
SOURCE = "marketing"
ORCHESTRATOR = "orchestrator"
HEARTBEAT_SECONDS = 60.0  # 총괄 기본 끊김 기준(300초)보다 자주
NOTE_MAX = 200
EXPECTED_SECONDS = {"image": 60.0, "audio": 120.0, "video": 600.0}
_DEFAULT_EXPECTED = 300.0
# 완료 알림에 싣는 짧은 사실 (결과 번호 `media_id` 포함). 긴 필드(프롬프트·기획 원문)는 뺀다
_FACT_KEYS = (
    "media_id", "media_kind", "post_draft_id", "asset_ref", "sha256", "provider", "model_id",
    "resolution", "quality", "duration_s", "clip_count", "muted", "usage", "usage_source",
    "ai_generated", "execution_id", "request_sha256", "scene_template_id", "replayed",
)  # fmt: skip

Runner = Callable[[AgentRequest], Awaitable[AgentResult]]

_RUNNING: dict[str, str] = {}  # 열쇠 → task_id (이 프로세스 안에서 도는 일)
_TASKS: set[asyncio.Task[None]] = set()  # 백그라운드 일을 붙잡아 둔다


def reset() -> None:
    """테스트용: 도는 일 표를 비운다."""
    _RUNNING.clear()
    _TASKS.clear()


def is_orchestrator(request: AgentRequest) -> bool:
    return request.meta.requester == ORCHESTRATOR


def task_key(request: AgentRequest) -> str:
    """같은 요청이면 같은 열쇠 (같은 사건·같은 입력). 입력은 정렬한 JSON 해시."""
    body = json.dumps(request.input, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    return f"{request.meta.tenant_id}:{request.meta.context_id}:{request.skill}:{digest}"


def expected_seconds(request: AgentRequest) -> float:
    kind = request.input.get("media_kind") if isinstance(request.input, dict) else None
    return EXPECTED_SECONDS.get(str(kind), _DEFAULT_EXPECTED)


def running_task_id(request: AgentRequest) -> str | None:
    return _RUNNING.get(task_key(request))


def completed_output(result: AgentResult) -> dict[str, Any]:
    out = result.output or {}
    facts = {k: out[k] for k in _FACT_KEYS if k in out}
    return {"outcome": result.outcome, **facts}


def _failure(result: AgentResult) -> tuple[dict[str, Any], str]:
    out = dict(result.output or {})
    detail = {k: v for k, v in out.items() if isinstance(v, str | int | float | bool)}
    detail["outcome"] = result.outcome or "failed"
    detail["status"] = str(result.status)
    note = (result.outcome or "failed")[:NOTE_MAX]
    return detail, note


async def _heartbeat(request: AgentRequest, task_id: str, work: asyncio.Task[Any]) -> None:
    ticks = 0
    while True:
        done, _ = await asyncio.wait({work}, timeout=HEARTBEAT_SECONDS)
        if done:
            return
        ticks += 1
        try:
            await async_task.report_progress(
                request, SOURCE, task_id, note=f"생성 중 ({int(ticks * HEARTBEAT_SECONDS)}초 경과)"
            )
        except Exception as error:  # noqa: BLE001 - 알림을 못 보내도 생성은 계속한다
            log.warning("generate_media %s 진행 알림 못 보냄 (%s)", task_id, type(error).__name__)


async def _work(request: AgentRequest, task_id: str, key: str, run: Runner) -> None:
    try:
        job = asyncio.ensure_future(run(request))
        beat = asyncio.ensure_future(_heartbeat(request, task_id, job))
        try:
            result = await job
        finally:
            beat.cancel()
        if result.status == TaskStatus.COMPLETED and result.outcome == "generated":
            await async_task.report_completed(
                request, SOURCE, task_id, output=completed_output(result)
            )
        else:
            detail, note = _failure(result)
            await async_task.report_completed(
                request, SOURCE, task_id, output=detail, failed=True, note=note
            )
    except Exception as error:  # noqa: BLE001 - 예외 내용에 비밀이 섞일 수 있어 종류만 남긴다
        log.warning("generate_media %s 실패 (%s)", task_id, type(error).__name__)
        try:
            await async_task.report_completed(
                request, SOURCE, task_id,
                output={"outcome": "provider_failed", "error_type": type(error).__name__},
                failed=True, note="provider_failed",
            )  # fmt: skip
        except Exception as send_error:  # noqa: BLE001
            log.warning("generate_media %s 완료 알림 못 보냄 (%s)", task_id,
                        type(send_error).__name__)  # fmt: skip
    finally:
        _RUNNING.pop(key, None)


async def generate_media_async(request: AgentRequest, run: Runner) -> AgentResult:
    """접수 즉시 WORKING. 같은 열쇠로 이미 도는 일이 있으면 그 task_id로 WORKING."""
    key = task_key(request)
    task_id = _RUNNING.get(key)
    if task_id is None:  # 확인과 등록 사이에 await가 없어 같은 열쇠가 두 번 시작되지 않는다
        task_id = async_task.new_task_id()
        _RUNNING[key] = task_id
        job = asyncio.create_task(_work(request, task_id, key, run))
        _TASKS.add(job)
        job.add_done_callback(_TASKS.discard)
    return async_task.accepted(request, task_id, expected_seconds=expected_seconds(request))
