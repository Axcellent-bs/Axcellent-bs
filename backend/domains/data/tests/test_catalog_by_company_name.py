"""새 회사(가입 때 ID가 `c-xxxxxx`로 정해진 회사)도 자기 카탈로그를 쓴다 (11/27).

회사 ID로는 `catalogs/nubi.toml` 같은 파일을 못 고른다. 그래서 회사 연결 정보(회사 목록
`companies.json`의 `name`, 가입 때 넣은 회사 이름)와 카탈로그 `name`이 같으면 그 카탈로그를 쓴다.
실화면: #1908로 누비투어 지역 이름 표를 넣었는데도 매출 분석 분류가 japan 같은 코드로 보였다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import company_scope, tenant
from ax_agent_template.companies import COMPANIES_FILE_ENV
from data_agent import catalog
from data_agent.skills import sales_analysis

NUBI, STUDY, ODD, CATNAME = "c-cf2f0d", "c-f6c188", "c-aaaaaa", "c-bbbbbb"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rows = [
        {"company_id": "catbooks", "name": "캣북스", "industry": "", "site_url": "",
         "created_at": "2026-11-20T00:00:00Z"},
        {"company_id": NUBI, "name": "누비투어", "industry": "여행", "site_url": "",
         "created_at": "2026-11-24T00:00:00Z"},
        {"company_id": STUDY, "name": " 스터디온에듀 ", "industry": "교육", "site_url": "",
         "created_at": "2026-11-25T00:00:00Z"},
        {"company_id": ODD, "name": "처음 보는 회사", "industry": "", "site_url": "",
         "created_at": "2026-11-26T00:00:00Z"},
        {"company_id": CATNAME, "name": "캣북스", "industry": "", "site_url": "",
         "created_at": "2026-11-26T00:00:00Z"},
    ]  # fmt: skip
    path = tmp_path / "companies.json"
    path.write_text(json.dumps({"companies": rows}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(COMPANIES_FILE_ENV, str(path))
    monkeypatch.setenv(catalog.CATALOG_ENV, "catbooks")
    monkeypatch.setattr(tenant, "current", lambda: None)
    catalog.use_catalog(None)


def test_new_company_uses_catalog_named_like_it() -> None:
    with company_scope.using(NUBI):
        cat = catalog.current()
        assert cat.id == "nubi"
        assert sales_analysis.category_label(cat.sales, "japan") == "일본"
    with company_scope.using(STUDY):
        cat = catalog.current()
        assert cat.id == "studyon"
        assert sales_analysis.category_label(cat.sales, "bundle") == "패키지"


def test_unknown_name_stays_on_common_and_catbooks_name_is_not_borrowed() -> None:
    with company_scope.using(ODD):
        assert catalog.current().id == "common"
    with company_scope.using(CATNAME):  # 이름이 캣북스여도 캣북스 카탈로그를 빌리지 않는다
        assert catalog.current().id == "common"


def test_catbooks_unchanged() -> None:
    assert catalog.current().id == "catbooks"
    with company_scope.using("catbooks"):
        assert catalog.current().id == "catbooks"
