"""trend_popular: "지금 인기" 주제 순위. 읽기 전용(grade read). **네이버를 부르지 않는다.**

급상승 판정(trend_scan·trend_candidates)과 별개다. "지금 많이 보이고 많이 찾는 주제"를
코드 점수로 줄 세운다. "급상승"이라는 말은 trend_scan 판정에만 쓴다.

- 주제: 최신 YouTube 인기 스냅샷의 태그·제목 구(trend_candidates와 같은 정리·별칭·수동 제외·
  자동 제외 규칙, `pick_names`)와 최신 인기 검색어 스냅샷(같은 정리 규칙, `pick_trending`).
  출처별 후보 수는 회사 설정 `[popular] max_youtube`·`max_trending`(trend_candidates보다 넉넉히)
- 같은 주제: 이름(공백·대소문자 무시)이 같거나, 오늘 trend_candidates가 자동으로 묶은 이름 쌍
  (오늘 판정 게시판 `today_board`)이면 한 주제. 자동 묶기는 네이버 시계열이 필요해 새로 재지 않는다
- 점수(코드) = `video_weight` × 영상 몫 + `velocity_weight` × 조회 속도 몫
  + `trending_weight` × 검색어 순위 몫
  - 몫은 출처마다 순위로 매긴다: 그 출처에 값이 있는 주제끼리 큰 순 순위 → (N − 순위 + 1) ÷ N.
    값이 없으면 0. (최댓값으로 나누면 한 주제가 나머지를 모두 작게 만들어서 바꿨다, README)
  - 영상: 그 주제가 나온 인기 영상 수 / 조회 속도: 그 영상들의 지금 시간당 조회 증가 합 /
    검색어: 인기 검색어 순위
- 카테고리별 보기: 화면 카테고리(`[popular] categories`)마다 1~`per_category`위. 그 밖 분류와
  분류하지 못한 주제는 응답에서 뺀다
  - 출처 하나를 못 읽으면(스냅샷 없음·오래됨, 스냅샷 2개 미만) 그 몫은 **unknown + 이유**이고
    점수에서 뺀다(0으로 채우지 않음). 주제에 영상이 있는데 속도를 하나도 못 재면 그 주제의 속도 몫만
    unknown
- 주제 분류: content_recommend와 같은 `classify_topics`(LLM 한 번에 묶어서, 주제별 하루 캐시).
  회사 "추천 안 함" 분류(`[recommend] exclude_categories`)에 들면 `brand_safe: false`
- "지금 급등 중" 배지: 오늘 이미 낸 네이버 판정(today_board)이 급상승이면 표시. 판정이 없으면 비운다
- 카테고리 주제가 `per_category`보다 적으면(`fill_always`면 늘), 회사 설정
  `[popular] fill_youtube_categories`에 YouTube
  카테고리가 있는 화면 카테고리만 그 카테고리의 최근 `fill_days`일 조회수 높은 영상을 `top_videos`로
  채운다(**지금 인기 아님**). YouTube 검색은 카테고리마다 하루 1번이고 저장해 다시 쓴다
  (`DATA_POPULAR_VIDEOS_DB`). `fill_language`가 있으면 그 언어 영상만 남긴다
  (shorts_stats와 같은 판단)
README "trend_popular".
"""

from __future__ import annotations

import json
import sqlite3
import threading
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    PopularCategory,
    PopularScore,
    PopularTopic,
    PopularVideo,
    TrendPopularOutput,
    TrendPopularRequest,
)
from data_agent import caution
from data_agent.connectors import ConnectorUnavailable
from data_agent.skills import trend_candidates as cand_mod
from data_agent.skills import trend_evidence as ev
from data_agent.skills.content_recommend import load_config as load_recommend_config
from data_agent.skills.shorts_stats import (
    SHORTS_DB_ENV,
    SHORTS_DEFAULT_DB,
    ShortsStore,
    is_language,
)
from data_agent.skills.shorts_stats import load_config as load_shorts_config
from data_agent.skills.topic_category import (
    TOPIC_CATEGORIES,
    UNKNOWN,
    CategoryCache,
    CategoryResult,
    TopicInput,
    VideoInput,
    classify_topics,
    classify_videos,
)
from data_agent.skills.trend_scan import TREND_CONFIG_ENV
from data_agent.skills.youtube_velocity import velocities
from data_agent.today_board import BOARD, TodayBoard
from data_agent.trending_store import latest_trending, news_links, news_titles
from data_agent.video_links import video_url
from data_agent.youtube_store import (
    DB_ENV,
    DEFAULT_DB,
    SnapshotStore,
    latest_popular,
    popular_history,
    recent_snapshots,
    video_details,
    video_thumbnails,
    video_titles,
)

EVIDENCE = 3  # 분류 근거 글 수


