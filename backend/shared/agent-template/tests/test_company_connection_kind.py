"""회사 연결 종류(kind): adapter면 AI들의 shop-api 주소가 dashboard-api 사이트 변환기로 간다."""

import json
import os
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template.config import agent_url

SITE = "http://10.0.0.9:3000"
ADAPTER = {"kind": "adapter", "adapter": {"base_url": SITE}}


@pytest.fixture
def conn_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "company_connection.json"
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    monkeypatch.setattr(cc, "_cache", None)
    return path


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_old_file_without_kind_is_shop_api(conn_file: Path) -> None:
    _write(conn_file, {"shop_api_url": "http://10.0.0.5:8010", "enabled": True})
    found = cc.read_connection()
    assert found is not None and found.kind == "shop_api" and found.adapter is None
    assert cc.shop_api_override() == "http://10.0.0.5:8010"


def test_adapter_kind_points_to_dashboard_site_adapter(conn_file: Path) -> None:
    _write(
        conn_file,
        {"shop_api_url": SITE, "enabled": True, **ADAPTER},
    )
    assert cc.shop_api_override() == "http://dash:8020/site-adapter"
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"
    found = cc.read_connection()
    assert found is not None and found.adapter == {"base_url": SITE}
    assert found.as_dict()["kind"] == "adapter"


def test_adapter_disabled_uses_no_override(conn_file: Path) -> None:
    _write(
        conn_file,
        {"shop_api_url": SITE, "enabled": False, **ADAPTER},
    )
    assert cc.shop_api_override() is None


def test_unknown_kind_is_rejected(conn_file: Path) -> None:
    _write(conn_file, {"shop_api_url": SITE, "kind": "db"})
    assert cc.read_connection() is None


def test_adapter_without_base_url_is_rejected(conn_file: Path) -> None:
    _write(conn_file, {"shop_api_url": SITE, "kind": "adapter"})
    assert cc.read_connection() is None


def test_save_adapter_roundtrip(conn_file: Path) -> None:
    saved = cc.save_connection(
        SITE,
        verified_at=None,
        updated_by="tester",
        kind="adapter",
        adapter={"base_url": SITE + "/"},
    )
    assert saved.kind == "adapter" and saved.adapter == {"base_url": SITE}
    assert cc.read_connection() == saved


def test_save_adapter_checks_host(conn_file: Path) -> None:
    with pytest.raises(ValueError):
        cc.save_connection(
            "http://169.254.169.254",
            verified_at=None,
            updated_by="tester",
            kind="adapter",
            adapter={"base_url": "http://169.254.169.254"},
        )
