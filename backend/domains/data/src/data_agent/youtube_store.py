"""YouTube 영상 통계 스냅샷 저장소 (data 자체 SQLite 파일, 커밋하지 않음).

YouTube는 지금 누적값만 주고 기간별 추이를 주지 않는다. 그래서 정해진 간격으로 받아 쌓아 두고,
두 시각의 차이로 "지금 속도"를 구한다(판정은 별도 PR).

- 위치: 환경변수 `DATA_YOUTUBE_DB`(없으면 `backend/.data/youtube_snapshots.db`)
- `snapshots`: 수집 시각·영상 id마다 한 줄. **(수집 시각, 영상 id)가 기본 키**라
  같은 시각 같은 영상은 두 번 들어가지 않는다(먼저 넣은 줄이 남는다).
  숫자 칸이 응답에 없으면 NULL이다(0이 아님).
- `runs`: 수집 한 번마다 한 줄. 쓴 할당량 단위, 개수, 검색을 했는지, 실패 이유.
- `video_titles`: 영상 id마다 제목 한 줄(마지막으로 받은 제목). `snapshots` 표 모양은 바꾸지 않는다
  (돌고 있는 예전 수집기의 INSERT가 깨지지 않게 따로 둔다). 제목이 없는 예전 스냅샷은
  `youtube_collect --titles`로 채운다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data_agent.connectors.youtube import VideoStat

DB_ENV = "DATA_YOUTUBE_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "youtube_snapshots.db"  # backend/.data
KST = timezone(timedelta(hours=9))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    collected_at  TEXT NOT NULL,      -- 수집 시각 (UTC, 초 단위)
    video_id      TEXT NOT NULL,
    view_count    INTEGER,            -- NULL = 응답에 없음
    like_count    INTEGER,            -- NULL = 좋아요 숨김
    comment_count INTEGER,            -- NULL = 댓글 막음
    popular_rank  INTEGER,            -- 인기 순위 (인기 목록에 없으면 NULL)
    published_at  TEXT NOT NULL,      -- 게시 시각 (UTC)
    category_id   TEXT,
    channel_id    TEXT NOT NULL,
    tags          TEXT NOT NULL,      -- JSON 배열
    sources       TEXT NOT NULL,      -- 어디서 받았는지: popular, tracked, search:<키워드> (쉼표)
    PRIMARY KEY (collected_at, video_id)
);
CREATE INDEX IF NOT EXISTS snapshots_video ON snapshots (video_id, collected_at);
-- 마지막 수집 시각·기간 조회용 (표 전체를 훑지 않게)
CREATE INDEX IF NOT EXISTS snapshots_at ON snapshots (collected_at);
CREATE INDEX IF NOT EXISTS snapshots_popular_at ON snapshots (collected_at)
    WHERE popular_rank IS NOT NULL;
CREATE TABLE IF NOT EXISTS runs (
    collected_at  TEXT PRIMARY KEY,
    quota_units   INTEGER NOT NULL,   -- 이 실행에서 쓴 할당량 (문서 단가, 실패한 호출 포함)
    popular_count INTEGER NOT NULL,
    tracked_count INTEGER NOT NULL,
    search_count  INTEGER NOT NULL,
    searched      INTEGER NOT NULL,   -- 회사 키워드 검색을 했는지 (한국 날짜 하루 1번)
    errors        TEXT NOT NULL       -- 실패 이유 JSON 배열 (없으면 [])
);
CREATE TABLE IF NOT EXISTS video_details (
    video_id        TEXT PRIMARY KEY,
    channel_title   TEXT,             -- 채널 이름 (snippet.channelTitle). NULL = 받은 적 없음
    duration_s      INTEGER,          -- 길이(초, contentDetails.duration). NULL = 못 읽음
    seen_at         TEXT NOT NULL     -- 받은 시각 (UTC)
);
CREATE TABLE IF NOT EXISTS channel_stats (
    channel_id    TEXT PRIMARY KEY,
    subscribers   INTEGER,            -- 구독자 수. NULL = 채널이 숨김
    seen_at       TEXT NOT NULL       -- 받은 시각 (UTC)
);
CREATE TABLE IF NOT EXISTS video_thumbnails (
    video_id      TEXT PRIMARY KEY,
    url           TEXT NOT NULL,      -- 받은 응답의 snippet.thumbnails 주소 (더 부르지 않음)
    seen_at       TEXT NOT NULL       -- 받은 시각 (UTC)
);
CREATE TABLE IF NOT EXISTS video_titles (
    video_id      TEXT PRIMARY KEY,
    title         TEXT NOT NULL,      -- 마지막으로 받은 제목
    seen_at       TEXT NOT NULL       -- 그 제목을 받은 시각 (UTC)
);
"""


