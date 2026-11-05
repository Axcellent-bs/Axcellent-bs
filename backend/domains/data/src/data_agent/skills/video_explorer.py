"""video_explorer: 영상 탐색 (카테고리 × 기간 × 길이). grade read. README "video_explorer".

- 기간은 올린 시각(publishedAt) 기준 1일·3일·7일(누적).
  숏 = `[shorts] max_seconds`(60초) 이하, 롱 = 그 초과
- 영상 풀: 올린 지 7일 이내인 영상. #639의 48시간 조회 범위와 따로 7일까지 읽는다
  1. YouTube 스냅샷 저장분(인기 차트·추적·회사 키워드 검색). 조회수·좋아요·채널은 마지막 스냅샷 값
  2. 숏츠 표본(`shorts.db` 최근 7일 검색)·하위 주제 검색(`subtopics.db`)·지금 인기 보충 영상
     (`popular_videos.db` 최근 7일)·이 skill의 보충 검색. 이 영상들은 올린 시각·좋아요·채널 id가
     없거나 오래돼 `videos.list`(50개 = 1단위, 하루 상한 `video_calls`)로 다시 읽어 이 skill 저장분
     (`video_explorer.db`)에 남긴다. 채널 id도 여기서 채운다
  - 한국 영상만(`[explorer] language`, 숏츠 통계와 같은 판단)
- 분류: 공유 영상 분류기(`classify_videos`, 저장 재사용, 실존 이름 판정 포함). 한 번 계산에 새로
  묻는 영상은 한 번 `classify_max`개·하루 `classify_daily`개까지(LLM 하루 상한 보호, 분류 대기는
  하루 몫씩 나눠서). 못 한 영상은 칸에 넣지 않고 수만 남긴다
- 칸마다 최소 `min_per_cell`(10)개: 풀에서 먼저 찾고 모자란 칸만 검색으로 보충한다
  - 검색어: 그 카테고리 지금 인기 주제 이름 앞 `query_topics`개(사람 이름 제외, trend_popular),
    `seed_categories`(교육·책)는 도메인 프로필 씨앗. 손으로 나열하지 않는다. 이름이 없으면
    `[popular] fill_youtube_categories`의 그 칸 YouTube 카테고리로 찾는다
  - regionCode KR, relevanceLanguage ko, 조회수 순
  - 7일 검색: 카테고리 × 숏/롱마다 하루 1번(publishedAfter 7일). 숏은 videoDuration=short로 찾고
    코드로 60초 이하만, 롱은 길이 조건 없이 찾고 60초 이하를 뺀다. 기간은 코드가 나눈다
  - 1일 칸이 모자라면 그날 한 번만 보충 검색(publishedAfter 1일)
  - 하루 검색 상한 `daily_searches`(20번)는 7일 검색에 먼저 쓰고 남은 횟수를 1일 보충에. 넘으면
    그 칸만 "검색 상한"으로 모자란 채로
  - 그래도 모자라면 있는 만큼 + 이유("이 기간 한국 영상 N개")
- 구독자 대비: channels.list(50개 = 1단위, 하루 상한 `channel_calls`, 저장 재사용). 조회수 많은
  영상의 채널부터
- 수집기가 미리 계산해 저장한다(`trend_precompute`). 화면 요청은 저장분을 읽어 칸을 자르고 정렬만
  한다(정렬은 코드). 저장분이 없을 때만 지금 계산한다
- 없는 값은 None + 이유 (좋아요 숨김 등). 0으로 채우지 않는다
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import tomllib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    ExplorerCell,
    ExplorerSearch,
    ExplorerVideo,
    VideoExplorerOutput,
    VideoExplorerRequest,
    YouTubeChartFailure,
)
from data_agent import caution, llm_failure
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat
from data_agent.skills.shorts_stats import is_language
from data_agent.skills.shorts_stats import load_config as load_shorts_config
from data_agent.skills.topic_category import UNKNOWN, VIDEO_KEY, VideoInput, classify_videos
from data_agent.skills.trend_scan import TREND_CONFIG_ENV
from data_agent.video_links import video_url

KST = timezone(timedelta(hours=9))
DB_ENV = "DATA_EXPLORER_DB"
DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "video_explorer.db"  # backend/.data
PERIOD_DAYS = {"1d": 1, "3d": 3, "7d": 7}
LENGTHS = ("short", "long")
POOL_DAYS = 7
PAGE = 50  # videos.list·channels.list 한 번에 50개 (1단위)
SEARCH_UNITS = 100
CAP_REASON = "검색 상한"
CHART_SEARCH = "chart:"  # 차트 대신 검색의 검색 기록 키 (chart:<YouTube 카테고리 id>)
CHART_SEARCH_DAYS = 3  # 차트 대신 검색: 최근 3일 (칸 보충 3일 검색과 같은 구간)
SOURCES = ("chart", "tracked", "shorts", "subtopic", "popular_fill", "search")


@dataclass(frozen=True)
class ExplorerConfig:
    categories: tuple[str, ...] = ("게임", "유머", "드라마", "교양", "교육·책")
    min_per_cell: int = 10
    daily_searches: int = 20
    video_calls: int = 10  # videos.list 하루 상한 (한 번 = 영상 50개·1단위)
    channel_calls: int = 4  # channels.list 하루 상한 (한 번 = 채널 50개·1단위)
    classify_max: int = 200  # 한 번 계산에 새로 분류할 영상 수 상한
    classify_daily: int = 300  # 하루에 새로 분류할 영상 수 (분류 대기는 하루 몫씩 나눠서)
    max_videos: int = 30  # 응답 칸마다 주는 영상 수 (count는 칸 전체 수)
    query_topics: int = 2
    seed_categories: tuple[str, ...] = ("교육·책",)
    # 숏 칸이 모자라면 7일 이전 숏츠 표본으로 채울 카테고리 (화면 "7일 이전")
    backfill_categories: tuple[str, ...] = ("교육·책",)
    # 카테고리별 인기 차트 (videos.list chart=mostPopular, regionCode KR, videoCategoryId별 1단위):
    # 화면 카테고리 → 대응하는 YouTube 카테고리 id. 받은 영상은 우리 분류기로 다시 분류한다
    chart: tuple[tuple[str, tuple[str, ...]], ...] = ()
    chart_minutes: int = 50  # 이보다 최근에 받았으면 다시 받지 않는다 (수집기 1시간마다)
    refresh_hours: float = 12.0  # 다시 읽은 지 이 시간이 지난 영상만 다시 읽는다
    language: str = "ko"


def load_config(env: Mapping[str, str]) -> ExplorerConfig:
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return ExplorerConfig()
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("explorer") or {}
        base = ExplorerConfig()
        values: dict[str, Any] = {}
        for k in dataclasses.fields(ExplorerConfig):
            default = getattr(base, k.name)
            value = raw.get(k.name, default)
            if k.name == "chart":  # 표: 화면 카테고리 → YouTube 카테고리 id 하나 또는 여러 개
                table = value if isinstance(value, dict) else dict(default)
                values[k.name] = tuple(
                    (str(cat), tuple(str(x) for x in (ids if isinstance(ids, list | tuple)
                                                      else [ids])))
                    for cat, ids in table.items()
                )  # fmt: skip
                continue
            values[k.name] = (
                tuple(str(x) for x in value) if isinstance(default, tuple) else type(default)(value)
            )
        return ExplorerConfig(**values)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return ExplorerConfig()


def _day(at: datetime) -> date:
    return at.astimezone(KST).date()


# ---------------------------------------------------------------- 저장


def _dump(v: VideoStat) -> str:
    data = dataclasses.asdict(v)
    data["published_at"] = v.published_at.isoformat()
    data["tags"] = list(v.tags)
    return json.dumps(data, ensure_ascii=False)


def _load(raw: str) -> VideoStat:
    data = json.loads(raw)
    data["published_at"] = datetime.fromisoformat(data["published_at"])
    data["tags"] = tuple(data.get("tags") or ())
    return VideoStat(**data)


class ExplorerStore:
    """다시 읽은·검색한 영상, 그날 검색 기록, 호출 수(단위), 미리 계산한 결과."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, stat TEXT NOT NULL,"
                " source TEXT NOT NULL, seen_at TEXT NOT NULL);"
                # 다시 읽었는데 응답에 없던 영상 (지워짐·비공개): 다시 읽지 않게
                "CREATE TABLE IF NOT EXISTS missing (video_id TEXT PRIMARY KEY,"
                " seen_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS searches (day TEXT NOT NULL, category TEXT NOT NULL,"
                " length TEXT NOT NULL, window TEXT NOT NULL, status TEXT NOT NULL, query TEXT,"
                " found INTEGER NOT NULL, reason TEXT,"
                " PRIMARY KEY (day, category, length, window));"
                "CREATE TABLE IF NOT EXISTS api_calls (day TEXT NOT NULL, kind TEXT NOT NULL,"
                " calls INTEGER NOT NULL, units INTEGER NOT NULL, PRIMARY KEY (day, kind));"
                "CREATE TABLE IF NOT EXISTS result (id INTEGER PRIMARY KEY CHECK (id = 1),"
                " computed_at TEXT NOT NULL, payload TEXT NOT NULL);"
                # 카테고리 인기 차트를 받지 못한 YouTube 카테고리 (차트가 다시 되면 지운다)
                "CREATE TABLE IF NOT EXISTS chart_failures (category_id TEXT PRIMARY KEY,"
                " categories TEXT NOT NULL, code TEXT NOT NULL, reason TEXT NOT NULL,"
                " failed_at TEXT NOT NULL, fallback TEXT NOT NULL DEFAULT 'not_run',"
                " found INTEGER, fallback_reason TEXT, fallback_day TEXT);"
            )

    def close(self) -> None:
        self._conn.close()

    # 호출 수 (trend_evidence.subscribers가 같은 이름으로 부른다)
    def calls(self, day: date, kind: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT calls FROM api_calls WHERE day = ? AND kind = ?", (day.isoformat(), kind)
            ).fetchone()
        return int(row[0]) if row else 0

    def add_calls(self, day: date, kind: str, calls: int, units: int | None = None) -> None:
        units = calls if units is None else units
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO api_calls VALUES (?,?,?,?) ON CONFLICT(day, kind) DO UPDATE"
                " SET calls = calls + excluded.calls, units = units + excluded.units",
                (day.isoformat(), kind, calls, units),
            )

    def units(self, day: date) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(units), 0) FROM api_calls WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        return int(row[0])

    # 영상
    def save_videos(self, at: datetime, videos: Sequence[VideoStat], source: str) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO videos VALUES (?,?,?,?) ON CONFLICT(video_id) DO UPDATE"
                " SET stat = excluded.stat, seen_at = excluded.seen_at",
                [(v.video_id, _dump(v), source, at.isoformat()) for v in videos],
            )

    def save_missing(self, at: datetime, ids: Sequence[str]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO missing VALUES (?,?)", [(i, at.isoformat()) for i in ids]
            )

    def videos(self) -> dict[str, tuple[VideoStat, str, datetime]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT video_id, stat, source, seen_at FROM videos"
            ).fetchall()
        return {vid: (_load(stat), src, datetime.fromisoformat(at)) for vid, stat, src, at in rows}

    def missing(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT video_id FROM missing")}

    # 검색 기록
    def searched(self, day: date) -> dict[tuple[str, str, str], ExplorerSearch]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT category, length, window, status, query, found, reason FROM searches"
                " WHERE day = ?",
                (day.isoformat(),),
            ).fetchall()
        out: dict[tuple[str, str, str], ExplorerSearch] = {}
        for c, ln, w, s, q, n, r in rows:
            if c.startswith(CHART_SEARCH):  # 차트 대신 검색: 칸 검색 기록이 아님 (상한만 같이 셈)
                continue
            if s == "running":  # 다른 계산이 검색 중 (또는 검색 중에 멈춤)
                s, r = "unknown", "다른 계산이 검색 중이거나 검색 중에 멈춤"
            out[(c, ln, w)] = ExplorerSearch.model_validate({
                "category": c, "length": ln, "window": w, "status": s, "query": q, "found": n,
                "reason": r, "day": day})  # fmt: skip
        return out

    def save_search(self, day: date, s: ExplorerSearch) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO searches VALUES (?,?,?,?,?,?,?,?)",
                (day.isoformat(), s.category, s.length, s.window, s.status, s.query, s.found,
                 s.reason),  # fmt: skip
            )

    def reserve(self, day: date, cat: str, length: str, window: str, cap: int) -> str:
        """칸 하나의 검색을 예약한다 (서버·수집기가 동시에 계산해도 한 번만, 상한도 여기서 센다).

        "ok" = 이 계산이 검색한다, "done" = 오늘 이미 검색했거나 다른 계산이 검색 중,
        "cap" = 하루 상한에 닿음.
        """
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status FROM searches WHERE day = ? AND category = ? AND length = ?"
                    " AND window = ?",
                    (day.isoformat(), cat, length, window),
                ).fetchone()
                if row is not None and row[0] != "skipped":
                    conn.rollback()
                    return "done"
                used = conn.execute(
                    "SELECT COUNT(*) FROM searches WHERE day = ? AND status != 'skipped'",
                    (day.isoformat(),),
                ).fetchone()[0]
                if used >= cap:
                    conn.rollback()
                    return "cap"
                conn.execute(
                    "INSERT OR REPLACE INTO searches VALUES (?,?,?,?,'running',NULL,0,NULL)",
                    (day.isoformat(), cat, length, window),
                )
                conn.commit()
                return "ok"
            except Exception:
                conn.rollback()
                raise

    def searches_on(self, day: date) -> int:
        """그날 실제로 부른 검색 수 (상한으로 건너뛴 것은 빼고)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM searches WHERE day = ? AND status != 'skipped'",
                (day.isoformat(),),
            ).fetchone()
        return int(row[0])

    def finish_search(self, day: date, key: str, status: str, found: int, reason: str | None,
                      query: str | None = None) -> None:  # fmt: skip
        """예약한 검색(`reserve`)의 결과를 남긴다 (차트 대신 검색처럼 ExplorerSearch가 아닌 것)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE searches SET status = ?, query = ?, found = ?, reason = ?"
                " WHERE day = ? AND category = ?",
                (status, query, found, reason, day.isoformat(), key),
            )

    # 차트 실패
    def chart_failed(self, cid: str, cats: Sequence[str], code: str, reason: str,
                     at: datetime) -> None:  # fmt: skip
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO chart_failures (category_id, categories, code, reason, failed_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT(category_id) DO UPDATE SET"
                " categories = excluded.categories, code = excluded.code,"
                " reason = excluded.reason, failed_at = excluded.failed_at",
                (cid, json.dumps(list(cats), ensure_ascii=False), code, reason, at.isoformat()),
            )

    def chart_fallback(self, cid: str, day: date, status: str, found: int | None,
                       reason: str | None) -> None:  # fmt: skip
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE chart_failures SET fallback = ?, found = ?, fallback_reason = ?,"
                " fallback_day = ? WHERE category_id = ?",
                (status, found, reason, day.isoformat(), cid),
            )

    def chart_ok(self, cid: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chart_failures WHERE category_id = ?", (cid,))

    def chart_failures(self) -> list[YouTubeChartFailure]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT category_id, categories, code, reason, failed_at, fallback, found,"
                " fallback_reason, fallback_day FROM chart_failures ORDER BY category_id"
            ).fetchall()
        return [
            YouTubeChartFailure(
                category_id=cid,
                categories=json.loads(cats),
                code=code,
                reason=reason,
                failed_at=datetime.fromisoformat(at),
                fallback=fb,
                fallback_found=found,
                fallback_reason=why,
                fallback_day=date.fromisoformat(day) if day else None,
            )
            for cid, cats, code, reason, at, fb, found, why, day in rows
        ]

    # 미리 계산한 결과
    def result(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM result WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else None

    def save_result(self, at: datetime, payload: Mapping[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO result VALUES (1, ?, ?)",
                (at.isoformat(), json.dumps(payload, ensure_ascii=False)),
            )


# ---------------------------------------------------------------- 영상 풀


@dataclass
class _Item:
    stat: VideoStat
    source: str
    at: datetime  # 값을 읽은 시각
    category: str = UNKNOWN
    ad: caution.Caution | None = None
    subscribers: int | None = None
    subscriber_reason: str | None = None


def snapshot_pool(db: str | Path, since: datetime) -> dict[str, _Item]:
    """YouTube 스냅샷에서 `since` 이후 올린 영상의 마지막 값 (제목·길이·썸네일·채널 이름 포함)."""
    from data_agent.youtube_store import iso, video_details, video_thumbnails, video_titles

    if str(db) != ":memory:" and not Path(db).is_file():
        return {}
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT video_id, view_count, like_count, comment_count, popular_rank, published_at,"
            " category_id, channel_id, tags, sources, collected_at FROM snapshots"
            " WHERE published_at >= ? ORDER BY collected_at",
            (iso(since),),
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    last = {r[0]: r for r in rows}
    ids = list(last)
    titles, details = video_titles(db, ids), video_details(db, ids)
    thumbs = video_thumbnails(db, ids)
    out: dict[str, _Item] = {}
    for vid, r in last.items():
        d = details.get(vid)
        sources = str(r[9] or "")
        source = "chart" if r[4] is not None else "tracked" if "tracked" in sources else "search"
        stat = VideoStat(
            video_id=vid, channel_id=r[7], category_id=r[6],
            published_at=datetime.fromisoformat(r[5]), view_count=r[1], like_count=r[2],
            comment_count=r[3], tags=tuple(json.loads(r[8] or "[]")), title=titles.get(vid, ""),
            channel_title=(d.channel_title if d else None) or "",
            duration_seconds=d.duration_seconds if d else None, thumbnail_url=thumbs.get(vid),
        )  # fmt: skip
        out[vid] = _Item(stat, source, datetime.fromisoformat(r[10]))
    return out


def other_ids(env: Mapping[str, str], now: datetime) -> list[tuple[str, str]]:
    """다른 저장분의 영상 id와 출처 (숏츠 표본·하위 주제·지금 인기 보충). 올린 시각은 모른다."""
    from data_agent.skills.domain_subtopics import pool_shorts
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB
    from data_agent.skills.trend_popular import FILL_DB_ENV, FILL_DEFAULT_DB

    since = (_day(now) - timedelta(days=POOL_DAYS)).isoformat()
    out: list[tuple[str, str]] = []

    def read(path: str | Path, sql: str) -> list[tuple[Any, ...]]:
        if not Path(path).is_file():
            return []
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            return conn.execute(sql, (since,)).fetchall()
        except sqlite3.DatabaseError:
            return []
        finally:
            conn.close()

    # 순서 = 다시 읽기 우선순위 (하루 상한 안에서 앞부터). 하위 주제·숏츠 표본이 먼저:
    # 교육·책처럼 7일 안 영상이 적은 칸을 채우는 숏츠가 여기 있다 (7일 이전 채우기에 올린 시각 필요)
    shorts = env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB
    out += [(s.video_id, "subtopic") for s in pool_shorts(env, now, shorts)]
    out += [(r[0], "shorts") for r in read(
        shorts, "SELECT DISTINCT video_id FROM searched_videos WHERE day >= ?")]  # fmt: skip
    fill = env.get(FILL_DB_ENV) or FILL_DEFAULT_DB
    for (raw,) in read(fill, "SELECT videos FROM fill WHERE day >= ?"):
        try:
            rows = json.loads(raw)
        except ValueError:
            continue
        out += [(r["video_id"], "popular_fill") for r in rows if isinstance(r, dict) and
                r.get("video_id")]  # fmt: skip
    seen: set[str] = set()  # 같은 영상은 앞(우선순위 높은) 출처로 한 번만
    return [(v, s) for v, s in out if not (v in seen or seen.add(v))]


# ---------------------------------------------------------------- 계산 (수집기)

TermsFn = Callable[[], Awaitable[Mapping[str, str]]]


def _length(stat: VideoStat, max_seconds: int) -> str | None:
    if stat.duration_seconds is None:
        return None
    return "short" if stat.duration_seconds <= max_seconds else "long"


async def compute(
    env: Mapping[str, str],
    *,
    youtube: Any,
    llm: Any,
    store: ExplorerStore,
    cache: Any = None,
    terms: TermsFn | None = None,
    now: datetime | None = None,
    context_id: str | None = None,
) -> dict[str, Any]:
    """영상 풀 → (모자란 칸만) 보충 검색 → 분류·구독자 → 저장할 결과. 상태를 바꾸지 않는다."""
    from data_agent.youtube_store import DB_ENV as YT_DB_ENV
    from data_agent.youtube_store import DEFAULT_DB as YT_DEFAULT_DB

    now = now or datetime.now(UTC)
    today = _day(now)
    config = load_config(env)
    max_seconds = load_shorts_config(env).max_seconds
    since = now - timedelta(days=POOL_DAYS)
    yt_db = str(env.get(YT_DB_ENV) or YT_DEFAULT_DB)
    queries: list[str] = []
    limits: list[str] = []

    # 1. 영상 풀: 스냅샷 + 다른 저장분(다시 읽기)
    items = snapshot_pool(yt_db, since)
    queries.append(f"youtube snapshots published>={since.date()} videos={len(items)}")
    await _charts(youtube, store, config, now, today, queries, limits)
    await _refresh(env, youtube, store, config, items, now, today, queries, limits)
    for vid, (stat, source, at) in store.videos().items():
        if vid not in items or items[vid].at < at:
            items[vid] = _Item(stat, source, at)
    pool = _korean_recent(items, config, since)

    # 2. 분류 (저장 재사용, 새로 묻는 것은 한 번 상한·하루 몫까지. 나머지는 다음으로)
    pending = await _classify(pool, config, llm, cache, context_id, queries, store=store,
                              today=today)  # fmt: skip

    # 3. 모자란 칸만 보충 검색: 7일 검색을 먼저 하고 그 결과까지 분류한 뒤, 남은 횟수로 1일 보충.
    #    검색 결과는 분류 상한과 무관하게 분류한다 (검색 1번에 50개 이하)
    names = _Terms(terms)
    for window in ("7d", "3d", "1d"):
        await _search(env, youtube, store, config, pool, now, today, max_seconds, names, queries,
                      window)  # fmt: skip
        added = _korean_recent(
            {v: i for v, i in _stored_search(store).items() if v not in pool}, config, since
        )
        pool = {**pool, **added}
        await _classify(pool, dataclasses.replace(config, classify_max=10**6), llm, cache,
                        context_id, queries, only=list(added))  # fmt: skip
    searches = list(store.searched(today).values())

    # 4. 구독자 (조회수 많은 영상의 채널부터, 하루 상한)
    kept = [i for i in pool.values() if i.category in config.categories]
    await _subscribers(yt_db, youtube, store, config, kept, now, today, queries)

    videos = [_video(i, max_seconds) for i in kept]
    shortages = _shortages(videos, config, now, store.searched(today), max_seconds)
    # 칸이 모자란 카테고리(설정, 교육·책)는 7일 이전 숏츠 표본으로 채울 후보를 같이 저장한다
    videos += _backfill(store, config, cache, pool, now, max_seconds, yt_db)
    if pending:
        limits.append(f"분류 대기 {pending}개 (새로 묻는 수: 한 번 {config.classify_max}개·하루"
                      f" {config.classify_daily}개까지, 다음 계산·다음 날 이어서)")  # fmt: skip
    limits.append("조회수·좋아요는 마지막으로 읽은 값이다 (영상마다 `at`)")
    limits.append("검색 결과는 조회수 순이라 이미 뜬 영상끼리의 비교다")
    payload = {
        "computed_at": now.isoformat(),
        "categories": list(config.categories),
        "min_per_cell": config.min_per_cell,
        "max_videos": config.max_videos,
        "videos": [v.model_dump(mode="json") for v in videos],
        "shortages": shortages,
        "searches": [s.model_dump(mode="json") for s in searches],
        "limits": limits,
        "queries": queries,
    }
    if llm_failure.failed():  # LLM 호출이 실패한 계산은 저장하지 않는다 (다음 계산이 다시)
        payload["limits"].append("LLM 호출이 실패해 이 결과는 저장하지 않음 (다음 계산에서 다시)")
    else:
        store.save_result(now, payload)
    return payload


def _korean_recent(items: Mapping[str, _Item], config: ExplorerConfig,
                   since: datetime) -> dict[str, _Item]:  # fmt: skip
    return {
        vid: i
        for vid, i in items.items()
        if i.stat.published_at >= since
        and (not config.language or is_language(config.language, i.stat.title,
                                                i.stat.channel_title, i.stat.language))
    }  # fmt: skip


def _stored_search(store: ExplorerStore) -> dict[str, _Item]:
    return {vid: _Item(stat, src, at) for vid, (stat, src, at) in store.videos().items()
            if src == "search"}  # fmt: skip


class _Terms:
    """칸 → 검색어. 처음 필요할 때 한 번만 만든다 (지금 인기·도메인 프로필)."""

    def __init__(self, fn: TermsFn | None) -> None:
        self.fn = fn
        self.names: Mapping[str, str] | None = None

    async def get(self, cat: str) -> str | None:
        if self.names is None:
            try:
                self.names = dict(await self.fn()) if self.fn is not None else {}
            except Exception:  # 검색어를 못 만들면 YouTube 카테고리로만 찾는다
                self.names = {}
        return self.names.get(cat) or None


async def _charts(
    youtube: Any,
    store: ExplorerStore,
    config: ExplorerConfig,
    now: datetime,
    today: date,
    queries: list[str],
    limits: list[str],
) -> None:
    """카테고리별 인기 차트 (`chart` 표의 YouTube 카테고리마다 1단위). 최근에 받았으면 건너뜀.

    받은 영상은 `chart_cat` 출처로 남기고, 칸은 우리 분류기 결과로 정한다(YouTube 카테고리는 고르는
    데만 씀). 실패한 카테고리(그 지역에 차트가 없음 등)는 실패를 기록하고(출처 현황) 그 카테고리를
    검색(최근 3일)으로 대신 찾는다. 카테고리마다 하루 1번, 칸 보충 검색과 같은 하루 상한 안에서.
    """
    ids = list(dict.fromkeys(i for _, cats in config.chart for i in cats))
    if not ids or youtube is None or not hasattr(youtube, "popular_category"):
        return
    last = [at for _, src, at in store.videos().values() if src == "chart_cat"]
    if last and now - max(last) < timedelta(minutes=config.chart_minutes):
        return
    screens = {cid: [cat for cat, cs in config.chart if cid in cs] for cid in ids}
    got: list[VideoStat] = []
    failed: list[str] = []
    for cid in ids:
        try:
            got += await youtube.popular_category(cid)
            store.chart_ok(cid)
        except ConnectorUnavailable as error:
            failed.append(f"{cid} [{error.code}]")
            store.chart_failed(cid, screens[cid], error.code, str(error) or error.code, now)
            if error.code == "QUOTA_EXCEEDED":
                store.chart_fallback(
                    cid, today, "not_run", None, "YouTube 할당량 초과라 검색 안 함"
                )
                store.add_calls(today, "chart", 1)
                break
            note = await _chart_search(youtube, store, config, cid, now, today, queries)
            failed[-1] += f" → {note}"
        store.add_calls(today, "chart", 1)
    store.save_videos(now, got, "chart_cat")
    queries.append(f"youtube chart=mostPopular categories={len(ids)} videos={len(got)}")
    if failed:
        limits.append("카테고리 인기 차트를 받지 못함: " + ", ".join(failed))


async def _chart_search(
    youtube: Any,
    store: ExplorerStore,
    config: ExplorerConfig,
    cid: str,
    now: datetime,
    today: date,
    queries: list[str],
) -> str:
    """차트를 받지 못한 카테고리를 검색으로 대신 찾는다 (하루 1번·하루 상한 공유). 한 줄 결과."""
    key = f"{CHART_SEARCH}{cid}"
    if not hasattr(youtube, "search_recent"):
        why = "YouTube 커넥터에 검색이 없음"
        store.chart_fallback(cid, today, "not_run", None, why)
        return why
    got = store.reserve(today, key, "all", f"{CHART_SEARCH_DAYS}d", config.daily_searches)
    if got == "done":
        return "오늘 이미 검색으로 대신 찾음"
    if got == "cap":
        why = f"{CAP_REASON} (하루 {config.daily_searches}번)"
        store.chart_fallback(cid, today, "cap", None, why)
        return why
    label = f"YouTube 카테고리 {cid} (차트 대신)"
    before = getattr(youtube, "quota_used", None)
    try:
        found = await youtube.search_recent(
            now - timedelta(days=CHART_SEARCH_DAYS), category_id=cid,
            language=config.language or None,
        )  # fmt: skip
    except ConnectorUnavailable as error:
        store.add_calls(today, "search", 1, SEARCH_UNITS)
        why = f"검색 실패 [{error.code}]"
        store.finish_search(today, key, "unknown", 0, why, label)
        store.chart_fallback(cid, today, "failed", None, why)
        return why
    after = getattr(youtube, "quota_used", None)
    units = after - before if isinstance(before, int) and isinstance(after, int) else (
        SEARCH_UNITS + -(-len(found) // PAGE))  # fmt: skip
    store.add_calls(today, "search", 1, units)
    keep = [v for v in found if not config.language
            or is_language(config.language, v.title, v.channel_title, v.language)]  # fmt: skip
    store.save_videos(now, keep, "chart_search")
    store.finish_search(today, key, "known", len(keep), None, label)
    store.chart_fallback(cid, today, "searched", len(keep), None)
    queries.append(f"youtube search category={cid} (차트 대신) found={len(keep)}")
    return f"검색으로 대신 {len(keep)}개"


def chart_failures(env: Mapping[str, str]) -> list[YouTubeChartFailure]:
    """카테고리 인기 차트를 받지 못한 YouTube 카테고리 (출처 현황). 저장이 없으면 빈 목록."""
    path = env.get(DB_ENV) or DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return []
    store = ExplorerStore(path)
    try:
        return store.chart_failures()
    finally:
        store.close()


async def _refresh(
    env: Mapping[str, str],
    youtube: Any,
    store: ExplorerStore,
    config: ExplorerConfig,
    items: Mapping[str, _Item],
    now: datetime,
    today: date,
    queries: list[str],
    limits: list[str],
) -> None:
    """다른 저장분 영상을 videos.list로 다시 읽는다 (올린 시각·좋아요·채널 id). 하루 상한."""
    saved = store.videos()
    gone = store.missing()
    fresh = timedelta(hours=config.refresh_hours)
    todo = [
        (vid, src)
        for vid, src in other_ids(env, now)
        if vid not in items and vid not in gone
        and (vid not in saved or now - saved[vid][2] >= fresh)
        # 이미 읽은 영상이 7일보다 오래됐으면 다시 읽지 않는다
        and not (vid in saved and saved[vid][0].published_at < now - timedelta(days=POOL_DAYS))
    ]  # fmt: skip
    if not todo:
        return
    if youtube is None or not hasattr(youtube, "videos"):
        limits.append("YouTube 커넥터가 없어 다른 저장분 영상을 다시 읽지 않음")
        return
    left = max(0, config.video_calls - store.calls(today, "videos"))
    take = todo[: left * PAGE]
    if len(take) < len(todo):
        limits.append(f"다시 읽기 하루 상한({config.video_calls}번)으로 {len(todo) - len(take)}개는"
                      " 다음 날")  # fmt: skip
    src = dict(take)
    for start in range(0, len(take), PAGE):
        chunk = [vid for vid, _ in take[start : start + PAGE]]
        try:
            got = await youtube.videos(chunk)
        except ConnectorUnavailable as error:
            limits.append(f"다시 읽기 실패 [{error.code}]")
            store.add_calls(today, "videos", 1)
            break
        store.add_calls(today, "videos", 1)
        for kind in ("shorts", "subtopic", "popular_fill"):
            mine = [v for v in got if src.get(v.video_id) == kind]
            if mine:
                store.save_videos(now, mine, kind)
        store.save_missing(now, [v for v in chunk if v not in {g.video_id for g in got}])
    queries.append(f"youtube videos.list refresh ids={len(take)}")


async def _classify(
    pool: Mapping[str, _Item],
    config: ExplorerConfig,
    llm: Any,
    cache: Any,
    context_id: str | None,
    queries: list[str],
    only: Sequence[str] | None = None,
    *,
    store: ExplorerStore | None = None,
    today: date | None = None,
) -> int:
    """풀 영상을 분류한다. 저장된 것은 그대로, 새로 묻는 것은 조회수 순으로 상한까지. 남은 수.

    새로 묻는 수 = min(한 번 상한 `classify_max`, 오늘 남은 몫 `classify_daily` − 오늘 물은 수).
    분류 대기 영상은 하루 몫씩 나눠 다음 계산·다음 날로 넘긴다.
    """
    ids = list(pool) if only is None else [v for v in only if v in pool]
    if not ids:
        return 0
    stored = [v for v in ids if cache is not None and _has(cache, v)]
    new = sorted((v for v in ids if v not in set(stored)),
                 key=lambda v: -(pool[v].stat.view_count or 0))  # fmt: skip
    room = config.classify_max
    if store is not None and today is not None:
        room = min(room, max(0, config.classify_daily - store.calls(today, "classify")))
    take = new[:room]
    ask = stored + take
    found = await classify_videos(
        [VideoInput(v, pool[v].stat.title, pool[v].stat.channel_title, pool[v].stat.tags)
         for v in ask], llm, cache=cache, context_id=context_id,
    ) if ask else {}  # fmt: skip
    if store is not None and today is not None and take:
        store.add_calls(today, "classify", len(take), 0)  # 물은 영상 수 (YouTube 단위 아님)
    queries.append(f"video classify stored={len(stored)} new={len(take)}")
    for v in ask:
        r = found.get(v)
        if r is None or r.category == UNKNOWN:
            continue
        pool[v].category = r.category
        pool[v].ad = caution.judge(r.category, real_entity=r.real_entity, is_person=r.is_person,
                                   names=r.entity_names, basis="영상 분류")  # fmt: skip
    return max(0, len(new) - len(take))


def _has(cache: Any, video_id: str) -> bool:
    hit = cache.get(VIDEO_KEY + video_id)
    return hit is not None and hit.real_entity is not None


def _cell_count(pool: Mapping[str, _Item], cat: str, length: str, days: int, now: datetime,
                max_seconds: int) -> int:  # fmt: skip
    since = now - timedelta(days=days)
    return sum(
        1 for i in pool.values()
        if i.category == cat and _length(i.stat, max_seconds) == length
        and i.stat.published_at >= since
    )  # fmt: skip


async def _search(
    env: Mapping[str, str],
    youtube: Any,
    store: ExplorerStore,
    config: ExplorerConfig,
    pool: Mapping[str, _Item],
    now: datetime,
    today: date,
    max_seconds: int,
    terms: _Terms,
    queries: list[str],
    window: str,
) -> None:
    """모자란 칸만 보충 검색한다 (카테고리 × 길이 × 구간마다 하루 1번, 하루 상한 공유)."""
    from data_agent.skills.trend_popular import load_config as load_popular

    done = store.searched(today)
    fill = dict(load_popular(env).fill_youtube_categories)
    days = PERIOD_DAYS[window]
    cells = [(cat, length) for cat in config.categories for length in LENGTHS]
    for cat, length in cells:
        key = (cat, length, window)
        if key in done and done[key].status != "skipped":
            continue  # 오늘 이미 검색함 (하루 1번)
        if _cell_count(pool, cat, length, days, now, max_seconds) >= config.min_per_cell:
            continue
        skip = None
        query = await terms.get(cat)
        category_id = None if query else (fill.get(cat) or (None,))[0]
        if not query and not category_id:
            skip = "검색어(지금 인기 주제·씨앗)도 YouTube 카테고리도 없음"
        else:  # 예약: 다른 계산과 겹쳐도 칸마다 한 번, 하루 상한도 여기서 센다
            got = store.reserve(today, cat, length, window, config.daily_searches)
            if got == "done":
                continue
            if got == "cap":
                skip = f"{CAP_REASON} (하루 {config.daily_searches}번)"
        if skip is not None:
            store.save_search(today, ExplorerSearch.model_validate({
                "category": cat, "length": length, "window": window, "status": "skipped",
                "reason": skip, "day": today}))  # fmt: skip
            continue
        s = await _one_search(youtube, store, config, cat, length, window, days, query,
                              category_id, now, today, max_seconds)  # fmt: skip
        store.save_search(today, s)
        queries.append(f"youtube search {cat}/{length}/{window} found={s.found}")


async def _one_search(
    youtube: Any,
    store: ExplorerStore,
    config: ExplorerConfig,
    cat: str,
    length: str,
    window: str,
    days: int,
    query: str | None,
    category_id: str | None,
    now: datetime,
    today: date,
    max_seconds: int,
) -> ExplorerSearch:
    label = query or (f"YouTube 카테고리 {category_id}" if category_id else None)
    base = {"category": cat, "length": length, "window": window, "day": today, "query": label}

    def result(**kw: Any) -> ExplorerSearch:
        return ExplorerSearch.model_validate({**base, **kw})

    if youtube is None or not hasattr(youtube, "search_recent"):
        return result(status="unknown", reason="YouTube 커넥터가 없어 검색하지 않음")
    before = getattr(youtube, "quota_used", None)
    try:
        got = await youtube.search_recent(
            now - timedelta(days=days), query=query, category_id=category_id,
            short=length == "short", language=config.language or None,
        )  # fmt: skip
    except ConnectorUnavailable as error:
        store.add_calls(today, "search", 1, SEARCH_UNITS)
        return result(status="unknown", reason=f"검색 실패 [{error.code}]")
    after = getattr(youtube, "quota_used", None)
    units = after - before if isinstance(before, int) and isinstance(after, int) else (
        SEARCH_UNITS + -(-len(got) // PAGE))  # fmt: skip
    store.add_calls(today, "search", 1, units)
    keep = [
        v for v in got
        if _length(v, max_seconds) == length
        and (not config.language
             or is_language(config.language, v.title, v.channel_title, v.language))
    ]  # fmt: skip
    store.save_videos(now, keep, "search")
    return result(status="known", found=len(keep))


async def _subscribers(
    yt_db: str,
    youtube: Any,
    store: ExplorerStore,
    config: ExplorerConfig,
    kept: Sequence[_Item],
    now: datetime,
    today: date,
    queries: list[str],
) -> None:
    from data_agent.skills import trend_evidence as ev

    order = sorted(kept, key=lambda i: -(i.stat.view_count or 0))
    channels = list(dict.fromkeys(i.stat.channel_id for i in order if i.stat.channel_id))
    room = max(0, config.channel_calls - store.calls(today, "channels")) * PAGE
    known = ev.channel_subscribers(yt_db, channels, now - ev.CHANNEL_FRESH)
    ask = [c for c in channels if c not in known][:room]
    subs, why = await ev.subscribers(
        yt_db, [*known, *ask], youtube=youtube, store=store, cap=config.channel_calls,
        now=now, today=today, queries=queries, kind="channels",
    ) if channels else ({}, None)  # fmt: skip
    for i in kept:
        cid = i.stat.channel_id
        if not cid:
            i.subscriber_reason = "채널 모름"
        elif cid in subs and subs[cid] is None:
            i.subscriber_reason = "채널이 구독자 수를 숨김"
        elif cid not in subs:
            i.subscriber_reason = why or f"구독자 받기 하루 상한({config.channel_calls}번) 밖"
        else:
            i.subscribers = subs[cid]


def _backfill(
    store: ExplorerStore,
    config: ExplorerConfig,
    cache: Any,
    pool: Mapping[str, _Item],
    now: datetime,
    max_seconds: int,
    yt_db: str,
) -> list[ExplorerVideo]:
    """`backfill_categories`의 숏 칸을 채울 7일 이전 숏츠 표본 (기간 무관, 최근 올린 것부터).

    숏츠 통계 표본·하위 주제 숏츠 중 다시 읽어 올린 시각을 아는 것만. 분류는 저장된 것만 쓴다
    (영상 분류, 없으면 숏츠 통계가 제목으로 분류한 것). 새로 묻지 않는다. 구독자는 저장된 값만.
    카테고리마다 최소 수만큼 저장하고, 화면 요청에서 모자란 칸에만 붙인다(`before_period`).
    """
    from data_agent.skills import trend_evidence as ev

    if not config.backfill_categories or cache is None:
        return []
    since = now - timedelta(days=POOL_DAYS)
    by_cat: dict[str, list[_Item]] = {}
    for vid, (stat, src, at) in store.videos().items():
        if vid in pool or src not in ("shorts", "subtopic") or stat.published_at >= since:
            continue
        if _length(stat, max_seconds) != "short":
            continue
        if config.language and not is_language(config.language, stat.title,
                                               stat.channel_title, stat.language):  # fmt: skip
            continue
        r = cache.get(VIDEO_KEY + vid) or (cache.get(stat.title) if stat.title else None)
        if r is None or r.category not in config.backfill_categories:
            continue
        item = _Item(stat, src, at, category=r.category)
        item.ad = caution.judge(r.category, real_entity=r.real_entity, is_person=r.is_person,
                                names=r.entity_names, basis="저장된 분류")  # fmt: skip
        by_cat.setdefault(r.category, []).append(item)
    out: list[ExplorerVideo] = []
    for items in by_cat.values():
        items.sort(key=lambda i: i.stat.published_at, reverse=True)
        take = items[: config.min_per_cell]
        subs = ev.channel_subscribers(yt_db, [i.stat.channel_id for i in take if i.stat.channel_id],
                                      now - ev.CHANNEL_FRESH)  # fmt: skip
        for i in take:
            cid = i.stat.channel_id
            if cid in subs and subs[cid] is not None:
                i.subscribers = subs[cid]
            else:
                i.subscriber_reason = "7일 이전 표본이라 구독자를 새로 받지 않음"
            out.append(_video(i, max_seconds).model_copy(update={"before_period": True}))
    return out


def _video(i: _Item, max_seconds: int) -> ExplorerVideo:
    s = i.stat
    short = _length(s, max_seconds) == "short"
    per_sub = (
        round(s.view_count / i.subscribers, 2)
        if s.view_count is not None and i.subscribers else None
    )  # fmt: skip
    sub_why = i.subscriber_reason
    if per_sub is None and sub_why is None:
        sub_why = "조회수를 몰라 셀 수 없음" if s.view_count is None else "구독자 수 0"
    ad = i.ad or caution.judge(None, real_entity=None, is_person=None)
    return ExplorerVideo(
        video_id=s.video_id, title=s.title or None, channel_title=s.channel_title or None,
        channel_id=s.channel_id or None, thumbnail_url=s.thumbnail_url,
        video_url=video_url(s.video_id, short=short), duration_seconds=s.duration_seconds,
        published_at=s.published_at, views=s.view_count,
        views_reason=None if s.view_count is not None else "조회수 숨김",
        likes=s.like_count, likes_reason=None if s.like_count is not None else "좋아요 숨김",
        channel_subscribers=i.subscribers, views_per_subscriber=per_sub,
        subscriber_reason=None if per_sub is not None else sub_why,
        category=i.category,  # type: ignore[arg-type]
        ad_usable=ad.ad_usable, caution_types=list(ad.types), caution_reason=ad.reason,
        source=i.source, at=i.at,
    )  # fmt: skip


def _in_cell(v: ExplorerVideo, cat: str, period: str, length: str, now: datetime,
             max_seconds: int) -> bool:  # fmt: skip
    if v.category != cat or v.duration_seconds is None or v.published_at is None:
        return False
    is_short = v.duration_seconds <= max_seconds
    return (is_short == (length == "short")
            and v.published_at >= now - timedelta(days=PERIOD_DAYS[period]))  # fmt: skip


def _shortages(videos: Sequence[ExplorerVideo], config: ExplorerConfig, now: datetime,
               searched: Mapping[tuple[str, str, str], ExplorerSearch],
               max_seconds: int) -> dict[str, str]:  # fmt: skip
    """칸마다 모자라면 이유 ("이 기간 한국 영상 N개", 검색 상한이면 덧붙임)."""
    out: dict[str, str] = {}
    for cat in config.categories:
        for length in LENGTHS:
            for period in PERIOD_DAYS:
                n = sum(_in_cell(v, cat, period, length, now, max_seconds) for v in videos)
                if n >= config.min_per_cell:
                    continue
                why = f"이 기간 한국 영상 {n}개"
                window = period  # 칸 기간과 같은 구간의 보충 검색 (1d·3d·7d)
                s = searched.get((cat, length, window))
                if s is not None and s.status == "skipped":
                    why += f" ({s.reason})"
                elif s is not None and s.status == "unknown":
                    why += f" (보충 검색: {s.reason})"
                out[f"{cat}|{period}|{length}"] = why
    return out


# ---------------------------------------------------------------- 화면 요청 (읽기·정렬만)


def _sort_key(sort: str) -> Callable[[ExplorerVideo], tuple[Any, ...]]:
    if sort == "views_per_subscriber":
        return lambda v: (v.views_per_subscriber is None, -(v.views_per_subscriber or 0),
                          -(v.views or 0))  # fmt: skip
    if sort == "likes":
        return lambda v: (v.likes is None, -(v.likes or 0), -(v.views or 0))
    return lambda v: (v.views is None, -(v.views or 0))


def view(
    payload: Mapping[str, Any] | None,
    request: VideoExplorerRequest,
    *,
    env: Mapping[str, str],
    store: ExplorerStore,
    now: datetime | None = None,
) -> tuple[VideoExplorerOutput, Evidence]:
    """저장된 결과에서 요청 칸만 골라 정렬한다 (외부 호출 없음)."""
    now = now or datetime.now(UTC)
    config = load_config(env)
    max_seconds = load_shorts_config(env).max_seconds
    out = VideoExplorerOutput(category=request.category, period=request.period,
                              length=request.length, sort=request.sort,
                              categories=list(config.categories),
                              min_per_cell=config.min_per_cell,
                              search_cap=config.daily_searches)  # fmt: skip
    today = _day(now)
    out.searches_today = store.searches_on(today)
    out.quota_used_today = store.units(today)
    if payload is None:
        out.reason = "아직 미리 계산한 결과가 없음 (data 수집기)"
        out.summary = f"확인할 수 없음: {out.reason}"
        return out, Evidence(queries=[], data_summary=out.summary, collected_at=now)
    computed = datetime.fromisoformat(payload["computed_at"])
    out.computed_at = computed
    out.categories = list(payload.get("categories") or config.categories)
    out.min_per_cell = int(payload.get("min_per_cell", config.min_per_cell))
    limit = int(payload.get("max_videos", config.max_videos))
    videos = [ExplorerVideo.model_validate(v) for v in payload.get("videos", [])]
    shortages = payload.get("shortages", {})
    cats = [request.category] if request.category else out.categories
    for cat in cats:
        mine = sorted((v for v in videos if not v.before_period and
                       _in_cell(v, cat, request.period, request.length, computed, max_seconds)),
                      key=_sort_key(request.sort))  # fmt: skip
        why = (shortages.get(f"{cat}|{request.period}|{request.length}")
               if len(mine) < out.min_per_cell else None)  # fmt: skip
        extra: list[ExplorerVideo] = []
        if len(mine) < out.min_per_cell and request.length == "short":
            # 빈 칸 대신 7일 이전 숏츠 표본으로 (최근 올린 것부터, "7일 이전" 표시)
            extra = sorted((v for v in videos if v.before_period and v.category == cat),
                           key=lambda v: v.published_at or computed,
                           reverse=True)[: out.min_per_cell - len(mine)]  # fmt: skip
            if extra:
                why = f"{why or ''} · 7일 이전 숏츠 표본 {len(extra)}개로 채움".lstrip(" ·")
        out.cells.append(ExplorerCell(
            category=cat, period=request.period, length=request.length, count=len(mine),
            shortage_reason=why, videos=(mine + extra)[:limit],
        ))  # fmt: skip
    out.searches = [ExplorerSearch.model_validate(s) for s in payload.get("searches", [])]
    out.limits = list(payload.get("limits", []))
    out.status = "known"
    filled = sum(c.count >= out.min_per_cell for c in out.cells)
    out.summary = (
        f"{request.period}·{'숏' if request.length == 'short' else '롱'} 영상: 카테고리"
        f" {len(out.cells)}개 중 {filled}개가 {out.min_per_cell}개 이상."
        f" 오늘 보충 검색 {out.searches_today}번(상한 {out.search_cap}),"
        f" 할당량 {out.quota_used_today}단위."
    )
    evidence = Evidence(
        queries=[f"video_explorer stored computed_at={computed.isoformat()}"],
        data_summary=out.summary, collected_at=computed,
    )  # fmt: skip
    return out, evidence
