"""인스타 레이더 계정 표: 계정 이름에 욕설·성인 표현이 있으면 "광고 근거 제외: 표현" (11/24).

단어 목록은 `ax_agent_template.ad_words` 한 곳이고, 회사 설정 `[instagram] blocked_words`로 더한다.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import ad_words, tenant
from ax_agent_template import company_connection as cc
from ax_dashboard_api.main import app

BASE = "/api/dashboard/instagram-radar"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.delenv(tenant.IG_RADAR_FILE_ENV, raising=False)
    trends = tmp_path / "trends.toml"
    trends.write_text('[instagram]\nradar_accounts = ["fuckjerry", "catbooks_a", "rude_page"]\n'
                      'blocked_words = ["Rude"]\n', "utf-8")  # fmt: skip
    monkeypatch.setenv("DATA_TREND_CONFIG", str(trends))
    return TestClient(app)


def test_blocked_accounts_get_the_mark(client: TestClient) -> None:
    data = client.get(BASE).json()["data"]
    assert data["accounts"] == ["fuckjerry", "catbooks_a", "rude_page"]  # 목록에서는 빼지 않는다
    assert data["ad_excluded"] == {"fuckjerry": ad_words.MARK, "rude_page": ad_words.MARK}
