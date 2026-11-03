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
- 카테고리 주제가 `per_category`보다 적으면, 회사 설정 `[popular] fill_youtube_categories`에 YouTube
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
from data_agent.connectors import ConnectorUnavailable
from data_agent.skills import trend_candidates as cand_mod
from data_agent.skills.content_recommend import load_config as load_recommend_config
from data_agent.skills.shorts_stats import is_language
from data_agent.skills.topic_category import (
    TOPIC_CATEGORIES,
    CategoryCache,
    TopicInput,
    classify_topics,
)
from data_agent.skills.trend_scan import TREND_CONFIG_ENV
from data_agent.skills.youtube_velocity import velocities
from data_agent.today_board import BOARD, TodayBoard
from data_agent.trending_store import latest_trending, news_titles
from data_agent.youtube_store import (
    DB_ENV,
    DEFAULT_DB,
    latest_popular,
    popular_history,
    recent_snapshots,
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
    fill_youtube_categories: tuple[tuple[str, str], ...] = ()
    fill_days: int = 7  # 최근 며칠 게시 영상에서 고를지
    fill_language: str = "ko"  # 그 언어 영상만 (""면 거르지 않음)


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
                (str(k).strip(), str(v).strip())
                for k, v in (raw.get("fill_youtube_categories") or {}).items()
                if str(k).strip() in TOPIC_CATEGORIES and str(v).strip()
            ),
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

    def close(self) -> None:
        self._conn.close()

    def get(self, day: date, cat: str, language: str) -> list[dict[str, Any]] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT videos FROM fill WHERE day = ? AND youtube_category = ? AND language = ?",
                (day.isoformat(), cat, language),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, day: date, cat: str, language: str, videos: list[dict[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO fill VALUES (?,?,?,?)",
                (day.isoformat(), cat, language, json.dumps(videos, ensure_ascii=False)),
            )


async def fill_videos(
    config: PopularConfig,
    youtube: Any,
    store: FillStore,
    yt_category: str,
    now: datetime,
    limit: int,
    queries: list[str],
) -> tuple[list[PopularVideo], str]:
    """YouTube 카테고리의 최근 조회수 높은 영상 (그날 저장한 결과가 있으면 그것).

    (영상, 고른 기준 또는 못 받은 이유).
    """
    today = now.astimezone(KST).date()
    lang = config.fill_language
    rows = store.get(today, yt_category, lang)
    if rows is None:
        if youtube is None or not hasattr(youtube, "search_category"):
            return [], "YouTube 커넥터가 없어 최근 조회수 높은 영상을 받지 않음"
        try:
            stats = await youtube.search_category(
                yt_category,
                now - timedelta(days=config.fill_days),
                language=lang or None,
            )
        except ConnectorUnavailable as error:
            return [], f"최근 조회수 높은 영상 검색 실패 [{error.code}]"
        queries.append(f"youtube search.list videoCategoryId={yt_category} order=viewCount")
        rows = [
            {
                "video_id": s.video_id,
                "title": s.title or None,
                "channel_title": s.channel_title or None,
                "views": s.view_count,
                "published_at": s.published_at.isoformat() if s.published_at else None,
                "duration_seconds": s.duration_seconds,
                "language": getattr(s, "language", None),
            }
            for s in stats
        ]
        store.put(today, yt_category, lang, rows)
    kept = [
        r
        for r in rows
        if not lang or is_language(lang, r.get("title"), r.get("channel_title"), r.get("language"))
    ]
    kept.sort(key=lambda r: -(r.get("views") or 0))
    videos = [
        PopularVideo(
            video_id=r["video_id"],
            title=r.get("title"),
            channel_title=r.get("channel_title"),
            views=r.get("views"),
            published_at=r.get("published_at"),
            duration_seconds=r.get("duration_seconds"),
        )
        for r in kept[:limit]
    ]
    basis = (
        f"지금 인기 아님: YouTube 카테고리 {yt_category}의 최근 {config.fill_days}일 게시 영상 중"
        f" 조회수 높은 순"
    )
    if lang:
        basis += f" ({lang} 영상만, 검색 {len(rows)}개 중 {len(kept)}개)"
    return videos, basis


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
        top_videos: list[PopularVideo] = []
        top_reason: str | None = None
        if shortage and category in fill_map:
            if fill_store is None:
                fill_store = FillStore(env.get(FILL_DB_ENV) or FILL_DEFAULT_DB)
            top_videos, top_reason = await fill_videos(
                config, youtube, fill_store, fill_map[category], now, per, queries
            )
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
    if fill_store is not None:
        fill_store.close()
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
