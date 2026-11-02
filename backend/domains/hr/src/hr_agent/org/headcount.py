"""`team_headcount`: 그달 1일 기준 팀별 인원수 (조회 전용, 재무 AI 팀 예산용).

이름·직원 ID는 내보내지 않고 팀 이름과 인원수만 돌려준다. 인원을 알 수 없으면(조직도에서
팀을 읽지 못함) 0명으로 채우지 않고 실패로 답한다.
발령은 발령일이 1일이면 그 달부터, 아니면 다음 달 1일부터 센다. 발령 기록은 아직 없어서
지금은 가상 명부(예시)로 세고 `example: true`로 밝힌다.
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
from hr_agent.org.roster import example_roster, org_teams
from hr_agent.results import completed, failed, invalid_input, make_evidence
from hr_agent.services import Services


async def team_headcount(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = TeamHeadcountInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)

    teams = org_teams()
    members = example_roster(teams)
    if not members:
        return failed(request, "NO_ORG_ROSTER", "조직도에서 팀을 읽지 못해 팀 인원을 셀 수 없다")

    counts = Counter(m.team for m in members)
    rows = [TeamHeadcountRow(team=t, headcount=counts.get(t, 0)) for t in teams]
    basis = f"가상 명부(예시) 기준, {params.month}-01 소속"
    output = TeamHeadcountOutput(
        month=params.month, rows=rows, basis=basis, example=True, collected_at=svc.clock()
    )
    summary = f"{params.month} 팀 인원: 팀 {len(rows)}곳, 합계 {sum(counts.values())}명 ({basis})"
    return completed(
        request,
        output,
        outcome="team_headcount",
        evidence=make_evidence(svc.clock, summary),
    )
