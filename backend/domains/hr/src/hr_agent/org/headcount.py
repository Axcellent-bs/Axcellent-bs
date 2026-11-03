"""`team_headcount`: 그달 1일 기준 팀별 인원수 (조회 전용, 재무 AI 팀 예산용).

이름·직원 ID는 내보내지 않고 팀 이름과 인원수만 돌려준다. 인원을 알 수 없으면(조직도에서
팀을 읽지 못함) 0명으로 채우지 않고 실패로 답한다.
발령은 발령일이 1일이면 그 달부터, 아니면 다음 달 1일부터 센다.

- 확정된 발령 기록이 하나도 없으면 가상 명부(예시)로 세고 `example: true`로 밝힌다.
- 확정 기록이 있으면 그 기록만으로 센다. 초안·반려는 세지 않는다.
  기록이 시작되기 전의 달은 인원을 알 수 없으므로 0명으로 채우지 않고 실패로 답한다.
- 전 직원 첫 등록이 끝났다고 인사가 밝히기 전(`baseline_complete`)에는 확정 기록으로 세어도
  `example: true`로 답한다. 일부만 등록된 낮은 인원으로 재무가 팀 예산을 확정하는 것을 막는다
  (재무는 전 직원이 등록됐는지 알 수 없다). 등록 방식은 화면 작업(6번)에서 정한다.
"""

from __future__ import annotations

from collections import Counter

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    TeamHeadcountInput,
    TeamHeadcountOutput,
    TeamHeadcountRow,
)
from hr_agent.org import changes, ledger
from hr_agent.org.roster import example_roster, org_teams
from hr_agent.results import completed, failed, invalid_input, make_evidence
from hr_agent.services import Services


def baseline_complete(svc: Services) -> bool:
    """전 직원 첫 등록이 끝났다고 인사가 밝혔는지. 아직 방법이 없어 늘 거짓(6번에서 채움)."""
    return False


async def team_headcount(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = TeamHeadcountInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)

    teams = org_teams()
    if not teams:
        return failed(request, "NO_ORG_ROSTER", "조직도에서 팀을 읽지 못해 팀 인원을 셀 수 없다")

    records = changes.confirmed(svc)
    if not records:
        members = [m.team for m in example_roster(teams)]
        basis = f"가상 명부(예시) 기준, {params.month}-01 소속"
        example = True
    else:
        first = ledger.earliest_month(records)
        if first is not None and params.month < first:
            return failed(
                request,
                "NO_HEADCOUNT_RECORD",
                f"{params.month}의 팀 인원을 알 수 있는 발령 기록이 없다 ({first}부터 기록됨)",
            )
        try:
            members = list(ledger.roster_at(records, params.month).values())
        except (
            ledger.LedgerError
        ):  # 확정할 때 맞춰 보지만, 저장소가 바뀌었으면 틀린 값을 내지 않는다
            return failed(
                request, "LEDGER_CONFLICT", "발령 기록이 서로 맞지 않아 인원을 셀 수 없다"
            )
        basis = f"확정된 발령 기록 {len(records)}건 기준, {params.month}-01 소속"
        example = not baseline_complete(svc)
        if example:
            basis += " (전 직원 등록 전)"

    counts = Counter(members)
    rows = [TeamHeadcountRow(team=t, headcount=counts.get(t, 0)) for t in teams]
    output = TeamHeadcountOutput(
        month=params.month, rows=rows, basis=basis, example=example, collected_at=svc.clock()
    )
    summary = f"{params.month} 팀 인원: 팀 {len(rows)}곳, 합계 {sum(counts.values())}명 ({basis})"
    return completed(
        request,
        output,
        outcome="team_headcount",
        evidence=make_evidence(svc.clock, summary),
    )