@dataclass(frozen=True)
class Row:
    """저장할 한 줄: 영상 값 + 어디서 받았는지."""

    stat: VideoStat
    sources: tuple[str, ...]


def iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


class SnapshotStore:
    """SQLite 파일(또는 `:memory:`) 저장소."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save(self, collected_at: datetime, rows: Iterable[Row]) -> int:
        """한 시각의 영상들을 넣는다. 이미 있는 (시각, 영상)은 건너뛴다. 넣은 줄 수를 돌려준다."""
        at = iso(collected_at)
        rows_list = list(rows)
        values = [
            (
                at,
                r.stat.video_id,
                r.stat.view_count,
                r.stat.like_count,
                r.stat.comment_count,
                r.stat.rank,
                iso(r.stat.published_at),
                r.stat.category_id,
                r.stat.channel_id,
                json.dumps(list(r.stat.tags), ensure_ascii=False),
                ",".join(r.sources),
            )
            for r in rows_list
        ]
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", values
            )
            saved = self._conn.total_changes - before
        self.save_titles(collected_at, [(r.stat.video_id, r.stat.title) for r in rows_list])
        self.save_details(collected_at, [r.stat for r in rows_list])
        return saved

    def save_thumbnails(self, seen_at: datetime, stats: Iterable[Any]) -> int:
        """받은 응답에 있던 썸네일 주소를 넣거나 바꾼다 (없는 영상은 건너뜀). 바꾼 줄 수."""
        values = [
            (s.video_id, url, iso(seen_at))
            for s in stats
            if (url := getattr(s, "thumbnail_url", None))
        ]
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT INTO video_thumbnails VALUES (?,?,?) ON CONFLICT(video_id) DO UPDATE"
                " SET url = excluded.url, seen_at = excluded.seen_at"
                " WHERE excluded.seen_at >= video_thumbnails.seen_at",
                values,
            )
            return self._conn.total_changes - before

    def save_details(self, seen_at: datetime, stats: Iterable[Any]) -> int:
        """채널 이름·길이를 넣거나 바꾼다(둘 다 없는 영상은 건너뜀). 바꾼 줄 수. 썸네일도 같이."""
        stats = list(stats)
        self.save_thumbnails(seen_at, stats)
        values = [
            (s.video_id, (s.channel_title or "").strip() or None, s.duration_seconds, iso(seen_at))
            for s in stats
            if (getattr(s, "channel_title", "") or "").strip()
            or getattr(s, "duration_seconds", None) is not None
        ]
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT INTO video_details VALUES (?,?,?,?) ON CONFLICT(video_id) DO UPDATE"
                " SET channel_title ="
                " COALESCE(excluded.channel_title, video_details.channel_title),"
                " duration_s = COALESCE(excluded.duration_s, video_details.duration_s),"
                " seen_at = excluded.seen_at WHERE excluded.seen_at >= video_details.seen_at",
                values,
            )
            return self._conn.total_changes - before

    def popular_ids_without_details(self) -> list[str]:
        """인기 목록에 나온 적이 있는데 채널 이름·길이가 없는 영상 id (최근에 본 순서)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.video_id, MAX(s.collected_at) AS last FROM snapshots s"
                " LEFT JOIN video_details d ON d.video_id = s.video_id"
                " WHERE s.popular_rank IS NOT NULL AND d.video_id IS NULL"
                " GROUP BY s.video_id ORDER BY last DESC"
            ).fetchall()
        return [r[0] for r in rows]

    def save_channels(self, seen_at: datetime, subscribers: Mapping[str, int | None]) -> None:
        """채널 구독자 수를 넣거나 바꾼다 (숨김은 NULL)."""
        values = [(cid, n, iso(seen_at)) for cid, n in subscribers.items()]
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO channel_stats VALUES (?,?,?) ON CONFLICT(channel_id) DO UPDATE"
                " SET subscribers = excluded.subscribers, seen_at = excluded.seen_at",
                values,
            )

    def save_titles(self, seen_at: datetime, titles: Iterable[tuple[str, str]]) -> int:
        """영상 제목을 넣거나 바꾼다(빈 제목은 건너뜀). 바꾼 줄 수."""
        values = [(vid, title.strip(), iso(seen_at)) for vid, title in titles if title.strip()]
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT INTO video_titles VALUES (?,?,?) ON CONFLICT(video_id) DO UPDATE"
                " SET title = excluded.title, seen_at = excluded.seen_at"
                " WHERE excluded.seen_at >= video_titles.seen_at",
                values,
            )
            return self._conn.total_changes - before

    def popular_ids_without_title(self) -> list[str]:
        """인기 목록에 나온 적이 있는데 제목이 없는 영상 id (최근에 본 순서)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.video_id, MAX(s.collected_at) AS last FROM snapshots s"
                " LEFT JOIN video_titles t ON t.video_id = s.video_id"
                " WHERE s.popular_rank IS NOT NULL AND t.video_id IS NULL"
                " GROUP BY s.video_id ORDER BY last DESC"
            ).fetchall()
        return [r[0] for r in rows]

    def log_run(
        self,
        collected_at: datetime,
        *,
        quota_units: int,
        popular: int,
        tracked: int,
        searched_videos: int,
        searched: bool,
        errors: list[str],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                (iso(collected_at), quota_units, popular, tracked, searched_videos, int(searched),
                 json.dumps(errors, ensure_ascii=False)),
            )  # fmt: skip

    def searched_on(self, day: date) -> bool:
        """그 한국 날짜에 회사 키워드 검색을 이미 했는지."""
        start = datetime(day.year, day.month, day.day, tzinfo=KST)
        end = start + timedelta(days=1)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM runs WHERE searched = 1 AND collected_at >= ? AND collected_at < ?",
                (iso(start), iso(end)),
            ).fetchone()
        return row is not None

    def seen_ids(self, since: datetime) -> list[str]:
        """`since` 뒤에 인기 목록이나 회사 키워드 검색에서 본 영상 (추적으로만 본 것은 뺀다).

        추적한 영상도 매번 저장되므로 "마지막 저장 시각"으로 고르면 한 번 들어온 영상이 영원히
        남는다. 그래서 추적 대상은 인기·검색에서 마지막으로 본 시각으로 고른다 (최근 순).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT video_id, MAX(collected_at) AS last FROM snapshots WHERE collected_at >= ?"
                " AND (popular_rank IS NOT NULL OR sources LIKE '%popular%'"
                " OR sources LIKE '%search:%') GROUP BY video_id ORDER BY last DESC",
                (iso(since),),
            ).fetchall()
        return [r[0] for r in rows]

    def last_run_ids(self) -> set[str]:
        """가장 최근 수집 시각에 저장한 영상 id."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT video_id FROM snapshots"
                " WHERE collected_at = (SELECT MAX(collected_at) FROM snapshots)"
            ).fetchall()
        return {r[0] for r in rows}

    def pruned_on(self, day: date) -> bool:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS prunes (day TEXT PRIMARY KEY, deleted INTEGER NOT NULL)"
            )
            row = self._conn.execute(
                "SELECT 1 FROM prunes WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        return row is not None

    def prune(self, day: date, before: datetime) -> int:
        """`before`보다 오래된 스냅샷 줄을 지우고 그날 정리했다고 남긴다. 지운 줄 수."""
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS prunes (day TEXT PRIMARY KEY, deleted INTEGER NOT NULL)"
            )
            cur = self._conn.execute("DELETE FROM snapshots WHERE collected_at < ?", (iso(before),))
            deleted = cur.rowcount
            self._conn.execute(
                "INSERT OR REPLACE INTO prunes VALUES (?, ?)", (day.isoformat(), deleted)
            )
        return deleted

    def recent_video_ids(self, since: datetime, limit: int) -> list[str]:
        """`since` 뒤에 한 번이라도 저장한 영상 id (최근에 본 순서, 최대 `limit`개)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT video_id, MAX(collected_at) AS last FROM snapshots WHERE collected_at >= ?"
                " GROUP BY video_id ORDER BY last DESC LIMIT ?",
                (iso(since), limit),
            ).fetchall()
        return [r[0] for r in rows]

    def count(self, video_id: str | None = None) -> int:
        with self._lock:
            if video_id is None:
                return int(self._conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE video_id = ?", (video_id,)
                ).fetchone()[0]
            )

    def rows(self, video_id: str) -> list[sqlite3.Row]:
        with self._lock:
            self._conn.row_factory = sqlite3.Row
            try:
                return list(
                    self._conn.execute(
                        "SELECT * FROM snapshots WHERE video_id = ? ORDER BY collected_at",
                        (video_id,),
                    )
                )
            finally:
                self._conn.row_factory = None


