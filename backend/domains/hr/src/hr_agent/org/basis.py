"""`find_org_change_basis`: 발령 사유와 비슷한 과거 업무 결정 카드를 찾는다 (조회 전용).

과거 결정을 저장하고 찾는 일은 아카이브 AI가 한다. 인사 AI는 사유를 아카이브의 읽기 skill
`find_similar_decisions`에 물어 후보를 받아 그대로 돌려준다(검색·임베딩 코드는 인사에 없다).
- 아카이브가 응답하지 않으면 후보를 지어내지 않고 실패로 답한다(화면은 "불러오지 못함"으로 보인다).
- 기준을 넘는 카드가 없으면 후보가 비어 있다. 과거에 기각된 안은 `rejected`로 표시된다.
- 찾기만 한다. 근거로 연결하는 것은 사람이 골라 `record_org_change`에 `basis_card_id`로 보낼 때다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    FindOrgChangeBasisInput,
    FindOrgChangeBasisOutput,
    FindSimilarDecisionsInput,
    RequestMeta,
    TaskStatus,
)
from hr_agent.results import completed, failed, invalid_input, make_evidence
from hr_agent.services import Services

CALL_TIMEOUT_SECONDS = 20
TARGET = "hr"  # 부서 이름으로 부르면 skill을 맡은 서비스로 간다 (services.py)
SKILL = "find_similar_decisions"
SEND = call_agent  # 테스트에서 바꿀 수 있다


async def find_org_change_basis(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = FindOrgChangeBasisInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    meta = request.meta
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=meta.context_id,
            tenant_id=meta.tenant_id,
            token="replaced-by-call_agent",
            path=list(meta.path),
            deadline=min(
                meta.deadline, datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS)
            ),
        ),
        skill=SKILL,
        input=FindSimilarDecisionsInput(reason=params.reason, limit=params.limit).model_dump(
            mode="json"
        ),
    )
    try:
        result = await SEND(TARGET, sent)
    except Exception:  # noqa: BLE001 - 아카이브에 닿지 못하면 후보를 지어내지 않는다
        return failed(
            request, "ARCHIVE_UNAVAILABLE", "아카이브 AI에 닿지 못해 근거 후보를 찾지 못했다"
        )
    if result.status != TaskStatus.COMPLETED:
        return failed(
            request,
            "ARCHIVE_UNAVAILABLE",
            f"아카이브 AI가 근거 후보를 찾지 못했다 ({result.status.value})",
        )
    try:
        output = FindOrgChangeBasisOutput.model_validate(result.output)
    except ValidationError:
        return failed(request, "ARCHIVE_BAD_RESPONSE", "아카이브 AI의 응답 형식이 맞지 않는다")
    n = len(output.candidates)
    return completed(
        request,
        output,
        outcome="found" if n else "none",
        evidence=make_evidence(
            svc.clock,
            f"근거 후보 결정 카드 {n}장 ({output.method}, 아카이브 AI)"
            if n
            else "근거 후보 없음: 기준을 넘는 결정 카드가 없다",
            [c.card_id for c in output.candidates],
        ),
    )
