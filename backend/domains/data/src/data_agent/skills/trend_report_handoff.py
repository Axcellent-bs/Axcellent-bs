"""트렌드 분석 보고서 인사이트를 기획 receive_insight로 보낸다 (trend_report_handoff, grade A).

사람이 넣은 의뢰용(총괄 의뢰는 총괄 plan ①②③이 이미 보낸다). event_proposal_send와 같은 모양:
1. trend_report_insight와 같은 함수(`build_insight`)로 꾸러미. 없으면 not_sent + 이유
2. data 자신의 `execute("send_trend_report_insight")`
   (safety registry 항목 필요, 없으면 실행 안 함)
3. executed일 때만 `call_agent("planning", "receive_insight", 꾸러미)`

- idempotency_key = "<report_id>:insight", insight_id = "trend_report:<report_id>" (총괄과 같은 키라
  총괄이 먼저 보냈어도 기획이 duplicate로 받는다)
- 결과(created·duplicate·rejected, 기획 proposal_id, 보낸 시각, 누른 쪽)는
  보고서 저장소 `handoff` 열에.
  이미 created·duplicate면 기획을 다시 부르지 않고 duplicate. safety가 실행하지 않았거나 기획에 닿지
  못했으면 저장하지 않는다(다시 누를 수 있다)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.data import TrendReportHandoffOutput
from ax_contracts.schemas.planning import ReceiveInsightOutput
from data_agent.skills.trend_report_insight import build_insight

ACTION = "send_trend_report_insight"  # data 자신의 execute() 액션 (safety registry 항목 필요)
TARGET = "planning"
PLANNING_SKILL = "receive_insight"
DEADLINE = timedelta(seconds=60)
SENT = ("created", "duplicate")

Send = Callable[[str, AgentRequest], Awaitable[AgentResult]]
Gate = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class HandoffResult:
    status: TaskStatus
    outcome: str
    output: TrendReportHandoffOutput


def _out(report_id: str, outcome: str, reason: str | None = None,
         **extra: Any) -> TrendReportHandoffOutput:  # fmt: skip
    return TrendReportHandoffOutput(report_id=report_id, outcome=outcome, reason=reason, **extra)  # type: ignore[arg-type]


async def handoff(service: Any, report_id: str, meta: RequestMeta, now: datetime, *,
                  send: Send, gate: Gate) -> HandoffResult:  # fmt: skip
    """보고서 인사이트 하나를 기획에 보낸다 (safety 관문 먼저)."""
    store = service.store
    saved = store.handoff(report_id)
    if saved is not None and saved.get("outcome") in SENT:
        prev = TrendReportHandoffOutput.model_validate(saved)
        why = f"이미 기획에 보냄 ({prev.proposal_id or '번호 없음'})"
        return HandoffResult(TaskStatus.COMPLETED, "duplicate", prev.model_copy(update={
            "outcome": "duplicate", "reason": why}))  # fmt: skip
    insight, why = build_insight(service.get(report_id))
    if insight is None:
        return HandoffResult(TaskStatus.COMPLETED, "not_sent", _out(report_id, "not_sent", why))
    call_meta = meta.model_copy(update={
        "token": "internal:data",  # call_agent가 data 토큰으로 바꿔 보낸다
        "deadline": min(meta.deadline, datetime.now(UTC) + DEADLINE),
        "idempotency_key": f"{report_id}:insight",
        "requester": "data",
    })  # fmt: skip
    decision = await gate(
        ACTION, {"report_id": report_id, "insight_id": insight.insight_id}, call_meta,
        Evidence(ref_ids=[f"trend_report:{report_id}"],
                 data_summary=f"트렌드 분석 보고서 {report_id} → 기획 ({insight.title})",
                 collected_at=now),
    )  # fmt: skip
    if decision.status != "executed":
        # A2A REJECTED는 오류 코드(ErrorCode)만 받으니 FAILED + outcome rejected로 돌려준다
        return HandoffResult(TaskStatus.FAILED, "rejected", _out(
            report_id, "rejected", f"safety가 실행하지 않음 ({decision.status})"))  # fmt: skip
    reply = await send(TARGET, AgentRequest(meta=call_meta, skill=PLANNING_SKILL,
                                            input=insight.model_dump(mode="json")))  # fmt: skip
    if reply.status != TaskStatus.COMPLETED:
        got = reply.outcome or reply.status.value
        return HandoffResult(TaskStatus.FAILED, "rejected", _out(
            report_id, "rejected", f"기획에 보내지 못함 ({got})"))  # fmt: skip
    try:
        answer = ReceiveInsightOutput.model_validate(reply.output or {})
    except ValidationError:
        return HandoffResult(TaskStatus.FAILED, "rejected", _out(
            report_id, "rejected", "기획 응답 형식이 계약과 다름"))  # fmt: skip
    outcome, reason = "created", None
    if answer.outcome == "duplicate":
        outcome = "duplicate"
    elif answer.outcome == "deleted_recently":
        outcome = "rejected"
        reason = f"기획에서 최근 지운 제안({answer.proposal_id})과 같아 만들지 않음"
    out = _out(report_id, outcome, reason, proposal_id=answer.proposal_id, sent_at=now,
               by=meta.requester)  # fmt: skip
    store.save_handoff(report_id, out.model_dump(mode="json"))
    return HandoffResult(TaskStatus.COMPLETED, outcome, out)
