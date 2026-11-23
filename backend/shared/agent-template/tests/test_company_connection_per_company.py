"""회사별 연결 한 줄 (여러 회사 설계 6.2, 2026-11-23).

- 회사를 안 주면 지금 요청 회사(`company_scope.active()`), 요청 밖이면 캣북스 (기존 호출 그대로)
- 캣북스: 옛 단일 파일 이관·되돌리기 그대로
- 다른 회사: 늘 `<회사 폴더>/<회사 ID>/company_connection.json`. 옛 파일을 옮기지도 되살리지도 않고,
  캣북스 연결을 덮어쓰지 않는다
- AI 주소(`config.agent_url("shop-api")`)는 요청 회사의 연결. 다른 회사인데 연결이 없으면
  캣북스 판매 서버로 빠지지 않고 그 회사 변환기 경로(503 연결 안 됨)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files, company_scope
from ax_agent_template import company_connection as cc
from ax_agent_template import tenant as tenants
from ax_agent_template.config import agent_url, shop_api_url

CAT_SHOP = "http://127.0.0.1:8090"
NUBI_SITE = "http://127.0.0.1:18081"
STUDY_SITE = "http://127.0.0.1:18082"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """run_all.sh 모양: `.data/company_connection.json`(설치 기준 자리) + `.data/companies/`."""
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(root / "company_connection.json"))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.delenv(tenants.TENANT_ENV, raising=False)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.setattr(cc, "_cache", None)
    company_files.reset_for_tests()
    return root


def _legacy(root: Path) -> Path:
    legacy = root / "company_connection.json"
    legacy.write_text(
        json.dumps({"shop_api_url": CAT_SHOP, "enabled": True, "updated_by": "admin"}),
        encoding="utf-8",
    )
    return legacy


def _save(
    url: str,
    company: str | None = None,
    *,
    enabled: bool = True,
    kind: cc.Kind = "shop_api",
    adapter: dict[str, str] | None = None,
    tenant: str | None = None,
) -> cc.Connection:
    return cc.save_connection(
        url,
        verified_at=None,
        updated_by="관리자",
        enabled=enabled,
        kind=kind,
        adapter=adapter,
        tenant=tenant,
        company_id=company,
    )


def _url(company: str | None = None) -> str:
    found = cc.read_connection(company)
    assert found is not None
    return found.shop_api_url


def test_without_company_id_reads_catbooks_and_moves_the_old_file(data: Path) -> None:
    """회사 ID 없는 요청(요청 밖·예전 호출)은 캣북스: 옛 단일 파일을 캣북스 폴더로 옮겨 읽는다."""
    legacy = _legacy(data)
    found = cc.read_connection()
    assert found is not None and found.shop_api_url == CAT_SHOP
    assert cc.connection_path() == str(data / "companies" / "catbooks" / cc.CONNECTION_NAME)
    assert not legacy.exists() and legacy.with_name(legacy.name + ".moved").exists()
    assert agent_url("shop-api") == CAT_SHOP


def test_other_company_never_takes_the_old_file(data: Path) -> None:
    """다른 회사가 먼저 읽어도 옛 파일(캣북스 것)은 그 회사로 가지 않는다."""
    legacy = _legacy(data)
    assert cc.read_connection("nubi") is None
    assert legacy.exists()  # 옮기지 않았다
    assert cc.connection_path("nubi") == str(data / "companies" / "nubi" / cc.CONNECTION_NAME)
    found = cc.read_connection()  # 그다음 캣북스가 읽으면 캣북스로 옮긴다
    assert found is not None and found.shop_api_url == CAT_SHOP


def test_two_companies_do_not_overwrite_each_other(data: Path) -> None:
    _legacy(data)
    _save(NUBI_SITE, "nubi")
    _save(STUDY_SITE, "studyon")
    cat = cc.read_connection("catbooks")
    assert cat is not None and cat.shop_api_url == CAT_SHOP  # 캣북스 연결 그대로
    assert _url("nubi") == NUBI_SITE
    assert _url("studyon") == STUDY_SITE
    # 기록도 회사마다
    assert [h["to"] for h in cc.read_history(company_id="nubi")] == [NUBI_SITE]
    assert all(h.get("to") != NUBI_SITE for h in cc.read_history(company_id="catbooks"))
    # 한 회사 연결을 지워도 다른 회사는 그대로
    assert cc.clear_connection("관리자", company_id="nubi") is True
    assert cc.read_connection("nubi") is None
    assert _url("catbooks") == CAT_SHOP


def test_request_company_picks_the_connection_and_ai_address(data: Path) -> None:
    """AI 요청(meta.tenant_id → `company_scope.using`)마다 그 회사 연결로 주소를 고른다."""
    _save(CAT_SHOP, "catbooks")
    _save(NUBI_SITE, "nubi")
    assert agent_url("shop-api") == CAT_SHOP  # 요청 밖 = 캣북스
    with company_scope.using("nubi"):
        assert _url() == NUBI_SITE
        assert agent_url("shop-api") == NUBI_SITE
        assert cc.company_hosts() == frozenset({"127.0.0.1"})
    with company_scope.using(None):  # 회사 ID 없는 요청 = 캣북스 (5단계 경고만 규칙)
        assert agent_url("shop-api") == CAT_SHOP
    with company_scope.using("shop-demo"):  # 별칭도 캣북스
        assert agent_url("shop-api") == CAT_SHOP


def test_saving_inside_a_request_writes_only_that_company(data: Path) -> None:
    _save(CAT_SHOP, "catbooks")
    with company_scope.using("nubi"):
        _save(NUBI_SITE)  # 회사를 안 주면 요청 회사
    assert _url() == CAT_SHOP
    assert _url("nubi") == NUBI_SITE


def test_adapter_address_carries_the_company(data: Path) -> None:
    """kind=adapter: 캣북스는 예전 `/site-adapter`, 다른 회사는 `/site-adapter/<회사 ID>`."""
    _save(CAT_SHOP, "catbooks", kind="adapter", adapter={"base_url": CAT_SHOP})
    _save(NUBI_SITE, "nubi", kind="adapter", adapter={"base_url": NUBI_SITE})
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"
    with company_scope.using("nubi"):
        assert agent_url("shop-api") == "http://dash:8020/site-adapter/nubi"
    assert cc.adapter_path("catbooks") == "/site-adapter"
    assert cc.adapter_path("nubi") == "/site-adapter/nubi"


def test_other_company_without_connection_does_not_fall_back_to_catbooks(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """연결 없는 다른 회사가 캣북스 판매 서버(`AX_SHOP_API_URL`)를 받지 않는다."""
    monkeypatch.setenv("AX_SHOP_API_URL", CAT_SHOP)
    assert agent_url("shop-api") == CAT_SHOP  # 캣북스는 예전 그대로
    with company_scope.using("nubi"):
        assert agent_url("shop-api") == "http://dash:8020/site-adapter/nubi"


def test_disabled_other_company_does_not_fall_back(data: Path, monkeypatch) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", CAT_SHOP)
    _save(NUBI_SITE, "nubi", enabled=False)
    with company_scope.using("nubi"):
        assert agent_url("shop-api") == "http://dash:8020/site-adapter/nubi"


def test_other_company_does_not_fall_back_even_without_connection_feature(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1634 리뷰 1: 연결 기능이 꺼진 설치(연결 파일 없음)에서도 다른 회사는 설치 값을 안 받는다."""
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV)
    monkeypatch.setenv("AX_SHOP_API_URL", CAT_SHOP)
    assert agent_url("shop-api") == CAT_SHOP
    with company_scope.using("nubi"):
        assert agent_url("shop-api") == "http://dash:8020/site-adapter/nubi"


