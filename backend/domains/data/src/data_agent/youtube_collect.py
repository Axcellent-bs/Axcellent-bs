"""YouTube 영상 통계 스냅샷 수집 (한 번 또는 `--every 초`마다). run_all.sh가 data와 같이 켠다.

새 스냅샷을 저장하면 바로 트렌드 감지 탭 결과를 미리 계산해 저장한다(`trend_precompute`).
같은 DB에 수집기가 둘 돌지 않게 DB 옆 `youtube_collect.lock`을 잡는다(잡혀 있으면 바로 끝냄).

    cd backend
    uv run python -m data_agent.youtube_collect              # 한 번
    uv run python -m data_agent.youtube_collect --every 3600 # 1시간마다 (Ctrl+C로 멈춤)
    uv run python -m data_agent.youtube_collect --titles     # 제목 없는 인기 영상 제목만 채우기
    uv run python -m data_agent.youtube_collect --details    # 채널 이름·길이 없는 인기 영상 채우기

`--titles`: 제목 저장(`video_titles`) 전에 쌓인 인기 영상 중 제목이 없는 것만 `videos.list`로 받아
제목을 채운다(50개당 1단위, 최대 `MAX_TITLE_BACKFILL`개). 스냅샷 표는 건드리지 않는다.

한 번 실행하면:
1. 인기 목록(매번): mostPopular 4쪽 = 200개, 4단위
2. 추적 중인 영상(매번): 인기 목록·검색에서 `[tracking] drop_after_hours`(기본 48)시간 안에 봤는데
   이번 인기 목록에 없는 영상의 지금 값. 추적으로만 본 것은 기간을 늘리지 않는다(그래서 빠진다).
   브리프에 쓰는 영상은 유지. 50개당 1단위, 최대 `[tracking] max_tracked`(기본 500)개.
   로그에 "추적에서 뺀 수"(지난 실행엔 있었고 이번에 뺀 영상)
5. 보관: `[tracking] retention_days`(기본 30)일이 지난 스냅샷 줄은 한국 날짜 하루 1번 지운다
3. 회사 키워드 검색(한국 날짜 하루 1번): 키워드마다 검색 100단위 + 통계 1단위
4. 저장(`youtube_store`): 같은 시각 같은 영상은 한 줄. 실행 기록에 쓴 할당량 단위를 남긴다

- 키: `YOUTUBE_API_KEY`. 환경변수가 먼저고, 없으면 `backend/.env`에서 읽는다(값은 출력하지 않는다)
- 회사 키워드: `DATA_YOUTUBE_CONFIG`(회사 폴더 `youtube.toml`, `scripts/tenant_env.py`가 정함).
  없으면 `config/tenants/<AX_TENANT, 기본 catbooks>/youtube.toml`. 코드에 키워드를 두지 않는다
- 할당량 초과(`QUOTA_EXCEEDED`)가 나면 그 실행의 남은 호출을 멈춘다. 받은 것까지는 저장한다
- 판정·태그 정리는 하지 않는다(스냅샷이 쌓인 뒤 별도 PR)
- 제목은 스냅샷 표가 아니라 `video_titles`에 영상마다 한 줄로 저장한다(`youtube_store`)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sqlite3
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from data_agent import collector_lock, youtube_quota
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat, YouTubeConnector
from data_agent.stage_timing import install_sqlite_timer
from data_agent.trend_precompute import precompute
from data_agent.youtube_store import DB_ENV, DEFAULT_DB, KST, Row, SnapshotStore

BACKEND = Path(__file__).resolve().parents[4]
CONFIG_ENV = "DATA_YOUTUBE_CONFIG"
MAX_TRACKED = 500  # 한 번에 추적하는 영상 수 상한 (10단위)
SEARCH_DAYS = 7  # 회사 키워드 검색은 최근 게시 영상만
MIN_EVERY_SECONDS = 600  # --every 하한 (할당량 보호)
MAX_TITLE_BACKFILL = 1000  # --titles 한 번에 채우는 영상 수 상한 (20단위)


@dataclass(frozen=True)
class YouTubeConfig:
    region: str = "KR"
    queries: tuple[str, ...] = ()
    # [tracking]: 인기·검색에서 빠진 지 이 시간이 지나면 추적에서 뺀다 (브리프에 쓰는 영상은 유지)
    drop_after_hours: int = 48
    max_tracked: int = MAX_TRACKED  # 한 번에 추적하는 영상 수 상한
    retention_days: int = 30  # 스냅샷 보관 기간. 지난 줄은 한국 날짜 하루 1번 지운다 (0 = 안 지움)


@dataclass
class RunResult:
    collected_at: datetime
    quota_units: int = 0
    popular: int = 0
    tracked: int = 0
    searched_videos: int = 0
    searched: bool = False
    search_skipped: str = ""  # 검색을 안 한 이유
    saved: int = 0
    dropped: int = 0  # 지난 실행엔 추적했지만 이번에 뺀 영상 수
    pruned: int = 0  # 보관 기간이 지나 지운 스냅샷 줄 수 (그날 첫 실행만)
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        search = (
            f"검색 {self.searched_videos}개"
            if self.searched
            else f"검색 안 함({self.search_skipped or '-'})"
        )
        errors = f" / 실패: {'; '.join(self.errors)}" if self.errors else ""
        return (
            f"[youtube] {self.collected_at.isoformat(timespec='seconds')} 인기 {self.popular}개,"
            f" 추적 {self.tracked}개(추적에서 뺀 수 {self.dropped}개), {search},"
            f" 저장 {self.saved}줄, 할당량 {self.quota_units}단위"
            + (f", 보관 기간 지나 지움 {self.pruned}줄" if self.pruned else "")
            + errors
        )


def load_env(env: Mapping[str, str], path: Path = BACKEND / ".env") -> dict[str, str]:
    """`backend/.env` 위에 환경변수를 얹는다(환경변수가 먼저). 값은 출력하지 않는다."""
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #")[0].strip().strip('"').strip("'")
            if value:
                out[key.strip()] = value
    return out | dict(env)


def config_path(env: Mapping[str, str]) -> Path:
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV])
    tenant = (env.get("AX_TENANT") or "catbooks").strip()
    return BACKEND / "config" / "tenants" / tenant / "youtube.toml"


def load_config(path: Path) -> YouTubeConfig:
    """회사 `youtube.toml`. 없거나 틀리면 키워드 없음(검색을 하지 않는다)."""
    if not path.is_file():
        return YouTubeConfig()
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
        queries = tuple(str(q["q"]).strip() for q in raw.get("queries", []) if str(q["q"]).strip())
        track = raw.get("tracking") or {}
        base = YouTubeConfig()
        return YouTubeConfig(
            region=str(raw.get("region") or "KR"), queries=queries,
            drop_after_hours=max(1, int(track.get("drop_after_hours", base.drop_after_hours))),
            max_tracked=max(0, int(track.get("max_tracked", base.max_tracked))),
            retention_days=max(0, int(track.get("retention_days", base.retention_days))),
        )  # fmt: skip
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
        return YouTubeConfig()


_VIDEO_ID = re.compile(r"(?:watch\?v=|/shorts/|\"video_id\":\s*\")([A-Za-z0-9_-]{11})")


def in_use_ids(env: Mapping[str, str], days: int = 2) -> set[str]:
    """브리프(근거 영상·기회 카드 근거 숏츠)에 쓰는 영상 id. 최근 `days`일 브리프에서 읽는다.

    브리프 저장 파일이 없거나 못 읽으면 빈 집합 (추적 규칙만 적용).
    """
    path = Path(env.get("DATA_INSIGHT_BRIEF_DB") or BACKEND / ".data" / "insight_brief.db")
    if not path.is_file():
        return set()
    since = (datetime.now(UTC).astimezone(KST).date() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            rows = conn.execute("SELECT output FROM briefs WHERE day >= ?", (since,)).fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return set()
    return {m for (text,) in rows for m in _VIDEO_ID.findall(text or "")}


def pick_tracked(seen: list[str], in_use: set[str], skip: set[str], cap: int) -> list[str]:
    """추적할 영상: 브리프에 쓰는 영상 먼저, 그다음 인기·검색에서 최근에 본 순. 상한 `cap`."""
    ordered = [*sorted(in_use - skip), *(i for i in seen if i not in in_use and i not in skip)]
    return list(dict.fromkeys(ordered))[:cap]


def _merge(rows: dict[str, Row], stats: list[VideoStat], source: str) -> None:
    """같은 실행에서 여러 곳에서 받은 영상은 한 줄로 (인기 순위는 인기 목록 값을 남긴다)."""
    for stat in stats:
        old = rows.get(stat.video_id)
        if old is None:
            rows[stat.video_id] = Row(stat, (source,))
        else:
            keep = old.stat if old.stat.rank is not None else stat
            rows[stat.video_id] = Row(keep, (*old.sources, source))


async def collect_once(
    connector: YouTubeConnector,
    store: SnapshotStore,
    config: YouTubeConfig,
    *,
    now: datetime | None = None,
    in_use: set[str] | None = None,
) -> RunResult:
    """한 번 수집해 저장한다. 실패는 기록하고 받은 것까지는 저장한다.

    `in_use`: 브리프에 쓰는 영상 (인기·검색에서 빠졌어도 추적 유지).
    """
    collected_at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    result = RunResult(collected_at)
    rows: dict[str, Row] = {}
    start_quota = connector.quota_used
    stop = False

    def failed(step: str, error: ConnectorUnavailable) -> None:
        nonlocal stop
        result.errors.append(f"{step} [{error.code}]")
        stop = stop or error.code == "QUOTA_EXCEEDED"

    try:
        popular = await connector.popular(config.region)
        _merge(rows, popular, "popular")
        result.popular = len(popular)
    except ConnectorUnavailable as error:
        failed("인기 목록", error)

    if not stop:
        since = collected_at - timedelta(hours=config.drop_after_hours)
        before = store.last_run_ids() - set(rows)  # 지난 실행에서 인기 목록 밖으로 본 영상
        ids = pick_tracked(store.seen_ids(since), in_use or set(), set(rows), config.max_tracked)
        result.dropped = len(before - set(ids))
        if ids:
            try:
                tracked = await connector.videos(ids)
                _merge(rows, tracked, "tracked")
                result.tracked = len(tracked)
            except ConnectorUnavailable as error:
                failed("추적 영상", error)

    today = collected_at.astimezone(KST).date()
    if stop:
        result.search_skipped = "할당량 초과로 멈춤"
    elif not config.queries:
        result.search_skipped = "회사 키워드 없음"
    elif store.searched_on(today):
        result.search_skipped = "오늘 이미 함"
    else:
        result.searched = True
        after = collected_at - timedelta(days=SEARCH_DAYS)
        for query in config.queries:
            if stop:
                break
            try:
                found = await connector.search(query, after, config.region)
                _merge(rows, found, f"search:{query}")
                result.searched_videos += len(found)
            except ConnectorUnavailable as error:
                failed(f"검색 '{query}'", error)

    result.saved = store.save(collected_at, rows.values())
    if config.retention_days > 0 and not store.pruned_on(today):  # 보관 기간 지난 줄 (하루 1번)
        result.pruned = store.prune(today, collected_at - timedelta(days=config.retention_days))
    result.quota_units = connector.quota_used - start_quota
    store.log_run(
        collected_at,
        quota_units=result.quota_units,
        popular=result.popular,
        tracked=result.tracked,
        searched_videos=result.searched_videos,
        searched=result.searched,
        errors=result.errors,
    )
    return result


async def backfill_titles(
    connector: YouTubeConnector,
    store: SnapshotStore,
    *,
    now: datetime | None = None,
    limit: int = MAX_TITLE_BACKFILL,
) -> tuple[int, int, list[str]]:
    """제목 없는 인기 영상의 제목을 채운다. (찾은 수, 채운 수, 실패 이유).

    지워졌거나 비공개가 된 영상은 응답에 없어 채우지 않는다(지어내지 않음).
    """
    ids = store.popular_ids_without_title()[:limit]
    if not ids:
        return 0, 0, []
    try:
        stats = await connector.videos(ids)
    except ConnectorUnavailable as error:
        return len(ids), 0, [f"제목 [{error.code}]"]
    at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    store.save_details(at, stats)
    return len(ids), store.save_titles(at, [(s.video_id, s.title) for s in stats]), []


async def backfill_details(
    connector: YouTubeConnector,
    store: SnapshotStore,
    *,
    now: datetime | None = None,
    limit: int = MAX_TITLE_BACKFILL,
) -> tuple[int, int, list[str]]:
    """채널 이름·길이가 없는 인기 영상을 채운다(제목도 같이). (찾은 수, 채운 수, 실패 이유)."""
    ids = store.popular_ids_without_details()[:limit]
    if not ids:
        return 0, 0, []
    try:
        stats = await connector.videos(ids)
    except ConnectorUnavailable as error:
        return len(ids), 0, [f"채널·길이 [{error.code}]"]
    at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    store.save_titles(at, [(s.video_id, s.title) for s in stats])
    return len(ids), store.save_details(at, stats), []


async def run_titles(env: Mapping[str, str]) -> int:
    store = SnapshotStore(env.get(DB_ENV) or DEFAULT_DB)
    connector = YouTubeConnector(env, caller="youtube_collect")
    try:
        found, saved, errors = await backfill_titles(connector, store)
    finally:
        store.close()
    tail = f" / 실패: {'; '.join(errors)}" if errors else ""
    print(
        f"[youtube] 제목 채우기: 제목 없는 인기 영상 {found}개, 채움 {saved}개,"
        f" 할당량 {connector.quota_used}단위{tail}",
        flush=True,
    )
    return 1 if errors else 0


async def run_details(env: Mapping[str, str]) -> int:
    store = SnapshotStore(env.get(DB_ENV) or DEFAULT_DB)
    connector = YouTubeConnector(env, caller="youtube_collect")
    try:
        found, saved, errors = await backfill_details(connector, store)
    finally:
        store.close()
    tail = f" / 실패: {'; '.join(errors)}" if errors else ""
    print(
        f"[youtube] 채널·길이 채우기: 없는 인기 영상 {found}개, 채움 {saved}개,"
        f" 할당량 {connector.quota_used}단위{tail}",
        flush=True,
    )
    return 1 if errors else 0


async def run(every: int | None, env: Mapping[str, str], precompute_after: bool = True) -> int:
    store = SnapshotStore(env.get(DB_ENV) or DEFAULT_DB)
    config = load_config(config_path(env))
    if not config.queries:
        print(
            f"[youtube] 회사 키워드 없음 ({config_path(env)}): 검색은 하지 않는다", file=sys.stderr
        )
    try:
        while True:
            connector = YouTubeConnector(env, caller="youtube_collect")
            result = await collect_once(connector, store, config,
                                        in_use=in_use_ids(env))  # fmt: skip
            print(result.line(), flush=True)
            await measure_videos(connector, env)  # 게시한 영상 성과: 지금 잴 시점만 (실패해도 계속)
            await track_fresh(env)  # 새 숏츠 추적: 같은 형식 비교 기준 표본 (실패해도 계속)
            if result.saved and precompute_after:
                # 새 스냅샷 직후 감지 탭 결과를 미리 계산해 저장 (화면은 읽기만).
                # 실패해도 수집은 계속
                problems = await precompute()
                if problems:
                    print(f"[precompute] 일부 못 함: {', '.join(problems)}", flush=True)
            # 기능별 오늘 YouTube 단위 (하나의 장부, 미리 계산까지 끝난 뒤)
            print(youtube_quota.LEDGER.status_line(env), flush=True)
            if every is None:
                return 1 if result.errors and not result.saved else 0
            await asyncio.sleep(every)
    finally:
        store.close()


async def measure_videos(
    connector: YouTubeConnector, env: Mapping[str, str], now: datetime | None = None
) -> int:
    """video_performance 추적 목록에서 지금 잴 시점(1시간·1일·3일·7일)이 있는 영상만 잰다.

    같은 YouTube 커넥터(장부·하루 상한)로 영상 50개당 1단위. 잰 값 수. 문제는 한 줄 남기고 계속.
    """
    from data_agent.skills import video_performance as vp

    path = env.get(vp.DB_ENV) or vp.DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return 0  # 등록된 영상이 없다
    store = vp.VideoStore(path)
    try:
        saved, problems = await vp.measure_due(store, connector, now)
    except Exception as error:  # 성과 측정이 실패해도 수집은 계속
        print(f"[video] 성과 측정 실패 ({type(error).__name__})", flush=True)
        return 0
    finally:
        store.close()
    if saved or problems:
        print(f"[video] 성과 시점 값 {saved}개" + (f", {'; '.join(problems)}" if problems else ""),
              flush=True)  # fmt: skip
    return saved


async def track_fresh(
    env: Mapping[str, str], now: datetime | None = None, connector: YouTubeConnector | None = None
) -> int:
    """새 숏츠 추적 한 번 (형식마다 6시간에 1번 찾기 + 매시간 추적). 저장한 스냅샷 수.

    장부의 부른 곳은 fresh_shorts (장부 예약 몫 1,200단위, 다른 기능은 나머지 6,800 안에서).
    실패해도 수집은 계속한다.
    """
    from data_agent import fresh_shorts

    yt = connector or YouTubeConnector(env, caller=fresh_shorts.CALLER)
    try:
        result = await fresh_shorts.run_tick(yt, env, now)
    except Exception as error:  # 형식 분류(LLM)·저장 실패 등: 다음 시간에 다시
        print(f"[fresh] 새 숏츠 추적 실패 ({type(error).__name__})", flush=True)
        return 0
    print(result.line(), flush=True)
    return result.snapshots


def lock_path(env: Mapping[str, str]) -> Path:
    """중복 실행 잠금 파일 (스냅샷 DB 옆)."""
    return Path(env.get(DB_ENV) or DEFAULT_DB).with_name("youtube_collect.lock")


def acquire_lock(path: Path) -> IO[str] | None:
    """다른 수집기가 잡고 있으면 None. pid를 적고, 끝난 pid가 남긴 잠금은 지우고 잡는다.

    잡은 파일은 프로세스가 끝나면 OS가 푼다 (`collector_lock`).
    """
    return collector_lock.acquire(path, "youtube", lambda line: print(line, flush=True))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔에서도 한글이 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="YouTube 영상 통계 스냅샷 수집")
    parser.add_argument(
        "--every", type=int, default=None, help=f"초마다 반복 (최소 {MIN_EVERY_SECONDS})"
    )
    parser.add_argument(
        "--titles", action="store_true", help="제목 없는 예전 인기 영상의 제목만 채우고 끝낸다"
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="채널 이름·길이가 없는 예전 인기 영상의 채널 이름·길이(와 제목)만 채우고 끝낸다",
    )
    args = parser.parse_args(argv)
    if args.details:
        return asyncio.run(run_details(load_env(os.environ)))
    if args.titles:
        return asyncio.run(run_titles(load_env(os.environ)))
    if args.every is not None and args.every < MIN_EVERY_SECONDS:
        parser.error(f"--every 는 {MIN_EVERY_SECONDS}초 이상 (할당량 보호)")
    env = load_env(os.environ)
    lock = acquire_lock(lock_path(env))
    if lock is None:
        print(f"[youtube] 이미 다른 수집기가 돌고 있음 (잠금 {lock_path(env)}): 끝냄", flush=True)
        return 0
    install_sqlite_timer()
    try:
        return asyncio.run(run(args.every, env))
    except KeyboardInterrupt:
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
