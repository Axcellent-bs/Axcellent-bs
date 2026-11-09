"""planning A2A skill 핸들러 (시나리오 A 필수 3개 + hr 연동 2개 + 변경 요청 접수).

- 입력은 계약 모델(`contracts/src/ax_contracts/schemas/planning.py`)로 검증하고, 출력도 계약 모델로
  만든 뒤 `output`에 넣는다. 형식을 새로 정의하지 않는다.
- `request.untrusted`는 읽지 않는다 (지시로 해석하지 않는다).
- 원인 판정 문장을 만들지 않는다 (원인 판정은 infra 몫). 근거는 `evidence`에 붙인다.
- 상태를 바꾸는 `link_incident`·`create_ticket`은 서비스 안에서 `execute()`를 거친다. 관문이
  거부하면 실행하지 않고 `failed`(outcome `gate_rejected`)로 돌려준다.
- 사람 버튼(기획서 확정·PR 머지·승급 승인)은 A2A로 받지 않는다. REST(`api.py`)로만 받는다.
- 기획 → 개발 넘기기(`start_dev`·`receive_dev_result`)는 같은 부서의 planning·dev 서비스끼리만
  주고받는다(A, execute 경유). 보내는 쪽은 `dev_handoff.py`다.
- hr 연동(`list_in_progress`·`verify_handover`, 계약 #103)은 읽기다. 담당자·영역 대응과 비교는
  `domain/handover.py`의 코드 규칙으로만 한다. 인계 내용의 좋고 나쁨은 판단하지 않는다.
"""

from __future__ import annotations

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.planning import (
    CreateTicketInput,
    CreateTicketOutput,
    DevResultInput,
    DevResultOutput,
    ReceiveCampaignUpdateInput,
    ReceiveCampaignUpdateOutput,
    ReceiveInsightInput,
    ReceiveInsightOutput,
    ReceivePlanningAnswerInput,
    ReceivePlanningAnswerOutput,
    RequestReleaseCheckInput,
    RequestReleaseCheckOutput,
)
from ax_workflow.a2a import _gate_rejected, _invalid, _missing_key, _requester
from ax_workflow.core import GateRefused
from planning_agent.runtime import get_service


async def create_ticket(request: AgentRequest) -> AgentResult:
    """변경 요청 접수 (A, execute 경유, #174). 같은 요청이면 linked_duplicate, 아니면 created.

    `request.untrusted`(고객 원문)는 읽지 않는다. `evidence` 요약만 저장한다.
    """
    try:
        inp = CreateTicketInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="missing_idempotency_key",
            output={"message": "상태 변경 skill에는 idempotency_key가 필요하다"},
        )
    try:
        result = await get_service().create_ticket(
            context_id=request.meta.context_id,
            requester=_requester(request),
            title=inp.title,
            evidence=inp.evidence,
            source_ref=inp.source_ref,
            evidence_ids=inp.evidence_ids,
            idempotency_key=request.meta.idempotency_key,
        )
    except GateRefused as exc:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="gate_rejected",
            output={"message": f"실행 관문이 {exc.action}을 허락하지 않아 접수하지 않았다"},
            evidence=Evidence(
                data_summary=f"execute({exc.action}) → {exc.decision.status}",
                ref_ids=[exc.decision.execution_id],
            ),
        )
    o = result.output
    out = CreateTicketOutput(
        request_id=o["request_id"],
        context_id=o["context_id"],
        outcome=result.outcome,  # type: ignore[arg-type]
        duplicate_of=o["duplicate_of"],
    )
    if out.outcome == "linked_duplicate":
        summary = f"같은 요청 {out.duplicate_of}에 연결 ({out.request_id}, 사건 {out.context_id})"
    else:
        summary = f"변경 요청 {out.request_id} 접수 (사건 {out.context_id})"
    refs = [f"req:{out.request_id}", f"ctx:{out.context_id}"]
    if out.duplicate_of:
        refs.append(f"req:{out.duplicate_of}")
    if inp.source_ref:
        refs.append(f"source:{inp.source_ref}")
    refs.extend(f"evidence:{e}" for e in inp.evidence_ids)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=["requests WHERE title = :title (같은 요청 판정, source_ref는 쓰지 않음)"],
            data_summary=summary,
            ref_ids=refs,
        ),
    )


