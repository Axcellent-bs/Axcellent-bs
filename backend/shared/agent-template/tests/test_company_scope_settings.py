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
        # 캣북스 환경변수를 쓰지 않는다. 회사 설정이 없으면 틀 기본값(공통 카탈로그, 11/25)
        assert tenant.setting("DATA_TENANT_CATALOG") == "common"


def test_company_without_settings_gets_frame_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """가입만 한 회사(서버 id c-cf2f0d, 회사 설정 없음)는 틀 기본값을 쓴다(11/25).

    공통 카탈로그·업무 배정 틀·시간대 Asia/Seoul. 다른 회사 폴더나 캣북스 값으로 가지 않는다."""
    from ax_agent_template import routing

    for name in ("DATA_TENANT_CATALOG", "AX_ORG_CONFIG", "DATA_TENANT_TIMEZONE"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(tenant, "current", lambda: None)
    monkeypatch.setattr(routing, "_cache", None)
    with company_scope.using("c-cf2f0d"):
        assert tenant.setting("DATA_TENANT_CATALOG") == "common"
        assert tenant.setting("DATA_TENANT_TIMEZONE") == "Asia/Seoul"
        org = tenant.setting("AX_ORG_CONFIG")
        assert org.endswith("config/frame/org.yaml")
        assert "tenants" not in org  # 다른 회사 폴더가 아니다
        table = routing.load_org()
        assert table["data"].unit and table["orchestrator"].unit
        assert tenant.setting("FINANCE_PER_HEAD_MONTHLY") == ""  # 틀에 없는 값은 비움(코드 기본값)


def test_catbooks_and_named_company_ignore_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """캣북스와 회사 설정이 정해진 회사는 틀 기본값을 보지 않는다."""
    monkeypatch.setenv("DATA_TENANT_CATALOG", "")
    monkeypatch.setenv("DATA_TENANT_TIMEZONE", "")
    monkeypatch.setattr(tenant, "current", lambda: None)
    assert tenant.setting("DATA_TENANT_CATALOG") == ""  # 요청 밖(캣북스): 예전 그대로
    with company_scope.using("catbooks"):
        assert tenant.setting("DATA_TENANT_CATALOG") == ""
    monkeypatch.setattr(tenant, "current", lambda: "nubi")
    with company_scope.using("nubi-tour"):
        assert tenant.setting("DATA_TENANT_CATALOG") == "nubi"  # 그 회사 폴더 그대로
        assert tenant.setting("DATA_TENANT_TIMEZONE") == "Asia/Seoul"
