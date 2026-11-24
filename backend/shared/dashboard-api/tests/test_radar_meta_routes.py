"""레이더 계정 메타 (11/24): 사람이 줄마다 고치면 tag_source=human. 다른 회사 메타는 안 보인다."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import tenant
from ax_dashboard_api.main import app

BASE = "/api/dashboard/instagram-radar"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(tenant.IG_RADAR_FILE_ENV, raising=False)
    monkeypatch.setenv(tenant.IG_RADAR_META_FILE_ENV, str(tmp_path / "meta.json"))
    trends = tmp_path / "trends.toml"
    trends.write_text('[instagram]\nradar_accounts = ["brand_a"]\n'
                      'radar_meme_accounts = ["meme_one"]\nradar_min_followers = 30000\n',
                      "utf-8")  # fmt: skip
    monkeypatch.setenv("DATA_TREND_CONFIG", str(trends))
    return TestClient(app)


def test_state_has_meta_and_the_follower_floor(client) -> None:
    data = client.get(BASE).json()["data"]
    assert data["meta"] == {} and data["min_followers"] == 30000 and data["meta_editable"]


def test_a_human_edit_keeps_followers_and_marks_human(client) -> None:
    def ai(data):  # noqa: ANN001, ANN202
        data["accounts"]["meme_one"] = {"kind": "meme", "ages": ["10s"], "tag_source": "ai",
                                        "followers": 120000}  # fmt: skip
        return data

    tenant.update_ig_radar_meta(ai)
    body = {"account": "@meme_one", "kind": "creator", "ages": ["20s", "30s", "20s"],
            "genders": ["female"], "topic": "  뷰티  패션 "}  # fmt: skip
    res = client.post(BASE + "/meta", json=body)
    assert res.status_code == 200, res.text
    row = res.json()["data"]["meta"]["meme_one"]
    assert row["kind"] == "creator" and row["ages"] == ["20s", "30s"]
    assert row["tag_source"] == "human" and row["followers"] == 120000
    assert row["topic"] == "뷰티 패션"


def test_bad_values_and_unknown_accounts_are_refused(client) -> None:
    res = client.post(BASE + "/meta", json={"account": "brand_a", "kind": "celebrity",
                                            "ages": [], "genders": []})  # fmt: skip
    assert res.status_code == 422
    res = client.post(BASE + "/meta", json={"account": "nobody", "kind": "brand",
                                            "ages": [], "genders": []})  # fmt: skip
    assert res.status_code == 404 and res.json()["error"]["code"] == "NOT_IN_RADAR"
