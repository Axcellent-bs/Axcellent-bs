"""숏츠 표본의 빠진 게시 시각(published) 채우기.

수집기(`youtube_collect`)가 미리 계산 바로 앞에 부른다.

숏츠 저장소(`shorts.db`)의 `published` 표는 나중에 생겨서, 그 전에 검색한 숏츠는 게시 시각이 없다.
요즘 유행·밈(trend_memes)·shorts_stats의 "최근 N일"은 게시 시각을 모르면 세지 않으므로 채운다.

- 대상: 최근 `[shorts] retention_days`(30일) 표본 중 게시 시각을 모르는 숏츠
  1. 숏츠 검색 저장분(`searched_videos`, 길이 `max_seconds` 이하·언어 조건은 shorts_stats와 같다)
  2. YouTube 인기 스냅샷의 숏츠(길이 `video_details`). 스냅샷 표 `published_at`으로 이미 아는 영상은
     부르지 않는다
  `published`에 있거나, 전에 응답에 없던 영상(`published_missing`)은 빼고 최근 검색일 순
- 방법: 커넥터 `videos(ids)`를 50개씩(한 번 = 1단위) 불러 숏츠 저장소의 기존 저장 함수
  (`ShortsStore.record_views`: 조회수·썸네일·채널 id·게시 시각)로 남긴다. 응답에 없는 영상(지워짐·
  비공개)은 `published_missing` 표에 남겨 다시 부르지 않는다(video_explorer `missing`과 같은 방식)
- 상한: 하루(YouTube 장부 날짜 = 태평양 0시) `DATA_SHORTS_BACKFILL_DAILY`번
  (기본 20번 = 영상 1,000개). 단위는 커넥터가 장부(`youtube_quota.LEDGER`)에 부른 곳
  `shorts_backfill`로 적는다. 새 숏츠 추적 예약 몫(`reserve`)은 쓰지 않는다
  (다른 기능 합계 + 1 > 상한 − 예약이면 부르지 않음). 남은 것은 다음 날
- 로그: `[backfill] published 채움 N · 없음 M · 남음 K` (값·키 없이)
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from data_agent import youtube_quota
from data_agent.connectors import ConnectorUnavailable

CALLER = "shorts_backfill"  # YouTube 장부의 부른 곳
DAILY_ENV = "DATA_SHORTS_BACKFILL_DAILY"
DEFAULT_DAILY = 20
PAGE = 50  # videos.list 한 번에 영상 50개 = 1단위


def daily_calls(env: Mapping[str, str]) -> int:
    try:
        value = int(str(env.get(DAILY_ENV, "")).strip())
    except ValueError:
        return DEFAULT_DAILY
    return max(0, value)


class MissingStore:
    """응답에 없던 영상 (숏츠 저장소 안 별도 표 `published_missing`)."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS published_missing (video_id TEXT PRIMARY KEY,"
                " at TEXT NOT NULL)"
            )

    def close(self) -> None:
        self._conn.close()

    def ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT video_id FROM published_missing")}

    def save(self, at: datetime, ids: Sequence[str]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO published_missing VALUES (?,?)",
                [(i, at.isoformat()) for i in ids],
            )


def missing_ids(path: str | Path) -> set[str]:
    """응답에 없던 영상 (읽기 전용, 파일·표가 없으면 빈 집합 — 파일을 만들지 않는다)."""
    if str(path) != ":memory:" and not Path(path).is_file():
        return set()
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        return {r[0] for r in conn.execute("SELECT video_id FROM published_missing")}
    except sqlite3.DatabaseError:
        return set()
    finally:
        conn.close()


def targets(env: Mapping[str, str], now: datetime, gone: set[str]) -> list[str]:
    """게시 시각을 모르는 최근 표본 숏츠 id (최근 검색일 순, 인기 스냅샷 숏츠는 뒤)."""
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB, is_language
    from data_agent.skills.shorts_stats import load_config as shorts_config
    from data_agent.youtube_store import DB_ENV as YT_ENV
    from data_agent.youtube_store import DEFAULT_DB as YT_DEFAULT
    from data_agent.youtube_store import published_times

    config = shorts_config(env)
    since = now - timedelta(days=config.retention_days)
    order: dict[str, None] = {}
    known: set[str] = set()
    path = env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB
    if str(path) == ":memory:" or Path(path).is_file():
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT video_id, title, channel_title, duration_s, language FROM searched_videos"
                " WHERE day >= ? ORDER BY day DESC",
                (since.astimezone(UTC).date().isoformat(),),
            ).fetchall()
            known = {r[0] for r in conn.execute("SELECT video_id FROM published")}
        except sqlite3.DatabaseError:
            rows = []
        finally:
            conn.close()
        for vid, title, channel, dur, lang in rows:
            if dur is None or dur > config.max_seconds:
                continue
            if config.language and not is_language(config.language, title, channel, lang):
                continue
            order.setdefault(vid)
    yt = env.get(YT_ENV) or str(YT_DEFAULT)
    for vid in _popular_shorts(yt, since, config.max_seconds):
        order.setdefault(vid)
    todo = [v for v in order if v not in known and v not in gone]
    # 스냅샷 표로 게시 시각을 이미 아는 영상은 부르지 않는다
    seen = published_times(yt, todo)
    return [v for v in todo if v not in seen]


