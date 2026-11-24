"""밈 소스 설정 (11/24): 인스타 밈·크리에이터 계정 칸과 유튜브 밈 키워드.

화면 저장이 회사 설정 trends.toml보다 먼저다. data가 같은 파일을 읽는다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import tenant
from ax_dashboard_api.main import app

RADAR = "/api/dashboard/instagram-radar"
WORDS = "/api/dashboard/youtube-meme-keywords"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(tenant.IG_RADAR_FILE_ENV, raising=False)
    monkeypatch.delenv(tenant.YT_MEME_KEYWORDS_FILE_ENV, raising=False)
    trends = tmp_path / "trends.toml"
    trends.write_text('[instagram]\nradar_accounts = ["brand_a"]\n'
                      'radar_meme_accounts = ["meme_file"]\n'
                      '[youtube]\nmeme_keywords = ["밈", "공감"]\n', "utf-8")  # fmt: skip
    monkeypatch.setenv("DATA_TREND_CONFIG", str(trends))
    return TestClient(app)


def test_meme_accounts_come_from_the_tenant_file_then_the_screen(client) -> None:
    data = client.get(RADAR).json()["data"]
    assert data["meme_accounts"] == ["meme_file"] and data["meme_source"] == "tenant"
    body = {"accounts": ["brand_a"], "meme_accounts": ["@meme.one", "meme_two"]}
    res = client.post(RADAR + "/save", json=body)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["meme_accounts"] == ["meme.one", "meme_two"] and data["meme_source"] == "screen"
    assert tenant.ig_radar_meme_accounts() == ["meme.one", "meme_two"]


def test_saving_only_benchmarks_keeps_the_saved_meme_list(client) -> None:
    client.post(RADAR + "/save", json={"accounts": ["a"], "meme_accounts": ["m"]})
    data = client.post(RADAR + "/save", json={"accounts": ["a", "b"]}).json()["data"]
    assert data["accounts"] == ["a", "b"] and data["meme_accounts"] == ["m"]


def test_same_handle_twice_and_over_200_together_are_refused(client) -> None:
    res = client.post(RADAR + "/save", json={"accounts": ["dup"], "meme_accounts": ["DUP"]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "DUPLICATE_HANDLE"
    body = {"accounts": [f"b{i}" for i in range(150)],
            "meme_accounts": [f"m{i}" for i in range(51)]}  # fmt: skip
    res = client.post(RADAR + "/save", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "TOO_MANY"


def test_keywords_tenant_file_then_screen_then_clear(client) -> None:
    data = client.get(WORDS).json()["data"]
    assert data["keywords"] == ["밈", "공감"] and data["source"] == "tenant"
    assert data["max"] == 10 and data["editable"] is True
    res = client.post(WORDS + "/save", json={"keywords": [" 책  추천 ", "짤", "짤", ""]})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["keywords"] == ["책 추천", "짤"]
    assert tenant.yt_meme_keywords() == ["책 추천", "짤"]
    data = client.post(WORDS + "/clear").json()["data"]
    assert data["source"] == "tenant" and tenant.yt_meme_keywords() is None


def test_keywords_over_ten_or_too_long_are_refused(client) -> None:
    res = client.post(WORDS + "/save", json={"keywords": [f"k{i}" for i in range(11)]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "TOO_MANY"
    res = client.post(WORDS + "/save", json={"keywords": ["가" * 31]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "TOO_LONG"
