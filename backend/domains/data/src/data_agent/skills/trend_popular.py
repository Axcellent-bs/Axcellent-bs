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
  - 영상 몫: 그 주제가 나온 인기 영상 수 ÷ 가장 많은 주제의 영상 수
  - 조회 속도 몫: 그 영상들의 지금 시간당 조회 증가 합(youtube_velocity와 같은 계산) ÷ 가장 큰 합
  - 검색어 순위 몫: (N − 순위 + 1) ÷ N (N = 이번에 본 인기 검색어 수). 인기 검색어에 없으면 0
  - 출처 하나를 못 읽으면(스냅샷 없음·오래됨, 스냅샷 2개 미만) 그 몫은 **unknown + 이유**이고
    점수에서 뺀다(0으로 채우지 않음). 주제에 영상이 있는데 속도를 하나도 못 재면 그 주제의 속도 몫만
    unknown
- 주제 분류: content_recommend와 같은 `classify_topics`(LLM 한 번에 묶어서, 주제별 하루 캐시).
  회사 "추천 안 함" 분류(`[recommend] exclude_categories`)에 들면 `brand_safe: false`
- "지금 급등 중" 배지: 오늘 이미 낸 네이버 판정(today_board)이 급상승이면 표시. 판정이 없으면 비운다
README "trend_popular".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    PopularScore,
    PopularTopic,
    TrendPopularOutput,
    TrendPopularRequest,
)
from data_agent.skills import trend_candidates as cand_mod
from data_agent.skills.content_recommend import load_config as load_recommend_config
from data_agent.skills.topic_category import CategoryCache, TopicInput, classify_topics
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

    top: int = 10
    max_youtube: int = 30
    max_trending: int = 20
    video_weight: float = 1.0
    velocity_weight: float = 1.0
    trending_weight: float = 1.0


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
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return PopularConfig()


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
    max_videos = max((len(t.video_ids) for t in topics), default=0)
    max_speed = max((s for s, _ in sums.values() if s is not None and s > 0), default=0.0)
    out = []
    for i, t in enumerate(topics):
        video_share = (
            round(len(t.video_ids) / max_videos, 4) if youtube_known and max_videos else None
        )
        if not youtube_known:
            video_share = None
        speed, counted = sums[i]
        if speeds is not None and not t.video_ids:
            velocity_share = 0.0  # 인기 영상에 없는 주제 (속도를 못 잰 것이 아님)
        elif speeds is None or speed is None:
            velocity_share = None
        else:
            velocity_share = round(max(speed, 0.0) / max_speed, 4) if max_speed else 0.0
        if trending_n:
            trending_share = (
                round((trending_n - t.trending_rank + 1) / trending_n, 4)
                if t.trending_rank is not None
                else 0.0
            )
        else:
            trending_share = None
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
    )[: request.max_items or config.top]

    # 주제 분류 (묶어서 한 번, 하루 캐시)
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

    items: list[PopularTopic] = []
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
        items.append(
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
            )
        )

    empty = None
    if not items:
        empty = "인기 주제를 볼 출처가 없음" if ywhy and twhy else "인기 주제가 없음"
    known = [s for s, w in (("YouTube", ywhy), ("조회 속도", vwhy), ("인기 검색어", twhy)) if not w]
    summary = (
        f"지금 인기 주제 {len(items)}개 (쓴 출처: {', '.join(known) or '없음'})."
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
    )
    lines = [summary]
    for label, why in (("YouTube", ywhy), ("조회 속도", vwhy), ("인기 검색어", twhy)):
        if why:
            lines.append(f"{label}: 확인할 수 없음 ({why})")
    return PopularResult(
        output, Evidence(queries=queries, data_summary="\n".join(lines), collected_at=now)
    )