@dataclass(frozen=True)
class PopularConfig:
    """회사 설정 `[popular]`. 파일·표가 없으면 이 값."""

    top: int = 10  # (예전 칸) 카테고리별 보기에서는 per_category를 쓴다
    max_youtube: int = 80  # 카테고리마다 5개를 채우려고 넉넉히 (네이버 호출 없음)
    max_trending: int = 30
    # 화면 카테고리 (이 순서로 보여 준다). 여기 없는 분류(기부·재난·정치·사회 등)는 응답에서 뺀다
    categories: tuple[str, ...] = ("게임", "뉴스·사건", "연예·스포츠", "IT")
    per_category: int = 5
    video_weight: float = 1.0
    velocity_weight: float = 1.0
    trending_weight: float = 1.0
    # 주제가 모자란 화면 카테고리 → YouTube 카테고리 id.
    # 여기 있는 카테고리만 최근 조회수 높은 영상으로 채운다
    # 값은 YouTube 카테고리 id 하나 또는 여러 개 (예: 연예·스포츠 = ["24", "17"])
    fill_youtube_categories: tuple[tuple[str, tuple[str, ...]], ...] = ()
    fill_always: bool = False  # 참이면 주제 수와 상관없이 늘 채운다 (화면은 영상만 보여 줌)
    fill_days: int = 7  # 최근 며칠 게시 영상에서 고를지
    fill_language: str = "ko"  # 그 언어 영상만 (""면 거르지 않음)
    # 보충 검색에 검색어를 붙일 칸과 붙일 주제 수: 그 칸 지금 인기 주제 앞 N개 이름
    # (사람 이름 제외)을 또는(|)으로 묶는다. 검색어 목록을 손으로 적지 않는다
    fill_query_cells: tuple[str, ...] = ()
    fill_query_topics: int = 2
    fill_search_calls: int = 6  # 보충 검색 하루 호출 상한 (넘으면 그 칸 보충은 unknown + 이유)
    # 주제 근거 영상 중 저장된 제목이 없는 것을 videos.list로 받는 하루 호출 상한
    # (한 번 = 영상 50개·1단위)
    title_fill_calls: int = 2
    # 근거: 채널 구독자 받기(channels.list) 하루 호출 상한 (한 번 = 채널 50개·1단위)
    channel_fill_calls: int = 2