# ---------------------------------------------------------------- 읽기 (trend_candidates)


@dataclass(frozen=True)
class PopularVideo:
    """스냅샷 한 시각의 인기 영상 하나 (태그는 저장된 그대로)."""

    video_id: str
    rank: int
    tags: tuple[str, ...]
    title: str = ""  # 제목 (`video_titles`에 없으면 "")
    category_id: str | None = None


@dataclass(frozen=True)
class PopularSnapshot:
    collected_at: datetime
    videos: tuple[PopularVideo, ...]  # 인기 순위 순


def latest_popular(
    path: str | Path, at_or_before: datetime | None = None
) -> PopularSnapshot | None:
    """가장 최근에 인기 목록을 받은 시각의 인기 영상들. 파일이 없거나 인기 영상이 없으면 None.

    `at_or_before`를 주면 그 시각 이전(같은 시각 포함) 스냅샷 중 가장 최근 것이다(지난 기간 판정용).
    읽기 전용으로 연다(파일이 없을 때 새로 만들지 않는다).
    """
    if str(path) != ":memory:" and not Path(path).is_file():
        return None
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        if at_or_before is None:
            row = conn.execute(
                "SELECT MAX(collected_at) FROM snapshots WHERE popular_rank IS NOT NULL"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(collected_at) FROM snapshots"
                " WHERE popular_rank IS NOT NULL AND collected_at <= ?",
                (iso(at_or_before),),
            ).fetchone()
        if not row or not row[0]:
            return None
        titles = _has_titles(conn)
        rows = conn.execute(
            "SELECT s.video_id, s.popular_rank, s.tags, s.category_id, "
            + ("t.title" if titles else "''")
            + " FROM snapshots s"
            + (" LEFT JOIN video_titles t ON t.video_id = s.video_id" if titles else "")
            + " WHERE s.collected_at = ? AND s.popular_rank IS NOT NULL ORDER BY s.popular_rank",
            (row[0],),
        ).fetchall()
    except sqlite3.DatabaseError:
        return None  # 표가 없는 파일 등
    finally:
        conn.close()
    videos = []
    for video_id, rank, tags, category, title in rows:
        videos.append(PopularVideo(video_id, int(rank), _tags(tags), title or "", category))
    return PopularSnapshot(datetime.fromisoformat(row[0]).astimezone(UTC), tuple(videos))


