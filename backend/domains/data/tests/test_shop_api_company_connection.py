"""데이터분석의 shop-api 주소도 회사 연결을 따른다 (부를 때마다 다시 정함, 재시작 없음)."""

import json
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template import company_scope
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
    conn_file.write_text(json.dumps({"shop_api_url": "http://10.20.30.40:8080/shop-api"}), "utf-8")
    assert shop_api_url(env) == "http://10.20.30.40:8080/shop-api"


def test_a_client_made_before_connecting_follows_the_connection(conn_file: Path) -> None:
    client = get_shop_client({})
    source = get_metric_source({})
    assert client.api.base_url == source.api.base_url == "http://shop-api:8010"
    conn_file.write_text(json.dumps({"shop_api_url": "http://10.20.30.40:8080/shop-api/"}), "utf-8")
    assert client.api.base_url == source.api.base_url == "http://10.20.30.40:8080/shop-api"
    conn_file.unlink()
    assert client.api.base_url == "http://shop-api:8010"


@pytest.mark.parametrize("with_connection_feature", [True, False])
def test_other_company_never_reads_the_catbooks_install_address(
    conn_file: Path, monkeypatch: pytest.MonkeyPatch, with_connection_feature: bool
) -> None:
    """#1634 리뷰 1: 연결 안 한 회사 B 요청은 `AX_SHOP_API_URL`(캣북스 판매 서버)로 빠지지 않는다.

    B의 영향 분석·매출 수집이 캣북스 데이터를 읽지 않게, 그 회사 변환기 경로(503 연결 안 됨)로 간다.
    """
    if not with_connection_feature:
        monkeypatch.delenv(cc.CONNECTION_FILE_ENV)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    env = {"AX_SHOP_API_URL": "http://127.0.0.1:8010"}
    client = get_shop_client(env)
    with company_scope.using("nubi"):
        assert shop_api_url(env) == "http://dash:8020/site-adapter/nubi"
        assert client.api.base_url == "http://dash:8020/site-adapter/nubi"
    with company_scope.using("catbooks"):
        assert shop_api_url(env) == "http://127.0.0.1:8010"  # 캣북스는 예전 그대로
    assert shop_api_url(env) == "http://127.0.0.1:8010"
