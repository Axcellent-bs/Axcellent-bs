"""브리프·기회 카드 응답의 숫자 목록(`facts`). 숫자는 모두 코드가 낸 값 그대로다.

- 응답에 나오는 숫자는 전부 `DataFact`(fact_id·label·value·unit·source·link·period·sample·about)로
  남긴다. 인사이트·카드는 `fact_ids`로 가리킨다
- 한 줄(LLM) 검증에 쓰는 숫자(`trend_insight_brief.facts`·`domain_opportunity.facts`)도 모두
  여기 있다. 그래서 "문장 속 숫자가 facts에 없으면 고정 문장"은 기존 숫자 검증과 같은 규칙이다
- fact_id = 항목 id + 이름의 해시 (같은 항목의 같은 칸이면 같은 id)
- 모르는 칸은 넣지 않는다 (0으로 채우지 않음)
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime

from ax_contracts.schemas.data import DataFact, DomainOpportunity, TrendInsight


def fact_id(about: str, label: str) -> str:
    return "f_" + hashlib.sha256(f"{about}|{label}".encode()).hexdigest()[:12]


class _Facts:
    def __init__(self, about: str) -> None:
        self.about = about
        self.rows: list[DataFact] = []

    def add(
        self, label: str, value: float | None, unit: str, source: str, *,
        link: str | None = None, period: str | None = None, sample: int | None = None,
    ) -> None:  # fmt: skip
        if value is None or isinstance(value, bool):
            return
        self.rows.append(
            DataFact(
                fact_id=fact_id(self.about, label),
                label=label,
                value=float(value),
                unit=unit,
                source=source,
                link=link,
                period=period,
                sample=sample,
                about=self.about,
            )  # fmt: skip
        )


def _at(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def insight_facts(i: TrendInsight) -> list[DataFact]:
    """인사이트 하나의 숫자 (근거·연령대·상품·숏츠 형식·점수)."""
    e = i.evidence
    f = _Facts(i.insight_id)
    yt_link = next((x for x in e.links if "youtube.com" in x), None)
    news = next((n.url for n in e.google_news if n.url), None)
    f.add("YouTube 인기 영상 수", e.youtube_videos or None, "개", "youtube", link=yt_link,
          period=_at(e.youtube_at), sample=e.youtube_videos)  # fmt: skip
    if e.youtube_views_per_hour is not None:
        f.add("지금 시간당 조회", round(e.youtube_views_per_hour), "회/시간", "youtube",
              link=yt_link, period=_at(e.youtube_at), sample=e.youtube_videos)  # fmt: skip
    f.add("구독자 대비 조회 최대 배수", e.youtube_views_per_subscriber, "배", "youtube",
          link=yt_link, period=_at(e.youtube_at))  # fmt: skip
    f.add("Google 인기 검색어 순위", e.google_rank, "위", "google_trending", link=news,
          period=_at(e.google_at))  # fmt: skip
    if e.naver_ratio is not None:
        f.add("네이버 평소 대비 배수", round(e.naver_ratio, 2), "배", "naver_search_trend",
              period=e.naver_until.isoformat() if e.naver_until else None)  # fmt: skip
    if i.target_age and i.target_age_ratio is not None:
        f.add(f"{i.target_age} 평소 대비 배수", i.target_age_ratio, "배", "trend_age")
    for b in i.age_bands:
        if b.ratio is not None and b.band != i.target_age:
            f.add(f"{b.band} 평소 대비 배수", b.ratio, "배", "trend_age")
    f.add("시즌 배율 (기간 평균 대비)", e.season_ratio, "배", "naver_search_trend",
          period=e.season_month.isoformat() if e.season_month else None)  # fmt: skip
    if i.products:
        f.add("연결 상품 수", len(i.products), "개", "product_link")
    for p in i.products:
        f.add(f"{p.name} 임베딩 유사도", p.similarity, "유사도", "product_link")
    if i.shorts_format and i.shorts_format_samples:
        f.add(f"{i.shorts_format} 숏츠 표본 수", i.shorts_format_samples, "개", "shorts_stats",
              period=_at(i.shorts_format_at))  # fmt: skip
    f.add("숏츠 형식 단계 표본 (이 형식)", i.shorts_format_source_count, "개", "shorts_stats")
    f.add("숏츠 형식 단계 표본 (전체)", i.shorts_format_source_total, "개", "shorts_stats")
    f.add("숏츠 형식 조회 중앙값", i.shorts_format_median_views, "회", "shorts_stats",
          period=_at(i.shorts_format_at))  # fmt: skip
    s = i.shorts_fit  # v2 칸: 숏츠 적합도·목표 참고값 (표본이 모자라면 None이라 넣지 않음)
    if s is not None:
        at = _at(s.snapshot_at)
        f.add("이 분류 숏츠 조회 중앙값", s.median_views, "회", "shorts_stats", period=at,
              sample=s.samples)  # fmt: skip
        f.add("이 분류 숏츠 중앙값 전체 대비 배수", s.ratio, "배", "shorts_stats", period=at,
              sample=s.samples)  # fmt: skip
        f.add("이 분류 숏츠 표본 수", s.samples, "개", "shorts_stats", period=at)
    f.add("목표 참고값 (숏츠 조회 중앙값)", i.target_views_reference, "회", "shorts_stats")
    f.add("순서 점수", i.score, "점", "code")
    return f.rows


def card_facts(c: DomainOpportunity, links: Sequence[str] = ()) -> list[DataFact]:
    """기회 카드 하나의 숫자 (검색 관심·소비·연령대·숏츠 적합도·형식·목표 참고값·점수)."""
    f = _Facts(c.opportunity_id)
    link = links[0] if links else None
    for e in [*c.search_interest, *c.consumption]:
        if e.status == "known" and e.value is not None:
            period = (
                f"{e.period_start.isoformat()}~{e.period_end.isoformat()}"
                if e.period_start and e.period_end else None
            )  # fmt: skip
            f.add(e.label, round(e.value, 2), e.unit or "", e.source_id, period=period,
                  sample=e.samples)  # fmt: skip
    for b in c.age_bands:
        if b.ratio is not None:
            f.add(f"{b.band} 평소 대비 배수", b.ratio, "배", "trend_age")
    s = c.shorts_fit
    if s is not None:
        at = _at(s.snapshot_at)
        f.add("숏츠 조회 중앙값", s.median_views, "회", "youtube", link=link, period=at,
              sample=s.samples)  # fmt: skip
        f.add("기준 숏츠 조회 중앙값", s.overall_median_views, "회", "youtube", period=at)
        f.add("숏츠 조회 중앙값 전체 대비 배수", s.ratio, "배", "youtube", link=link, period=at,
              sample=s.samples)  # fmt: skip
        if s.ratio is not None:
            f.add("숏츠 표본 수", s.samples, "개", "youtube", link=link, period=at)
    for x in c.formats:
        f.add(f"{x.name} 형식 숏츠 수", x.count, "개", "shorts_stats")
        f.add(f"{x.name} 형식 조회 중앙값", x.median_views, "회", "shorts_stats")
    f.add("목표 참고값 (숏츠 조회 중앙값)", c.target_views_reference, "회", "youtube", link=link)
    if c.products:
        f.add("연결 상품 수", len(c.products), "개", "catalog")
    f.add("순서 점수", c.score, "점", "code")
    return f.rows
