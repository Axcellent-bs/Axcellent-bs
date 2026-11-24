"""data 요청 회사 고르기 (11/24 누비투어 연결)."""


def test_active_follows_the_request_company_the_server_set() -> None:
    """data가 따로 걸지 않은 곳은 서버가 건 요청 회사(공용 company_scope)를 읽는다(11/24)."""
    from ax_agent_template import company_scope as request_scope
    from data_agent import company_scope

    with request_scope.using("nubi"):
        assert company_scope.active() == "nubi"
        with company_scope.using("studyon"):  # data가 건 값이 먼저
            assert company_scope.active() == "studyon"
