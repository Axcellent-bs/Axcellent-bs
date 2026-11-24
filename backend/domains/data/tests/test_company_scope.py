"""data 요청 회사 고르기 (11/24 누비투어 연결)."""


def test_active_follows_the_request_company_the_server_set() -> None:
    """data가 따로 걸지 않은 곳은 서버가 건 요청 회사(공용 company_scope)를 읽는다(11/24)."""
    from ax_agent_template import company_scope as request_scope
    from data_agent import company_scope

    with request_scope.using("nubi"):
        assert company_scope.active() == "nubi"
        with company_scope.using("studyon"):  # data가 건 값이 먼저
            assert company_scope.active() == "studyon"


def test_sales_tenant_compares_canonical_ids_and_follows_the_request_company() -> None:
    """판매 skill 회사 검사: 별칭은 같은 회사, 입력이 기본값이면 요청 회사, 다른 회사는 거절."""
    from data_agent.handlers import sales_tenant

    assert sales_tenant("shop-demo", "shop-demo") == "shop-demo"
    assert sales_tenant("shop-demo", "catbooks") == "shop-demo"  # 캣북스는 입력 값 그대로
    assert sales_tenant("catbooks", "shop-demo") == "catbooks"
    assert sales_tenant("shop-demo", "nubi") == "nubi"  # 입력 기본값 → 요청 회사
    assert sales_tenant("nubi", "nubi") == "nubi"
    assert sales_tenant("nubi", "shop-demo") is None  # 캣북스 요청이 다른 회사 판매를 읽지 않는다
    assert sales_tenant("nubi", "studyon") is None
