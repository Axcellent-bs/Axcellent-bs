"""YouTube 영상 통계 스냅샷 저장소 (data 자체 SQLite 파일, 커밋하지 않음).

YouTube는 지금 누적값만 주고 기간별 추이를 주지 않는다. 그래서 정해진 간격으로 받아 쌓아 두고,
두 시각의 차이로 "지금 속도"를 구한다(판정은 별도 PR).

- 위치: 환경변수 `DATA_YOUTUBE_DB`(없으면 `backend/.data/youtube_snapshots.db`)
- `snapshots`: 수집 시각·영상 id마다 한 줄. **(수집 시각, 영상 id)가 기본 키**라
  같은 시각 같은 영상은 두 번 들어가지 않는다(먼저 넣은 줄이 남는다).
  숫자 칸이 응답에 없으면 NULL이다(0이 아님).
- `runs`: 수집 한 번마다 한 줄. 쓴 할당량 단위, 개수, 검색을 했는지, 실패 이유.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

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
CREATE TABLE IF NOT EXISTS runs (
    collected_at  TEXT PRIMARY KEY,
    quota_units   INTEGER NOT NULL,   -- 이 실행에서 쓴 할당량 (문서 단가, 실패한 호출 포함)
    popular_count INTEGER NOT NULL,
    tracked_count INTEGER NOT NULL,
    search_count  INTEGER NOT NULL,
    searched      INTEGER NOT NULL,   -- 회사 키워드 검색을 했는지 (한국 날짜 하루 1번)
    errors        TEXT NOT NULL       -- 실패 이유 JSON 배열 (없으면 [])
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
            for r in rows
        ]
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", values
            )
            return self._conn.total_changes - before

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
        rows = conn.execute(
            "SELECT video_id, popular_rank, tags FROM snapshots"
            " WHERE collected_at = ? AND popular_rank IS NOT NULL ORDER BY popular_rank",
            (row[0],),
        ).fetchall()
    except sqlite3.DatabaseError:
        return None  # 표가 없는 파일 등
    finally:
        conn.close()
    videos = []
    for video_id, rank, tags in rows:
        try:
            parsed = json.loads(tags)
        except ValueError:
            parsed = []
        videos.append(
            PopularVideo(video_id, int(rank), tuple(str(t) for t in parsed if isinstance(t, str)))
        )
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