@dataclass(frozen=True)
class StoredVideo:
    """스냅샷 한 시각의 영상 한 줄 (숫자 칸이 응답에 없었으면 None)."""

    video_id: str
    channel_id: str
    published_at: datetime
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    popular_rank: int | None
    tags: tuple[str, ...]


@dataclass(frozen=True)
class StoredSnapshot:
    collected_at: datetime
    videos: dict[str, StoredVideo]  # 영상 id → 그 시각의 값


def recent_snapshots(path: str | Path, count: int = 3) -> list[StoredSnapshot]:
    """가장 최근 수집 시각 `count`개(최근 것부터)의 영상 값. 파일이 없으면 빈 목록. 읽기 전용."""
    if str(path) != ":memory:" and not Path(path).is_file():
        return []
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        times = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT collected_at FROM snapshots ORDER BY collected_at DESC LIMIT ?",
                (count,),
            )
        ]
        out = []
        for at in times:
            rows = conn.execute(
                "SELECT video_id, channel_id, published_at, view_count, like_count, comment_count,"
                " popular_rank, tags FROM snapshots WHERE collected_at = ?",
                (at,),
            ).fetchall()
            videos = {}
            for vid, ch, pub, views, likes, comments, rank, tags in rows:
                try:
                    parsed = json.loads(tags)
                except ValueError:
                    parsed = []
                videos[vid] = StoredVideo(
                    vid,
                    ch,
                    datetime.fromisoformat(pub).astimezone(UTC),
                    views,
                    likes,
                    comments,
                    rank,
                    tuple(str(t) for t in parsed if isinstance(t, str)),
                )
            out.append(StoredSnapshot(datetime.fromisoformat(at).astimezone(UTC), videos))
        return out
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


def _has_titles(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'video_titles'"
    ).fetchone()
    return row is not None


