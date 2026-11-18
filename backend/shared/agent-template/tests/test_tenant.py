"""지금 회사: 회사 연결에서 고른 회사(`tenant`)를 따른다.

연결 전에는 어느 회사도 아니다(캣북스 기본값 없음).
"""

import json
import os
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template import tenant
from ax_agent_template.config import agent_url

TOML = """name = "냉장 물류"
catalog = "coldchain"
connector = "shop-api"
org = "org.yaml"
trends = "trends.toml"
timezone = "Asia/Seoul"
[finance]
per_head_monthly = 50000
"""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "tenants"
    (root / "cold").mkdir(parents=True)
    (root / "cold" / "tenant.toml").write_text(TOML, encoding="utf-8")
    (root / "cold" / "org.yaml").write_text("areas: []\n", encoding="utf-8")
    (root / "cold" / "trends.toml").write_text("", encoding="utf-8")
    (root / "empty").mkdir()  # tenant.toml 없는 폴더는 회사가 아니다
    monkeypatch.setenv(tenant.TENANTS_DIR_ENV, str(root))
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(tmp_path / "conn.json"))
    for name in (tenant.TENANT_ENV, "AX_SHOP_API_URL", *tenant.DERIVED):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.setattr(cc, "_cache", None)
    return root


def _connect(path: Path, **extra: object) -> None:
    file = Path(os.environ[cc.CONNECTION_FILE_ENV])
    file.write_text(json.dumps({"shop_api_url": "http://10.0.0.5:8010", **extra}), encoding="utf-8")
    stat = file.stat()
    os.utime(file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_without_a_connection_there_is_no_company(world: Path) -> None:
    assert tenant.current() is None
    assert tenant.setting("AX_ORG_CONFIG") == ""
    assert tenant.setting("DATA_TENANT_CATALOG") == ""


def test_companies_are_folders_with_a_tenant_file(world: Path) -> None:
    assert tenant.list_tenants() == [{"id": "cold", "name": "냉장 물류"}]


def test_the_connected_company_gives_its_settings(world: Path) -> None:
    _connect(world, tenant="cold")
    assert tenant.current() == "cold"
    folder = (world / "cold").resolve()
    assert tenant.setting("AX_ORG_CONFIG") == (folder / "org.yaml").as_posix()
    assert tenant.setting("DATA_TENANT_CATALOG") == "coldchain"
    assert tenant.setting("DATA_TREND_CONFIG") == (folder / "trends.toml").as_posix()
    assert tenant.setting("DATA_YOUTUBE_CONFIG") == ""  # 회사 폴더에 없음
    assert tenant.setting("DATA_TENANT_TIMEZONE") == "Asia/Seoul"
    assert tenant.setting("DATA_TENANT_CONFIG") == (folder / "tenant.toml").as_posix()
    assert tenant.setting("FINANCE_PER_HEAD_MONTHLY") == "50000"


def test_an_env_value_still_wins(world: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(world, tenant="cold")
    monkeypatch.setenv("AX_ORG_CONFIG", "/x/org.yaml")
    assert tenant.setting("AX_ORG_CONFIG") == "/x/org.yaml"


def test_ax_tenant_picks_a_company_without_a_connection(
    world: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tenant.TENANT_ENV, "cold")
    assert tenant.current() == "cold"


def test_an_unknown_company_counts_as_none(world: Path) -> None:
    _connect(world, tenant="nope")
    assert tenant.current() is None
    assert tenant.setting("AX_ORG_CONFIG") == ""


def test_save_keeps_the_company(world: Path) -> None:
    saved = cc.save_connection(
        "http://10.0.0.5:8010", verified_at=None, updated_by="t", tenant="cold"
    )
    assert saved.tenant == "cold" and saved.as_dict()["tenant"] == "cold"
    assert cc.read_connection() == saved


def test_save_refuses_an_unknown_company(world: Path) -> None:
    with pytest.raises(ValueError):
        cc.save_connection("http://10.0.0.5:8010", verified_at=None, updated_by="t", tenant="x")


def test_shop_api_before_connecting_goes_to_the_not_connected_answer(world: Path) -> None:
    # 연결 파일 기능이 켜져 있고 연결·주소가 없으면 캣북스 판매 서버로 빠지지 않는다.
    # 사이트 변환기가 503 NOT_CONFIGURED(연결 안 됨)로 답한다
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"


def test_a_disabled_connection_is_also_not_connected(world: Path) -> None:
    _connect(world, enabled=False)
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"


def test_an_installed_copy_finds_the_working_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """도커 이미지는 패키지를 site-packages에 깐다(--no-editable).

    그때는 작업 폴더의 config/tenants를 본다.
    """
    (tmp_path / "config" / "tenants" / "cold").mkdir(parents=True)
    (tmp_path / "config" / "tenants" / "cold" / "tenant.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.delenv(tenant.TENANTS_DIR_ENV, raising=False)
    monkeypatch.setattr(tenant, "DEFAULT_TENANTS_DIR", tmp_path / "site-packages" / "nope")
    monkeypatch.chdir(tmp_path)
    assert tenant.list_tenants() == [{"id": "cold", "name": "냉장 물류"}]
