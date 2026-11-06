"""새 숏츠 추적: 같은 형식 비교 기준(#763 시간 맞춤) 표본을 게시 직후부터 모은다.

수집기(1시간마다)가 차트에 뜬 뒤에야 숏츠를 봐서 1h·1d·3d·7d 창에 걸리는 표본이 없었다.
그래서 새로 올라온 숏츠를 찾아 게시 직후부터 매시간 잰다. 수집기 루프(`youtube_collect.run`)가
1시간마다 `run_tick`을 부른다 (run_all.sh 일정 그대로, 따로 돌리지 않음).

- 대상 형식(`target_formats`): 형식 분류 목록 중 지금 브리프 인사이트·캠페인 영상에서 쓰는 형식
  상위 5개 (모자라면 형식 분류 저장분에 많은 형식으로 채움, 기타·unknown 제외)
- 찾기: 형식마다 6시간에 1번 `search.list`(짧은 영상, 최신순, KR, ko, 지금 − 6시간 이후 게시,
  q = 형식 키워드). 60초 넘는 영상은 뺀다. 형식 판정은 기존 형식 분류 그대로(`classify`, 기본은
  shorts_stats의 LLM 분류, 저장 재사용). 형식당 추적 상한 40개, 전체 200개 (새로 찾은 것 앞부터).
  그 형식 칸이 다 찼으면 검색하지 않는다 (넣지 못할 결과에 검색 100단위를 쓰지 않음)
- 추적: 추적 목록을 매시간 `videos.list`(50개씩)로 재고 기존 스냅샷 표(`snapshots`)에
  source "fresh_track"으로 저장. 게시 후 8일이 지나면 추적 종료
- 비용: 기존 YouTube 장부(같은 하루 전체 상한 8,000)에 부른 곳 "fresh_shorts"로 적힌다. 이 작업
  몫 `DATA_FRESH_SHORTS_DAILY_UNITS`(기본 1,200)은 장부가 먼저 떼어 두는 예약 몫이다(다른 기능은
  나머지 6,800 안에서). 찾기는 쓴 단위 + 101이 몫 − 추적 몫(100) 이하일 때만, 추적은 몫 안이면
  계속 (몫에 가까우면 찾기를 먼저 멈춤)
"""

from __future__ import annotations

import inspect
import json
import math
import os
import sqlite3
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import FreshTrackStatus
from data_agent import youtube_quota
from data_agent.connectors import ConnectorUnavailable

CALLER = "fresh_shorts"
SOURCE = "fresh_track"
DB_ENV = "DATA_FRESH_SHORTS_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "fresh_shorts.db"  # backend/.data
CAP_ENV = youtube_quota.RESERVE_ENV
DEFAULT_CAP = youtube_quota.DEFAULT_RESERVE  # 1,200 (장부 예약 몫과 같은 값)
FIND_EVERY = timedelta(hours=6)
LOOKBACK = timedelta(hours=6)
TRACK_FOR = timedelta(days=8)
MAX_SECONDS = 60
PER_FORMAT = 40
TOTAL = 200
TOP_FORMATS = 5
RESERVE = 100  # 찾기를 멈추고 남겨 둘 추적 몫
SEARCH_UNITS = youtube_quota.SEARCH_UNITS
SKIP_FORMATS = frozenset({"기타", "unknown"})

Classify = Callable[[Sequence[Any]], Mapping[str, str] | Awaitable[Mapping[str, str]]]


def daily_cap(env: Mapping[str, str]) -> int:
    """이 작업 하루 몫 = 장부 예약 몫."""
    return youtube_quota.reserve(env)


def day_of(now: datetime) -> date:
    return now.astimezone(youtube_quota.KST).date()


def units_today(now: datetime) -> int:
    """이 작업이 오늘(한국 날짜) 쓴 YouTube 단위 (장부의 부른 곳 fresh_shorts)."""
    return sum(u for c, u, _ in youtube_quota.LEDGER.rows(day_of(now)) if c == CALLER)


def query_for(fmt: str) -> str:
    """형식 → 검색어 (형식 이름의 가운뎃점은 띄어쓰기로)."""
    return fmt.replace("·", " ").strip()


