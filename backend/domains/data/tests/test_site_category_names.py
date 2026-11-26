"""사이트 변환기로 연결한 회사의 분류 이름.

11/26 누비투어 매출·고객 분석에 japan·DEV 같은 코드가 보임.

사이트 /api/v1은 분류 이름을 주지 않아 사이트 레포 정의를 카탈로그 `category_names`에 옮겼다.
닫힌 목록이 아니다: 표에 없는 새 사이트 값은 그대로 보인다(#1841). 캣북스 분류 표는 그대로.
"""

from __future__ import annotations

import pytest

from ax_agent_template import company_scope, tenant
from data_agent import catalog
from data_agent.skills.sales_analysis import NO_CATEGORY, category_label
from data_agent.skills.segment_analysis import options


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(catalog.CATALOG_ENV, "catbooks")
    monkeypatch.setattr(tenant, "current", lambda: None)
    catalog.use_catalog(None)


def test_nubi_regions_show_site_names_and_unknown_stays_code() -> None:
    with company_scope.using("nubi-tour"):
        cfg = catalog.current().sales
    assert cfg is not None and not cfg.categories  # 닫힌 분류 표가 아니다
    assert category_label(cfg, "japan") == "일본"
    assert category_label(cfg, "southeast-asia") == "동남아"
    assert category_label(cfg, "oceania") == "괌·사이판·대양주"
    assert category_label(cfg, "africa") == "africa"  # 표에 없는 새 지역은 사이트 값 그대로
    assert category_label(cfg, "") == NO_CATEGORY


def test_studyon_subjects_and_package_lines_show_site_names() -> None:
    with company_scope.using("studyon"):
        cfg = catalog.current().sales
    assert cfg is not None
    assert category_label(cfg, "DEV") == "개발·프로그래밍"
    assert category_label(cfg, "EXAM") == "수능·내신"
    assert category_label(cfg, "bundle") == "패키지"  # 패키지 줄(#1901)
    assert category_label(cfg, "NEW") == "NEW"


def test_catbooks_and_signup_only_company_unchanged() -> None:
    with company_scope.using("catbooks"):
        cfg = catalog.current().sales
    assert cfg is not None and cfg.categories and not cfg.category_names
    assert category_label(cfg, "book") == "도서"
    # 캣북스 표에 없는 값은 그대로 이름 없는 분류
    assert category_label(cfg, "japan") == NO_CATEGORY
    with company_scope.using("c-cf2f0d"):  # 가입만 한 회사(틀 기본): 이름 표 없음 → 값 그대로
        common = catalog.current().sales
    assert common is not None and not common.category_names
    assert category_label(common, "japan") == "japan"


def test_segment_options_use_site_names_keep_codes() -> None:
    with company_scope.using("nubi-tour"):
        cat = catalog.current()
    out = options(cat.sales, cat.id, None, site_categories=["japan", "africa"])
    assert [(o.code, o.label) for o in out.categories] == [("japan", "일본"), ("africa", "africa")]


def test_names_do_not_leak_between_companies() -> None:
    with company_scope.using("studyon"):
        study = catalog.current().sales
    with company_scope.using("nubi-tour"):
        nubi = catalog.current().sales
    assert study is not None and nubi is not None
    assert category_label(study, "japan") == "japan"
    assert category_label(nubi, "DEV") == "DEV"
