"""서비스 에이전트 요청자를 원래 부서 이름으로도 인정한다 (services.py)."""

from ax_agent_template.services import department_of


def test_service_requester_maps_to_department() -> None:
    assert department_of("dev") == "planning"
    assert department_of("claims") == "support"
    assert department_of("safety") == "safety"
