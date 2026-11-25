"""재무 의견 중 기획이 알아야 하는 것을 총괄이 기획 `receive_insight`로 넘긴다 (11/18 태윤).

재무는 의견을 총괄에만 보내고, 기획에 넘길 내용(`planning_insight`,
planning `ReceiveInsightInput`)을 의견에 함께 싣는다. 총괄은 부서 사이 알림을 나누는 자리라
그 내용을 그대로 기획에 넘긴다. 총괄이 숫자를 바꾸거나 판단을 더하지 않는다.
기획은 같은 인사이트를 다시 받으면 `duplicate`로 답한다.

- 넘기기는 의견 받기와 따로 돈다(재무가 기다리지 않게).
  못 넘기면 `RETRY_DELAYS` 간격으로 다시 보낸다.
- 다 실패하거나 총괄을 다시 켜서 끊기면 `pending`으로 남고, 다음 의견을 받을 때 다시 보낸다.
- 넘긴 결과(전달됨·제안 번호·실패 이유)는 의견 줄의 `relay`에 남아 총괄 화면에 보인다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import ReceiveInsightInput, ReceiveInsightOutput

TARGET = "planning"
SKILL = "receive_insight"
CALL_TIMEOUT_SECONDS = 30  # 기획이 제안 초안을 LLM으로 쓸 수 있어 넉넉히
RETRY_DELAYS: tuple[float, ...] = (30, 120, 600)


def planning_input(opinion: dict[str, Any]) -> ReceiveInsightInput | str | None:
    """의견에 실린 기획용 내용. 없으면 None, 계약과 다르면 이유(문자열)."""
    raw = opinion.get("planning_insight")
    if not raw:
        return None
    try:
        return ReceiveInsightInput.model_validate(raw)
    except ValidationError as error:
        return f"기획 입력이 계약과 다름 ({error.error_count()}곳)"


async def send_to_planning(
    inp: ReceiveInsightInput, context_id: str, tenant_id: str | None = None
) -> dict[str, Any]:
    """기획에 넘긴다. {ok, outcome, proposal_id, problem}을 돌려준다.

    `tenant_id`: 그 의견을 받은 매니저의 회사(11/25). 없으면 받는 쪽 기본값(캣북스)."""
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=context_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS),
            idempotency_key=f"relay:{TARGET}:{inp.insight_id}",
            **({"tenant_id": tenant_id} if tenant_id else {}),
        ),
        skill=SKILL,
        input=inp.model_dump(mode="json"),
    )
    try:
        result = await call_agent(TARGET, sent)
    except Exception as exc:  # noqa: BLE001 - 서비스 꺼짐·연결 실패
        return {"ok": False, "problem": f"호출 실패 ({type(exc).__name__})"}
    if result.status != TaskStatus.COMPLETED:
        return {"ok": False, "problem": f"{result.status.value} {result.outcome or ''}".strip()}
    try:
        out = ReceiveInsightOutput.model_validate(result.output or {})
    except ValidationError:
        return {"ok": False, "problem": "기획 응답이 계약과 다름"}
    return {"ok": True, "outcome": out.outcome, "proposal_id": out.proposal_id}
