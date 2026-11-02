"""프로세스 안의 상태 저장소 하나 (목 모드). A2A 핸들러와 REST가 같은 서비스를 본다.

v0은 DB 없이 프로세스 메모리에만 둔다 (재시작하면 초기 데이터로 돌아간다).
서비스 역할(`PLANNING_ROLE`)이 dev면 개발 서비스(`DevService`), 아니면 기획 서비스를 띄운다.
"""

from __future__ import annotations

import os
from typing import Any

from planning_agent.domain.service import DevService, PlanningCore, PlanningService

_service: PlanningCore | None = None


def get_service() -> Any:
    """지금 프로세스의 서비스 (기획 `PlanningService` 또는 개발 `DevService`)."""
    global _service
    if _service is None:
        role = os.getenv("PLANNING_ROLE", "").strip()
        _service = (
            DevService(real_example=True) if role == "dev" else PlanningService(real_example=True)
        )
    return _service


def set_service(service: Any) -> Any:
    """테스트에서 시계·관문을 바꾼 서비스를 끼울 때 쓴다."""
    global _service
    _service = service
    return service