def _popular_shorts(path: str, since: datetime, max_seconds: int) -> list[str]:
    if not Path(path).is_file():
        return []
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT DISTINCT s.video_id FROM snapshots s JOIN video_details d"
            " ON d.video_id = s.video_id WHERE s.popular_rank IS NOT NULL AND s.collected_at >= ?"
            " AND d.duration_s IS NOT NULL AND d.duration_s <= ?",
            (since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"), max_seconds),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    return [r[0] for r in rows]


def _reserve_reason(ledger: Any, env: Mapping[str, str]) -> str | None:
    """새 숏츠 추적 예약 몫을 건드리게 되면 이유 (1단위 호출도 예약을 남긴다)."""
    rows = ledger.rows()
    mine = sum(u for c, u, _ in rows if c == youtube_quota.RESERVE_CALLER)
    others = sum(u for _, u, _ in rows) - mine
    room = youtube_quota.cap(env) - youtube_quota.reserve(env)
    if others + 1 > room:
        return f"새 숏츠 추적 예약 몫을 남기려고 멈춤 (다른 기능 오늘 {others:,}/{room:,}단위)"
    return None


@dataclass(frozen=True)
class BackfillResult:
    filled: int  # 게시 시각을 채운 영상
    missing: int  # 응답에 없던 영상 (지워짐·비공개)
    left: int  # 남은 대상 (다음 날)
    calls: int  # 이번에 부른 videos.list 수
    stopped: str | None = None  # 멈춘 이유 (하루 상한·예약 몫·실패)

    def line(self) -> str:
        head = f"[backfill] published 채움 {self.filled} · 없음 {self.missing} · 남음 {self.left}"
        return head + (f" ({self.stopped})" if self.stopped else "")


async def backfill(
    connector: Any,
    env: Mapping[str, str],
    *,
    now: datetime | None = None,
    ledger: Any = None,
    store: Any = None,
    missing: MissingStore | None = None,
) -> BackfillResult:
    """빠진 게시 시각을 50개씩 채운다. 하루 상한·예약 몫 안에서만, 남으면 다음 날."""
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB, ShortsStore

    now = now or datetime.now(UTC)
    ledger = ledger if ledger is not None else youtube_quota.LEDGER
    path = env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB
    own_missing = missing is None
    own_store = store is None
    try:
        todo = targets(env, now, missing.ids() if missing is not None else missing_ids(path))
        used = sum(u for c, u, _ in ledger.rows() if c == CALLER)
        limit = daily_calls(env)
        if not todo:
            return BackfillResult(0, 0, 0, 0)
        if connector is None or not hasattr(connector, "videos"):
            return BackfillResult(0, 0, len(todo), 0, "YouTube 커넥터 없음")
        # 부를 것이 있을 때만 쓰는 표를 연다 (대상은 이미 있는 파일에서만 나온다)
        missing = missing or MissingStore(path)
        store = store or ShortsStore(path)
        filled = gone = calls = 0
        stopped: str | None = None
        done = 0
        for start in range(0, len(todo), PAGE):
            if used + calls >= limit:
                stopped = f"하루 상한 {limit}번"
                break
            if why := _reserve_reason(ledger, env):
                stopped = why
                break
            chunk = todo[start : start + PAGE]
            try:
                got = await connector.videos(chunk)
            except ConnectorUnavailable as error:
                stopped = f"YouTube를 읽지 못함 [{error.code}]"
                break
            calls += 1
            done += len(chunk)
            store.record_views(now, got)
            found = {v.video_id for v in got}
            filled += sum(1 for v in got if isinstance(getattr(v, "published_at", None), datetime))
            lost = [v for v in chunk if v not in found]
            missing.save(now, lost)
            gone += len(lost)
        return BackfillResult(filled, gone, len(todo) - done, calls, stopped)
    finally:
        if own_store and store is not None:
            store.close()
        if own_missing and missing is not None:
            missing.close()


async def run(env: Mapping[str, str] | None = None) -> BackfillResult:
    """수집기용: 부른 곳 `shorts_backfill`인 커넥터로 채우고 한 줄을 찍는다.

    실패해도 수집은 계속한다.
    """
    from data_agent.connectors.youtube import YouTubeConnector

    env = os.environ if env is None else env
    try:
        result = await backfill(YouTubeConnector(env, caller=CALLER), env)
    except Exception as error:  # noqa: BLE001 - 채우기 실패가 수집을 막지 않게
        result = BackfillResult(0, 0, 0, 0, f"실패 ({type(error).__name__})")
    print(result.line(), flush=True)
    return result