def test_shop_api_url_helper_blocks_the_install_fallback_for_other_companies(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1634 리뷰 1: 도메인이 주는 폴백(`AX_SHOP_API_URL`·기본 주소)은 캣북스·요청 밖에서만 쓴다."""
    fallback = "http://shop-api:8010"
    assert shop_api_url(fallback) == fallback  # 요청 밖
    with company_scope.using("catbooks"):
        assert shop_api_url(fallback) == fallback
    with company_scope.using("shop-demo"):  # 별칭도 캣북스
        assert shop_api_url(fallback) == fallback
    with company_scope.using("nubi"):
        assert shop_api_url(fallback) == "http://dash:8020/site-adapter/nubi"
    _save(NUBI_SITE, "nubi")
    _save(CAT_SHOP, "catbooks")
    with company_scope.using("nubi"):
        assert shop_api_url(fallback) == NUBI_SITE  # 연결이 있으면 그 회사 주소
    assert shop_api_url(fallback) == CAT_SHOP


def test_folders_off_restores_catbooks_and_keeps_other_company(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """되돌리기: off면 캣북스는 옛 자리로 되살리고, 다른 회사는 회사 폴더 값 그대로 읽는다."""
    legacy = _legacy(data)
    assert cc.read_connection() is not None  # 옮김
    _save(NUBI_SITE, "nubi")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    found = cc.read_connection()
    assert legacy.exists() and found is not None and found.shop_api_url == CAT_SHOP
    assert json.loads(legacy.read_text(encoding="utf-8"))["shop_api_url"] == CAT_SHOP
    assert _url("nubi") == NUBI_SITE


def test_other_company_save_does_not_change_install_tenant(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설치 기록(`install_tenant.json`)은 캣북스 연결만 남긴다."""
    remembered: list[str] = []
    monkeypatch.setattr(tenants, "is_tenant", lambda name: name in {"catbooks", "nubi"})
    monkeypatch.setattr(tenants, "remember", remembered.append)
    _save(NUBI_SITE, "nubi", tenant="nubi")
    assert remembered == []
    _save(CAT_SHOP, "catbooks", tenant="catbooks")
    assert remembered == ["catbooks"]
