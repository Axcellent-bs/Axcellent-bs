"""자동 게시 스위치 스킬: `get_publish_control`(읽기)·`set_publish_control`(쓰기, 안전 관문).

계정이 정지돼도 마케팅 전체는 계속 돌고 자동 게시(`publish_sns`)만 꺼진다.
수동 게시는 후속 PR이다. 상태는 게시 대기 DB의
`sns_publish_control` 표에 있다(`sns_publish/control.py`).
바꾸는 일은 상태 변경이라 `execute()`를 지난다:

- 중지(`paused=true`)는 A등급: 마찰 없이 바로 된다(멈추는 쪽은 안전한 방향).
- 재개(`paused=false`)는 안전 레이어 규칙(`publish_resume_needs_human`, `resume`>0)으로 C등급:
  사람이 승인해야 한다. 보류되면 `held`와 `review_id`를 돌려주고, 승인한 뒤 같은 요청을 다시 보내면
  (키는 현재 상태 시각이 들어가 있어 상태가 바뀌면 옛 승인은 쓰이지 않는다) 그때 적용한다.
- `updated_by`는 대시보드 API가 사람 토큰에서 채워 보낸다(요청자가 dashboard-api일 때만 쓰기 가능,
  Agent Card). 바뀐 내력은 `sns_publish_control_log`에 남는다(누가·언제·무엇으로·execution_id).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, StrictBool, ValidationError

from ax_agent_template.execute import execute
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent.sns_publish import publisher
from marketing_agent.sns_publish.control import (
    DEFAULT_CHANNEL,
    MAX_REASON,
    PublishControlError,
    clean_reason,
    read_control,
)
from marketing_agent.sns_publish.pending import PendingStorageError

ACTION = "set_publish_control"
Channel = Literal["instagram"]  # 채널·계정이 늘면 여기와 pending 표의 channel 값만 늘린다


class GetPublishControlInput(AxModel):
    channel: Channel = DEFAULT_CHANNEL


class SetPublishControlInput(AxModel):
    channel: Channel = DEFAULT_CHANNEL
    paused: StrictBool
    reason: str = Field(default="", max_length=MAX_REASON)
    updated_by: str = Field(min_length=1, max_length=64)  # 사람 이름: 대시보드 API가 토큰에서 채움


class PublishControlOutput(AxModel):
    channel: str
    paused: bool
    reason: str = ""
    updated_by: str | None = None
    updated_at: str | None = None
    source: Literal["db", "default"] = (
        "default"  # default = 행이 없다(환경변수 초기값 또는 게시 가능)
    )
    history: list[dict[str, Any]] = Field(default_factory=list)
    auto_publish_off: bool = False  # paused와 같은 뜻: 자동 게시가 꺼져 있다
    manual_available: bool = False  # 수동 게시 가능 여부. 후속 PR에서 true가 되는 자리


def _state(channel: str, limit: int = 5) -> dict[str, Any]:
    store = publisher.store()
    control = read_control(store, channel)
    return PublishControlOutput(
        channel=channel,
        paused=control.paused,
        auto_publish_off=control.paused,
        reason=control.reason,
        updated_by=control.updated_by,
        updated_at=control.updated_at,
        source="db" if control.updated_by is not None else "default",
        history=store.control_log(channel, limit),
    ).model_dump(mode="json")


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="invalid_input",
        output={"errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]},
    )


def _unreadable(request: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="publish_control_error",
        output={"reason": "게시 중지 설정을 읽거나 저장할 수 없다. 게시는 막힌 상태로 둔다"},
    )


async def get_publish_control(request: AgentRequest) -> AgentResult:
    try:
        data = GetPublishControlInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    try:
        output = _state(data.channel)
    except (PublishControlError, PendingStorageError):
        return _unreadable(request)
    return AgentResult(
        meta=request.meta, status=TaskStatus.COMPLETED, outcome="state", output=output
    )


async def set_publish_control(request: AgentRequest, *, from_decision: Any) -> AgentResult:
    """`from_decision`은 `handlers._from_decision`(executed가 아니면 결과를 만든다)."""
    try:
        data = SetPublishControlInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    reason = clean_reason(data.reason)
    try:
        current = read_control(publisher.store(), data.channel)
    except (PublishControlError, PendingStorageError):
        return _unreadable(request)
    if current.updated_by is not None and (current.paused, current.reason) == (data.paused, reason):
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="unchanged",
            output=_state(data.channel),
        )
    digest = hashlib.sha256(
        "|".join([data.channel, str(data.paused), reason, current.updated_at or "none"]).encode()
    ).hexdigest()[:24]
    key = f"{request.meta.context_id}:{ACTION}:{digest}"
    verb = "끄기" if data.paused else "켜기"
    evidence = Evidence(
        queries=[ACTION],
        data_summary=f"{data.channel} 자동 게시 {verb} 요청 ({data.updated_by})",
        collected_at=datetime.now(UTC),
        ref_ids=[data.channel],
    )
    decision = await execute(
        ACTION,
        {
            "channel": data.channel,
            "paused": data.paused,
            "resume": 0 if data.paused else 1,  # 재개만 안전 규칙에 걸려 사람 승인(C)
            "reason": reason,
            "updated_by": data.updated_by,
        },
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    blocked = from_decision(request, decision, evidence)
    if blocked:  # 보류·거절: 바꾸지 않는다. 승인 뒤 같은 요청을 다시 보내면 이어서 처리한다
        return blocked
    try:
        publisher.store().control_set(
            data.channel, data.paused, reason, data.updated_by, decision.execution_id
        )
        output = _state(data.channel)
    except (PublishControlError, PendingStorageError):
        return _unreadable(request)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="updated",
        output=output,
        evidence=evidence,
    )
