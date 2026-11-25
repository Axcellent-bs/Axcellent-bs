"""다른 회사 요청은 그 회사 카탈로그를 쓴다. 설치 환경변수(캣북스 카탈로그)를 쓰지 않는다
(11/25 누비투어 데이터 화면에 "소설은 누가 사나" 같은 캣북스 서점 문구)."""

from __future__ import annotations

import pytest

from ax_agent_template import company_scope, tenant
from data_agent import catalog


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(catalog.CATALOG_ENV, "catbooks")
    monkeypatch.setattr(tenant, "current", lambda: None)
    catalog.use_catalog(None)


def test_other_company_gets_its_own_catalog() -> None:
    assert catalog.current().id == "catbooks"  # 요청 밖: 예전 그대로
    with company_scope.using("nubi-tour"):
        cat = catalog.current()
        assert cat.id == "nubi"
        assert all("소설" not in g for g in (cat.sales.genres.values() if cat.sales else []))
    with company_scope.using("studyon"):
        assert catalog.current().id == "studyon"
    with company_scope.using("catbooks"):
        assert catalog.current().id == "catbooks"


def test_company_without_catalog_does_not_borrow_catbooks() -> None:
    """회사 설정·카탈로그가 없는 회사(가입만 한 회사)는 틀 기본 카탈로그를 쓴다(11/25).
    캣북스 카탈로그(서점 장르·판매 세부)로 떨어지지 않는다."""
    with company_scope.using("c-cf2f0d"):
        cat = catalog.current()
        assert cat.id == "common"
        assert cat.id != "catbooks"
        # 매출 분석은 공통 칸만 (11/25). 캣북스 장르·분류·회원 속성·서점 낱말은 없다
        assert cat.sales is not None and cat.sales.item_noun == "상품"
        assert not cat.sales.genres and not cat.sales.categories and not cat.sales.attributes
        assert "배송비" not in cat.sales.revenue_definition
        assert cat.connector == "shop-api"
        assert {m.id for m in cat.metrics} == {"order_count", "inquiry_count"}


def test_company_without_settings_counts_days_in_korea_time() -> None:
    """시간대도 틀 기본값(Asia/Seoul). UTC로 떨어지면 요일·날짜 경계가 9시간 어긋난다."""
    from data_agent.skills.campaign_effect import tenant_timezone

    with company_scope.using("c-cf2f0d"):
        assert str(tenant_timezone({})) == "Asia/Seoul"


def test_site_companies_get_common_sales_section() -> None:
    """누비투어·스터디온도 매출·객단가·재구매율을 낸다(11/25). 품목 이름은 기본 "상품", 서점 칸은 없다.
    캣북스 매출 설정은 그대로."""
    expected = {"nubi-tour": "nubi", "studyon": "studyon"}
    for company, cat_id in expected.items():
        with company_scope.using(company):
            cat = catalog.current()
            assert cat.id == cat_id
            assert cat.sales is not None and cat.sales.item_noun == "상품"  # 기본 품목 이름
            assert not cat.sales.genres and not cat.sales.attributes
            assert "배송비" not in cat.sales.revenue_definition
    with company_scope.using("catbooks"):
        sales = catalog.current().sales
        assert sales is not None and sales.item_noun == "책" and sales.genres
