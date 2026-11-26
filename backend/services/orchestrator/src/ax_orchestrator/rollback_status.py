"""대화방 의뢰 `incident_rollback_status`(11/26, 롤백 요청 2단계): 열린 사건의 롤백 상태 읽기.

읽기 전용이다. 총괄이 자기 사건 기록(`Incident`)을 읽기만 하고 부서를 부르지 않으며 `execute()`도
부르지 않는다. 승인·거절·롤백 실행 길을 열지 않는다
(롤백은 기존 사건 흐름의 안전 관문(B)에서만 한다).

- 회사 범위: 총괄은 회사마다 사건 목록(`IncidentManager`)을 따로 가지므로,
  요청 회사의 목록만 넘겨받아 다른 회사 사건은 섞이지 않는다.
- "열린 사건" = 롤백 단계가 있는 사건 중 진행 중(`open`)이거나 장애가 안 풀린 채 대응이 끝난
  (`degraded`, 복구 신호 전) 것.
- 의뢰 단계에는 번호만 남긴다(`open:<열린 사건 수>`). 롤백 상태(서비스·버전·확신도 등) 자세한 값은
  카드가 같은 사건 목록(`GET /api/incidents`, 로그인 회사 범위)에서 읽는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ax_orchestrator.models import Incident

ROLLBACK_STEP = "rollback"


def is_open(inc: Incident) -> bool:
    """롤백 단계가 있고, 진행 중이거나 장애가 안 풀린 채 대응이 끝난 사건."""
    if ROLLBACK_STEP not in inc.steps:
        return False
    return inc.status == "open" or (inc.status == "degraded" and inc.recovered_at is None)


def open_incidents(incidents: Iterable[Incident]) -> list[Incident]:
    """열린 사건, 최근에 열린 것부터."""
    return sorted((i for i in incidents if is_open(i)), key=lambda i: i.opened_at, reverse=True)


def read_rollback_status(incidents: Iterable[Incident]) -> dict[str, Any]:
    """의뢰 단계 결과: 열린 사건 수만 번호로 낸다(문장·사건 제목은 싣지 않는다)."""
    return {"ref": f"open:{len(open_incidents(incidents))}"}
