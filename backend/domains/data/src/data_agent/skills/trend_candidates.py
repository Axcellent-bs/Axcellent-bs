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
- 스냅샷이 없거나 `max_age_hours`보다 오래됐으면 후보를 뽑지 않고 unknown + 이유다.
- 설정: 회사 `trends.toml`의 `[candidates]` (`DATA_TREND_CONFIG`). README "trend_candidates".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    TrendCandidate,
    TrendCandidatesOutput,
    TrendCandidatesRequest,
    TrendKeywordGroup,
    TrendScanInput,
    TrendVerdict,
    YouTubeTagEvidence,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import NaverDatalabConnector
from data_agent.skills.trend_scan import (
    DEFAULT_SETTINGS,
    REQUEST_DAYS,
    TREND_CONFIG_ENV,
    Settings,
    judge,
    yesterday_kst,
)
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
            raw = tomllib.load(f).get("candidates") or {}
        base = CandidateConfig()
        return CandidateConfig(
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


def _snapshot_problem(
    snapshot: PopularSnapshot | None, config: CandidateConfig, now: datetime
) -> str | None:
    if snapshot is None:
        return "YouTube 스냅샷이 없음 (인기 영상을 저장한 기록이 없음, data youtube_collect)"
    age = now - snapshot.collected_at
    if age > timedelta(hours=config.max_age_hours):
        hours = age.total_seconds() / 3600
        return (
            f"YouTube 스냅샷이 오래됨: 마지막 수집 {snapshot.collected_at.isoformat()}"
            f" ({hours:.1f}시간 전, 기준 {config.max_age_hours:g}시간)"
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


async def find_candidates(
    request: TrendCandidatesRequest,
    connector: NaverDatalabConnector,
    *,
    env: Mapping[str, str],
    settings: Settings = DEFAULT_SETTINGS,
    now: datetime | None = None,
) -> CandidatesResult:
    """최신 스냅샷에서 후보를 뽑고 후보마다 네이버를 한 번 불러 판정한다. 상태를 바꾸지 않는다."""
    now = now or datetime.now(UTC)
    config = load_config(env)
    db = env.get(DB_ENV) or str(DEFAULT_DB)
    snapshot = latest_popular(db)
    snapshot_query = f"youtube snapshot popular latest db={Path(db).name}"
    why = _snapshot_problem(snapshot, config, now)
    if snapshot is None or why:
        why = why or ""
        output = TrendCandidatesOutput(
            status="unknown",
            unknown_reason=why,
            snapshot_at=snapshot.collected_at if snapshot else None,
            snapshot_videos=len(snapshot.videos) if snapshot else 0,
            summary=f"확인할 수 없음: {why}",
        )
        evidence = Evidence(queries=[snapshot_query], data_summary=output.summary, collected_at=now)
        return CandidatesResult(output, evidence)
    tags = pick_candidates(snapshot, config, request.max_candidates or config.max_candidates)
    end = request.end_date or yesterday_kst(now)
    start = end - timedelta(days=REQUEST_DAYS - 1)
    candidates: list[TrendCandidate] = []
    queries = [f"{snapshot_query} at={snapshot.collected_at.isoformat()}"]
    for tag in tags:
        keywords = config.keywords(tag.keyword)
        query = TrendScanInput(
            keyword_groups=[TrendKeywordGroup(group_name=tag.keyword, keywords=keywords)],
            start_date=start,
            end_date=end,
        )
        queries.append(
            f"naver search-trend group={tag.keyword} keywords={','.join(keywords)}"
            f" {start}~{end} date"
        )
        candidates.append(
            TrendCandidate(
                keyword=tag.keyword,
                youtube=YouTubeTagEvidence(
                    video_count=tag.video_count,
                    snapshot_at=snapshot.collected_at,
                    video_ids=list(tag.video_ids[: config.rep_videos]),
                    matched_tags=list(tag.matched),
                ),
                naver=await naver_verdict(query, connector, settings),
                aliases=keywords[1:],
            )
        )
    summary = _summary(candidates, len(snapshot.videos))
    output = TrendCandidatesOutput(
        status="known",
        snapshot_at=snapshot.collected_at,
        snapshot_videos=len(snapshot.videos),
        start_date=start,
        end_date=end,
        candidates=candidates,
        naver_calls=len(tags),
        summary=summary,
    )
    failed = [
        c.keyword for c in candidates if (c.naver.unknown_reason or "").startswith(NAVER_FAILED)
    ]
    notes = [summary] + (["네이버를 읽지 못한 후보: " + ", ".join(failed)] if failed else [])
    evidence = Evidence(queries=queries, data_summary="\n".join(notes), collected_at=now)
    return CandidatesResult(output, evidence)
