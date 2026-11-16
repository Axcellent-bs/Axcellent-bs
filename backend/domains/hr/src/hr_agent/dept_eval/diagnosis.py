"""`request_agent_diagnosis`: 확인 필요가 반복되면 인사 AI가 총괄 AI에 진단을 요청한다.

아카이브 AI가 이슈의 단계 시간과 판정을 카드에 저장한 뒤 부른다. 인사는 저장된 판정을 읽어 같은
(에이전트, 단계)의 확인 필요가 반복됐는지 보고, 반복됐으면 총괄 `diagnose_agent_issue`로 보낸다.
원인은 인사가 모른다. 시간으로 본 결과(어느 단계가 몇 번, 근거 이슈)만 보내고 진단은 총괄이 한다.

- 판정(`judge_step_timings`)은 그대로 순수 계산이다. 이 skill이 요청 보내기를 따로 맡는다.
- 총괄이 못 받아도(꺼짐·시간 초과) 판정 저장은 막지 않는다. 다음 판정 때 같은 조건이면 다시 보낸다.
- 같은 반복은 총괄이 열린 요청에 합치고 에이전트당 하루 한도도 센다(인사는 상태를 쌓지 않는다).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.diagnosis import (
    DiagnoseAgentIssueInput,
    DiagnoseAgentIssueOutput,
    DiagnosisEvidence,
    RequestAgentDiagnosisInput,
    RequestAgentDiagnosisOutput,
    SentDiagnosis,
)
from hr_agent.dept_eval.cards import load_cards
from hr_agent.dept_eval.criteria import CriteriaError, load_criteria
from hr_agent.dept_eval.repeat import Finding, find_repeats
from hr_agent.dept_eval.summary import _event, _latest_cards
from hr_agent.results import completed, failed, invalid_input, make_evidence
from hr_agent.services import Services

CALL_TIMEOUT_SECONDS = 10
TARGET = "orchestrator"
SKILL = "diagnose_agent_issue"
LIMIT = 30  # 살펴볼 최근 이슈 수 (evaluate_agents 기본과 같다)

# (요청 메타, 진단 요청) -> 총괄의 답. 못 보냈으면 None
SendFn = Callable[
    [RequestMeta, DiagnoseAgentIssueInput], Awaitable[DiagnoseAgentIssueOutput | None]
]


async def send_to_orchestrator(
    meta: RequestMeta, payload: DiagnoseAgentIssueInput
) -> DiagnoseAgentIssueOutput | None:
    deadline = min(meta.deadline, datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS))
    newest = payload.evidence[0].context_id
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=meta.context_id,
            tenant_id=meta.tenant_id,
            token="replaced-by-call_agent",
            # 이슈를 기록하던 호출(총괄 → 아카이브 → 인사)의 뒤를 잇는 새 요청이다.
            # 경로를 이으면 총괄이 이미 있어 순환(LOOP_DETECTED)으로 거절되므로 새로 시작한다
            path=[],
            deadline=deadline,
            idempotency_key=f"diag:{payload.agent}:{payload.step_id}:{newest}",
        ),
        skill=SKILL,
        input=payload.model_dump(mode="json"),
    )
    try:
        result = await call_agent(TARGET, sent)
    except Exception:  # noqa: BLE001 - 총괄에 못 보내도 판정 저장은 막지 않는다
        return None
    if result.status != TaskStatus.COMPLETED:
        return None
    try:
        return DiagnoseAgentIssueOutput.model_validate(result.output)
    except ValueError:
        return None


def _payload(finding: Finding, version: int, now: datetime) -> DiagnoseAgentIssueInput:
    return DiagnoseAgentIssueInput(
        agent=finding.agent,
        step_id=finding.step_id,
        step_label=finding.step_label,
        window=finding.window,
        flagged=finding.flagged,
        criteria_version=version,
        evidence=[
            DiagnosisEvidence(
                context_id=event.context_id,
                title=event.title,
                verdict=step.verdict,
                work_seconds=step.work_seconds,
                attempts=step.attempts,
                limit_seconds=step.limit_seconds,
                reason=step.reason,
                at=event.at,
            )
            for event, step in finding.evidence
        ],
        requested_at=now,
    )


def _sender(svc: Services) -> SendFn | None:
    injected = svc.extras.get("diagnosis_send")
    if injected is not None:
        return injected  # type: ignore[no-any-return]
    return None if svc.settings.mock else send_to_orchestrator


async def request_agent_diagnosis(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = RequestAgentDiagnosisInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    try:
        criteria = load_criteria()
    except CriteriaError:
        return failed(request, "CRITERIA_UNREADABLE", "기준값 파일을 읽지 못해 요청하지 않았다")
    cards = _latest_cards(await load_cards(svc, request.meta))[:LIMIT]
    events = [_event(c) for c in cards]
    findings = find_repeats(events, criteria.repeat, context_id=params.context_id)
    send = _sender(svc)
    now = svc.clock()
    sent: list[SentDiagnosis] = []
    for finding in findings:
        answer = (
            await send(request.meta, _payload(finding, criteria.version, now))
            if send is not None
            else None
        )
        sent.append(
            SentDiagnosis(
                agent=finding.agent,
                step_id=finding.step_id,
                status=answer.status if answer else "failed",
                request_id=answer.request_id if answer else None,
            )
        )
    output = RequestAgentDiagnosisOutput(checked_events=len(events), found=len(findings), sent=sent)
    return completed(
        request,
        output,
        outcome="requested" if sent else "no_repeat",
        evidence=make_evidence(
            svc.clock,
            f"반복 확인: 이슈 {len(events)}건 중 반복 {len(findings)}건, 총괄에 보냄 "
            f"{sum(1 for s in sent if s.status != 'failed')}건",
            [c.card_id for c in cards[:5]],
        ),
    )
