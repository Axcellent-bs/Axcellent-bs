"""출처 현황의 수집기 상태(`collectors`): 돌고 있는지, 마지막 수집 시각, 오래되면 "수집 멈춤"."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_agent import collector_lock
from data_agent.skills.data_sources import collector_states

NOW = datetime(2026, 11, 5, 3, tzinfo=UTC)


def _env(tmp_path: Path, last: datetime | None, hours: str | None = None) -> dict[str, str]:
    yt, tr = tmp_path / "youtube.db", tmp_path / "trending.db"
    for path, table in ((yt, "snapshots"), (tr, "trending")):
        conn = sqlite3.connect(path)
        conn.execute(f"CREATE TABLE {table} (collected_at TEXT)")
        if last is not None:
            conn.execute(f"INSERT INTO {table} VALUES (?)", (last.isoformat(),))  # noqa: S608
        conn.commit()
        conn.close()
    env = {"DATA_YOUTUBE_DB": str(yt), "DATA_TRENDING_DB": str(tr)}
    if hours:
        env["DATA_COLLECTOR_MAX_AGE_HOURS"] = hours
    return env


def test_running_and_fresh_has_no_warning(tmp_path: Path) -> None:
    env = _env(tmp_path, NOW - timedelta(minutes=30))
    locks = [collector_lock.acquire(tmp_path / n, "t", lambda _: None)
             for n in ("youtube_collect.lock", "trending_collect.lock")]  # fmt: skip
    try:
        states = collector_states(env, NOW)
    finally:
        for lock in locks:
            assert lock is not None
            lock.close()
    assert [s.collector_id for s in states] == ["youtube_collect", "trending_collect"]
    assert all(s.running and not s.stalled and s.warning is None for s in states)
    assert states[0].last_collected_at == NOW - timedelta(minutes=30) and states[0].pid


def test_not_running_or_old_snapshot_is_stalled(tmp_path: Path) -> None:
    env = _env(tmp_path, NOW - timedelta(hours=3))
    off = collector_states(env, NOW)
    assert all(s.stalled and "돌고 있지 않음" in (s.warning or "") for s in off)
    lock = collector_lock.acquire(tmp_path / "youtube_collect.lock", "t", lambda _: None)
    try:
        old = collector_states(env, NOW)[0]
        assert old.running and old.stalled
        assert old.warning == "수집 멈춤: 마지막 수집 3.0시간 전 (기준 2시간)"
        looser = collector_states({**env, "DATA_COLLECTOR_MAX_AGE_HOURS": "4"}, NOW)[0]
        assert not looser.stalled and looser.max_age_hours == 4
    finally:
        assert lock is not None
        lock.close()


def test_no_snapshot_yet_is_stalled(tmp_path: Path) -> None:
    env = _env(tmp_path, None)
    lock = collector_lock.acquire(tmp_path / "youtube_collect.lock", "t", lambda _: None)
    try:
        state = collector_states(env, NOW)[0]
        assert state.running and state.warning == "수집 멈춤: 저장된 스냅샷이 없음"
        assert state.last_collected_at is None
    finally:
        assert lock is not None
        lock.close()
