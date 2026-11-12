"""개발 서비스(dev, 8111)의 A2A 핸들러 (분리 5단계: handlers.py에서 옮김).

관련 PR 찾기·장애 연결·롤백 대상 확인·진행 중 항목·인수인계 확인·기획서 넘겨받기.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.chat import ChatRemoveOutput
from ax_contracts.schemas.planning import (
    DevChatListInput,
    DevChatRef,
    DevChatSendInput,
    DevHandoffInput,
    DevHandoffOutput,
    FindRelatedPrsInput,
    FindRelatedPrsOutput,
    InProgressItem,
    LinkIncidentInput,
    LinkIncidentOutput,
    ListDevOutcomesInput,
    ListDevOutcomesOutput,
    ListInProgressInput,
    ListInProgressOutput,
    ReceiveReleaseOrderInput,
    ReceiveReleaseOrderOutput,
    RelatedPr,
    RequestPauseInput,
    RequestPauseOutput,
    VerifyHandoverInput,
    VerifyHandoverOutput,
    VerifyRollbackTargetInput,
    VerifyRollbackTargetOutput,
)
from ax_workflow.a2a import _gate_rejected, _invalid, _missing_key, _requester
from ax_workflow.core import ActionError, GateRefused
from ax_workflow.handover import ROSTER_AREAS, Item, is_roster_area
from ax_workflow.models import PR
from ax_workflow.rules import ROLLBACK_LOOKBACK
from dev_agent import dev_chat
from dev_agent.runtime import get_service


def _related(rows: list[tuple[PR, str | None, str | None]]) -> list[RelatedPr]:
    return [
        RelatedPr(
            pr_number=pr.number,
            title=pr.title,
            merged_at=pr.merged_at,
            spec_id=spec_id,
            para_id=para_id,
            changed_paths=list(pr.changed_paths),
        )
        for pr, spec_id, para_id in rows
    ]


def _refs(rows: list[RelatedPr]) -> list[str]:
    refs: list[str] = []
    for pr in rows:
        refs.append(f"pr:{pr.pr_number}")
        if pr.spec_id and pr.para_id:
            refs.append(f"para:{pr.spec_id}:{pr.para_id}")
    return refs


async def find_related_prs(request: AgentRequest) -> AgentResult:
    """구간 안 머지 PR 후보 (read). 원인은 판정하지 않는다."""
    try:
        inp = FindRelatedPrsInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    rows = await get_service().find_related_prs(
        context_id=request.meta.context_id,
        requester=_requester(request),
        window_from=inp.window_from,
        window_to=inp.window_to,
        component=inp.component,
    )
    candidates = _related(rows)
    comp = f" AND changed_paths 접두사 '{inp.component}'" if inp.component else ""
    summary = (
        f"구간 안 머지 PR {len(candidates)}건 (시간 관계만, 원인 판정은 하지 않음)"
        if candidates
        else "확인할 수 없음: 구간 안 머지 PR 없음"
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=FindRelatedPrsOutput(candidates=candidates).model_dump(mode="json"),
        evidence=Evidence(
            queries=[
                f"prs WHERE merged AND merged_at BETWEEN '{inp.window_from.isoformat()}' "
                f"AND '{inp.window_to.isoformat()}'{comp} ORDER BY merged_at DESC"
            ],
            data_summary=summary,
            ref_ids=_refs(candidates),
        ),
    )


async def link_incident(request: AgentRequest) -> AgentResult:
    """사건을 PR·문단에 연결하고 재발 방지 요청을 등록한다 (A, execute 경유)."""
    try:
        inp = LinkIncidentInput.model_validate(request.input)
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
        result = await get_service().link_incident(
            context_id=request.meta.context_id,
            requester=_requester(request),
            incident_context_id=inp.incident_context_id,
            pr_numbers=inp.pr_numbers,
            follow_up_title=inp.follow_up_title,
            idempotency_key=request.meta.idempotency_key,
        )
    except GateRefused as exc:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="gate_rejected",
            output={"message": f"실행 관문이 {exc.action}을 허락하지 않아 실행하지 않았다"},
            evidence=Evidence(
                data_summary=f"execute({exc.action}) → {exc.decision.status}",
                ref_ids=[exc.decision.execution_id],
            ),
        )
    out = LinkIncidentOutput(
        linked_paras=result.output["linked_paras"],
        follow_up_request_id=result.output["follow_up_request_id"],
    )
    missing = result.output.get("missing_pr_numbers") or []
    summary = f"문단 {len(out.linked_paras)}개에 사건 {inp.incident_context_id} 표시"
    if missing:
        summary += f" · 확인할 수 없음: PR {', '.join(f'#{n}' for n in missing)}"
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=result.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[f"prs WHERE number IN {sorted(set(inp.pr_numbers))}"],
            data_summary=summary,
            ref_ids=[f"pr:{n}" for n in inp.pr_numbers if n not in missing]
            + [f"para:{p}" for p in out.linked_paras]
            + ([f"req:{out.follow_up_request_id}"] if out.follow_up_request_id else []),
        ),
    )


async def verify_rollback_target(request: AgentRequest) -> AgentResult:
    """infra rollback_deployment(B)의 교차검증 (read). 거절도 completed + rejected_by_review.

    입력은 계약 `VerifyRollbackTargetInput` 또는 safety 검토 형식(`SafetyReviewInput`) 둘 다 받는다.
    출력은 계약 `VerifyRollbackTargetOutput`이고 safety는 `approved == true`일 때만 승인으로 본다.
    """
    try:
        inp, execution_id, sources = _rollback_input(request.input, request.meta.context_id)
    except ValidationError as exc:
        return _invalid(request, exc)
    except ValueError as exc:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="invalid_input",
            output={"message": str(exc)},
        )
    approved, reason, rows = await get_service().verify_rollback_target(
        context_id=request.meta.context_id,
        requester=_requester(request),
        service=inp.service,
        target_version=inp.target_version,
        deployed_at=inp.deployed_at,
        incident_opened_at=inp.incident_opened_at,
    )
    related = _related(rows)
    minutes = int(ROLLBACK_LOOKBACK.total_seconds() // 60)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=None if approved else "rejected_by_review",
        output=VerifyRollbackTargetOutput(
            approved=approved, reason_code=reason, related_prs=related
        ).model_dump(mode="json"),
        evidence=Evidence(
            queries=[
                f"deployed_at({inp.deployed_at.isoformat()}) <= "
                f"incident_opened_at({inp.incident_opened_at.isoformat()})",
                f"prs WHERE merged_at BETWEEN deployed_at - {minutes}m AND deployed_at",
            ]
            + [f"{k} 출처: {v}" for k, v in sources.items()],
            data_summary=(
                f"{inp.service} {inp.target_version}: {reason}, 구간 안 머지 PR {len(related)}건"
                + "".join(f" · {k} ← {v}" for k, v in sources.items())
            ),
            ref_ids=([f"execution:{execution_id}"] if execution_id else []) + _refs(related),
        ),
    )


def _items(rows: list[Item]) -> list[InProgressItem]:
    return [
        InProgressItem(
            item_id=i.item_id,
            kind=i.kind,  # type: ignore[arg-type]
            title=i.title,
            status=i.status,
            owner_id=i.owner_id,
            area=i.area,
            updated_at=i.updated_at,
        )
        for i in rows
    ]


def _item_refs(rows: list[Item]) -> list[str]:
    refs: list[str] = []
    for i in rows:
        refs.append(f"item:{i.item_id}")
        refs.extend(r for r in i.refs if r not in refs)
    return refs


def _where(owner_id: str, area: str | None) -> str:
    return f"owner_id = '{owner_id}'" + (f" AND area = '{area}'" if area else "")


async def list_in_progress(request: AgentRequest) -> AgentResult:
    """담당자·영역의 진행 중 배포·미완료 작업·열린 PR (read). 상태를 바꾸지 않는다."""
    try:
        inp = ListInProgressInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    rows, at = await get_service().list_in_progress(
        context_id=request.meta.context_id,
        requester=_requester(request),
        owner_id=inp.owner_id,
        area=inp.area,
    )
    kinds = {k: sum(1 for i in rows if i.kind == k) for k in ("deployment", "ticket", "pr")}
    if rows:
        summary = (
            f"진행 중 항목 {len(rows)}건 (배포 확인 전 {kinds['deployment']}, "
            f"미완료 작업 {kinds['ticket']}, 열린 PR {kinds['pr']})"
        )
    elif inp.area is not None and not is_roster_area(inp.area):
        summary = f"확인할 수 없음: 명부에 없는 영역 '{inp.area}' ({', '.join(ROSTER_AREAS)})"
    else:
        summary = f"확인할 수 없음: {inp.owner_id}의 진행 중 항목 없음"
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=ListInProgressOutput(items=_items(rows), collected_at=at).model_dump(mode="json"),
        evidence=Evidence(
            queries=[
                f"tasks WHERE state != '완료' AND {_where(inp.owner_id, inp.area)}",
                f"prs WHERE NOT merged AND {_where(inp.owner_id, inp.area)}",
                "prs WHERE merged AND (linked incident follow-up open OR request not closed) "
                f"AND {_where(inp.owner_id, inp.area)}",
            ],
            data_summary=summary,
            ref_ids=_item_refs(rows),
        ),
    )


async def list_dev_outcomes(request: AgentRequest) -> AgentResult:
    """개발 작업 결과 (read): 인사·총괄의 부서·에이전트 평가 근거. 상태를 바꾸지 않는다."""
    try:
        inp = ListDevOutcomesInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    rows, summary, at = await get_service().list_dev_outcomes(
        context_id=request.meta.context_id,
        requester=_requester(request),
        since=inp.since,
        until=inp.until,
        coder=inp.coder,
    )
    if rows:
        text = (
            f"작업 {summary.total}건 (머지 {summary.merged}, 실패 {summary.failed}, "
            f"반영됨 {summary.covered}) · 서버 AI 토큰 {summary.llm_tokens:,}"
        )
    else:
        text = "확인할 수 없음: 조건에 맞는 개발 작업 없음"
    out = ListDevOutcomesOutput(outcomes=rows, summary=summary, collected_at=at)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=["tasks WHERE coding IS NOT NULL", "prs WHERE task = tasks.id"],
            data_summary=text,
            ref_ids=[r.task_id for r in rows][:50],
        ),
    )


class SafetyReviewInput(BaseModel):
    """safety 교차검증 호출 형식 (`safety/src/ax_safety/gate.py` `_cross_verify`, #187).

    safety는 B 액션의 검증자 skill을 계약 입력이 아니라 실행 요청 그대로 부른다:
    `{execution_id, action, requester, params, computed_amount, threshold_version}`.
    hr `confirm_handover`의 params는 `handover_id`·`owner_id`·`area`·`item_ids`(또는
    `handover_item_ids`)·`completeness_percent` 등이다. 완성도는 safety 규칙이 보므로 쓰지 않는다.
    infra `rollback_deployment`의 params는 `service`·`from_version`·`to_version`이고,
    `deployed_at`·`incident_opened_at`은 선택이다(planning 기록이 먼저, `verify_rollback_target`).
    """

    model_config = ConfigDict(extra="ignore")

    execution_id: str
    action: str
    requester: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


HANDOVER_ACTIONS = ("confirm_handover",)


def _handover_input(raw: dict[str, Any]) -> tuple[VerifyHandoverInput, str | None]:
    """계약 형식이면 그대로, safety 검토 형식이면 계약 입력으로 바꾼다. (입력, execution_id)."""
    if "execution_id" not in raw or "params" not in raw:
        return VerifyHandoverInput.model_validate(raw), None
    review = SafetyReviewInput.model_validate(raw)
    if review.action not in HANDOVER_ACTIONS:
        raise ValueError(f"verify_handover가 검증하지 않는 액션: {review.action}")
    p = review.params
    items = p.get("handover_item_ids")
    if items is None:
        items = p.get("item_ids", [])
    converted = {
        "handover_id": p.get("handover_id") or p.get("target"),
        "owner_id": p.get("owner_id"),
        "area": p.get("area"),
        "handover_item_ids": items,
    }
    return VerifyHandoverInput.model_validate(converted), review.execution_id


ROLLBACK_ACTIONS = ("rollback_deployment",)


def _rollback_input(
    raw: dict[str, Any], context_id: str
) -> tuple[VerifyRollbackTargetInput, str | None, dict[str, str]]:
    """계약 형식이면 그대로, safety 검토 형식이면 계약 입력으로 바꾼다.

    돌려주는 값은 (입력, execution_id, 시각 출처). infra `rollback_deployment`
    params(`RollbackDeploymentParams`)의 `from_version`이 되돌릴 대상 배포, 즉 계약의
    `target_version`이다. 검토 형식의 두 시각은 planning 자기 기록을 먼저 쓴다:
    - `deployed_at`: 그 버전으로 나간 머지 PR의 `merged_at` (데모: 머지 = dev 배포)
    - `incident_opened_at`: 요청 `context_id`의 사건 기록 `opened_at`
    기록이 없으면 params의 같은 이름 값을 쓰고, 그것도 없으면 판정하지 않는다(failed → 보류).
    """
    if "execution_id" not in raw or "params" not in raw:
        return VerifyRollbackTargetInput.model_validate(raw), None, {}
    review = SafetyReviewInput.model_validate(raw)
    if review.action not in ROLLBACK_ACTIONS:
        raise ValueError(f"verify_rollback_target가 검증하지 않는 액션: {review.action}")
    p = review.params
    service = p.get("service")
    version = p.get("target_version") or p.get("from_version")
    pr, incident = (None, None)
    if isinstance(service, str) and isinstance(version, str):
        pr, incident = get_service().rollback_record(context_id, service, version)
    sources: dict[str, str] = {}
    deployed_at: Any = p.get("deployed_at")
    if pr is not None:
        deployed_at = pr.merged_at
        sources["deployed_at"] = f"머지 기록 {pr.id} ({pr.deployed_version})"
    elif deployed_at is not None:
        sources["deployed_at"] = "params"
    opened_at: Any = p.get("incident_opened_at")
    if incident is not None:
        opened_at = incident.opened_at
        sources["incident_opened_at"] = f"사건 기록 {incident.context_id}"
    elif opened_at is not None:
        sources["incident_opened_at"] = "params"
    converted = {
        "service": service,
        "target_version": version,
        "deployed_at": deployed_at,
        "incident_opened_at": opened_at,
    }
    return VerifyRollbackTargetInput.model_validate(converted), review.execution_id, sources


async def verify_handover(request: AgentRequest) -> AgentResult:
    """hr confirm_handover(B)의 교차검증 (read). 거절도 completed + rejected_by_review.

    입력은 계약 `VerifyHandoverInput` 또는 safety 검토 형식(`SafetyReviewInput`) 둘 다 받는다.
    출력은 계약 `VerifyHandoverOutput`이고 safety는 `approved == true`일 때만 승인으로 본다.
    """
    try:
        inp, execution_id = _handover_input(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    except ValueError as exc:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="invalid_input",
            output={"message": str(exc)},
        )
    if inp.area is not None and not is_roster_area(inp.area):
        # 비교할 영역을 모르면 승인하지 않는다 (safety는 failed를 무응답으로 보고 보류한다)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="invalid_input",
            output={"message": f"명부에 없는 영역 '{inp.area}' ({', '.join(ROSTER_AREAS)})"},
        )
    check, at = await get_service().verify_handover(
        context_id=request.meta.context_id,
        requester=_requester(request),
        handover_id=inp.handover_id,
        owner_id=inp.owner_id,
        area=inp.area,
        handover_item_ids=inp.handover_item_ids,
    )
    approved = not check.missing
    out = VerifyHandoverOutput(
        approved=approved,
        reason_code="complete" if approved else "missing_items",
        missing=_items(check.missing),
        stale_item_ids=check.stale_item_ids,
        checked_at=at,
    )
    summary = (
        f"{inp.handover_id}: 진행 중 {len(check.current)}건 중 누락 {len(check.missing)}건"
        f", 이미 끝난 항목 {len(check.stale_item_ids)}건"
    )
    if check.unknown_item_ids:
        summary += f" · 확인할 수 없음: {', '.join(check.unknown_item_ids)}"
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=None if approved else "rejected_by_review",
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[
                f"list_in_progress({_where(inp.owner_id, inp.area)})",
                f"missing = in_progress - handover_item_ids ({len(inp.handover_item_ids)}건)",
            ],
            data_summary=summary,
            ref_ids=[f"handover:{inp.handover_id}"]
            + ([f"execution:{execution_id}"] if execution_id else [])
            + _item_refs(check.current),
        ),
    )


async def start_dev(request: AgentRequest) -> AgentResult:
    """dev 서비스: planning이 넘긴 확정 기획서·작업을 받아 PR 만들기로 잇는다 (A, execute 경유)."""
    try:
        inp = DevHandoffInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().start_dev(
            context_id=request.meta.context_id,
            requester=_requester(request),
            request=inp.request,
            spec=inp.spec,
            tasks=inp.tasks,
            idempotency_key=request.meta.idempotency_key,
        )
    except ValidationError as exc:
        return _invalid(request, exc)
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = DevHandoffOutput.model_validate(result.output)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=result.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{out.spec_id} 작업 {len(out.task_ids)}건 받음",
            ref_ids=[f"spec:{out.spec_id}"] + [f"task:{t}" for t in out.task_ids],
        ),
    )


async def receive_release_order(request: AgentRequest) -> AgentResult:
    """dev 서비스: 기획의 배포 지시를 받아 지금의 배포 요청 흐름을 탄다 (A, execute 경유)."""
    try:
        inp = ReceiveReleaseOrderInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().receive_release_order(
            context_id=request.meta.context_id,
            requester=_requester(request),
            inp=inp,
            idempotency_key=request.meta.idempotency_key,
        )
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = ReceiveReleaseOrderOutput.model_validate(result.output)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{inp.check_id} 배포 지시 → {out.outcome}"
            + (f" {out.deployment_id}" if out.deployment_id else ""),
            ref_ids=[f"spec:{inp.spec_id}", *(f"pr:{n}" for n in inp.pr_numbers)],
        ),
    )


async def receive_request_pause(request: AgentRequest) -> AgentResult:
    """dev 서비스: 기획이 요청을 사람 중단·재개했다 (A, execute 경유)."""
    try:
        inp = RequestPauseInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    if not request.meta.idempotency_key:
        return _missing_key(request)
    try:
        result = await get_service().receive_request_pause(
            context_id=request.meta.context_id,
            requester=_requester(request),
            inp=inp,
            idempotency_key=request.meta.idempotency_key,
        )
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    out = RequestPauseOutput.model_validate(result.output)
    how = "중단" if inp.paused else "재개"
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=out.outcome,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            data_summary=f"{inp.request_id} {how} → {out.outcome} · 멈춘 작업 "
            f"{len(out.tasks_stopped)} · PR {len(out.prs_held)}",
            ref_ids=[f"request:{inp.request_id}", *(f"pr:{n}" for n in out.prs_held)],
        ),
    )


# ---- 개발 AI 직접 대화 (dashboard-api /api/dashboard/dev-chat → dev_chat.py)
# 말한 사람(`who`)은 dashboard-api가 사람 토큰에서만 채운다. 메시지마다 관문 dev_chat(A)을 타고,
# 대화는 작업을 띄우지 않고 "진행해"면 기획 create_ticket으로 넘긴다 (작업은 기획 확정 뒤
# start_dev). 거절은 FAILED + outcome(소문자 코드)


# 대시보드 AI 대화방에서 연 대화. 개발 화면 대화 패널(screen)과 서로 보이지 않는다
DASHBOARD = "dashboard"


def _chat_done(request: AgentRequest, skill: str, output: dict[str, Any]) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="ok",
        output=output,
        evidence=Evidence(data_summary=f"개발 AI 대화 {skill}", ref_ids=[]),
    )


def _chat_refused(request: AgentRequest, exc: ActionError) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=exc.code.lower(),
        output={"message": exc.message},
        evidence=Evidence(data_summary=f"개발 AI 대화 거절: {exc.code}", ref_ids=[]),
    )


async def dev_chat_send(request: AgentRequest) -> AgentResult:
    """말을 적고 answering으로 바로 돌려준다 (A, 메시지마다 관문 dev_chat)."""
    try:
        inp = DevChatSendInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    svc = get_service()
    try:
        th = await dev_chat.send(
            svc, inp.thread_id, inp.message, inp.connection, inp.mode, inp.who, DASHBOARD
        )
    except ActionError as exc:
        return _chat_refused(request, exc)
    except GateRefused as exc:
        return _gate_rejected(request, exc)
    return _chat_done(request, "dev_chat_send", {"thread": dev_chat.view(th, inp.who)})


async def dev_chat_list(request: AgentRequest) -> AgentResult:
    try:
        inp = DevChatListInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    svc = get_service()
    await dev_chat.close_idle(svc)
    threads = dev_chat.listed(svc, inp.who, inp.limit, DASHBOARD)
    return _chat_done(request, "dev_chat_list", {"threads": threads})


async def dev_chat_get(request: AgentRequest) -> AgentResult:
    try:
        inp = DevChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    svc = get_service()
    await dev_chat.close_idle(svc)
    th = svc.chat_of(inp.thread_id)
    if th is None or not dev_chat.seen_from(th, DASHBOARD):
        return _chat_refused(request, ActionError("NOT_FOUND", f"대화 {inp.thread_id}가 없다"))
    return _chat_done(request, "dev_chat_get", {"thread": dev_chat.view(th, inp.who)})


async def dev_chat_close(request: AgentRequest) -> AgentResult:
    try:
        inp = DevChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        th = await dev_chat.close(get_service(), inp.thread_id, inp.who, DASHBOARD)
    except ActionError as exc:
        return _chat_refused(request, exc)
    return _chat_done(request, "dev_chat_close", {"thread": dev_chat.view(th, inp.who)})


async def dev_chat_remove(request: AgentRequest) -> AgentResult:
    """본인 대화를 저장소에서 지운다 (대화 기록만, 업무 상태는 안 바꾼다)."""
    try:
        inp = DevChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread_id = await dev_chat.remove(get_service(), inp.thread_id, inp.who, DASHBOARD)
    except ActionError as exc:
        return _chat_refused(request, exc)
    out = ChatRemoveOutput(thread_id=thread_id, removed=True)
    return _chat_done(request, "dev_chat_remove", out.model_dump(mode="json"))


# 개발 서비스(dev 8111)가 받는 skill
DEV_HANDLERS = {
    "find_related_prs": find_related_prs,
    "link_incident": link_incident,
    "verify_rollback_target": verify_rollback_target,
    "list_in_progress": list_in_progress,
    "list_dev_outcomes": list_dev_outcomes,
    "verify_handover": verify_handover,
    "start_dev": start_dev,
    "receive_release_order": receive_release_order,
    "receive_request_pause": receive_request_pause,
    # 사람과 직접 대화 (dashboard-api /api/dashboard/dev-chat)
    "dev_chat_send": dev_chat_send,
    "dev_chat_list": dev_chat_list,
    "dev_chat_get": dev_chat_get,
    "dev_chat_close": dev_chat_close,
    "dev_chat_remove": dev_chat_remove,
}
