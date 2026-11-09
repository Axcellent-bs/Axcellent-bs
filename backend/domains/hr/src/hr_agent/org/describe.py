"""`describe_org_change`: 승인하는 사람이 읽을 발령 요약 설명문 (LLM은 문장만, 조회 전용).

"12월 1일부터 개발1팀 -1명, 개발2팀 +1명"처럼 이 발령이 팀 인원에 주는 변화를 쓴다.
- 인원 변화는 코드가 센다: 확정 기록에 이 발령이 있을 때와 없을 때의 그 달 1일 인원을 비교한다.
- 문장은 `Narrator`가 다듬는다. LLM 문장에 코드가 센 값에 없는 숫자가 있거나 금액·예산을
  말하면 버리고 템플릿 문장을 쓴다(`fallback: true`). LLM이 꺼져 있으면 템플릿 그대로다.
- 인사는 사람 수와 팀 변화까지만 설명한다. 금액은 재무 쪽 일이라 쓰지 않는다.
- 앞선 확정 기록과 맞지 않는 발령(없는 사람의 이동 등)은 설명 대신 충돌 사유를 돌려준다.
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    DescribeOrgChangeInput,
    DescribeOrgChangeOutput,
    OrgChangeDelta,
)
from hr_agent.org import changes, ledger
from hr_agent.org.roster import org_teams
from hr_agent.results import completed, invalid_input, make_evidence, refused
from hr_agent.services import Services

# 금액·예산을 말하는 표현(숫자 뒤의 '원', 예산, 비용, 금액). "인원"의 '원'은 걸리지 않는다
_MONEY = re.compile(r"\d\s*원|예산|비용|금액|₩")
PURPOSE = "조직 변동 발령이 팀 인원에 주는 변화를 승인하는 사람이 읽기 쉽게 설명하는 문장"


def _template(month: str, deltas: list[OrgChangeDelta]) -> str:
    parts = ", ".join(f"{d.team} {d.delta:+d}명" for d in deltas)
    return f"{int(month[5:])}월 1일부터 {parts}"


async def describe_org_change(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = DescribeOrgChangeInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    row = changes.get(svc, params.change_id)
    if row is None:
        return refused(request, "UNKNOWN_CHANGE", "찾을 수 없는 발령 기록이에요")

    month = row["counts_from_month"]
    this = changes._change(row)
    base = [c for c in changes.confirmed(svc) if c.change_id != this.change_id]
    try:
        ledger.validate([*base, this])
        before = Counter(ledger.roster_at(base, month).values())
        after = Counter(ledger.roster_at([*base, this], month).values())
    except ledger.LedgerError as exc:
        return refused(request, "LEDGER_CONFLICT", str(exc))

    deltas = [
        OrgChangeDelta(team=t, delta=after.get(t, 0) - before.get(t, 0))
        for t in org_teams()
        if after.get(t, 0) != before.get(t, 0)
    ]
    template = _template(month, deltas)
    # 숫자는 코드가 센 값뿐이다: 월과 팀별 변화. 직원 번호는 사실에 넣지 않는다
    facts = {"반영 월": str(int(month[5:])), "일": "1", "팀별 인원 변화": template}
    sentence = await svc.narrator.sentence(PURPOSE, facts, template)
    text, fallback = sentence.text, sentence.fallback
    if not fallback and _MONEY.search(text):
        text, fallback = template, True  # 금액·예산은 재무 쪽 설명이다
    output = DescribeOrgChangeOutput(
        change_id=row["change_id"],
        month=month,
        deltas=deltas,
        text=text,
        fallback=fallback,
        llm_used=svc.narrator.enabled and not fallback,
    )
    return completed(
        request,
        output,
        outcome="described",
        evidence=make_evidence(svc.clock, f"발령 요약 설명 (팀 {len(deltas)}곳, 숫자는 코드 값)"),
    )
