"""서비스운영(infra)의 shop-api 주소도 회사 연결을 따른다 (부를 때마다 다시 정함)."""

import json
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template import company_scope
from infra_agent.clients import HttpShop, default_shop, shop_api_url


@pytest.fixture
def conn_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "company_connection.json"
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setenv("AX_SHOP_API_URL", "http://127.0.0.1:8010")
    monkeypatch.setattr(cc, "_cache", None)
    return path


def test_default_shop_follows_the_connection(conn_file: Path) -> None:
    shop = default_shop()
    assert isinstance(shop, HttpShop) and shop.base_url == "http://127.0.0.1:8010"
    conn_file.write_text(json.dumps({"shop_api_url": "http://10.20.30.40:8080/shop-api"}), "utf-8")
    assert shop.base_url == "http://10.20.30.40:8080/shop-api"


@pytest.mark.parametrize("with_connection_feature", [True, False])
def test_other_company_never_reads_the_catbooks_install_address(
    conn_file: Path, monkeypatch: pytest.MonkeyPatch, with_connection_feature: bool
) -> None:
    """#1634 리뷰 1: 연결 안 한 회사 B 요청은 `AX_SHOP_API_URL`(캣북스 판매 서버)로 빠지지 않는다.

    B의 장애 상태 읽기·해제가 캣북스 판매 서버로 가지 않게,
    그 회사 변환기 경로(503 연결 안 됨)로 간다.
    """
    if not with_connection_feature:
        monkeypatch.delenv(cc.CONNECTION_FILE_ENV)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    shop = default_shop()
    assert isinstance(shop, HttpShop)
    with company_scope.using("nubi"):
        assert shop_api_url() == shop.base_url == "http://dash:8020/site-adapter/nubi"
    with company_scope.using("catbooks"):
        assert shop.base_url == "http://127.0.0.1:8010"  # 캣북스는 예전 그대로
    assert shop.base_url == "http://127.0.0.1:8010"


def test_a_fixed_address_still_works() -> None:
    assert HttpShop("http://shop-api:8010/").base_url == "http://shop-api:8010"
