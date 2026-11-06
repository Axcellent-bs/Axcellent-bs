"""서비스운영(infra)의 shop-api 주소도 회사 연결을 따른다 (부를 때마다 다시 정함)."""

import json
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from infra_agent.clients import HttpShop, default_shop


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
    conn_file.write_text(
        json.dumps({"shop_api_url": "http://192.168.0.251:8080/shop-api"}), "utf-8"
    )
    assert shop.base_url == "http://192.168.0.251:8080/shop-api"


def test_a_fixed_address_still_works() -> None:
    assert HttpShop("http://shop-api:8010/").base_url == "http://shop-api:8010"
