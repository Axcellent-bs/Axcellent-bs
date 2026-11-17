"""에이전트 화면의 "총괄 AI가 보낸 진단" 칸: 에이전트가 받아 둔 진단 결과를 읽어 준다 (읽기 전용).

총괄이 같은 단계의 반복 지연을 진단하면 해당 에이전트(서비스)가 `receive_improvement_note`로 받아
저장한다. 이 파일은 그 목록을 서비스의 `GET /api/improvement-notes`에서 읽어 화면에 넘길 뿐이다.
상태를 바꾸지 않고 철회도 여기서 하지 않는다(철회는 총괄 화면에서만).

- 에이전트(부서) 하나에 서비스가 여럿일 수 있어(예: 고객지원) 받는 skill이 있는 서비스를
  모두 읽고 서비스 이름을 같이 붙인다.
- 한 서비스가 응답하지 않아도 나머지는 보여 주고 `meta.unavailable`에 이유를 남긴다.
- 진단 문장에는 고객 이름·문의 원문이 없다(단계·시간·건수·이벤트 종류·처리 결과만).
"""

from __future__ import annotations

from typing import Any

from ax_agent_template.services import SERVICES, Service
from ax_contracts.schemas.diagnosis import ListImprovementNotesOutput
from ax_dashboard_api import relay
from ax_dashboard_api.envelope import ApiError, note_unavailable, unavailable_text

SKILL = "receive_improvement_note"
PATH = "/api/improvement-notes"


def services_for(agent: str) -> list[Service]:
    """이 에이전트(부서)의 진단 결과를 받는 서비스."""
    return [s for s in SERVICES if s.department == agent and SKILL in s.skills]


async def received_notes(agent: str) -> list[dict[str, Any]]:
    """서비스마다 읽어 합친다(최근 것부터). 서비스 이름(`service`·`service_name`)을 붙인다."""
    items: list[dict[str, Any]] = []
    for service in services_for(agent):
        try:
            data = await relay._get(service.id, PATH)  # noqa: SLF001 (같은 패키지의 읽기 도우미)
            out = ListImprovementNotesOutput.model_validate(data)
        except (ApiError, ValueError):
            note_unavailable(unavailable_text(service.id))
            continue
        for note in out.notes:
            items.append(
                {
                    **note.model_dump(mode="json"),
                    "service": service.id,
                    "service_name": service.name,
                }
            )
    items.sort(key=lambda n: n["received_at"], reverse=True)
    return items