def load_config(env: Mapping[str, str]) -> PopularConfig:
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return PopularConfig()
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("popular") or {}
        base = PopularConfig()
        return PopularConfig(
            top=max(1, min(50, int(raw.get("top", base.top)))),
            max_youtube=max(1, int(raw.get("max_youtube", base.max_youtube))),
            max_trending=max(1, int(raw.get("max_trending", base.max_trending))),
            video_weight=float(raw.get("video_weight", base.video_weight)),
            velocity_weight=float(raw.get("velocity_weight", base.velocity_weight)),
            trending_weight=float(raw.get("trending_weight", base.trending_weight)),
            categories=tuple(
                str(c).strip()
                for c in raw.get("categories", base.categories)
                if str(c).strip() in TOPIC_CATEGORIES
            ),
            per_category=max(1, min(20, int(raw.get("per_category", base.per_category)))),
            fill_youtube_categories=tuple(
                (str(k).strip(), ids)
                for k, v in (raw.get("fill_youtube_categories") or {}).items()
                if str(k).strip() in TOPIC_CATEGORIES
                and (
                    ids := tuple(
                        str(i).strip()
                        for i in (v if isinstance(v, list) else [v])
                        if str(i).strip()
                    )
                )  # fmt: skip
            ),
            fill_always=bool(raw.get("fill_always", base.fill_always)),
            fill_query_cells=tuple(
                str(c).strip()
                for c in raw.get("fill_query_cells", base.fill_query_cells)
                if str(c).strip() in TOPIC_CATEGORIES
            ),
            fill_query_topics=max(
                1, min(5, int(raw.get("fill_query_topics", base.fill_query_topics)))
            ),  # fmt: skip
            fill_search_calls=max(0, int(raw.get("fill_search_calls", base.fill_search_calls))),
            title_fill_calls=max(0, int(raw.get("title_fill_calls", base.title_fill_calls))),
            channel_fill_calls=max(
                0, int(raw.get("channel_fill_calls", base.channel_fill_calls))
            ),  # fmt: skip
            fill_days=max(1, min(30, int(raw.get("fill_days", base.fill_days)))),
            fill_language=str(raw.get("fill_language", base.fill_language)).strip().lower(),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return PopularConfig()


# ------------------------------------------------ 카테고리 채우기 영상 (하루 1번 검색, 저장)

FILL_DB_ENV = "DATA_POPULAR_VIDEOS_DB"
FILL_DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "popular_videos.db"
KST = timezone(timedelta(hours=9))


class FillStore:
    """카테고리별 그날 검색한 영상 (day, YouTube 카테고리) → 영상 목록 JSON 또는 실패 이유."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS fill (day TEXT NOT NULL,"
                " youtube_category TEXT NOT NULL, language TEXT NOT NULL, videos TEXT NOT NULL,"
                " PRIMARY KEY (day, youtube_category, language))"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS title_calls"
                " (day TEXT PRIMARY KEY, calls INTEGER NOT NULL)"
            )

    def close(self) -> None:
        self._conn.close()

    def get(self, day: date, cat: str, language: str) -> list[dict[str, Any]] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT videos FROM fill WHERE day = ? AND youtube_category = ? AND language = ?",
                (day.isoformat(), cat, language),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def count(self, day: date) -> int:
        """그날 한 검색 수 (검색 한 번 = 한 줄)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM fill WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        return int(row[0])

    def title_calls(self, day: date) -> int:
        """그날 제목을 받으려고 부른 videos.list 수."""
        with self._lock:
            row = self._conn.execute(
                "SELECT calls FROM title_calls WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        return int(row[0]) if row else 0

    def add_title_calls(self, day: date, calls: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO title_calls VALUES (?, ?) ON CONFLICT(day)"
                " DO UPDATE SET calls = calls + excluded.calls",
                (day.isoformat(), calls),
            )

    def put(self, day: date, cat: str, language: str, videos: list[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO fill VALUES (?,?,?,?)",
                (day.isoformat(), cat, language, json.dumps(videos, ensure_ascii=False)),
            )


def _video(r: dict[str, Any], cell: str, yt_cell: str | None, source: str) -> PopularVideo:
    yt = r.get("category_id")
    return PopularVideo(
        video_id=r["video_id"],
        title=r.get("title"),
        channel_title=r.get("channel_title"),
        views=r.get("views"),
        published_at=r.get("published_at"),
        duration_seconds=r.get("duration_seconds"),
        topic_category=cell,  # type: ignore[arg-type]
        youtube_category_id=yt,
        category_mismatch=None if yt_cell is None else yt_cell != cell,
        source=source,  # type: ignore[arg-type]
        video_url=video_url(r["video_id"]),
        thumbnail_url=r.get("thumbnail_url"),
    )


async def fill_cells(
    config: PopularConfig,
    cells: Sequence[str],
    chart: Sequence[dict[str, Any]],
    *,
    youtube: Any,
    store: FillStore,
    llm: LLMClient,
    cache: CategoryCache | None,
    now: datetime,
    limit: int,
    queries: list[str],
    context_id: str | None,
    search_terms: Mapping[str, str] | None = None,
) -> dict[str, tuple[list[PopularVideo], str]]:
    """화면 칸마다 최근 조회수 높은 영상 (칸 → (영상, 고른 기준 또는 이유)).

    1. 한국 인기 목록(저장된 스냅샷, 외부 호출 없음)의 한국 영상을 공유 분류기로 분류해 칸을 채운다.
       YouTube 카테고리 번호는 참고만 하고, 분류기와 다르면 분류기를 따르고 표시한다
    2. `limit`개 미만인 칸만 그 칸의 YouTube 카테고리(`fill_youtube_categories`)를 검색해 보충한다.
       검색 결과도 같은 분류기·한국 영상 기준을 통과해야 칸에 들어간다
    """
    fill_map = dict(config.fill_youtube_categories)
    yt_cell = {yt: cell for cell, ids in config.fill_youtube_categories for yt in ids}
    lang = config.fill_language
    korean = [
        r
        for r in chart
        if not lang or is_language(lang, r.get("title"), r.get("channel_title"), r.get("language"))
    ]
    found = await classify_videos([_video_input(r) for r in korean], llm, cache=cache,
                                  context_id=context_id)  # fmt: skip
    unknown = sum(
        1 for r in korean if found.get(r["video_id"], _UNKNOWN_RESULT).category == UNKNOWN
    )
    picked: dict[str, list[PopularVideo]] = {c: [] for c in cells}
    used: set[str] = set()
    for r in sorted(korean, key=lambda r: -(r.get("views") or 0)):
        cat = found.get(r["video_id"], _UNKNOWN_RESULT).category
        if cat in picked and len(picked[cat]) < limit:
            picked[cat].append(_video(r, cat, yt_cell.get(r.get("category_id") or ""), "chart"))
            used.add(r["video_id"])
    out: dict[str, tuple[list[PopularVideo], str]] = {}
    today = now.astimezone(KST).date()
    for cell in cells:
        videos = picked[cell]
        chart_n = len(videos)
        note = (
            f"지금 인기 아님: 한국 인기 목록 {len(chart)}개 중 한국 영상 {len(korean)}개를"
            f" 분류해 이 칸 {chart_n}개"
        )
        if unknown:
            note += f" (분류 못 한 영상 {unknown}개 뺌)"
        if chart_n < limit and fill_map.get(cell):
            rows: list[dict[str, Any]] = []
            failed: list[str] = []
            term = (search_terms or {}).get(cell)
            for yt in fill_map[cell]:
                got = await _fill_rows(config, youtube, store, yt, now, today, queries, term)
                if isinstance(got, str):
                    failed.append(got)
                else:
                    rows += [r for r in got if r["video_id"] not in used]
            if failed and not rows and all(f.startswith(OVER_CAP) for f in failed):
                note += f". 확인할 수 없음(보충): {failed[0]}"
                videos.sort(key=lambda v: -(v.views or 0))
                out[cell] = (videos, note)
                continue
            fresh = [
                r
                for r in rows
                if not lang
                or is_language(lang, r.get("title"), r.get("channel_title"), r.get("language"))
            ]
            more = await classify_videos([_video_input(r) for r in fresh], llm, cache=cache,
                                         context_id=context_id)  # fmt: skip
            added = 0
            for r in sorted(fresh, key=lambda r: -(r.get("views") or 0)):
                if len(videos) >= limit:
                    break
                if (
                    r["video_id"] in used
                    or more.get(r["video_id"], _UNKNOWN_RESULT).category != cell
                ):
                    continue
                videos.append(_video(r, cell, yt_cell.get(r.get("category_id") or ""), "search"))
                used.add(r["video_id"])
                added += 1
            ids = "·".join(fill_map[cell])
            what = f"검색어 '{term}'" if term else "검색어 없이"
            note += (
                f". 5개 미만이라 YouTube 카테고리 {ids} 최근 {config.fill_days}일 {what} 검색으로"
                f" 보충 (검색 {len(rows)}개 중 한국 영상 {len(fresh)}개, 이 칸 {added}개)"
            )
            if failed:
                note += f". 검색 실패: {', '.join(failed)}"
        videos.sort(key=lambda v: -(v.views or 0))
        out[cell] = (videos, note)
    return out


_UNKNOWN_RESULT = CategoryResult(UNKNOWN, None, 0)


def _video_input(r: dict[str, Any]) -> VideoInput:
    return VideoInput(r["video_id"], r.get("title"), r.get("channel_title"),
                      tuple(r.get("tags") or ()))  # fmt: skip


def _chart_rows(db: str, snapshot: Any, since: datetime) -> list[dict[str, Any]]:
    """최신 한국 인기 목록 스냅샷의 영상 (since 이후 게시, 조회수·채널·길이는 저장된 값)."""
    if snapshot is None:
        return []
    latest = recent_snapshots(db, 1)
    values = latest[0].videos if latest else {}
    ids = [v.video_id for v in snapshot.videos]
    details = video_details(db, ids)
    thumbs = video_thumbnails(db, ids)
    rows: list[dict[str, Any]] = []
    for v in snapshot.videos:
        stored = values.get(v.video_id)
        if stored is None or stored.published_at < since:
            continue
        d = details.get(v.video_id)
        rows.append(
            {
                "video_id": v.video_id,
                "title": v.title or None,
                "channel_title": d.channel_title if d else None,
                "views": stored.view_count,
                "published_at": stored.published_at.isoformat(),
                "duration_seconds": d.duration_seconds if d else None,
                "language": None,
                "category_id": v.category_id,
                "tags": list(v.tags),
                "thumbnail_url": thumbs.get(v.video_id),
            }
        )
    return rows


async def _fill_rows(
    config: PopularConfig,
    youtube: Any,
    store: FillStore,
    yt_category: str,
    now: datetime,
    today: date,
    queries: list[str],
    term: str | None = None,
) -> list[dict[str, Any]] | str:
    """YouTube 카테고리 하나의 그날 검색 결과 (저장한 것이 있으면 그것). 실패면 이유 문자열.

    같은 카테고리·검색어는 하루 1번. 그날 검색 수가 `fill_search_calls`에 닿으면 더 부르지 않는다.
    """
    lang = config.fill_language
    key = f"{yt_category} q={term}" if term else yt_category
    rows = store.get(today, key, lang)
    if rows is None:
        if youtube is None or not hasattr(youtube, "search_category"):
            return "YouTube 커넥터가 없어 최근 조회수 높은 영상을 받지 않음"
        if store.count(today) >= config.fill_search_calls:
            return f"{OVER_CAP}({config.fill_search_calls}번)에 닿아 검색하지 않음"
        try:
            stats = await youtube.search_category(
                yt_category,
                now - timedelta(days=config.fill_days),
                language=lang or None,
                **({"query": term} if term else {}),
            )
        except ConnectorUnavailable as error:
            return f"최근 조회수 높은 영상 검색 실패 [{error.code}]"
        queries.append(
            f"youtube search.list videoCategoryId={yt_category} order=viewCount"
            + (f" q={term}" if term else "")
        )
        rows = [
            {
                "video_id": s.video_id,
                "title": s.title or None,
                "channel_title": s.channel_title or None,
                "views": s.view_count,
                "published_at": s.published_at.isoformat() if s.published_at else None,
                "duration_seconds": s.duration_seconds,
                "language": getattr(s, "language", None),
                "category_id": s.category_id,
                "tags": list(s.tags),
                "thumbnail_url": getattr(s, "thumbnail_url", None),  # 받은 검색 응답 값
            }
            for s in stats
        ]
        store.put(today, key, lang, rows)
    return rows


OVER_CAP = "하루 보충 검색 상한"
TITLE_PAGE = 50  # videos.list 한 번에 받는 영상 수


async def add_evidence(
    topics: Sequence[PopularTopic],
    *,
    env: Mapping[str, str],
    db: str,
    youtube: Any,
    llm: LLMClient,
    config: PopularConfig,
    board: TodayBoard,
    speeds: Mapping[str, float],
    tdb: Any,
    tsnap: Any,
    raw_names: Mapping[str, str],
    now: datetime,
    queries: list[str],
    context_id: str | None,
) -> None:
    """주제·영상마다 "왜 뜨나" 근거를 붙인다 (topics를 바꾼다). 숫자는 코드, 한 줄만 LLM."""
    today = now.astimezone(KST).date()
    latest = recent_snapshots(db, 1)
    stored = latest[0].videos if latest else {}
    ids = [v.video_id for t in topics for v in t.videos]
    channels = [stored[i].channel_id for i in ids if i in stored]
    store = ev.EvidenceStore(env.get(FILL_DB_ENV) or FILL_DEFAULT_DB)
    shorts = ShortsStore(env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB)
    try:
        subs, subs_why = await ev.subscribers(
            db, channels, youtube=youtube, store=store, cap=config.channel_fill_calls,
            now=now, today=today, queries=queries,
        )  # fmt: skip
        formats = shorts.formats(ids, now - timedelta(days=30))
        short_max = load_shorts_config(env).max_seconds
        news: dict[str, list[dict[str, Any]]] = {}
        if tsnap is not None and raw_names:
            news = news_links(tdb, tsnap.geo, tsnap.collected_at, raw_names.values())
        for t in topics:
            t.videos = [
                ev.video_evidence(
                    v,
                    stored.get(v.video_id),
                    speed=speeds.get(v.video_id),
                    subs=subs,
                    subs_reason=subs_why,
                    fmt=(formats.get(v.video_id) or (None,))[0],
                    short_max=short_max,
                    now=now,
                )  # fmt: skip
                for v in t.videos
            ]
            raw = raw_names.get(t.keyword)
            t.news = ev.topic_news(news.get(raw, [])) if raw else []
            t.news_reason = (
                None if t.news
                else "인기 검색어에 없는 주제" if not raw
                else "인기 검색어 피드가 관련 뉴스를 주지 않음 (또는 출처·주소 저장 전 스냅샷)"
            )  # fmt: skip
            t.rising_started, t.rising_reason = ev.rising_start(
                board.verdict([t.keyword, *t.aliases])
            )
        await ev.why_lines(
            topics, llm, store=store, today=today, context_id=context_id, queries=queries
        )
    finally:
        store.close()
        shorts.close()


async def attach_videos(
    db: str,
    topics: Sequence[PopularTopic],
    *,
    youtube: Any,
    store: FillStore | None,
    config: PopularConfig,
    now: datetime,
    queries: list[str],
) -> None:
    """주제마다 근거 영상(video_ids 순서)의 제목·채널·조회수를 붙인다 (topics를 바꾼다).

    저장된 제목(`video_titles`)을 먼저 쓰고, 없는 것만 videos.list로 한 번에 받아 저장한다.
    그날 호출이 `title_fill_calls`에 닿았거나 커넥터가 없으면 받지 않고 그 주제에 이유를 남긴다.
    """
    ids = list(dict.fromkeys(v for t in topics for v in t.video_ids))
    if not ids:
        return
    titles = video_titles(db, ids)
    missing = [i for i in ids if not titles.get(i)]
    why: str | None = None
    fetched: dict[str, Any] = {}
    if missing:
        calls = -(-len(missing) // TITLE_PAGE)
        today = now.astimezone(KST).date()
        if youtube is None or not hasattr(youtube, "videos") or store is None:
            why = "YouTube 커넥터 없이 불러 저장된 제목만 붙임"
        elif store.title_calls(today) + calls > config.title_fill_calls:
            why = f"제목 받기 하루 상한({config.title_fill_calls}번)에 닿아 저장된 제목만 붙임"
        else:
            try:
                stats = await youtube.videos(missing)
            except ConnectorUnavailable as error:
                why = f"제목 받기 실패 [{error.code}]"
            else:
                store.add_title_calls(today, calls)
                queries.append(f"youtube videos.list titles ids={len(missing)} calls={calls}")
                at = now.astimezone(UTC).replace(microsecond=0)
                snap = SnapshotStore(db)
                try:
                    snap.save_titles(at, [(x.video_id, x.title) for x in stats if x.title])
                    snap.save_details(at, stats)
                finally:
                    snap.close()
                fetched = {x.video_id: x for x in stats}
                titles = video_titles(db, ids)
                gone = [i for i in missing if not titles.get(i)]
                if gone:
                    why = f"YouTube가 영상 {len(gone)}개를 주지 않음 (지워졌거나 비공개)"
    details = video_details(db, ids)
    thumbs = video_thumbnails(db, ids)
    latest = recent_snapshots(db, 1)
    stored = latest[0].videos if latest else {}
    for t in topics:
        rows = []
        for vid in t.video_ids:
            d, s, f = details.get(vid), stored.get(vid), fetched.get(vid)
            views = f.view_count if f is not None else (s.view_count if s else None)
            published = f.published_at if f is not None else (s.published_at if s else None)
            rows.append(
                PopularVideo(
                    video_id=vid,
                    title=titles.get(vid) or None,
                    channel_title=(d.channel_title if d else None) or None,
                    views=views,
                    published_at=published,
                    duration_seconds=d.duration_seconds if d else None,
                    source="chart",
                    video_url=video_url(vid),
                    thumbnail_url=thumbs.get(vid),
                )
            )
        t.videos = rows
        t.videos_reason = why if any(r.title is None for r in rows) else None


def search_terms(config: PopularConfig, every: Sequence[PopularTopic]) -> dict[str, str]:
    """검색어를 붙일 칸마다 그 칸 지금 인기 주제 앞 N개 이름(사람 이름 제외)을 |로 묶는다."""
    out: dict[str, str] = {}
    for cell in config.fill_query_cells:
        names = [
            i.keyword
            for i in sorted(every, key=lambda i: i.rank)
            if i.topic_category == cell and i.is_person is not True
        ][: config.fill_query_topics]
        if names:
            out[cell] = "|".join(names)
    return out


# ---------------------------------------------------------------- 계산


def _key(name: str) -> str:
    return "".join(name.lower().lstrip("#").split())


@dataclass
class _Topic:
    keyword: str
    aliases: list[str] = field(default_factory=list)
    origins: list[str] = field(default_factory=list)
    video_ids: list[str] = field(default_factory=list)
    trending_rank: int | None = None
    trending_traffic: str | None = None
    trending_raw: str | None = None

    def names(self) -> list[str]:
        return [self.keyword, *self.aliases]


def _merge_pairs(topics: list[_Topic], pairs: Sequence[tuple[str, str]]) -> list[_Topic]:
    """오늘 자동으로 묶은 이름 쌍이면 한 주제로 (영상이 많은 쪽 이름이 대표)."""
    for a, b in pairs:
        ta = next((t for t in topics if a in {_key(n) for n in t.names()}), None)
        tb = next((t for t in topics if b in {_key(n) for n in t.names()}), None)
        if ta is None or tb is None or ta is tb:
            continue
        keep, drop = (ta, tb) if len(ta.video_ids) >= len(tb.video_ids) else (tb, ta)
        keep.aliases += [n for n in drop.names() if _key(n) not in {_key(x) for x in keep.names()}]
        keep.video_ids += [v for v in drop.video_ids if v not in keep.video_ids]
        keep.origins += [o for o in drop.origins if o not in keep.origins]
        if drop.trending_rank is not None and (
            keep.trending_rank is None or drop.trending_rank < keep.trending_rank
        ):
            keep.trending_rank = drop.trending_rank
            keep.trending_traffic = drop.trending_traffic
            keep.trending_raw = drop.trending_raw
        topics.remove(drop)
    return topics


def rank_shares(values: Mapping[int, float]) -> dict[int, float]:
    """값이 큰 순 순위 몫: (N − 순위 + 1) ÷ N. 같은 값은 같은(높은) 순위. 값이 없으면 빈 dict."""
    n = len(values)
    out: dict[int, float] = {}
    for i, v in values.items():
        rank = 1 + sum(1 for w in values.values() if w > v)
        out[i] = round((n - rank + 1) / n, 4)
    return out


def score_topics(
    topics: Sequence[_Topic],
    *,
    speeds: Mapping[str, float] | None,
    trending_n: int | None,
    youtube_known: bool,
    config: PopularConfig,
) -> list[tuple[_Topic, PopularScore, float | None, int]]:
    """주제마다 (주제, 점수, 속도 합, 속도를 잰 영상 수). 아는 몫만 더한다."""
    sums: dict[int, tuple[float | None, int]] = {}
    for i, t in enumerate(topics):
        if speeds is None:
            sums[i] = (None, 0)
            continue
        known = [speeds[v] for v in t.video_ids if v in speeds]
        if not known:
            sums[i] = (None, 0)  # 인기 영상이 없거나 그 영상 속도를 하나도 못 잼
        else:
            sums[i] = (round(sum(known), 1), len(known))
    # 출처마다 값이 있는 주제끼리 순위를 매겨 몫 = (N − 순위 + 1) ÷ N (같은 값은 같은 순위).
    # 최댓값으로 나누면 한 주제(인기 영상 28개)가 나머지를 모두 작게 만들어서 순위 기반으로 바꿨다
    videos = {i: float(len(t.video_ids)) for i, t in enumerate(topics) if t.video_ids}
    speed_vals = {i: s for i, (s, _) in sums.items() if s is not None and s > 0}
    trend_vals = {
        i: -float(t.trending_rank) for i, t in enumerate(topics) if t.trending_rank is not None
    }
    video_rank = rank_shares(videos)
    speed_rank = rank_shares(speed_vals)
    trend_rank = rank_shares(trend_vals)
    out = []
    for i, t in enumerate(topics):
        video_share = video_rank.get(i, 0.0) if youtube_known else None
        speed, counted = sums[i]
        if speeds is not None and not t.video_ids:
            velocity_share = 0.0  # 인기 영상에 없는 주제 (속도를 못 잰 것이 아님)
        elif speeds is None or speed is None:
            velocity_share = None
        else:
            velocity_share = speed_rank.get(i, 0.0)
        trending_share = trend_rank.get(i, 0.0) if trending_n else None
        total = sum(
            w * s
            for w, s in (
                (config.video_weight, video_share),
                (config.velocity_weight, velocity_share),
                (config.trending_weight, trending_share),
            )
            if s is not None
        )
        score = PopularScore(
            score=round(total, 4),
            video_share=video_share,
            velocity_share=velocity_share,
            trending_share=trending_share,
        )
        out.append((t, score, speed, counted))
    out.sort(key=lambda x: (-x[1].score, -len(x[0].video_ids), x[0].keyword))
    return out


@dataclass(frozen=True)
class PopularResult:
    output: TrendPopularOutput
    evidence: Evidence


async def popular(
    request: TrendPopularRequest,
    llm: LLMClient,
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
    context_id: str | None = None,
    category_cache: CategoryCache | None = None,
    board: TodayBoard | None = None,
    youtube: Any = None,
    fill: bool = True,
) -> PopularResult:
    """지금 인기 주제 순위. 네이버를 부르지 않는다. 상태를 바꾸지 않는다."""
    now = now or datetime.now(UTC)
    board = board if board is not None else BOARD
    config = load_config(env)
    cand = cand_mod.load_config(env)
    exclude = list(load_recommend_config(env).exclude_categories)
    db = env.get(DB_ENV) or str(DEFAULT_DB)
    queries: list[str] = []

    # YouTube 인기 스냅샷 → 주제 (trend_candidates와 같은 규칙)
    snapshot = latest_popular(db)
    ywhy = cand_mod._snapshot_problem(snapshot, cand, now)
    topics: list[_Topic] = []
    if snapshot is not None and not ywhy:
        queries.append(f"youtube snapshot popular at={snapshot.collected_at.isoformat()}")
        history = popular_history(db, snapshot.collected_at) if cand.auto_exclude else []
        common = cand_mod.common_terms(history, cand)
        for tc in cand_mod.pick_names(snapshot, cand, config.max_youtube, common).candidates:
            aliases = [m for m in tc.matched if _key(m) != _key(tc.keyword)]
            topics.append(_Topic(tc.keyword, aliases, list(tc.origins), list(tc.video_ids)))

    # 지금 조회 속도 (youtube_velocity와 같은 계산)
    speeds: dict[str, float] | None = None
    vwhy: str | None = None
    vfrom = vto = None
    snaps = recent_snapshots(db, 3)
    if ywhy:
        vwhy = f"YouTube 스냅샷을 쓸 수 없음 ({ywhy})"
    elif len(snaps) < 2:
        vwhy = "YouTube 스냅샷이 2개 미만이라 조회 속도를 잴 수 없음"
    else:
        rows, _, _ = velocities(snaps)
        speeds = {r.video_id: r.views_per_hour for r in rows if r.views_per_hour is not None}
        vfrom, vto = snaps[1].collected_at, snaps[0].collected_at
        queries.append(f"youtube snapshots velocity {vfrom.isoformat()}~{vto.isoformat()}")

    # 인기 검색어 스냅샷
    tdb = cand_mod.trending_db(env)
    tsnap = latest_trending(tdb, cand.trending_geo)
    twhy = cand_mod._trending_problem(tsnap, cand, now, None)
    trending_n: int | None = None
    if tsnap is not None and not twhy:
        queries.append(f"trending snapshot geo={tsnap.geo} at={tsnap.collected_at.isoformat()}")
        picks = cand_mod.pick_trending(tsnap, cand)[: config.max_trending]
        trending_n = len(picks) or None
        for n, pick in enumerate(picks, start=1):
            hit = next(
                (t for t in topics if _key(pick.keyword) in {_key(x) for x in t.names()}), None
            )
            if hit is None:
                hit = _Topic(pick.keyword, origins=["trending_search"])
                topics.append(hit)
            elif "trending_search" not in hit.origins:
                hit.origins.append("trending_search")
            if hit.trending_rank is None:
                hit.trending_rank, hit.trending_traffic = n, pick.traffic
                hit.trending_raw = pick.item_keyword

    topics = _merge_pairs(topics, board.pairs())
    ranked = score_topics(
        topics,
        speeds=speeds,
        trending_n=trending_n,
        youtube_known=not ywhy,
        config=config,
    )

    # 주제 분류 (묶어서, 저장된 분류는 다시 묻지 않음). 카테고리별로 채우려고 후보 전부를 분류한다
    evidence: dict[str, list[str]] = {}
    for t, *_ in ranked:
        if t.video_ids:
            ids = t.video_ids[:EVIDENCE]
            titles = video_titles(db, ids)
            evidence[t.keyword] = [titles[i] for i in ids if titles.get(i)]
        elif tsnap is not None and t.trending_raw:
            found = news_titles(tdb, tsnap.geo, tsnap.collected_at, [t.trending_raw])
            evidence[t.keyword] = found.get(t.trending_raw, [])[:EVIDENCE]
        else:
            evidence[t.keyword] = []
    categories = await classify_topics(
        [TopicInput(t.keyword, tuple(t.aliases), tuple(evidence[t.keyword])) for t, *_ in ranked],
        llm,
        cache=category_cache,
        context_id=context_id,
    )

    every: list[PopularTopic] = []
    for rank, (t, score, speed, counted) in enumerate(ranked, start=1):
        cat = categories.get(t.keyword)
        verdict = board.verdict(t.names())
        velocity_reason = vwhy
        if speeds is not None and speed is None:
            velocity_reason = (
                "인기 영상에 나오지 않은 주제"
                if not t.video_ids
                else "이 주제 영상의 조회수를 두 스냅샷에서 모두 읽지 못함"
            )
        every.append(
            PopularTopic(
                rank=rank,
                keyword=t.keyword,
                aliases=t.aliases,
                origins=t.origins,  # type: ignore[arg-type]
                score=score,
                youtube_videos=len(t.video_ids) if not ywhy else None,
                video_ids=t.video_ids[:5],
                views_per_hour=speed,
                velocity_videos=counted,
                velocity_reason=velocity_reason if speed is None else None,
                trending_rank=t.trending_rank,
                trending_traffic=t.trending_traffic,
                topic_category=cat.category if cat else None,
                topic_category_reason=cat.reason if cat else None,
                brand_safe=(
                    None
                    if cat is None or cat.category == "unknown"
                    else cat.category not in exclude
                ),
                rising_now=(None if verdict is None else verdict.status == "rising"),
                rising_ratio=verdict.ratio if verdict is not None else None,
                real_entity=cat.real_entity if cat else None,
                entity_names=list(cat.entity_names) if cat else [],
                is_person=cat.is_person if cat else None,
            )
        )

    # 화면 카테고리마다 1~N위. 그 밖 분류·분류 못 한 주제는 응답에서 뺀다
    per = request.max_items or config.per_category
    from_youtube = sum(1 for t, *_ in ranked if t.video_ids)
    from_trending = sum(1 for t, *_ in ranked if t.trending_rank is not None)
    groups: list[PopularCategory] = []
    shown: list[PopularTopic] = []
    fill_map = dict(config.fill_youtube_categories)
    fill_store: FillStore | None = None
    counted = {c: sum(1 for i in every if i.topic_category == c) for c in config.categories}
    cells = [
        c for c in config.categories if c in fill_map and (config.fill_always or counted[c] < per)
    ]
    filled: dict[str, tuple[list[PopularVideo], str]] = {}
    if fill and cells:
        fill_store = FillStore(env.get(FILL_DB_ENV) or FILL_DEFAULT_DB)
        chart = _chart_rows(db, snapshot, now - timedelta(days=config.fill_days))
        filled = await fill_cells(
            config, cells, chart, youtube=youtube, store=fill_store, llm=llm,
            cache=category_cache, now=now, limit=per, queries=queries, context_id=context_id,
            search_terms=search_terms(config, every),
        )  # fmt: skip
    named = [(i.entity_names, i.is_person) for i in every if i.real_entity and i.entity_names]
    for category in config.categories:
        mine = [i for i in every if i.topic_category == category][:per]
        for n, item in enumerate(mine, start=1):
            item.category_rank = n
        shortage = None
        if len(mine) < per:
            shortage = (
                f"이 카테고리로 분류된 주제가 {len(mine)}개뿐 (본 후보: YouTube {from_youtube}개·"
                f"인기 검색어 {from_trending}개)"
            )
        top_videos, top_reason = filled.get(category, ([], None))
        # v2 칸: 음원은 영상 분류로, 실존 이름은 제목에 지금 인기 주제의 이름이 들었을 때만 (코드)
        top_videos = [
            caution.apply(v, caution.for_title(v.title, v.topic_category, named))
            for v in top_videos
        ]
        groups.append(
            PopularCategory(
                category=category,  # type: ignore[arg-type]
                items=mine,
                shortage_reason=shortage,
                top_videos=top_videos,
                top_videos_reason=top_reason,
            )
        )
        shown += mine
    shown.sort(key=lambda i: i.rank)
    for n, item in enumerate(shown, start=1):
        item.rank = n
    if youtube is not None and fill_store is None:
        fill_store = FillStore(env.get(FILL_DB_ENV) or FILL_DEFAULT_DB)
    await attach_videos(
        db, shown, youtube=youtube, store=fill_store, config=config, now=now, queries=queries
    )
    if fill_store is not None:
        fill_store.close()
    if youtube is not None and shown:
        # "왜 뜨나" 근거 (화면 경로만. 기획안 재료는 커넥터 없이 불러 이 근거를 쓰지 않는다)
        raw_names = {t.keyword: t.trending_raw for t, *_ in ranked if t.trending_raw}
        await add_evidence(
            shown, env=env, db=db, youtube=youtube, llm=llm, config=config, board=board,
            speeds=speeds or {}, tdb=tdb, tsnap=tsnap, raw_names=raw_names, now=now,
            queries=queries, context_id=context_id,
        )  # fmt: skip
    hidden = sum(1 for i in every if i.topic_category not in config.categories)
    items = shown

    empty = None
    if not items:
        empty = "인기 주제를 볼 출처가 없음" if ywhy and twhy else "인기 주제가 없음"
    known = [s for s, w in (("YouTube", ywhy), ("조회 속도", vwhy), ("인기 검색어", twhy)) if not w]
    summary = (
        f"지금 인기 주제 {len(items)}개, 화면 카테고리 {len(config.categories)}개"
        f" (쓴 출처: {', '.join(known) or '없음'}). 화면 카테고리가 아니거나 분류하지 못해 뺀"
        f" 주제 {hidden}개."
        if items
        else f"지금 인기 주제를 고르지 못함 ({empty})."
    )
    output = TrendPopularOutput(
        items=items,
        empty_reason=empty,
        youtube_status="unknown" if ywhy else "known",
        youtube_reason=ywhy,
        snapshot_at=snapshot.collected_at if snapshot else None,
        velocity_status="unknown" if vwhy else "known",
        velocity_reason=vwhy,
        velocity_from=vfrom,
        velocity_to=vto,
        trending_status="unknown" if twhy else "known",
        trending_reason=twhy,
        trending_at=tsnap.collected_at if tsnap else None,
        weights={
            "video": config.video_weight,
            "velocity": config.velocity_weight,
            "trending": config.trending_weight,
        },
        excluded_categories=exclude,
        summary=summary,
        by_category=groups,
        display_categories=list(config.categories),
        score_method="rank",
    )
    lines = [summary]
    for label, why in (("YouTube", ywhy), ("조회 속도", vwhy), ("인기 검색어", twhy)):
        if why:
            lines.append(f"{label}: 확인할 수 없음 ({why})")
    return PopularResult(
        output, Evidence(queries=queries, data_summary="\n".join(lines), collected_at=now)
    )
