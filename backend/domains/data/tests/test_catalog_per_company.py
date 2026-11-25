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
    with company_scope.using("zz-unknown"), pytest.raises(ValueError):
        catalog.current()
