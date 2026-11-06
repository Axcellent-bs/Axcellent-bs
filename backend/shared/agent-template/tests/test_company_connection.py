"""회사 연결 파일: 화면에서 정한 판매 서버 주소가 `agent_url("shop-api")`에 재시작 없이 반영된다."""

import json
import os
from pathlib import Path

import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template.config import agent_url


@pytest.fixture
def conn_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "company_connection.json"
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.delenv("AX_SHOP_API_URL", raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    return path


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    # 같은 크기로 다시 쓸 때도 바뀐 시각이 달라야 캐시가 풀린다
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://192.168.0.251:8080/shop-api/", "http://192.168.0.251:8080/shop-api"),
        ("  https://shop.example.com  ", "https://shop.example.com"),
    ],
)
def test_valid_urls_are_trimmed(raw: str, expected: str) -> None:
    assert cc.validate_shop_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://shop.example.com",
        "shop.example.com",
        "http://user:pw@shop.example.com",
        "http://shop.example.com/?x=1",
        "http://shop.example.com/#a",
        "http://shop example.com",
        "http://shop.example.com:99999",
        "http://",
    ],
)
def test_invalid_urls_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        cc.validate_shop_url(raw)


def test_without_a_file_shop_api_keeps_the_env_and_default(conn_file: Path, monkeypatch) -> None:
    assert cc.read_connection() is None
    assert agent_url("shop-api") == "http://shop-api:8010"
    monkeypatch.setenv("AX_SHOP_API_URL", "http://127.0.0.1:8010")
    assert agent_url("shop-api") == "http://127.0.0.1:8010"


def test_the_file_wins_over_the_env_and_follows_changes(conn_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("AX_SHOP_API_URL", "http://127.0.0.1:8010")
    _write(conn_file, {"shop_api_url": "http://192.168.0.251:8080/shop-api"})
    assert agent_url("shop-api") == "http://192.168.0.251:8080/shop-api"
    _write(conn_file, {"shop_api_url": "http://192.168.0.252:8080/shop-api"})
    assert agent_url("shop-api") == "http://192.168.0.252:8080/shop-api"
    conn_file.unlink()
    assert agent_url("shop-api") == "http://127.0.0.1:8010"


def test_other_targets_ignore_the_connection(conn_file: Path) -> None:
    _write(conn_file, {"shop_api_url": "http://192.168.0.251:8080/shop-api"})
    assert agent_url("support") == "http://support:8102"


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps([1]),
        json.dumps({"shop_api_url": 3}),
        json.dumps({"shop_api_url": "ftp://x"}),
    ],
)
def test_a_broken_file_is_ignored(conn_file: Path, content: str, caplog) -> None:
    conn_file.write_text(content, encoding="utf-8")
    assert cc.read_connection() is None
    assert agent_url("shop-api") == "http://shop-api:8010"
    assert "회사 연결 파일이 깨져" in caplog.text


def test_save_and_clear_round_trip(conn_file: Path) -> None:
    saved = cc.save_connection(
        "http://192.168.0.251:8080/shop-api/",
        verified_at="2026-11-06T05:00:00+00:00",
        updated_by="yejin",
    )
    assert saved.shop_api_url == "http://192.168.0.251:8080/shop-api"
    stored = json.loads(conn_file.read_text(encoding="utf-8"))
    assert stored["updated_by"] == "yejin" and stored["verified_at"].startswith("2026-11-06")
    assert cc.shop_api_override() == "http://192.168.0.251:8080/shop-api"
    assert not (conn_file.parent / "company_connection.json.tmp").exists()
    assert cc.clear_connection() is True
    assert cc.clear_connection() is False
    assert cc.shop_api_override() is None


def test_without_the_env_the_feature_is_off(monkeypatch) -> None:
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)
    assert cc.read_connection() is None
    assert cc.clear_connection() is False
    with pytest.raises(RuntimeError):
        cc.save_connection("http://x.example.com", verified_at="t", updated_by="y")
