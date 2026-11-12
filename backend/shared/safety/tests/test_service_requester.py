"""서비스 에이전트 요청자를 원래 부서 이름으로도 인정한다 (services.py)."""

from ax_agent_template.services import department_of


def test_service_requester_maps_to_department() -> None:
    assert department_of("dev") == "dev"  # 개발은 기획과 다른 부서
    assert department_of("claims") == "support"
    assert department_of("safety") == "safety"


def test_dev_is_not_planning() -> None:
    """개발은 기획 이름을 빌리지 않는다. 예전 주소(planning + 개발 skill)는 dev로만 보낸다."""
    from ax_agent_template.services import route_target

    assert route_target("dev", "find_related_prs") == "dev"
    assert route_target("planning", "find_related_prs") == "dev"
    assert route_target("planning", "create_ticket") == "planning"
    assert department_of("dev") != "planning"


def test_settlement_service_speaks_for_support() -> None:
    """판매자 정산은 claims에서 settlement 서비스로 나왔다(2026-11-12). 권한은 고객지원 그대로."""
    from ax_agent_template.services import route_target

    assert department_of("settlement") == "support"
    for skill in (
        "seller_settlement",
        "seller_settlement_weeks",
        "seller_settlement_batches",
        "seller_settlement_deadlines",
        "request_seller_settlement",
    ):
        assert route_target("support", skill) == "settlement"
    assert route_target("support", "decide_claim") == "claims"
