"""켠 서비스를 파일(`AX_ENABLED_SERVICES_FILE`)에서 읽기: 있음·없음·깨짐, 재시작 없이 반영."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from ax_agent_template import routing


@pytest.fixture(autouse=True)
def _clean(monkeypatch):  # noqa: ANN001, ANN202
    monkeypatch.delenv(routing.ENABLED_ENV, raising=False)
    monkeypatch.delenv(routing.ENABLED_FILE_ENV, raising=False)
    monkeypatch.setattr(routing, "_enabled_cache", None)


def _write(path: Path, enabled, bump: int = 0) -> None:  # noqa: ANN001
    path.write_text(
        json.dumps(
            {"enabled": enabled, "updated_at": "2026-10-30T09:00:00Z", "updated_by": "test"}
        ),
        encoding="utf-8",
    )
    if bump:  # 같은 초 안에 다시 써도 바뀐 시각이 달라지게
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + bump * 1_000_000_000))


def test_file_wins_over_the_env(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "enabled_services.json"
    _write(path, ["data", "finance"])
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    assert routing.enabled_services() == frozenset({"data", "finance"})
    assert routing.is_enabled("data") and not routing.is_enabled("inquiry")
    assert routing.is_enabled("safety")  # 서비스 13개가 아닌 대상은 늘 켜져 있다


def test_without_the_file_env_the_env_list_is_used(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry,coupon")
    assert routing.enabled_services() == frozenset({"inquiry", "coupon"})
    monkeypatch.delenv(routing.ENABLED_ENV)
    assert routing.enabled_services() is None  # 지금처럼: 비어 있으면 전부 켬


def test_an_empty_file_list_turns_every_service_off(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "enabled_services.json"
    _write(path, [])
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    assert routing.enabled_services() == frozenset()
    assert not routing.is_enabled("data")


def test_a_missing_file_falls_back_to_the_env_with_a_warning(monkeypatch, tmp_path, caplog) -> None:  # noqa: ANN001
    monkeypatch.setenv(routing.ENABLED_ENV, "data")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(tmp_path / "없음.json"))
    with caplog.at_level(logging.WARNING, logger="ax.routing"):
        assert routing.enabled_services() == frozenset({"data"})
        assert routing.enabled_services() == frozenset({"data"})
    warnings = [r for r in caplog.records if "읽을 수 없어" in r.getMessage()]
    assert len(warnings) == 1  # 같은 상태면 한 번만


@pytest.mark.parametrize(
    "content",
    ["{깨진 json", '["data"]', '{"enabled": "data"}', '{"enabled": [1, 2]}', '{"updated_by": "x"}'],
    ids=["bad-json", "not-object", "not-list", "not-strings", "no-enabled"],
)
def test_a_broken_file_uses_the_env_not_everything(monkeypatch, tmp_path, caplog, content) -> None:  # noqa: ANN001
    path = tmp_path / "enabled_services.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv(routing.ENABLED_ENV, "data,finance")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    with caplog.at_level(logging.WARNING, logger="ax.routing"):
        assert routing.enabled_services() == frozenset({"data", "finance"})
        assert not routing.is_enabled("inquiry")  # 전부 켬으로 바뀌지 않는다
    assert any("깨져" in r.getMessage() for r in caplog.records)


def test_a_broken_file_with_no_env_keeps_the_old_default(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "enabled_services.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    assert routing.enabled_services() is None  # 환경변수도 비어 있으면 예전 기본값(전부 켬)


def test_file_changes_apply_without_restart(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "enabled_services.json"
    _write(path, ["data"])
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    assert routing.enabled_services() == frozenset({"data"})

    _write(path, ["data", "finance", "inquiry"], bump=1)
    assert routing.enabled_services() == frozenset({"data", "finance", "inquiry"})
    assert routing.is_enabled("inquiry")

    path.write_text("{깨짐", encoding="utf-8")  # 깨지면 환경변수(여기선 비어 있음 = 전부 켬)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert routing.enabled_services() is None

    _write(path, ["coupon"], bump=3)  # 고쳐지면 다시 파일 값
    assert routing.enabled_services() == frozenset({"coupon"})
    path.unlink()  # 지워지면 환경변수
    assert routing.enabled_services() is None


def test_unchanged_file_is_not_read_again(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "enabled_services.json"
    _write(path, ["data"])
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(path))
    assert routing.enabled_services() == frozenset({"data"})
    reads = []
    original = Path.read_text
    monkeypatch.setattr(
        Path, "read_text", lambda self, *a, **k: reads.append(self) or original(self, *a, **k)
    )
    for _ in range(5):
        assert routing.enabled_services() == frozenset({"data"})
    assert reads == []
