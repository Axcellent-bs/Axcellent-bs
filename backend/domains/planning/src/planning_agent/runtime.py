"""프로세스 안의 기획 서비스 (planning, 8101). A2A 핸들러와 REST가 같은 서비스를 본다.

v0은 DB 없이 프로세스 메모리에만 둔다 (재시작하면 초기 데이터로 돌아간다). 상태 파일
(`PLANNING_STATE_DIR`)이 있으면 이어 쓴다. 개발 서비스는 `dev_agent.runtime`.

회사마다 따로 (11/24 누비투어 연결): 서버가 요청마다 회사를 건다(skill 요청은 `meta.tenant_id`,
화면 HTTP는 로그인 세션 회사, `ax_agent_template.server.SessionCompanyScope`). 캣북스(설치 회사)와
회사를 모르는 요청은 예전 서비스 하나 그대로고, 다른 회사는 그 회사 서비스(상태 파일
`planning_state_planning.<회사>.json`, 빈 상태로 시작)를 본다. 그래서 누비투어 세션의 제안·기획서·
마케팅 브리프 목록에 캣북스 건이 섞이지 않는다.
"""

from __future__ import annotations

from ax_agent_template import company_scope
from planning_agent.domain.service import PlanningService

_service: PlanningService | None = None
_by_company: dict[str, PlanningService] = {}


def company_of_request() -> str | None:
    """지금 요청의 회사가 캣북스가 아니면 그 회사 id, 아니면 None (예전 서비스)."""
    active = company_scope.active()
    if active is None:
        return None
    company = company_scope.scope_id(active)
    return None if company == company_scope.CATBOOKS else company


def get_service() -> PlanningService:
    company = company_of_request()
    if company is not None:
        svc = _by_company.get(company)
        if svc is None:
            svc = _by_company[company] = PlanningService(
                real_example=True, tenant_id=company, company=company
            )
        return svc
    global _service
    if _service is None:
        _service = PlanningService(real_example=True)
    return _service


def set_service(service: PlanningService, company: str | None = None) -> PlanningService:
    """테스트에서 시계·관문을 바꾼 서비스를 끼울 때 쓴다. `company`면 그 회사 자리에."""
    global _service
    if company is not None:
        _by_company[company_scope.scope_id(company)] = service
        return service
    _service = service
    return service
