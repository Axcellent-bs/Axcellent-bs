"""content_recommend: 지금 만들 만한 영상 소재 추천 (콘텐츠 루프 2단계). 읽기 전용(grade read).

- 후보: 회사 기본 키워드 그룹(trend_scan과 같은 요청·판정)과 YouTube 인기 태그 후보
  (trend_candidates) 중 **급상승(rising)으로 판정된 것만**. 평범·판정 불가는 넣지 않는다.
  급상승이 없으면 빈 목록 + 이유("이번 기간 급상승한 소재가 없음")이고 억지로 채우지 않는다.
- 순위(코드 규칙, LLM 안 씀): 점수 = `z_weight` × z + (검색 급상승 + YouTube 근거 두 소스면
  `two_source_bonus`). 같으면 z 큰 순 → 이름. 가중치·개수는 회사 `trends.toml` `[recommend]`.
- 회사 관련성: 소재 이름(같이 본 이름 포함)이 회사 키워드(기본 키워드 그룹 이름·키워드 +
  `company_keywords`)와 겹치면 related, 아니면 low. **표시만** 하고 순위에서 빼지 않는다.
- 같은 소재: 기본 키워드 그룹과 YouTube 후보의 이름(같이 본 이름 포함)이 겹치면 한 소재로 본다.
  그 그룹이 급상승이면 그 판정을 쓰고 YouTube 근거를 붙인다(두 소스).
- 아이디어 문장(LLM): 소재마다 `explain.explain_idea`. 프롬프트의 숫자는 배수 하나("평소보다 N배")
  뿐이고 z·영상 수는 넣지 않는다. 회사 소개(`company_intro`)를 넣어 구체적으로 쓰게 한다.
  이름(외부 태그 등)은 데이터 블록에만 넣는다. 숫자 검증에 걸리면 고정 문장 + fallback.
- 네이버: trend_scan·trend_candidates와 같은 요청 모양이라 오늘 이미 부른 요청은 커넥터의 하루
  캐시에서 나온다. 이번 실행에서 실제로 부른 수(`naver_calls`)를 출력에 남긴다.
README "content_recommend".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    ContentRecommendation,
    ContentRecommendOutput,
    ContentRecommendRequest,
    ContentScore,
    ContentSource,
    TrendCandidate,
    TrendCandidatesRequest,
    TrendKeywordGroup,
    TrendScanInput,
    TrendVerdict,
)
from data_agent.connectors.naver_datalab import NaverDatalabConnector
from data_agent.explain import explain_idea
from data_agent.skills.trend_candidates import (
    NAVER_FAILED,
    clean_tag,
    find_candidates,
    naver_verdict,
)
from data_agent.skills.trend_scan import (
    DEFAULT_SETTINGS,
    REQUEST_DAYS,
    TREND_CONFIG_ENV,
    Settings,
    default_groups,
    yesterday_kst,
)

NO_RISING = "이번 기간 급상승한 소재가 없음"


@dataclass(frozen=True)
class RecommendConfig:
    """회사 설정 `[recommend]`. 파일·표가 없으면 이 값(회사 키워드는 기본 키워드 그룹만)."""

    max_items: int = 5
    z_weight: float = 1.0
    two_source_bonus: float = 2.0
    company_keywords: tuple[str, ...] = ()
    company_intro: str = ""  # 아이디어 문장 프롬프트에 넣는 회사 소개 (무엇을 파는지, 고객)


def load_config(env: Mapping[str, str]) -> RecommendConfig:
    """`DATA_TREND_CONFIG`(회사 `trends.toml`)의 `[recommend]`. 없거나 틀리면 기본값."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return RecommendConfig()
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("recommend") or {}
        base = RecommendConfig()
        return RecommendConfig(
            max_items=int(raw.get("max_items", base.max_items)),
            z_weight=float(raw.get("z_weight", base.z_weight)),
            two_source_bonus=float(raw.get("two_source_bonus", base.two_source_bonus)),
            company_keywords=tuple(str(k) for k in raw.get("company_keywords", [])),
            company_intro=str(raw.get("company_intro") or "").strip(),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return RecommendConfig()


def _norm(name: str) -> str:
    """이름 비교용: 태그 정리(앞뒤 공백·#, 영문 소문자) 뒤 안쪽 공백도 뺀다."""
    return clean_tag(name).replace(" ", "")


def company_match(names: Sequence[str], company: Sequence[str]) -> list[str]:
    """소재 이름들과 겹치는 회사 키워드.

    한쪽이 다른 쪽을 품으면 겹친다고 본다(공백·대소문자 무시).
    """
    mine = [n for n in (_norm(x) for x in names) if n]
    hits: list[str] = []
    for word in company:
        key = _norm(word)
        if key and any(key in n or (len(n) >= 2 and n in key) for n in mine) and word not in hits:
            hits.append(word)
    return hits


@dataclass
class Topic:
    """급상승 소재 하나 (순위 매기기 전)."""

    keyword: str
    aliases: list[str]
    verdict: TrendVerdict
    youtube_videos: int | None = None  # YouTube 근거가 있으면 인기 영상 수
    sources: list[ContentSource] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [self.keyword, *self.aliases]

    @property
    def two_sources(self) -> bool:
        return "naver" in self.sources and "youtube" in self.sources


def score(topic: Topic, config: RecommendConfig) -> float:
    z = topic.verdict.evidence.score or 0.0
    bonus = config.two_source_bonus if topic.two_sources else 0.0
    return round(config.z_weight * z + bonus, 4)


def rank_topics(topics: list[Topic], config: RecommendConfig, limit: int) -> list[Topic]:
    """급상승만 남기고 점수 큰 순(같으면 z 큰 순 → 이름)으로 `limit`개."""
    rising = [t for t in topics if t.verdict.status == "rising"]
    rising.sort(key=lambda t: (-score(t, config), -(t.verdict.evidence.score or 0.0), t.keyword))
    return rising[:limit]


def merge_topics(
    groups: list[tuple[TrendKeywordGroup, TrendVerdict]], candidates: list[TrendCandidate]
) -> list[Topic]:
    """기본 키워드 그룹과 YouTube 후보를 소재로 합친다 (이름이 겹치면 한 소재).

    그룹이 급상승이면 그 판정에 YouTube 근거를 붙인다. 그룹이 급상승이 아니면 후보 판정을 쓴다.
    """
    topics: list[Topic] = []
    used: set[str] = set()
    for group, verdict in groups:
        names = {_norm(n) for n in [group.group_name, *group.keywords]}
        match = next(
            (c for c in candidates if names & {_norm(n) for n in [c.keyword, *c.aliases]}), None
        )
        aliases = [k for k in group.keywords if _norm(k) != _norm(group.group_name)]
        if match is not None and verdict.status != "rising":
            continue  # 후보 쪽 판정으로 아래에서 다룬다
        topic = Topic(group.group_name, aliases, verdict, sources=["naver"])
        if match is not None:
            used.add(match.keyword)
            topic.youtube_videos = match.youtube.video_count
            topic.sources.append("youtube")
            topic.aliases += [a for a in [match.keyword, *match.aliases] if _norm(a) not in names]
        topics.append(topic)
    for c in candidates:
        if c.keyword in used:
            continue
        topics.append(
            Topic(c.keyword, list(c.aliases), c.naver, c.youtube.video_count, ["naver", "youtube"])
        )
    return topics


@dataclass(frozen=True)
class RecommendResult:
    output: ContentRecommendOutput
    evidence: Evidence
    fallback: bool  # 아이디어 문장 중 하나라도 고정 문장으로 바꿨는지


def _calls(connector: Any) -> int | None:
    value = getattr(connector, "calls_today", None)
    return value if isinstance(value, int) else None


async def _judge_groups(
    groups: list[TrendKeywordGroup],
    connector: NaverDatalabConnector,
    start: date,
    end: date,
    settings: Settings,
) -> list[tuple[TrendKeywordGroup, TrendVerdict]]:
    """회사 기본 키워드 그룹을 trend_scan과 같은 요청(그룹마다 한 번)·판정으로 본다."""
    out = []
    for group in groups:
        query = TrendScanInput(keyword_groups=[group], start_date=start, end_date=end)
        out.append((group, await naver_verdict(query, connector, settings)))
    return out


def _summary(items: list[ContentRecommendation], calls: int) -> str:
    if not items:
        return f"추천할 영상 소재가 없습니다 ({NO_RISING}). 네이버 호출 {calls}번."
    both = sum(i.score.two_sources for i in items)
    names = ", ".join(i.keyword for i in items)
    return (
        f"영상 소재 {len(items)}개 추천 ({names}), 검색 급상승 + YouTube 근거 {both}개."
        f" 네이버 호출 {calls}번."
    )


async def recommend(
    request: ContentRecommendRequest,
    connector: NaverDatalabConnector,
    llm: LLMClient,
    *,
    env: Mapping[str, str],
    settings: Settings = DEFAULT_SETTINGS,
    now: datetime | None = None,
    context_id: str | None = None,
) -> RecommendResult:
    """급상승 소재를 순위대로 고르고 소재마다 아이디어 문장을 붙인다. 상태를 바꾸지 않는다."""
    now = now or datetime.now(UTC)
    config = load_config(env)
    before = _calls(connector)
    end = request.end_date or yesterday_kst(now)
    start = end - timedelta(days=REQUEST_DAYS - 1)

    groups = default_groups(env)
    judged = await _judge_groups(groups, connector, start, end, settings)
    found = await find_candidates(
        TrendCandidatesRequest(end_date=end), connector, env=env, settings=settings, now=now
    )
    cand = found.output
    topics = merge_topics(judged, cand.candidates)
    limit = request.max_items or config.max_items
    picked = rank_topics(topics, config, limit)

    company = [g.group_name for g in groups] + [k for g in groups for k in g.keywords]
    company += list(config.company_keywords)
    items: list[ContentRecommendation] = []
    notes: list[str] = []
    for rank, topic in enumerate(picked, start=1):
        v = topic.verdict
        z = float(v.evidence.score or 0.0)
        ratio = float(v.ratio or 0.0)
        # 문장에 쓸 수 있는 숫자는 배수 하나뿐이다(z는 순위 근거로만, 영상 수는 고정 문장에만)
        numbers: dict[str, int | float | str] = {"배수": ratio}
        names = {"키워드": topic.keyword}
        if topic.aliases:
            names["같이 본 이름"] = ", ".join(topic.aliases)
        idea = await explain_idea(
            numbers,
            names,
            llm,
            context_id=context_id,
            company=config.company_intro,
            sources="검색 급상승 + YouTube 인기 영상" if topic.two_sources else "검색 급상승",
            youtube_videos=topic.youtube_videos,
        )
        if note := idea.note():
            notes.append(f"{topic.keyword}: {note}")
        matched = company_match(topic.names, company)
        items.append(
            ContentRecommendation(
                rank=rank,
                keyword=topic.keyword,
                aliases=topic.aliases,
                sources=topic.sources,
                score=ContentScore(
                    score=score(topic, config),
                    z=z,
                    ratio=ratio,
                    two_sources=topic.two_sources,
                    youtube_videos=topic.youtube_videos if topic.two_sources else None,
                ),
                company_relevance="related" if matched else "low",
                company_match=matched,
                idea=idea.idea,
                format=idea.format,  # type: ignore[arg-type]  # IDEA_FORMATS
                fallback=idea.fallback,
                fallback_reason=idea.reason,
            )
        )

    failed = [g.group_name for g, v in judged if (v.unknown_reason or "").startswith(NAVER_FAILED)]
    failed += [
        c.keyword
        for c in cand.candidates
        if (c.naver.unknown_reason or "").startswith(NAVER_FAILED)
    ]
    after = _calls(connector)
    calls = (after - before) if before is not None and after is not None else 0
    empty_reason = None
    if not items:
        empty_reason = NO_RISING
        if failed:
            empty_reason += f" (네이버를 읽지 못한 것: {', '.join(failed)})"
    output = ContentRecommendOutput(
        start_date=start,
        end_date=end,
        items=items,
        empty_reason=empty_reason,
        naver_calls=calls,
        naver_failed=failed,
        youtube_status=cand.status,
        youtube_reason=cand.unknown_reason,
        snapshot_at=cand.snapshot_at,
        summary=_summary(items, calls),
    )
    queries = [f"naver search-trend group={g.group_name} {start}~{end} date" for g in groups]
    queries += found.evidence.queries
    lines = [output.summary]
    if failed:
        lines.append("네이버를 읽지 못한 것: " + ", ".join(failed))
    if cand.status == "unknown":
        lines.append(f"YouTube 후보 없음: {cand.unknown_reason}")
    lines += notes
    evidence = Evidence(queries=queries, data_summary="\n".join(lines), collected_at=now)
    return RecommendResult(output, evidence, any(i.fallback for i in items))
