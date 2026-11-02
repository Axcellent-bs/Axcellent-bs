"""trend_candidates: YouTube 인기 영상 태그에서 후보 키워드를 뽑고 네이버 급상승을 붙인다.

읽기 전용(grade read).

- 후보 뽑기는 코드 규칙이다(LLM 안 씀). 최신 YouTube 스냅샷(`youtube_store`, #277)의
  인기 영상 태그를 정리(앞뒤 공백·# 제거, 영문 소문자)하고, `min_videos`개 이상 영상에 나온
  태그를 나온 영상 수 많은 순으로 최대 `max_candidates`개 고른다.
  회사 제외 목록(`exclude_tags`)은 뺀다.
- 별칭 묶기: 태그 정리 다음에 회사 별칭 표(`[candidates.aliases]`, 대표 이름 = [별칭…])로
  별칭을 대표 이름으로 바꾼다(lol → 리그오브레전드). 나온 영상 수는 영상 기준으로 중복 없이
  합친다(한 영상에 lol·리그오브레전드가 같이 있어도 1개). 표에 없는 이름은 묶지 않는다.
- 네이버 판정은 trend_scan과 같은 함수(`trend_scan.judge`)를 그대로 쓴다. 후보마다 한 그룹
  (대표 이름 + 별칭들을 키워드로)으로 한 번씩 부르고, 못 읽은 후보는 그 후보만 unknown이다.
- 별칭이 있으면 대표 이름·별칭을 하나씩 따로도 판정한다(이름마다 네이버 1번). 묶음 그룹은 평범인데
  이름 하나가 급상승이면 급상승으로 올리고, 어느 이름이 올랐는지(`rising_names`)를 남긴다.
- 스냅샷이 없거나 `max_age_hours`보다 오래됐으면 후보를 뽑지 않고 unknown + 이유다.
- 인기 검색어(Google Trends 공개 RSS 스냅샷, `trending_store`)도 후보 출처다. 정리·별칭·제외 규칙은
  태그와 같고, YouTube 후보와 이름이 겹치면 그 후보에 근거(`trending`)를 붙인다. 겹치지 않는 것은
  `trending_candidates`(최대 `[trending] max_candidates`개)로 따로 네이버 판정한다(같은 함수).
  인기 검색어 스냅샷이 없거나 오래되면 그 출처만 unknown + 이유다(YouTube 후보는 그대로).
- 설정: 회사 `trends.toml`의 `[candidates]`·`[trending]` (`DATA_TREND_CONFIG`).
  README "trend_candidates".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    TrendCandidate,
    TrendCandidatesOutput,
    TrendCandidatesRequest,
    TrendingCandidate,
    TrendingSearchEvidence,
    TrendKeywordGroup,
    TrendScanInput,
    TrendVerdict,
    YouTubeTagEvidence,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import KST, NaverDatalabConnector
from data_agent.skills.trend_scan import (
    DEFAULT_SETTINGS,
    REQUEST_DAYS,
    TREND_CONFIG_ENV,
    Settings,
    judge,
    yesterday_kst,
)
from data_agent.trending_store import DB_ENV as TRENDING_DB_ENV
from data_agent.trending_store import TrendingSnapshot, latest_trending
from data_agent.youtube_store import DB_ENV, DEFAULT_DB, PopularSnapshot, latest_popular

NAVER_FAILED = "네이버 검색어 트렌드를 읽지 못함"  # trend_scan과 같은 문장
MAX_KEYWORDS = 20  # 네이버 그룹 하나에 넣을 수 있는 키워드 수 (TrendKeywordGroup)


@dataclass(frozen=True)
class CandidateConfig:
    """회사 설정 `[candidates]`. 파일·표가 없으면 이 값(제외 목록·별칭 표는 비어 있음)."""

    max_candidates: int = 5
    min_videos: int = 2
    max_age_hours: float = 24.0
    rep_videos: int = 3
    exclude_tags: frozenset[str] = field(default_factory=frozenset)
    # 대표 이름 → 별칭들 (정리한 값, 설정 순서). 별칭 → 대표 이름은 `canonical()`
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # 인기 검색어 `[trending]`: 지역, YouTube와 겹치지 않는 후보 상한, 스냅샷 나이 기준
    trending_geo: str = "KR"
    trending_max: int = 5
    trending_max_age_hours: float = 24.0

    def canonical(self, tag: str) -> str:
        """정리한 태그의 대표 이름 (별칭이 아니면 그대로)."""
        for name, others in self.aliases.items():
            if tag in others:
                return name
        return tag

    def keywords(self, name: str) -> list[str]:
        """네이버 그룹에 넣을 키워드: 대표 이름 + 별칭들 (최대 20개)."""
        return [name, *self.aliases.get(name, ())][:MAX_KEYWORDS]


def clean_tag(tag: str) -> str:
    """앞뒤 공백·#을 떼고 영문을 소문자로 바꾼다 ("  #T1 " → "t1"). 남는 게 없으면 ""."""
    return tag.strip().strip("#").strip().lower()


