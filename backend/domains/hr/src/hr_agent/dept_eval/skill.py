"""`judge_step_timings`: 이슈 하나의 단계 시간을 기준값과 견줘 판정한다 (읽기, 시간 계산만).

아카이브 AI가 이슈 시각을 카드에 기록할 때 부른다. 판정은 인사 AI(에이전트 평가)가 하고, 아카이브는
돌려받은 결과를 기준값 버전과 같이 카드에 둔다. 기준값 파일을 읽지 못하면 판정을 지어내지 않고
실패로 답한다(아카이브는 판정을 비워 둔다).
"""

from __future__ import annotations

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    JudgeStepTimingsInput,
    JudgeStepTimingsOutput,
)
from hr_agent.dept_eval.criteria import CriteriaError, load_criteria
from hr_agent.dept_eval.judge import judge_timings
from hr_agent.results import completed, failed, invalid_input, make_evidence
from hr_agent.services import Services


async def judge_step_timings(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = JudgeStepTimingsInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    try:
        criteria = load_criteria()
    except CriteriaError:
        return failed(request, "CRITERIA_UNREADABLE", "기준값 파일을 읽지 못해 판정하지 않았다")
    judged = judge_timings(params.step_timings, criteria)
    output = JudgeStepTimingsOutput.model_validate(judged)
    return completed(
        request,
        output,
        outcome="judged",
        evidence=make_evidence(
            svc.clock,
            f"단계 {len(output.items)}개 판정 (기준값 v{output.criteria_version})",
        ),
    )
