"""인스타 레이더: 회사 연결 화면에서 저장한 계정이 회사 설정 trends.toml보다 먼저다."""

import json
from pathlib import Path

import pytest

from ax_agent_template import tenant
from data_agent.skills import instagram as igs


@pytest.fixture
def saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "instagram_radar.json"
    monkeypatch.setenv(tenant.IG_RADAR_FILE_ENV, str(path))
    return path


def test_saved_accounts_win_over_the_trends_file(saved, tmp_path) -> None:
    trends = tmp_path / "trends.toml"
    trends.write_text(
        '[instagram]\nradar_accounts = ["from_file"]\nmedia_per_account = 7\n', "utf-8"
    )
    saved.write_text(json.dumps({"accounts": ["@from_screen", "two"]}), "utf-8")
    config = igs.load_config({"DATA_TREND_CONFIG": str(trends)})
    assert config.radar_accounts == ("from_screen", "two")
    assert config.media_per_account == 7  # 나머지 설정은 trends.toml 그대로


def test_saved_accounts_work_without_a_trends_file(saved) -> None:
    saved.write_text(json.dumps({"accounts": ["only_screen"]}), "utf-8")
    assert igs.load_config({}).radar_accounts == ("only_screen",)


def test_without_a_saved_file_the_trends_file_is_used(saved, tmp_path) -> None:
    trends = tmp_path / "trends.toml"
    trends.write_text('[instagram]\nradar_accounts = ["from_file"]\n', "utf-8")
    assert igs.load_config({"DATA_TREND_CONFIG": str(trends)}).radar_accounts == ("from_file",)
    assert igs.load_config({}).radar_accounts == ()
