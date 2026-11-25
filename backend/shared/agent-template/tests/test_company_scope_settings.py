"""다른 회사 요청은 설치(캣북스) 환경변수·키 저장소 기본 회사로 떨어지지 않는다.

11/25 누비투어에 캣북스 유튜브·인스타 계정, 서점 카탈로그가 보이던 것."""

from __future__ import annotations

import pytest

from ax_agent_template import company_ai, company_scope, tenant


def test_company_id_follows_other_company_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "current", lambda: "catbooks")
    assert company_ai.company_id() == "catbooks"  # 요청 밖: 예전 그대로
    with company_scope.using("nubi-tour"):
        assert company_ai.company_id() == "nubi-tour"
    with company_scope.using("catbooks"):
        assert company_ai.company_id() == "catbooks"


def test_derived_env_setting_is_catbooks_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_TENANT_CATALOG", "catbooks")
    monkeypatch.setattr(tenant, "current", lambda: None)  # 회사 폴더 없음
    assert tenant.setting("DATA_TENANT_CATALOG") == "catbooks"
    with company_scope.using("catbooks"):
        assert tenant.setting("DATA_TENANT_CATALOG") == "catbooks"
    with company_scope.using("nubi-tour"):
        assert tenant.setting("DATA_TENANT_CATALOG") == ""  # 캣북스 환경변수를 쓰지 않는다