def _tags(raw: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ()
    return tuple(str(t) for t in parsed if isinstance(t, str)) if isinstance(parsed, list) else ()


# ---------------------------------------------------------------- 쌓인 스냅샷 전체 (자동 제외)


@dataclass(frozen=True)
class HistoryVideo:
    """쌓인 인기 목록 전체의 영상 하나: 태그(모든 시각의 합), 제목, 카테고리, 나온 한국 날짜."""

    video_id: str
    category_id: str | None
    tags: tuple[str, ...]
    title: str
    days: frozenset[date]


def popular_history(path: str | Path, until: datetime | None = None) -> list[HistoryVideo]:
    """`until`(포함) 이전 모든 인기 목록 스냅샷의 영상. 파일이 없으면 빈 목록. 읽기 전용."""
    if str(path) != ":memory:" and not Path(path).is_file():
        return []
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        titles = _has_titles(conn)
        sql = (
            "SELECT s.video_id, s.category_id, s.tags, s.collected_at, "
            + ("t.title" if titles else "''")
            + " FROM snapshots s"
            + (" LEFT JOIN video_titles t ON t.video_id = s.video_id" if titles else "")
            + " WHERE s.popular_rank IS NOT NULL"
        )
        args: list[str] = []
        if until is not None:
            sql += " AND s.collected_at <= ?"
            args.append(iso(until))
        rows = conn.execute(sql, args).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    acc: dict[str, tuple[str | None, set[str], str, set[date]]] = {}
    for vid, category, tags, at, title in rows:
        cat, seen, old_title, days = acc.setdefault(vid, (category, set(), title or "", set()))
        seen.update(_tags(tags))
        days.add(datetime.fromisoformat(at).astimezone(KST).date())
    return [
        HistoryVideo(vid, cat, tuple(sorted(seen)), title, frozenset(days))
        for vid, (cat, seen, title, days) in sorted(acc.items())
    ]


def video_titles(path: str | Path, video_ids: list[str]) -> dict[str, str]:
    """영상 id → 제목 (`video_titles` 표, 읽기 전용). 파일·표가 없거나 제목이 없으면 빠진다."""
    if not video_ids or (str(path) != ":memory:" and not Path(path).is_file()):
        return {}
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        if not _has_titles(conn):
            return {}
        marks = ",".join("?" * len(video_ids))
        rows = conn.execute(
            f"SELECT video_id, title FROM video_titles WHERE video_id IN ({marks})",  # noqa: S608
            video_ids,
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    return {vid: title for vid, title in rows if title}


def video_thumbnails(path: str | Path, video_ids: list[str]) -> dict[str, str]:
    """영상 id → 썸네일 주소 (`video_thumbnails` 표, 읽기 전용). 없으면 빠진다."""
    if not video_ids or (str(path) != ":memory:" and not Path(path).is_file()):
        return {}
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        marks = ",".join("?" * len(video_ids))
        rows = conn.execute(
            f"SELECT video_id, url FROM video_thumbnails WHERE video_id IN ({marks})",  # noqa: S608
            video_ids,
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}  # 표가 없는 예전 파일
    finally:
        conn.close()
    return {vid: url for vid, url in rows if url}


@dataclass(frozen=True)
class VideoDetail:
    channel_title: str | None
    duration_seconds: int | None


def video_details(path: str | Path, video_ids: list[str]) -> dict[str, VideoDetail]:
    """영상 id → 채널 이름·길이 (`video_details` 표, 읽기 전용). 없으면 빠진다."""
    if not video_ids or (str(path) != ":memory:" and not Path(path).is_file()):
        return {}
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        marks = ",".join("?" * len(video_ids))
        rows = conn.execute(
            "SELECT video_id, channel_title, duration_s FROM video_details"
            f" WHERE video_id IN ({marks})",  # noqa: S608
            video_ids,
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}  # 표가 없는 예전 파일
    finally:
        conn.close()
    return {vid: VideoDetail(ch, dur) for vid, ch, dur in rows}


def channel_subscribers(
    path: str | Path, channel_ids: Iterable[str], since: datetime
) -> dict[str, int | None]:
    """`since` 이후에 받은 채널 구독자 수 (숨김은 None). 받은 적 없거나 오래된 채널은 빠진다."""
    ids = [c for c in dict.fromkeys(channel_ids) if c]
    if not ids or (str(path) != ":memory:" and not Path(path).is_file()):
        return {}
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT channel_id, subscribers FROM channel_stats WHERE channel_id IN ({marks})"  # noqa: S608
            " AND seen_at >= ?",
            (*ids, iso(since)),
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}  # 표가 없는 예전 파일
    finally:
        conn.close()
    return {str(c): (int(n) if n is not None else None) for c, n in rows}
