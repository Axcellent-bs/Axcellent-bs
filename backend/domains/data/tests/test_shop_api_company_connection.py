"""데이터분석의 shop-api 주소도 회사 연결을 따른다 (부를 때마다 다시 정함, 재시작 없음)."""

import json
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from data_agent.clients.shop_client import get_shop_client, shop_api_url
from data_agent.skills.anomaly_detection import get_metric_source


@pytest.fixture
def conn_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "company_connection.json"
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    return path


def test_the_connection_wins_over_the_env(conn_file: Path) -> None:
    env = {"AX_SHOP_API_URL": "http://127.0.0.1:8010"}
    assert shop_api_url(env) == "http://127.0.0.1:8010"
    conn_file.write_text(
        json.dumps({"shop_api_url": "http://192.168.0.251:8080/shop-api"}), "utf-8"
    )
    assert shop_api_url(env) == "http://192.168.0.251:8080/shop-api"


def test_a_client_made_before_connecting_follows_the_connection(conn_file: Path) -> None:
    client = get_shop_client({})
    source = get_metric_source({})
    assert client.api.base_url == source.api.base_url == "http://shop-api:8010"
    conn_file.write_text(
        json.dumps({"shop_api_url": "http://192.168.0.251:8080/shop-api/"}), "utf-8"
    )
    assert client.api.base_url == source.api.base_url == "http://192.168.0.251:8080/shop-api"
    conn_file.unlink()
    assert client.api.base_url == "http://shop-api:8010"