class FreshStore:
    """추적 목록과 형식마다 마지막 찾기 시각."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS tracked (video_id TEXT PRIMARY KEY,"
                " format TEXT NOT NULL, published_at TEXT NOT NULL, found_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS finds (format TEXT PRIMARY KEY, at TEXT NOT NULL);"
            )

    def close(self) -> None:
        self._conn.close()

    def has(self, video_id: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM tracked WHERE video_id = ?", (video_id,))
            return row.fetchone() is not None

    def add(self, video_id: str, fmt: str, published_at: datetime, now: datetime) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO tracked VALUES (?,?,?,?)",
                (video_id, fmt, published_at.astimezone(UTC).isoformat(), now.isoformat()),
            )

    def live(self, now: datetime) -> list[tuple[str, str]]:
        """지금 추적 중 (게시 8일 안): (영상 id, 형식), 찾은 순."""
        since = (now - TRACK_FOR).astimezone(UTC).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT video_id, format FROM tracked WHERE published_at >= ? ORDER BY found_at",
                (since,),
            ).fetchall()
        return [(str(v), str(f)) for v, f in rows]

    def last_find(self, fmt: str) -> datetime | None:
        with self._lock:
            row = self._conn.execute("SELECT at FROM finds WHERE format = ?", (fmt,)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def set_find(self, fmt: str, at: datetime) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO finds VALUES (?, ?)", (fmt, at.isoformat()))


def store_of(env: Mapping[str, str]) -> FreshStore:
    return FreshStore(env.get(DB_ENV) or DEFAULT_DB)


# ---------------------------------------------------------------- 대상 형식


def _brief_formats(env: Mapping[str, str]) -> list[str]:
    from data_agent.skills.trend_insight_brief import DB_ENV as BRIEF_ENV
    from data_agent.skills.trend_insight_brief import DEFAULT_DB as BRIEF_DB

    path = Path(env.get(BRIEF_ENV) or BRIEF_DB)
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        row = conn.execute(
            "SELECT output FROM briefs ORDER BY day DESC, created_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        conn.close()
    if not row:
        return []
    return [
        i.get("shorts_format")
        for i in json.loads(row[0]).get("insights", [])
        if i.get("shorts_format")
    ]


def _campaign_formats(env: Mapping[str, str]) -> list[str]:
    from data_agent.skills import video_performance as vp

    path = env.get(vp.DB_ENV) or vp.DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return []
    store = vp.VideoStore(path)
    try:
        return [v.shorts_format for v in store.videos(include_test=False) if v.shorts_format]
    finally:
        store.close()


def _stored_formats(env: Mapping[str, str]) -> list[str]:
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB

    path = Path(env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB)
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        return [str(f) for (f,) in conn.execute("SELECT format FROM formats")]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def target_formats(env: Mapping[str, str]) -> list[str]:
    """지금 브리프·캠페인에서 쓰는 형식 상위 5개, 모자라면 형식 분류 저장분에 많은 형식으로."""
    from data_agent.skills.shorts_stats import SHORTS_FORMATS

    allowed = [f for f in SHORTS_FORMATS if f not in SKIP_FORMATS]
    used = Counter(f for f in [*_brief_formats(env), *_campaign_formats(env)] if f in allowed)
    out = [f for f, _ in sorted(used.items(), key=lambda kv: (-kv[1], allowed.index(kv[0])))]
    stored = Counter(f for f in _stored_formats(env) if f in allowed and f not in out)
    out += [f for f, _ in sorted(stored.items(), key=lambda kv: (-kv[1], allowed.index(kv[0])))]
    out += [f for f in allowed if f not in out]
    return out[:TOP_FORMATS]


# ---------------------------------------------------------------- 한 번 (1시간마다)


@dataclass
class TickResult:
    searches: int = 0  # 이번에 한 찾기 (검색 호출)
    skipped_searches: int = 0  # 하루 상한으로 건너뛴 찾기
    full_skips: int = 0  # 그 형식 추적 칸(40개)·전체(200개)가 다 차서 건너뛴 찾기
    found: int = 0  # 검색 결과 중 60초 이하 새 영상
    added: int = 0  # 추적 목록에 넣은 수
    tracked_calls: int = 0  # 추적 videos.list 호출 수 (50개씩)
    snapshots: int = 0  # 저장한 스냅샷 줄
    tracking: int = 0  # 지금 추적 중
    problems: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (
            f"[fresh] 찾기 {self.searches}번(상한으로 건너뜀 {self.skipped_searches}), 새로 추적"
            f" {self.added}개, 추적 중 {self.tracking}개, 스냅샷 {self.snapshots}줄"
            + (f", {'; '.join(self.problems)}" if self.problems else "")
        )


async def _llm_classify(stats: Sequence[Any], env: Mapping[str, str]) -> dict[str, str]:
    """기존 형식 분류 그대로 (저장분 재사용, 없으면 LLM으로 묶어서)."""
    from data_agent.handlers import get_llm
    from data_agent.skills.shorts_stats import (
        SHORTS_DB_ENV,
        SHORTS_DEFAULT_DB,
        UNKNOWN,
        ShortsStore,
        _Short,
        classify_formats,
    )

    store = ShortsStore(env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB)
    try:
        ids = [s.video_id for s in stats]
        known = {k: f for k, (f, _) in store.formats(ids, datetime(2000, 1, 1, tzinfo=UTC)).items()}
        todo = [
            _Short(
                video_id=s.video_id,
                title=s.title,
                channel_title=s.channel_title or None,
                views=s.view_count,
                views_per_hour=None,
                tags=tuple(s.tags),
                source="search",
            )
            for s in stats
            if s.video_id not in known
        ]
        if todo:
            got = await classify_formats(todo, get_llm(), None)
            store.save_formats(datetime.now(UTC), got)
            known |= {k: f for k, (f, _) in got.items() if f != UNKNOWN}
        return known
    finally:
        store.close()


async def run_tick(
    connector: Any,
    env: Mapping[str, str],
    now: datetime | None = None,
    *,
    formats: Sequence[str] | None = None,
    classify: Classify | None = None,
) -> TickResult:
    """찾기(형식마다 6시간에 1번) → 추적(매시간). 실패해도 다음 단계는 한다."""
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB, ShortsStore
    from data_agent.youtube_store import DB_ENV as YT_ENV
    from data_agent.youtube_store import DEFAULT_DB as YT_DB
    from data_agent.youtube_store import Row, SnapshotStore

    now = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    formats = list(formats) if formats is not None else target_formats(env)
    cap = daily_cap(env)
    res = TickResult()
    store = store_of(env)
    try:
        live = store.live(now)
        for fmt in formats:
            last = store.last_find(fmt)
            if last is not None and now - last < FIND_EVERY:
                continue
            store.set_find(fmt, now)  # 건너뛰어도 다음 시도는 6시간 뒤 (매시간 다시 보지 않음)
            full = Counter(f for _, f in live)[fmt] >= PER_FORMAT or len(live) >= TOTAL
            if full:  # 추적 칸이 다 찼으면 찾아도 넣지 못하니 검색 비용을 쓰지 않는다
                res.full_skips += 1
                continue
            if units_today(now) + SEARCH_UNITS + 1 > cap - RESERVE:
                res.skipped_searches += 1
                continue
            try:
                stats = await connector.search_fresh_shorts(
                    query_for(fmt), now - LOOKBACK, region="KR", language="ko"
                )
            except ConnectorUnavailable as error:
                res.problems.append(f"{fmt} 찾기 실패 [{error.code}]")
                continue
            res.searches += 1
            fresh = [
                s
                for s in stats
                if s.duration_seconds is not None
                and s.duration_seconds <= MAX_SECONDS
                and not store.has(s.video_id)
            ]
            res.found += len(fresh)
            if not fresh:
                continue
            kinds = (classify or (lambda st: _llm_classify(st, env)))(fresh)
            if inspect.isawaitable(kinds):
                kinds = await kinds
            shorts = ShortsStore(env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB)
            try:  # 형식 판정을 기준 계산(video_baseline)이 읽는 저장분에 남긴다
                shorts.save_formats(
                    now,
                    {s.video_id: (kinds[s.video_id], None) for s in fresh if s.video_id in kinds},
                )
            finally:
                shorts.close()
            counts = Counter(f for _, f in live)
            for s in fresh:
                f = kinds.get(s.video_id)
                if f not in formats or counts[f] >= PER_FORMAT or len(live) >= TOTAL:
                    continue
                store.add(s.video_id, f, s.published_at, now)
                live.append((s.video_id, f))
                counts[f] += 1
                res.added += 1
        live = store.live(now)
        res.tracking = len(live)
        calls = math.ceil(len(live) / 50)
        if live and units_today(now) + calls <= cap:
            try:
                stats = await connector.videos([v for v, _ in live])
                res.tracked_calls = calls
                keep = [
                    s
                    for s in stats
                    if s.duration_seconds is None or s.duration_seconds <= MAX_SECONDS
                ]
                snaps = SnapshotStore(env.get(YT_ENV) or YT_DB)
                try:
                    res.snapshots = snaps.save(now, [Row(s, (SOURCE,)) for s in keep])
                finally:
                    snaps.close()
            except ConnectorUnavailable as error:
                res.problems.append(f"추적 실패 [{error.code}]")
    finally:
        store.close()
    return res


def status(env: Mapping[str, str] | None = None, now: datetime | None = None) -> FreshTrackStatus:
    """수집기 상태 화면: "새 숏츠 추적: 오늘 N유닛 / 1,200, 추적 중 N개"."""
    env = os.environ if env is None else env
    now = now or datetime.now(UTC)
    path = env.get(DB_ENV) or DEFAULT_DB
    tracking = 0
    if str(path) == ":memory:" or Path(path).is_file():
        store = FreshStore(path)
        try:
            tracking = len(store.live(now))
        finally:
            store.close()
    used, cap = units_today(now), daily_cap(env)
    return FreshTrackStatus(
        units_today=used,
        unit_cap=cap,
        tracking=tracking,
        formats=target_formats(env),
        find_stopped=used + SEARCH_UNITS + 1 > cap - RESERVE,
        line=f"새 숏츠 추적: 오늘 {used:,}유닛 / {cap:,}, 추적 중 {tracking}개",
    )
