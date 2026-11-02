"""YouTube 영상 통계 스냅샷 수집 (한 번 또는 `--every 초`마다). run_all.sh에는 아직 넣지 않았다.

    cd backend
    uv run python -m data_agent.youtube_collect              # 한 번
    uv run python -m data_agent.youtube_collect --every 3600 # 1시간마다 (Ctrl+C로 멈춤)

한 번 실행하면:
1. 인기 목록(매번): mostPopular 4쪽 = 200개, 4단위
2. 추적 중인 영상(매번): 지난 `TRACK_HOURS`시간 안에 저장했는데 이번 인기 목록에 없는 영상의
   지금 값.
   50개당 1단위, 최대 `MAX_TRACKED`개
3. 회사 키워드 검색(한국 날짜 하루 1번): 키워드마다 검색 100단위 + 통계 1단위
4. 저장(`youtube_store`): 같은 시각 같은 영상은 한 줄. 실행 기록에 쓴 할당량 단위를 남긴다

- 키: `YOUTUBE_API_KEY`. 환경변수가 먼저고, 없으면 `backend/.env`에서 읽는다(값은 출력하지 않는다)
- 회사 키워드: `DATA_YOUTUBE_CONFIG`(회사 폴더 `youtube.toml`, `scripts/tenant_env.py`가 정함).
  없으면 `config/tenants/<AX_TENANT, 기본 catbooks>/youtube.toml`. 코드에 키워드를 두지 않는다
- 할당량 초과(`QUOTA_EXCEEDED`)가 나면 그 실행의 남은 호출을 멈춘다. 받은 것까지는 저장한다
- 판정·태그 정리는 하지 않는다(스냅샷이 쌓인 뒤 별도 PR)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat, YouTubeConnector
from data_agent.youtube_store import DB_ENV, DEFAULT_DB, KST, Row, SnapshotStore

BACKEND = Path(__file__).resolve().parents[4]
CONFIG_ENV = "DATA_YOUTUBE_CONFIG"
TRACK_HOURS = 72  # 이 시간 안에 본 영상을 계속 추적한다
MAX_TRACKED = 500  # 한 번에 추적하는 영상 수 상한 (10단위)
SEARCH_DAYS = 7  # 회사 키워드 검색은 최근 게시 영상만
MIN_EVERY_SECONDS = 600  # --every 하한 (할당량 보호)


@dataclass(frozen=True)
class YouTubeConfig:
    region: str = "KR"
    queries: tuple[str, ...] = ()


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
            f" 추적 {self.tracked}개, {search}, 저장 {self.saved}줄, 할당량 {self.quota_units}단위"
            f"{errors}"
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
        return YouTubeConfig(region=str(raw.get("region") or "KR"), queries=queries)
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return YouTubeConfig()


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
) -> RunResult:
    """한 번 수집해 저장한다. 실패는 기록하고 받은 것까지는 저장한다."""
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
        since = collected_at - timedelta(hours=TRACK_HOURS)
        ids = [i for i in store.recent_video_ids(since, MAX_TRACKED + len(rows)) if i not in rows]
        ids = ids[:MAX_TRACKED]
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


async def run(every: int | None, env: Mapping[str, str]) -> int:
    store = SnapshotStore(env.get(DB_ENV) or DEFAULT_DB)
    config = load_config(config_path(env))
    if not config.queries:
        print(
            f"[youtube] 회사 키워드 없음 ({config_path(env)}): 검색은 하지 않는다", file=sys.stderr
        )
    try:
        while True:
            result = await collect_once(YouTubeConnector(env), store, config)
            print(result.line(), flush=True)
            if every is None:
                return 1 if result.errors and not result.saved else 0
            await asyncio.sleep(every)
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔에서도 한글이 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="YouTube 영상 통계 스냅샷 수집")
    parser.add_argument(
        "--every", type=int, default=None, help=f"초마다 반복 (최소 {MIN_EVERY_SECONDS})"
    )
    args = parser.parse_args(argv)
    if args.every is not None and args.every < MIN_EVERY_SECONDS:
        parser.error(f"--every 는 {MIN_EVERY_SECONDS}초 이상 (할당량 보호)")
    try:
        return asyncio.run(run(args.every, load_env(os.environ)))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
