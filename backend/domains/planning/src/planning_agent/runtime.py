"""프로세스 안의 기획 서비스 하나 (planning, 8101). A2A 핸들러와 REST가 같은 서비스를 본다.

v0은 DB 없이 프로세스 메모리에만 둔다 (재시작하면 초기 데이터로 돌아간다). 상태 파일
(`PLANNING_STATE_DIR`)이 있으면 이어 쓴다. 개발 서비스는 `dev_agent.runtime`.
"""

from __future__ import annotations

from planning_agent.domain.service import PlanningService

_service: PlanningService | None = None


def get_service() -> PlanningService:
    global _service
    if _service is None:
        _service = PlanningService(real_example=True)
    return _service


def set_service(service: PlanningService) -> PlanningService:
    """테스트에서 시계·관문을 바꾼 서비스를 끼울 때 쓴다."""
    global _service
    _service = service
    return service