async def receive_dev_result(request: AgentRequest) -> AgentResult:
    """planning 서비스: dev가 돌려준 작업·PR·요청 상태를 반영한다 (A, execute 경유)."""
    try:
        inp = DevResultInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().receive_dev_result(
            context_id=request.meta.context_id,
            requester=_requester(request),
            spec_id=inp.spec_id,
            request_id=inp.request_id,
            request_state=inp.request_state,
            tasks=inp.tasks,
            prs=inp.prs,
            idempotency_key=request.meta.idempotency_key,
            request=inp.request,
            spec=inp.spec,
            auto_policy=inp.auto_policy,
        )
    except ValidationError as exc:
        return _invalid(request, exc)
    except ValueError as exc:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="invalid_input",
            output={"message": str(exc)},
        )
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = DevResultOutput(applied=result.output["applied"])
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{inp.spec_id} 작업·PR {out.applied}건 반영 · {inp.request_id} "
            f"{inp.request_state}",
            ref_ids=[f"spec:{inp.spec_id}", f"req:{inp.request_id}"],
        ),
    )


async def receive_planning_answer(request: AgentRequest) -> AgentResult:
    """planning 서비스: 요청 서비스가 나중에 보낸 기획 질문 답 (A, execute 경유)."""
    try:
        inp = ReceivePlanningAnswerInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().receive_planning_answer(
            question_id=inp.question_id,
            request_id=inp.request_id,
            answer=inp.answer,
            answered_by=inp.answered_by,
            evidence_ids=inp.evidence_ids,
            idempotency_key=request.meta.idempotency_key,
        )
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = ReceivePlanningAnswerOutput.model_validate(result.output)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{inp.question_id} 답 {out.outcome}"
            + (f" · {out.spec_id}" if out.spec_id else ""),
            ref_ids=[f"question:{inp.question_id}", f"req:{inp.request_id}"],
        ),
    )


async def request_release_check(request: AgentRequest) -> AgentResult:
    """배포 전 기획서 대조 (A, dev → planning). 코드 규칙으로 ready / hold / needs_human."""
    try:
        inp = RequestReleaseCheckInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().request_release_check(
            context_id=request.meta.context_id,
            requester=_requester(request),
            inp=inp,
            idempotency_key=request.meta.idempotency_key,
        )
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = RequestReleaseCheckOutput.model_validate(result.output)
    why = f" ({', '.join(out.hold_reasons)})" if out.hold_reasons else ""
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.verdict,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{out.check_id} {inp.spec_id} 대조 {out.verdict}{why}"
            + (" · 배포 지시 보냄" if out.order_sent else ""),
            ref_ids=[f"spec:{inp.spec_id}", f"req:{inp.request_id}"]
            + [f"pr:{p.number}" for p in inp.prs],
        ),
    )


async def receive_insight(request: AgentRequest) -> AgentResult:
    """기획 제안 받기 (A, data·marketing → planning). 인사이트 한 건 → 제안 PROP-n (확인 대기)."""
    try:
        inp = ReceiveInsightInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().receive_insight(requester=_requester(request), inp=inp)
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = ReceiveInsightOutput.model_validate(result.output)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{out.proposal_id} {out.outcome} · {inp.skill} 근거 {len(inp.facts)}개",
            ref_ids=[f"proposal:{out.proposal_id}"] + ([inp.ref] if inp.ref else []),
        ),
    )


async def receive_campaign_update(request: AgentRequest) -> AgentResult:
    """넘긴 제안의 진행·성과 (A, marketing → planning)."""
    try:
        inp = ReceiveCampaignUpdateInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().receive_campaign_update(inp=inp, requester=_requester(request))
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = ReceiveCampaignUpdateOutput.model_validate(result.output)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{inp.proposal_id} {inp.stage} {out.outcome}",
            ref_ids=[f"proposal:{inp.proposal_id}", f"campaign:{inp.campaign_id}"],
        ),
    )


# 기획 서비스(planning 8101)가 받는 skill
PLANNING_HANDLERS = {
    "create_ticket": create_ticket,
    "receive_dev_result": receive_dev_result,
    "receive_planning_answer": receive_planning_answer,
    "request_release_check": request_release_check,
    "receive_insight": receive_insight,
    "receive_campaign_update": receive_campaign_update,
}
