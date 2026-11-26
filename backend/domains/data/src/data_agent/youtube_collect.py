"""YouTube 영상 통계 스냅샷 수집 (한 번 또는 `--every 초`마다). run_all.sh가 data와 같이 켠다.

새 스냅샷을 저장하면 바로 트렌드 감지 탭 결과를 미리 계산해 저장한다(`trend_precompute`).
그 바로 앞에 숏츠 표본의 빠진 게시 시각을 하루 상한 안에서 채운다(`shorts_backfill`).
검색 트렌드 키워드 풀도 한국 날짜 하루 1번 채운다(`keyword_pool`).
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
3. 회사 키워드 검색(한국 날짜 하루 1번): 키워드마다 검색 100단위 + 통계 1단위.
   11/26부터 회사마다: 캣북스(예전 그대로) 다음에 회사 연결이 켜진 회사마다 그 회사 `youtube.toml`
   (회사 설정이 없으면 틀 기본값 — 키워드 없음이면 검색 안 함)으로 검색해
   `search@<회사 id>:<키워드>`로 남긴다. 캣북스 키워드로 다른 회사를 검색하지 않는다.
   한 회사 실패는 다른 회사를 막지 않는다
4. 저장(`youtube_store`): 같은 시각 같은 영상은 한 줄. 실행 기록에 쓴 할당량 단위를 남긴다

- 키: `YOUTUBE_API_KEY`. 환경변수가 먼저고, 없으면 `backend/.env`에서 읽는다(값은 출력하지 않는다)
- 회사 키워드: `DATA_YOUTUBE_CONFIG`(회사 폴더 `youtube.toml`, `scripts/tenant_env.py`가 정함).
  없으면 `config/tenants/<AX_TENANT>/youtube.toml`. 코드에 키워드를 두지 않는다
- 할당량 초과(`QUOTA_EXCEEDED`)가 나면 그 실행의 남은 호출을 멈춘다. 받은 것까지는 저장한다
- 판정·태그 정리는 하지 않는다(스냅샷이 쌓인 뒤 별도 PR)
- 제목은 스냅샷 표가 아니라 `video_titles`에 영상마다 한 줄로 저장한다(`youtube_store`)
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import os
import re
import sqlite3
import subprocess
import sys
import tomllib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from ax_agent_template.tenant import setting as tenant_setting
from data_agent import collector_lock, keyword_pool, meme_shorts, shorts_backfill, youtube_quota
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.youtube import VideoStat, YouTubeConnector
from data_agent.connectors.youtube_analytics import YouTubeAnalytics
from data_agent.stage_timing import install_sqlite_timer
from data_agent.trend_precompute import precompute
from data_agent.youtube_store import (
    CATBOOKS,
    DB_ENV,
    DEFAULT_DB,
    KST,
    Row,
    SnapshotStore,
    search_source,
)

BACKEND = Path(__file__).resolve().parents[4]
CONFIG_ENV = "DATA_YOUTUBE_CONFIG"
NOT_CONNECTED = "(회사 연결 안 됨)"  # 회사 전에는 키워드가 없다(검색하지 않음)
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
    company_searched: dict[str, int] = field(default_factory=dict)  # 다른 회사 id → 검색한 영상 수

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
            + "".join(f", {cid} 검색 {n}개" for cid, n in self.company_searched.items())
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
    """회사 `youtube.toml`: 환경변수 → `AX_TENANT` 폴더 → 회사 연결에서 고른 회사.

    연결 전이면 없는 경로(설정 없음). 환경변수·`AX_TENANT`는 설치 하나에 하나(캣북스 것)라
    캣북스가 아닌 회사 범위에서는 보지 않고 그 회사 설정(없으면 틀 기본값)만 본다."""
    if _other_company():
        return Path(tenant_setting(CONFIG_ENV) or NOT_CONNECTED)
    if value := env.get(CONFIG_ENV):
        return Path(value)
    if tenant := (env.get("AX_TENANT") or "").strip():
        return BACKEND / "config" / "tenants" / tenant / "youtube.toml"
    return Path(tenant_setting(CONFIG_ENV) or NOT_CONNECTED)


def _other_company() -> bool:
    from ax_agent_template.company_connection import company_of

    return company_of() != CATBOOKS


def company_configs(env: Mapping[str, str]) -> list[tuple[str, YouTubeConfig]]:
    """캣북스 말고 이번 회차에 키워드를 검색할 회사와 그 회사 키워드 (#1878 `watched_companies`).

    회사마다 그 회사 범위에서 `youtube.toml`을 읽는다(캣북스 환경변수·폴더로 떨어지지 않음).
    회사 하나를 못 읽어도 다른 회사는 그대로."""
    from ax_agent_template import company_scope
    from data_agent.sales_collect import watched_companies

    out: list[tuple[str, YouTubeConfig]] = []
    for cid in watched_companies(env):
        try:
            with company_scope.using(cid):
                out.append((cid, load_config(config_path(env))))
        except Exception as error:  # noqa: BLE001 - 한 회사 설정 오류가 다른 회사를 막지 않게
            print(f"[youtube] {cid} 키워드를 읽지 못함 ({type(error).__name__})", flush=True)
    return out


MAX_AUTO_QUERIES = 3  # 설정 없는 회사의 자동 키워드 수 (검색 1번 = 100단위, 회사마다 하루 1번)
_BRACKETS = re.compile(r"[\[\(（【].*?[\]\)）】]")


def auto_queries(categories: Mapping[str, str], product_names: Sequence[str]) -> tuple[str, ...]:
    """회사 연결 정보(그 회사 상품 목록)에서 만든 검색 키워드 (11/26).

    분류 이름(코드와 다른 진짜 이름)을 먼저, 그다음 상품 이름(괄호 꾸밈말 뺌) 순서로
    `MAX_AUTO_QUERIES`개. 다른 회사·캣북스 키워드는 쓰지 않는다. 없으면 빈 값(검색 안 함)."""
    out: list[str] = []
    names = [name for code, name in categories.items() if name and name.strip() != code]
    for raw in [*names, *product_names]:
        text = " ".join(_BRACKETS.sub(" ", str(raw)).split())
        if len(text) >= 2 and text not in out:
            out.append(text)
        if len(out) >= MAX_AUTO_QUERIES:
            break
    return tuple(out)


async def with_auto_queries(
    companies: list[tuple[str, YouTubeConfig]],
    env: Mapping[str, str],
    read: Callable[[], Awaitable[tuple[Mapping[str, str], Sequence[str]]]] | None = None,
) -> list[tuple[str, YouTubeConfig]]:
    """설정(`youtube.toml`) 키워드가 없는 회사: 그 회사 범위에서 상품 목록을 읽어 자동 키워드로.

    회사가 넣은 키워드가 있으면 그대로(우선). 한 회사 상품 목록을 못 읽어도 다른 회사는 그대로."""
    from ax_agent_template import company_scope

    out: list[tuple[str, YouTubeConfig]] = []
    for cid, config in companies:
        if config.queries:
            out.append((cid, config))
            continue
        try:
            with company_scope.using(cid):
                categories, products = await (read or (lambda: _company_catalog(env)))()
            queries = auto_queries(categories, products)
        except Exception as error:  # noqa: BLE001 - 한 회사가 다른 회사를 막지 않게
            print(f"[youtube] {cid} 상품 목록을 읽지 못해 자동 키워드 없음 "
                  f"({type(error).__name__})", flush=True)  # fmt: skip
            queries = ()
        if queries:
            print(f"[youtube] {cid} 설정 키워드 없음: 상품 목록에서 {len(queries)}개", flush=True)
        out.append((cid, replace(config, queries=queries)))
    return out


async def _company_catalog(env: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """지금 회사 범위의 상품 목록 커넥터(그 회사 연결 주소)에서 분류 이름·상품 이름."""
    from data_agent.clients.shop_client import get_connector
    from data_agent.skills.product_link import read_catalog

    got = await read_catalog(get_connector(env))
    return dict(got.categories), [p.name for p in got.products if p.name]


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
    companies: Sequence[tuple[str, YouTubeConfig]] = (),
) -> RunResult:
    """한 번 수집해 저장한다. 실패는 기록하고 받은 것까지는 저장한다.

    `in_use`: 브리프에 쓰는 영상 (인기·검색에서 빠졌어도 추적 유지).
    `config`의 키워드는 캣북스 것, `companies`는 다른 회사 (회사 id, 그 회사 키워드).
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

    searched: list[str] = []
    for cid, company in companies:  # 다른 회사 키워드 (회사마다 한국 날짜 하루 1번)
        if stop:
            break
        if not company.queries or store.searched_on(today, cid):
            continue
        found_n = 0
        after = collected_at - timedelta(days=SEARCH_DAYS)
        for query in company.queries:
            if stop:
                break
            try:
                found = await connector.search(query, after, company.region)
                _merge(rows, found, search_source(cid, query))
                found_n += len(found)
            except ConnectorUnavailable as error:
                failed(f"{cid} 검색 '{query}'", error)
            except Exception as error:  # noqa: BLE001 - 한 회사 실패가 다른 회사를 막지 않게
                result.errors.append(f"{cid} 검색 '{query}' [{type(error).__name__}]")
        searched.append(cid)
        result.company_searched[cid] = found_n

    result.saved = store.save(collected_at, rows.values())
    for cid in searched:
        store.mark_searched(today, cid, result.company_searched[cid])
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
    config = load_config(config_path(env))  # 캣북스 (예전 그대로)
    if not config.queries:
        print(
            f"[youtube] 회사 키워드 없음 ({config_path(env)}): 검색은 하지 않는다", file=sys.stderr
        )
    try:
        while True:
            connector = YouTubeConnector(env, caller="youtube_collect")
            result = await collect_once(connector, store, config, in_use=in_use_ids(env),
                                        companies=await with_auto_queries(
                                            company_configs(env), env))  # fmt: skip
            print(result.line(), flush=True)
            await measure_videos(connector, env)  # 게시한 영상 성과: 지금 잴 시점만 (실패해도 계속)
            await measure_instagram_videos(env)  # 인스타 게시 영상: 계정마다 한 번 (실패해도 계속)
            await collect_insights(env)  # 영상 깊은 지표: 영상마다 하루 1번 (실패해도 계속)
            await track_fresh(env)  # 새 숏츠 추적: 같은 형식 비교 기준 표본 (실패해도 계속)
            # 숏츠 표본의 빠진 게시 시각 채우기 (하루 상한, 미리 계산 바로 앞. 실패해도 계속)
            await shorts_backfill.run(env)
            # 검색 트렌드 키워드 풀 (한국 날짜 하루 1번, 이미 채웠으면 건너뜀. 실패해도 계속)
            await keyword_pool.run(env)
            # 밈 키워드 쇼츠 검색 (11/24, 한국 날짜 하루 1번·남은 키워드만, 쿼터 30% 넘으면 다음
            # 회차로. 키워드가 없으면 아무것도 안 함. 실패해도 계속)
            await meme_shorts.run(env)
            # 인스타 (이 루프 회차마다, run_all.sh는 1시간마다): 인기 오디오는 한국 날짜 하루 1번,
            # 레이더는 회차마다 한 묶음(50곳, 커서 이어받기). 토큰이 없으면 한 줄만. 실패해도 계속
            from data_agent.skills import instagram

            await instagram.run(env)
            # 11/24 레이더 계정 대상층 AI 태그 (하루 1번, 태그 없는 새 계정만, 팔로워 수 갱신.
            # 사람이 고친 태그는 덮지 않는다. 실패해도 계속)
            from data_agent import radar_tags

            await radar_tags.run(env)
            if result.saved and precompute_after:
                # 새 스냅샷 직후 감지 탭 결과를 미리 계산해 저장 (화면은 읽기만).
                # 실패해도 수집은 계속
                problems = await precompute()
                if problems:
                    print(f"[precompute] 일부 못 함: {', '.join(problems)}", flush=True)
            # 기능별 오늘 YouTube 단위 (하나의 장부, 미리 계산까지 끝난 뒤) + 이 수집기의 코드
            print(f"{youtube_quota.LEDGER.status_line(env)} · 코드 {code_version(env)}", flush=True)
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


async def measure_instagram_videos(
    env: Mapping[str, str], now: datetime | None = None, graph: object | None = None
) -> int:
    """video_performance 추적 목록의 인스타 영상 중 잴 시점이 된 것만, 우리 게시 계정을 한 번 읽어
    잰다 (Instagram Graph 하루 상한·장부 그대로). 잰 값 수. 문제는 한 줄 남기고 계속."""
    from data_agent.connectors.instagram_graph import InstagramGraph
    from data_agent.skills import instagram_video
    from data_agent.skills import video_performance as vp

    path = env.get(vp.DB_ENV) or vp.DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return 0  # 등록된 영상이 없다
    store = vp.VideoStore(path)
    try:
        saved, problems = await instagram_video.measure_due(
            store, graph or InstagramGraph(env), env, now or datetime.now(UTC)
        )
    except Exception as error:  # 성과 측정이 실패해도 수집은 계속
        print(f"[video] 인스타 성과 측정 실패 ({type(error).__name__})", flush=True)
        return 0
    finally:
        store.close()
    if saved or problems:
        print(f"[video] 인스타 성과 시점 값 {saved}개" + (f", {'; '.join(problems)}" if problems
                                                    else ""), flush=True)  # fmt: skip
    return saved


async def collect_insights(
    env: Mapping[str, str], now: datetime | None = None, conn: YouTubeAnalytics | None = None
) -> int:
    """video_performance 추적 목록의 영상 깊은 지표 (유튜브 Analytics·Reporting, 영상마다
    `DATA_YT_ANALYTICS_EVERY_HOURS`에 한 번). 저장한 영상 수. 실패해도 수집은 계속한다.
    토큰·주소는 찍지 않는다 (오류 코드만)."""
    from data_agent.skills import video_insights as vi
    from data_agent.skills import video_performance as vp

    path = env.get(vp.DB_ENV) or vp.DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return 0  # 등록된 영상이 없다
    videos, store = vp.VideoStore(path), vi.InsightStore(path)
    try:
        result = await vi.collect_due(videos, store, conn or YouTubeAnalytics(env), env, now)
    except Exception as error:  # 깊은 지표가 실패해도 수집은 계속
        print(f"[insights] 깊은 지표 수집 실패 ({type(error).__name__})", flush=True)
        return 0
    finally:
        videos.close()
        store.close()
    if result.saved or result.problems:
        print(result.line(), flush=True)
    return result.saved


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


COMMIT_ENV = "AX_GIT_COMMIT"  # run_all.sh가 켤 때의 짧은 커밋을 넘긴다


@functools.cache
def _git_commit() -> str:
    """이 프로세스가 처음 물었을 때의 짧은 커밋 (run_all.sh 없이 켰을 때)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=5, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return "알 수 없음"
    return out.stdout.strip() or "알 수 없음"


def code_version(env: Mapping[str, str]) -> str:
    """[quota] 줄에 붙이는 짧은 커밋: 로그만 보고 옛 코드가 도는지 알 수 있게."""
    return (env.get(COMMIT_ENV) or "").strip() or _git_commit()


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