def _load_aliases(raw: object) -> dict[str, tuple[str, ...]]:
    """`[candidates.aliases]` (대표 이름 = [별칭…]). 이름·별칭은 태그처럼 정리한다.

    별칭이 대표 이름과 같거나, 이미 다른 대표 이름의 별칭이거나, 그 자체가 대표 이름이면 뺀다
    (앞에 적은 것이 이긴다). 틀린 모양이면 `TypeError`.
    """
    if not isinstance(raw, Mapping):
        raise TypeError("aliases는 표여야 한다")
    names = [clean_tag(str(k)) for k in raw]
    taken: set[str] = set(n for n in names if n)
    out: dict[str, tuple[str, ...]] = {}
    for key, values in raw.items():
        name = clean_tag(str(key))
        if not name or not isinstance(values, list):
            raise TypeError("aliases의 값은 별칭 목록이어야 한다")
        mine: list[str] = []
        for value in values:
            other = clean_tag(str(value))
            if other and other not in taken:
                mine.append(other)
                taken.add(other)
        out[name] = tuple(mine[: MAX_KEYWORDS - 1])
    return out


def load_config(env: Mapping[str, str]) -> CandidateConfig:
    """`DATA_TREND_CONFIG`(회사 `trends.toml`)의 `[candidates]`. 없거나 틀리면 기본값."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return CandidateConfig()
    try:
        with Path(path).open("rb") as f:
            whole = tomllib.load(f)
        raw = whole.get("candidates") or {}
        trending = whole.get("trending") or {}
        base = CandidateConfig()
        return CandidateConfig(
            trending_geo=str(trending.get("geo") or base.trending_geo).strip().upper(),
            trending_max=int(trending.get("max_candidates", base.trending_max)),
            trending_max_age_hours=float(
                trending.get("max_age_hours", base.trending_max_age_hours)
            ),
            max_candidates=int(raw.get("max_candidates", base.max_candidates)),
            min_videos=int(raw.get("min_videos", base.min_videos)),
            max_age_hours=float(raw.get("max_age_hours", base.max_age_hours)),
            rep_videos=int(raw.get("rep_videos", base.rep_videos)),
            exclude_tags=frozenset(
                t for t in (clean_tag(str(x)) for x in raw.get("exclude_tags", [])) if t
            ),
            aliases=_load_aliases(raw.get("aliases", {})),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return CandidateConfig()


@dataclass(frozen=True)
class TagCount:
    keyword: str  # 대표 이름
    video_ids: tuple[str, ...]  # 인기 순위 순, 중복 없음
    matched: tuple[str, ...] = ()  # 이 후보로 센 정리된 태그 (처음 나온 순서)

    @property
    def video_count(self) -> int:
        return len(self.video_ids)


def pick_candidates(
    snapshot: PopularSnapshot, config: CandidateConfig, limit: int
) -> list[TagCount]:
    """정리한 태그를 대표 이름으로 바꿔 붙은 인기 영상을 세고 후보를 고른다.

    한 영상에 같은 이름이 여러 번(대소문자·# 차이, 별칭 포함) 붙어도 한 번만 센다. 정리한 태그나
    대표 이름이 제외 목록에 있으면 뺀다. 순서: 나온 영상 수 많은 순 → 그 이름 영상 중 가장 높은
    인기 순위 → 대표 이름.
    """
    videos: dict[str, list[str]] = {}
    matched: dict[str, list[str]] = {}
    best_rank: dict[str, int] = {}
    for video in sorted(snapshot.videos, key=lambda v: v.rank):
        names: dict[str, list[str]] = {}  # 이 영상에서 대표 이름 → 붙은 태그
        for tag in sorted({clean_tag(t) for t in video.tags}):
            name = config.canonical(tag)
            if not tag or tag in config.exclude_tags or name in config.exclude_tags:
                continue
            names.setdefault(name, []).append(tag)
        for name, tags in names.items():
            videos.setdefault(name, []).append(video.video_id)
            best_rank.setdefault(name, video.rank)
            seen = matched.setdefault(name, [])
            seen.extend(t for t in tags if t not in seen)
    picked = [
        TagCount(name, tuple(ids), tuple(matched[name]))
        for name, ids in videos.items()
        if len(ids) >= config.min_videos
    ]
    picked.sort(key=lambda c: (-c.video_count, best_rank[c.keyword], c.keyword))
    return picked[:limit]


def snapshot_cutoff(end_date: date | None) -> datetime | None:
    """지난 기간 판정의 스냅샷 기준 시각.

    판정 마지막 날(한국 날짜)이 끝나는 시각. `end_date`가 없으면 None(지금).
    """
    if end_date is None:
        return None
    day_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=KST) + timedelta(days=1)
    return day_end.astimezone(UTC)


def _snapshot_problem(
    snapshot: PopularSnapshot | None,
    config: CandidateConfig,
    now: datetime,
    cutoff: datetime | None = None,
) -> str | None:
    """`cutoff`(지난 기간 판정)가 있으면 그 시각 기준으로 나이를 잰다. 없으면 지금 기준."""
    when = cutoff or now
    if snapshot is None:
        if cutoff is not None:
            return (
                f"그 시점({cutoff.astimezone(KST).date().isoformat()} 0시 KST) 이전"
                " YouTube 스냅샷이 없음 (지난 기간은 그때 스냅샷의 태그로만 본다)"
            )
        return "YouTube 스냅샷이 없음 (인기 영상을 저장한 기록이 없음, data youtube_collect)"
    age = when - snapshot.collected_at
    if age > timedelta(hours=config.max_age_hours):
        hours = age.total_seconds() / 3600
        base = "판정 기간 끝" if cutoff is not None else "지금"
        return (
            f"YouTube 스냅샷이 오래됨: 마지막 수집 {snapshot.collected_at.isoformat()}"
            f" ({base}보다 {hours:.1f}시간 전, 기준 {config.max_age_hours:g}시간)"
        )
    if not snapshot.videos:
        return "YouTube 스냅샷에 인기 영상이 없음"
    return None


def _summary(candidates: list[TrendCandidate], videos: int) -> str:
    rising = [c.keyword for c in candidates if c.naver.status == "rising"]
    unknown = sum(c.naver.status == "unknown" for c in candidates)
    text = (
        f"YouTube 인기 영상 {videos}개의 태그에서 후보 {len(candidates)}개,"
        f" 네이버 급상승 {len(rising)}개"
    )
    if rising:
        text += f" ({', '.join(rising)})"
    if unknown:
        text += f", 판정 불가 {unknown}개"
    return text + "."


@dataclass(frozen=True)
class CandidatesResult:
    output: TrendCandidatesOutput
    evidence: Evidence


async def naver_verdict(
    query: TrendScanInput, connector: NaverDatalabConnector, settings: Settings
) -> TrendVerdict:
    """그룹 하나를 네이버에서 읽어 trend_scan과 같은 함수로 판정한다 (content_recommend도 쓴다).

    못 읽으면 이 후보만 unknown이다.
    """
    name = query.keyword_groups[0].group_name
    try:
        response = await connector.search_trend(query)
    except ConnectorUnavailable as error:
        return TrendVerdict(
            group_name=name, status="unknown", unknown_reason=f"{NAVER_FAILED} [{error.code}]"
        )
    results = judge(query, response, settings).results
    if not results:
        return TrendVerdict(
            group_name=name, status="unknown", unknown_reason="네이버 응답에 이 키워드가 없음"
        )
    return results[0]


def pick_verdict(
    group: TrendVerdict, names: list[TrendVerdict]
) -> tuple[TrendVerdict, Literal["group", "name"], list[str]]:
    """묶음 그룹 판정과 이름별 판정에서 후보의 판정을 고른다.

    - 이름별로 급상승인 이름 목록(`rising`)은 늘 남긴다
    - 묶음 그룹이 급상승이면 그 판정을 쓴다(`group`)
    - 묶음은 급상승이 아닌데(평범·판정 불가) 이름 하나라도 급상승이면, 급상승인 이름 중 z가 가장
      큰 판정을 올린다(`name`). 작은 이름의 급상승이 큰 이름에 묻히지 않게 한다
    """
    rising = [v for v in names if v.status == "rising"]
    rising_names = [v.group_name for v in rising]
    if group.status == "rising" or not rising:
        return group, "group", rising_names
    best = max(rising, key=lambda v: v.evidence.score or 0.0)
    return best, "name", rising_names


def _norm(name: str) -> str:
    """이름 비교용: 태그 정리 뒤 안쪽 공백도 뺀다 ("리그 오브 레전드" = "리그오브레전드")."""
    return clean_tag(name).replace(" ", "")


@dataclass(frozen=True)
class TrendingPick:
    keyword: str  # 정리한 이름 (별칭 표가 있으면 대표 이름)
    item_keyword: str  # 피드에 나온 그대로
    rank: int
    traffic: str | None


def pick_trending(snapshot: TrendingSnapshot, config: CandidateConfig) -> list[TrendingPick]:
    """인기 검색어를 태그와 같은 규칙(정리·별칭·제외 목록)으로 거른다.

    피드 순위 순, 이름마다 한 번.
    """
    out: list[TrendingPick] = []
    seen: set[str] = set()
    for item in snapshot.items:
        tag = clean_tag(item.keyword)
        name = config.canonical(tag)
        if not tag or tag in config.exclude_tags or name in config.exclude_tags:
            continue
        if _norm(name) in seen:
            continue
        seen.add(_norm(name))
        out.append(TrendingPick(name, item.keyword, item.rank, item.traffic))
    return out


def trending_db(env: Mapping[str, str]) -> str:
    """인기 검색어 DB: `DATA_TRENDING_DB`, 없으면 YouTube 스냅샷 DB와 같은 폴더의 기본 파일."""
    if env.get(TRENDING_DB_ENV):
        return str(env[TRENDING_DB_ENV])
    youtube = Path(env.get(DB_ENV) or str(DEFAULT_DB))
    return str(youtube.parent / "trending_snapshots.db")


def _trending_problem(
    snapshot: TrendingSnapshot | None,
    config: CandidateConfig,
    now: datetime,
    cutoff: datetime | None,
) -> str | None:
    when = cutoff or now
    if snapshot is None:
        if cutoff is not None:
            return (
                f"그 시점({cutoff.astimezone(KST).date().isoformat()} 0시 KST) 이전"
                " 인기 검색어 스냅샷이 없음"
            )
        return "인기 검색어 스냅샷이 없음 (수집 기록이 없음, data trending_collect)"
    age = when - snapshot.collected_at
    if age > timedelta(hours=config.trending_max_age_hours):
        hours = age.total_seconds() / 3600
        base = "판정 기간 끝" if cutoff is not None else "지금"
        return (
            f"인기 검색어 스냅샷이 오래됨: 마지막 수집 {snapshot.collected_at.isoformat()}"
            f" ({base}보다 {hours:.1f}시간 전, 기준 {config.trending_max_age_hours:g}시간)"
        )
    if not snapshot.items:
        return "인기 검색어 스냅샷에 검색어가 없음"
    return None


@dataclass
class Judged:
    keywords: list[str]  # 네이버 그룹 키워드 (대표 이름 + 별칭)
    verdict: TrendVerdict
    source: Literal["group", "name"]
    rising: list[str]
    names: list[TrendVerdict]


async def judge_name(
    keyword: str,
    config: CandidateConfig,
    connector: NaverDatalabConnector,
    settings: Settings,
    start: date,
    end: date,
    queries: list[str],
) -> tuple[Judged, int]:
    """후보 하나: 묶음 그룹 한 번 + 별칭이 있으면 이름마다 한 번. (판정, 네이버 부른 수)."""
    keywords = config.keywords(keyword)
    query = TrendScanInput(
        keyword_groups=[TrendKeywordGroup(group_name=keyword, keywords=keywords)],
        start_date=start,
        end_date=end,
    )
    queries.append(
        f"naver search-trend group={keyword} keywords={','.join(keywords)} {start}~{end} date"
    )
    group = await naver_verdict(query, connector, settings)
    requests = 1
    names: list[TrendVerdict] = []
    if len(keywords) > 1:  # 별칭이 있으면 이름마다 따로도 판정한다 (작은 이름이 묻히지 않게)
        for name in keywords:
            one = TrendScanInput(
                keyword_groups=[TrendKeywordGroup(group_name=name, keywords=[name])],
                start_date=start,
                end_date=end,
            )
            queries.append(f"naver search-trend group={name} {start}~{end} date")
            names.append(await naver_verdict(one, connector, settings))
            requests += 1
    verdict, source, rising = pick_verdict(group, names)
    return Judged(keywords, verdict, source, rising, names), requests


def _trending_summary(found: list[TrendingCandidate], attached: int) -> str:
    rising = [c.keyword for c in found if c.naver.status == "rising"]
    text = f" 인기 검색어 후보 {len(found)}개(YouTube 후보와 겹친 {attached}개 제외)"
    text += f", 그중 네이버 급상승 {len(rising)}개"
    if rising:
        text += f" ({', '.join(rising)})"
    return text + "."


async def find_candidates(
    request: TrendCandidatesRequest,
    connector: NaverDatalabConnector,
    *,
    env: Mapping[str, str],
    settings: Settings = DEFAULT_SETTINGS,
    now: datetime | None = None,
) -> CandidatesResult:
    """최신 스냅샷(YouTube·인기 검색어)에서 후보를 뽑아 후보마다 네이버로 판정한다.

    상태를 바꾸지 않는다.
    """
    now = now or datetime.now(UTC)
    config = load_config(env)
    db = env.get(DB_ENV) or str(DEFAULT_DB)
    # 지난 기간(end_date를 준 요청)은 그 기간 끝 이전의 스냅샷을 쓴다. 비우면 지금 가장 최근 스냅샷
    cutoff = snapshot_cutoff(request.end_date)
    snapshot = latest_popular(db, cutoff)
    snapshot_query = f"youtube snapshot popular latest db={Path(db).name}"
    why = _snapshot_problem(snapshot, config, now, cutoff)

    tdb = trending_db(env)
    tsnap = latest_trending(tdb, config.trending_geo, cutoff)
    twhy = _trending_problem(tsnap, config, now, cutoff)
    tpicks = pick_trending(tsnap, config) if tsnap is not None and not twhy else []

    end = request.end_date or yesterday_kst(now)
    start = end - timedelta(days=REQUEST_DAYS - 1)
    queries = [snapshot_query]
    if snapshot is not None and not why:
        queries = [f"{snapshot_query} at={snapshot.collected_at.isoformat()}"]
    if tsnap is not None:
        queries.append(
            f"trending snapshot geo={tsnap.geo} at={tsnap.collected_at.isoformat()}"
            f" db={Path(tdb).name}"
        )
    requests = 0

    tags: list[TagCount] = []
    if snapshot is not None and not why:
        tags = pick_candidates(snapshot, config, request.max_candidates or config.max_candidates)
    youtube_names = {_norm(t.keyword) for t in tags}
    attach = {_norm(p.keyword): p for p in tpicks if _norm(p.keyword) in youtube_names}
    alone = [p for p in tpicks if _norm(p.keyword) not in youtube_names][: config.trending_max]

    def evidence_of(pick: TrendingPick, at: datetime) -> TrendingSearchEvidence:
        return TrendingSearchEvidence(
            rank=pick.rank, traffic=pick.traffic, snapshot_at=at, keyword=pick.item_keyword
        )

    candidates: list[TrendCandidate] = []
    for tag in tags:
        judged, n = await judge_name(tag.keyword, config, connector, settings, start, end, queries)
        requests += n
        hit = attach.get(_norm(tag.keyword))
        candidates.append(
            TrendCandidate(
                keyword=tag.keyword,
                youtube=YouTubeTagEvidence(
                    video_count=tag.video_count,
                    snapshot_at=snapshot.collected_at if snapshot else now,
                    video_ids=list(tag.video_ids[: config.rep_videos]),
                    matched_tags=list(tag.matched),
                ),
                naver=judged.verdict,
                aliases=judged.keywords[1:],
                name_verdicts=judged.names,
                rising_names=judged.rising,
                naver_from=judged.source,
                origins=["youtube_tag", "trending_search"] if hit else ["youtube_tag"],
                trending=evidence_of(hit, tsnap.collected_at) if hit and tsnap else None,
            )
        )
    trending: list[TrendingCandidate] = []
    for pick in alone:
        assert tsnap is not None  # alone이 있으면 스냅샷이 있다
        judged, n = await judge_name(pick.keyword, config, connector, settings, start, end, queries)
        requests += n
        trending.append(
            TrendingCandidate(
                keyword=pick.keyword,
                trending=evidence_of(pick, tsnap.collected_at),
                naver=judged.verdict,
                aliases=judged.keywords[1:],
                name_verdicts=judged.names,
                rising_names=judged.rising,
                naver_from=judged.source,
            )
        )

    tail = "" if twhy else _trending_summary(trending, len(attach))
    trending_status: Literal["known", "unknown"] = "unknown" if twhy else "known"
    if snapshot is None or why:
        why = why or ""
        summary = f"확인할 수 없음: {why}" + ("." + tail if tail else "")
        output = TrendCandidatesOutput(
            status="unknown",
            unknown_reason=why,
            snapshot_at=snapshot.collected_at if snapshot else None,
            snapshot_videos=len(snapshot.videos) if snapshot else 0,
            start_date=start if requests else None,
            end_date=end if requests else None,
            naver_calls=requests,
            summary=summary,
            trending_candidates=trending,
            trending_status=trending_status,
            trending_reason=twhy,
            trending_snapshot_at=tsnap.collected_at if tsnap else None,
        )
    else:
        summary = _summary(candidates, len(snapshot.videos)) + tail
        output = TrendCandidatesOutput(
            status="known",
            snapshot_at=snapshot.collected_at,
            snapshot_videos=len(snapshot.videos),
            start_date=start,
            end_date=end,
            candidates=candidates,
            naver_calls=requests,
            summary=summary,
            trending_candidates=trending,
            trending_status=trending_status,
            trending_reason=twhy,
            trending_snapshot_at=tsnap.collected_at if tsnap else None,
        )
    failed = [
        c.keyword
        for c in [*candidates, *trending]
        if (c.naver.unknown_reason or "").startswith(NAVER_FAILED)
    ]
    notes = [summary] + (["네이버를 읽지 못한 후보: " + ", ".join(failed)] if failed else [])
    if twhy:
        notes.append(f"인기 검색어 후보 없음: {twhy}")
    evidence = Evidence(queries=queries, data_summary="\n".join(notes), collected_at=now)
    return CandidatesResult(output, evidence)
