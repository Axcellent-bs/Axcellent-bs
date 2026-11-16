"""hr.db → archive.db 이전 스크립트: 아카이브 행만 옮기고 원본은 두며, 다시 돌려도 안전하다."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from archive_agent.config import ArchiveSettings
from archive_agent.store import SCHEMA

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "migrate_archive_db.py"


def _load():
    spec = importlib.util.spec_from_file_location("migrate_archive_db", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def src(tmp_path: Path) -> Path:
    data = tmp_path / ".data"
    data.mkdir()
    p = data / "hr.db"
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    c.execute("CREATE TABLE org_changes (id TEXT)")  # 인사 전용 표
    now = "2026-11-16T00:00:00"
    c.execute("INSERT INTO mock_cards (card_id, data, created_at) VALUES ('c1','{}',?)", (now,))
    for kind in (
        "record_case.card_created",
        "dept_eval.judged",
        "org_change.confirmed",
        "start_handover",
    ):
        c.execute(
            "INSERT INTO audit_logs (context_id, kind, payload, created_at) "
            "VALUES ('x', ?, '{}', ?)",
            (kind, now),
        )
    for key in ("record_case:ctx1:a", "ctx1:record_event_times:b", "confirm_handover:h1:z"):
        c.execute(
            "INSERT INTO idempotency (key, tenant_id, result, created_at) "
            "VALUES (?, 'default', '{}', ?)",
            (key, now),
        )
    c.commit()
    c.close()
    return p


def _n(path: Path, table: str) -> int:
    c = sqlite3.connect(path)
    try:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        c.close()


def test_옮기는_것은_아카이브_행뿐이다(src: Path, tmp_path: Path) -> None:
    mod = _load()
    dst = src.with_name("archive.db")
    rep = mod.migrate(src, dst, backup_root=tmp_path)
    assert (_n(dst, "mock_cards"), _n(dst, "audit_logs"), _n(dst, "idempotency")) == (1, 2, 2)
    assert all(r["이전 후"] >= r["원본"] for r in rep.values())
    # 원본은 그대로, 백업이 생겼다
    assert (_n(src, "audit_logs"), _n(src, "idempotency")) == (4, 3)
    assert list(tmp_path.glob(".data_backup_*_archive_migrate/hr.db"))


def test_다시_돌려도_중복이_생기지_않는다(src: Path, tmp_path: Path) -> None:
    mod = _load()
    dst = src.with_name("archive.db")
    mod.migrate(src, dst, backup_root=tmp_path)
    mod.migrate(src, dst, backup_root=tmp_path)
    assert (_n(dst, "mock_cards"), _n(dst, "audit_logs"), _n(dst, "idempotency")) == (1, 2, 2)


def test_미리보기는_파일을_만들지_않는다(src: Path) -> None:
    rep = _load().migrate(src, src.with_name("archive.db"), dry_run=True)
    assert not src.with_name("archive.db").exists()
    assert rep["audit_logs"]["원본"] == 2


def test_벡터_파일_이름은_db_이름을_따른다(tmp_path: Path) -> None:
    from archive_agent.retrieval.cards import build_dense_search

    env = {
        "LLM_BASE_URL": "http://x",
        "LLM_API_KEY": "k",
        "EMBED_MODEL": "m",
        "MEMORY_EMBED_MODEL": "m",
    }
    d = build_dense_search(str(tmp_path / "archive.db"), env)
    if d is not None:  # 임베딩 설정을 읽을 때만 만들어진다
        assert "archive_vectors.db" in repr(vars(d._index))


def test_전용_파일_설정이_먼저다() -> None:
    s = ArchiveSettings.from_env({"ARCHIVE_DB_PATH": "a.db", "HR_DB_PATH": "h.db"})
    assert s.db_path == "a.db"
    assert ArchiveSettings.from_env({"HR_DB_PATH": "h.db"}).db_path == "h.db"
