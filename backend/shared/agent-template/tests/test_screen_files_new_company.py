"""설정 폴더 없이 가입만 한 회사의 화면 저장 자리 (11/25 실서버: 누비투어 c-cf2f0d).

- 저장 열쇠(`screen_key`): 설정 폴더 이름 → 없으면 정식 회사 id. 캣북스는 예전 그대로
- 인스타 레이더·유튜브 밈 키워드·레이더 자동 목록·메타는 다른 회사면 그 회사 폴더만 쓴다
  (회사 연결 파일 옆 옛 자리는 캣북스 것이라 다른 회사가 읽거나 덮지 않는다)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template import company_scope
from ax_agent_template import tenant as tenants
from ax_agent_template.companies import COMPANIES_DIR_ENV

NEW = "c-cf2f0d"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "company_connection.json"))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(tenants.TENANT_ENV, "catbooks")
    monkeypatch.delenv(tenants.TENANTS_DIR_ENV, raising=False)
    for name in (tenants.PROMO_FILE_ENV, tenants.IG_RADAR_FILE_ENV,
                 tenants.YT_MEME_KEYWORDS_FILE_ENV, tenants.IG_RADAR_AUTO_FILE_ENV,
                 tenants.IG_RADAR_META_FILE_ENV):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(COMPANIES_DIR_ENV, str(tmp_path / "companies"))
    return tmp_path


def test_screen_key_uses_company_id_without_tenant_folder(env: Path) -> None:
    assert tenants.screen_key() == "catbooks"  # 요청 밖·캣북스: 설정 폴더 이름 그대로
    with company_scope.using(NEW):
        assert tenants.current() is None
        assert tenants.screen_key() == NEW


def test_other_company_files_live_in_its_own_folder(env: Path) -> None:
    legacy = env / "instagram_radar.json"
    legacy.write_text('{"accounts": ["catbooks_rival"]}', encoding="utf-8")
    (env / "youtube_meme_keywords.json").write_text('{"keywords": ["책"]}', encoding="utf-8")
    assert tenants.ig_radar_file() == legacy  # 캣북스는 예전 자리
    assert tenants.ig_radar_accounts() == ["catbooks_rival"]
    with company_scope.using(NEW):
        own = env / "companies" / NEW
        assert tenants.ig_radar_file() == own / tenants.IG_RADAR_FILE
        assert tenants.yt_meme_keywords_file() == own / tenants.YT_MEME_KEYWORDS_FILE
        assert tenants.ig_radar_auto_file() == own / tenants.IG_RADAR_AUTO_FILE
        assert tenants.ig_radar_meta_file() == own / tenants.IG_RADAR_META_FILE
        assert tenants.ig_radar_accounts() is None  # 캣북스 목록이 안 보인다
        assert tenants.yt_meme_keywords() is None
    assert legacy.exists()  # 캣북스 옛 파일은 옮기지 않는다


def test_other_company_without_company_folders_cannot_save(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(COMPANIES_DIR_ENV, raising=False)
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)  # 회사 폴더 자리를 정할 곳이 없다
    with company_scope.using(NEW):
        assert tenants.ig_radar_file() is None
        assert tenants.yt_meme_keywords_file() is None
