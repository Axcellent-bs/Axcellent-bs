"""공통층 파일을 회사 폴더로 옮기기 (여러 회사 설계 1-4).

- 회사 폴더 켜기: `AX_COMPANIES_DIR`을 정했을 때만(run_all.sh가 정한다). 안 정했으면 지금 그대로
- 파일마다 경로 환경변수(`AX_TOOL_CONNECTIONS_FILE` 등)를 정했으면 그 경로를 그대로 쓴다
- 옛 경로에 파일이 있으면 처음 읽을 때 캣북스 폴더로 한 번 옮기고, 옛 파일은 `.moved`로 남긴다
- 옮기다 실패하면 옛 파일을 그대로 두고 옛 경로로 읽는다
- 되돌리기: `AX_COMPANY_FOLDERS=off`만 넣고 다시 켜면 회사 폴더 값으로 옛 경로를 되살린다
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files, routing
from ax_agent_template import company_connection as cc
from ax_agent_template import tool_connections as tc


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """run_all.sh 모양: `.data/company_connection.json`(설치 기준 자리) + `.data/companies/`."""
    root = tmp_path / ".data"
    root.mkdir()
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(root / "company_connection.json"))
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    monkeypatch.delenv(company_files.FOLDERS_ENV, raising=False)
    monkeypatch.delenv(tc.FILE_ENV, raising=False)
    monkeypatch.delenv(routing.ENABLED_FILE_ENV, raising=False)
    monkeypatch.delenv(routing.ENABLED_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setattr(tc, "_cache", None)
    monkeypatch.setattr(routing, "_enabled_cache", None)
    company_files.reset_for_tests()
    return root


def _catbooks(root: Path, name: str) -> Path:
    return root / "companies" / "catbooks" / name


CONNECTION = {
    "shop_api_url": "http://127.0.0.1:8090",
    "enabled": True,
    "verified_at": None,
    "updated_at": "2026-11-19T09:00:00+00:00",
    "updated_by": "admin",
}


def _write_legacy_connection(root: Path) -> tuple[Path, Path]:
    legacy = root / "company_connection.json"
    legacy.write_text(json.dumps(CONNECTION), encoding="utf-8")
    history = root / "company_connection.json.history.jsonl"
    history.write_text('{"action": "save"}\n', encoding="utf-8")
    return legacy, history


# ---- 꺼짐: 지금 그대로 ----


def test_without_companies_dir_everything_stays(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    conn = tmp_path / "company_connection.json"
    conn.write_text(json.dumps(CONNECTION), encoding="utf-8")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(conn))
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV, raising=False)
    monkeypatch.delenv(tc.FILE_ENV, raising=False)
    monkeypatch.delenv(routing.ENABLED_FILE_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    company_files.reset_for_tests()
    assert not company_files.folders_on()
    assert cc.connection_path() == str(conn)
    assert cc.read_connection() is not None
    assert tc.connections_file() == ""
    assert conn.exists() and not (tmp_path / "companies").exists()


def test_off_switch_keeps_old_paths(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy, _ = _write_legacy_connection(data)
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    assert not company_files.folders_on()
    assert cc.connection_path() == str(legacy)
    assert cc.read_connection() is not None
    assert legacy.exists() and not _catbooks(data, "company_connection.json").exists()


# ---- 회사 연결 ----


def test_connection_moves_once_to_catbooks(data: Path, caplog) -> None:  # noqa: ANN001
    legacy, history = _write_legacy_connection(data)
    caplog.set_level(logging.INFO, logger="ax.company_files")
    found = cc.read_connection()
    assert found is not None and found.shop_api_url == CONNECTION["shop_api_url"]
    new = _catbooks(data, "company_connection.json")
    assert cc.connection_path() == str(new)
    assert json.loads(new.read_text(encoding="utf-8")) == CONNECTION
    assert (new.parent / "company_connection.json.history.jsonl").read_text(
        encoding="utf-8"
    ) == '{"action": "save"}\n'
    # 옛 파일은 지우지 않고 .moved로 둔다
    assert not legacy.exists() and not history.exists()
    assert json.loads((data / "company_connection.json.moved").read_text(encoding="utf-8")) == (
        CONNECTION
    )
    assert (data / "company_connection.json.history.jsonl.moved").exists()
    moved_lines = [r for r in caplog.records if "회사 폴더로 옮겼다" in r.getMessage()]
    assert len(moved_lines) == 1
    # 다시 읽어도 또 옮기지 않는다
    cc._cache = None
    assert cc.read_connection() is not None
    assert len([r for r in caplog.records if "회사 폴더로 옮겼다" in r.getMessage()]) == 1


def test_anchor_stays_for_files_beside_it(data: Path) -> None:
    """계정·연결 키·설치 기록은 회사 밖(전역)이라 연결 파일 자리 옆 그대로다."""
    _write_legacy_connection(data)
    cc.read_connection()
    assert cc.connection_file() == str(data / "company_connection.json")


def test_no_legacy_file_means_new_path_and_nothing_made(data: Path) -> None:
    assert cc.read_connection() is None
    assert cc.connection_path() == str(_catbooks(data, "company_connection.json"))
    assert not (data / "companies").exists()


def test_save_writes_into_company_folder(data: Path) -> None:
    cc.save_connection("http://127.0.0.1:8090", verified_at=None, updated_by="admin")
    new = _catbooks(data, "company_connection.json")
    assert new.exists() and not (data / "company_connection.json").exists()
    assert (new.parent / "company_connection.json.history.jsonl").exists()
    assert cc.read_history()[0]["action"] == "save"


def test_already_moved_leaves_both_alone(data: Path) -> None:
    new = _catbooks(data, "company_connection.json")
    new.parent.mkdir(parents=True)
    new.write_text(json.dumps({**CONNECTION, "shop_api_url": "http://127.0.0.1:9000"}), "utf-8")
    legacy, _ = _write_legacy_connection(data)
    found = cc.read_connection()
    assert found is not None and found.shop_api_url == "http://127.0.0.1:9000"
    assert legacy.exists()  # 손대지 않는다


def test_failure_keeps_old_file_and_reads_old_path(data: Path, monkeypatch, caplog) -> None:  # noqa: ANN001
    legacy, history = _write_legacy_connection(data)

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise OSError("disk full")

    monkeypatch.setattr(company_files.shutil, "copy2", boom)
    caplog.set_level(logging.WARNING, logger="ax.company_files")
    found = cc.read_connection()
    assert found is not None and found.shop_api_url == CONNECTION["shop_api_url"]
    assert cc.connection_path() == str(legacy)
    assert legacy.exists() and history.exists()
    assert not (data / "company_connection.json.moved").exists()
    assert not _catbooks(data, "company_connection.json").exists()
    leftovers = list(_catbooks(data, "").glob("*")) if _catbooks(data, "").exists() else []
    assert [p.name for p in leftovers if not p.name.startswith(".")] == []
    assert any("옮기지 못했다" in r.getMessage() for r in caplog.records)


def test_existing_moved_backup_is_not_overwritten(data: Path) -> None:
    old_backup = data / "company_connection.json.moved"
    old_backup.write_text("earlier", encoding="utf-8")
    _write_legacy_connection(data)
    cc.read_connection()
    assert old_backup.read_text(encoding="utf-8") == "earlier"
    extra = [p for p in data.glob("company_connection.json.moved.*") if "history" not in p.name]
    assert len(extra) == 1
    assert json.loads(extra[0].read_text(encoding="utf-8")) == CONNECTION


def test_rollback_restores_old_paths(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy, history = _write_legacy_connection(data)
    cc.read_connection()
    # 사람이 손으로 옛 파일을 되돌려 둔 경우: off면 그 옛 파일을 그대로 읽는다(덮어쓰지 않음)
    shutil.rmtree(data / "companies" / "catbooks")
    (data / "company_connection.json.moved").rename(legacy)
    (data / "company_connection.json.history.jsonl.moved").rename(history)
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    cc._cache = None
    found = cc.read_connection()
    assert found is not None and cc.connection_path() == str(legacy)
    assert legacy.exists() and not (data / "companies" / "catbooks").exists()


# ---- 여러 프로세스(여기서는 스레드)가 같이 켜질 때 ----


def test_concurrent_first_reads_move_exactly_once(data: Path, caplog) -> None:  # noqa: ANN001
    _write_legacy_connection(data)
    caplog.set_level(logging.INFO, logger="ax.company_files")
    seen: list[str] = []
    barrier = threading.Barrier(8)

    def run() -> None:
        barrier.wait()
        seen.append(cc.connection_path())

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    new = str(_catbooks(data, "company_connection.json"))
    assert seen == [new] * 8
    assert len([r for r in caplog.records if "회사 폴더로 옮겼다" in r.getMessage()]) == 1
    assert json.loads(Path(new).read_text(encoding="utf-8")) == CONNECTION


def test_lock_held_elsewhere_reads_old_path(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy, _ = _write_legacy_connection(data)
    lock = data / "companies" / "catbooks" / company_files.LOCK_NAME
    lock.parent.mkdir(parents=True)
    lock.write_text("other", encoding="utf-8")
    monkeypatch.setattr(company_files, "LOCK_TIMEOUT", 0.05)
    assert cc.connection_path() == str(legacy)
    assert legacy.exists()
    # 잠금이 풀리면 다음 읽기에서 옮긴다
    lock.unlink()
    assert cc.connection_path() == str(_catbooks(data, "company_connection.json"))


def test_stale_lock_is_broken(data: Path, monkeypatch) -> None:  # noqa: ANN001
    _write_legacy_connection(data)
    lock = data / "companies" / "catbooks" / company_files.LOCK_NAME
    lock.parent.mkdir(parents=True)
    lock.write_text("dead", encoding="utf-8")
    monkeypatch.setattr(company_files, "LOCK_STALE", 0.0)
    assert cc.connection_path() == str(_catbooks(data, "company_connection.json"))
    assert not lock.exists()


# ---- 도구 연결 ----


def test_tool_connections_move_with_history(data: Path) -> None:
    legacy = data / "tool_connections.json"
    legacy.write_text("[]", encoding="utf-8")
    (data / "tool_connections.json.history.jsonl").write_text("{}\n", encoding="utf-8")
    new = _catbooks(data, "tool_connections.json")
    assert tc.connections_file() == str(new)
    assert new.exists() and (new.parent / "tool_connections.json.history.jsonl").exists()
    assert (data / "tool_connections.json.moved").exists() and not legacy.exists()


def test_tool_connections_env_is_respected(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "pinned" / "tools.json"
    monkeypatch.setenv(tc.FILE_ENV, str(pinned))
    (data / "tool_connections.json").write_text("{}", encoding="utf-8")
    assert tc.connections_file() == str(pinned)
    assert (data / "tool_connections.json").exists()  # 환경변수를 정했으면 옮기지 않는다


def test_tool_connections_save_into_company_folder(data: Path) -> None:
    tc.upsert(
        "jenkins",
        "빌드",
        {"url": "http://ci.local"},
        {"token": "t" * 20},
        conn_id=None,
        by="tester",
    )
    assert _catbooks(data, "tool_connections.json").exists()
    assert [c.id for c in tc.read_all()] == ["jenkins-1"]


# ---- 켠 서비스 ----


def test_enabled_services_move_and_still_apply(data: Path) -> None:
    legacy = data / "enabled_services.json"
    legacy.write_text(json.dumps({"enabled": ["data"]}), encoding="utf-8")
    assert routing.enabled_services() == frozenset({"data"})
    assert _catbooks(data, "enabled_services.json").exists()
    assert (data / "enabled_services.json.moved").exists() and not legacy.exists()


def test_enabled_services_env_is_respected(data: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    pinned = tmp_path / "enabled.json"
    pinned.write_text(json.dumps({"enabled": ["finance"]}), encoding="utf-8")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(pinned))
    assert routing.enabled_services() == frozenset({"finance"})
    assert not (data / "companies").exists()


def test_enabled_services_no_file_falls_back_to_env(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    assert routing.enabled_services() == frozenset({"inquiry"})


# ---- 폴더 옮기기 (변환기 폴더처럼) ----


def test_folder_moves_whole(data: Path) -> None:
    legacy = data / "site_adapter"
    legacy.mkdir()
    (legacy / "mapping.json").write_text("{}", encoding="utf-8")
    (legacy / "bookkeeping.jsonl").write_text("x\n", encoding="utf-8")
    got = company_files.company_file("site_adapter")
    assert got == _catbooks(data, "site_adapter")
    assert sorted(p.name for p in got.iterdir()) == ["bookkeeping.jsonl", "mapping.json"]
    assert (data / "site_adapter.moved" / "mapping.json").exists() and not legacy.exists()


# ---- 되돌리기: off만 넣고 다시 켜면 회사 폴더 값으로 옛 경로를 되살린다 (리뷰 #1537) ----


def _migrated_then_changed(data: Path) -> tuple[Path, Path]:
    """옮긴 뒤 화면에서 주소를 바꾼 상태 (옛 자리는 .moved뿐)."""
    legacy, history = _write_legacy_connection(data)
    cc.read_connection()
    cc.save_connection("http://127.0.0.1:9100", verified_at=None, updated_by="admin")
    assert not legacy.exists()
    return legacy, history


def test_off_restores_connection_from_company_folder(data: Path, monkeypatch, caplog) -> None:  # noqa: ANN001
    legacy, history = _migrated_then_changed(data)
    new = _catbooks(data, "company_connection.json")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    caplog.set_level(logging.INFO, logger="ax.company_files")
    cc._cache = None
    found = cc.read_connection()
    # 옮긴 뒤 바꾼 값(9100)이 살아 있다. .moved의 옛 값(8090)으로 돌아가지 않는다
    assert found is not None and found.shop_api_url == "http://127.0.0.1:9100"
    assert cc.connection_path() == str(legacy)
    assert json.loads(legacy.read_text(encoding="utf-8"))["shop_api_url"] == "http://127.0.0.1:9100"
    assert [r["action"] for r in cc.read_history()][:2] == ["save", "save"]
    assert history.exists()
    assert new.exists()  # 회사 폴더 파일은 지우지 않는다
    assert (data / "company_connection.json.moved").exists()
    restored = [r for r in caplog.records if "옛 경로를 되살렸다" in r.getMessage()]
    assert len(restored) == 1
    cc._cache = None
    cc.read_connection()
    assert len([r for r in caplog.records if "옛 경로를 되살렸다" in r.getMessage()]) == 1


def test_off_restore_does_not_overwrite_existing_old_file(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy, _ = _migrated_then_changed(data)
    legacy.write_text(json.dumps({**CONNECTION, "shop_api_url": "http://127.0.0.1:7000"}), "utf-8")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    cc._cache = None
    found = cc.read_connection()
    assert found is not None and found.shop_api_url == "http://127.0.0.1:7000"


def test_off_restore_failure_keeps_reading_old_path(data: Path, monkeypatch, caplog) -> None:  # noqa: ANN001
    legacy, _ = _migrated_then_changed(data)
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")

    def boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise OSError("disk full")

    monkeypatch.setattr(company_files.shutil, "copy2", boom)
    caplog.set_level(logging.WARNING, logger="ax.company_files")
    assert cc.connection_path() == str(legacy)
    assert not legacy.exists()
    assert [p.name for p in data.iterdir() if p.name.endswith(".tmp")] == []
    assert any("되살리지 못했다" in r.getMessage() for r in caplog.records)


def test_off_restores_tool_connections_into_old_default(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy = data / "tool_connections.json"
    legacy.write_text("[]", encoding="utf-8")
    tc.connections_file()  # 옮긴다
    tc._cache = None
    tc.upsert(
        "jenkins",
        "빌드",
        {"url": "http://ci.local"},
        {"token": "t" * 20},
        conn_id=None,
        by="tester",
    )
    # run_all.sh는 off일 때 예전 기본 경로를 정한다
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    monkeypatch.setenv(tc.FILE_ENV, str(legacy))
    tc._cache = None
    assert [c.id for c in tc.read_all()] == ["jenkins-1"]
    assert legacy.exists() and _catbooks(data, "tool_connections.json").exists()


def test_off_restores_enabled_services(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy = data / "enabled_services.json"
    legacy.write_text(json.dumps({"enabled": ["data"]}), encoding="utf-8")
    routing.enabled_services()  # 옮긴다
    _catbooks(data, "enabled_services.json").write_text(
        json.dumps({"enabled": ["finance"]}), encoding="utf-8"
    )
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(legacy))
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    routing._enabled_cache = None
    assert routing.enabled_services() == frozenset({"finance"})


def test_off_restores_folder(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy = data / "site_adapter"
    legacy.mkdir()
    (legacy / "mapping.json").write_text("{}", encoding="utf-8")
    company_files.company_file("site_adapter")
    (_catbooks(data, "site_adapter") / "token").write_text("t", encoding="utf-8")
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    assert company_files.restore_old("site_adapter", target=legacy)
    assert sorted(p.name for p in legacy.iterdir()) == ["mapping.json", "token"]
    assert _catbooks(data, "site_adapter").exists()


def test_restore_needs_off_switch(data: Path) -> None:
    legacy, _ = _migrated_then_changed(data)
    assert not company_files.restore_old("company_connection.json", target=legacy)
    assert not legacy.exists()


# ---- 없는 파일은 건너뛴다: 빈 파일·빈 폴더를 만들지 않는다 ----


def test_missing_extras_are_skipped_on_move(data: Path) -> None:
    legacy = data / "company_connection.json"
    legacy.write_text(json.dumps(CONNECTION), encoding="utf-8")  # 이력 파일 없음
    cc.read_connection()
    folder = data / "companies" / "catbooks"
    assert sorted(p.name for p in folder.iterdir() if p.name != company_files.LOCK_NAME) == [
        "company_connection.json"
    ]
    assert not (data / "company_connection.json.history.jsonl.moved").exists()
    assert sorted(p.name for p in data.iterdir()) == [
        "companies",
        "company_connection.json.moved",
    ]


def test_missing_extras_are_skipped_on_restore(data: Path, monkeypatch) -> None:  # noqa: ANN001
    legacy = data / "company_connection.json"
    legacy.write_text(json.dumps(CONNECTION), encoding="utf-8")
    cc.read_connection()
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    cc._cache = None
    cc.read_connection()
    assert legacy.exists()
    assert not (data / "company_connection.json.history.jsonl").exists()


def test_off_with_nothing_anywhere_makes_nothing(data: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    monkeypatch.setenv(tc.FILE_ENV, str(data / "tool_connections.json"))
    assert cc.read_connection() is None
    assert tc.read_all() == []
    assert sorted(p.name for p in data.iterdir()) == []


@pytest.mark.skipif(__import__("os").name == "nt", reason="윈도는 권한 비트가 없다")
def test_company_folder_is_private(data: Path) -> None:
    _write_legacy_connection(data)
    cc.read_connection()
    for folder in (data / "companies", data / "companies" / "catbooks"):
        assert folder.stat().st_mode & 0o777 == 0o700
