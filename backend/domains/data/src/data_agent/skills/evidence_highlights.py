"""근거 요약 (`evidence_highlights`): 인사이트·기회 카드마다 facts 중 센 것 최대 3개. LLM 안 씀.

- 고르는 규칙(코드): 평소 대비 배수가 있는 근거를 배수가 큰 순으로 먼저. 표본이 기준
  (`[brief] highlight_min_sample`, 기본 10)보다 작은 것은 뒤로 보내고 "표본 N개"를 붙인다. 평소 값이
  없는 근거(Google 순위·YouTube 조회 등)는 그다음, 값만 준다(baseline·ratio 없음). 숏츠 조회 중앙값
  배수처럼 분모가 작을 수 있는 값이 상한(`highlight_max_ratio`, 기본 10배)을 넘으면 "표본 적음"으로
  그 뒤. 1배 아래로 내려간 근거는 맨 뒤
- 출처는 네이버 검색(연령대별 포함)·네이버 쇼핑·YouTube·Google만. 상품 유사도·점수·표본 수 같은
  내부 숫자는 근거 요약에 넣지 않는다
- 문장은 템플릿 하나 (예: "네이버 검색이 평소의 1.42배예요 (지난 8주 기준)"). 숫자는 facts 값 그대로
- 근거 숏츠(`evidence_shorts`): 근거 영상 중 썸네일·제목이 있는 것을 조회수 순으로 최대 3개
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ax_contracts.schemas.data import (
    DataFact,
    DomainOpportunity,
    EvidenceHighlight,
    TrendInsight,
    VideoLink,
)
from data_agent.skills.trend_scan import BASELINE_DAYS

MAX = 3
NAVER_PERIOD = f"지난 {BASELINE_DAYS // 7}주 기준"
FEW = "표본 적음"


@dataclass(frozen=True)
class Rule:
    """회사 설정 `[brief]`: 표본 기준(`highlight_min_sample`)·숏츠 배수 상한."""

    min_sample: int = 10
    max_ratio: float = 10.0


DEFAULT_RULE = Rule()

@dataclass(frozen=True)
class _Cand:
    source: str
    label: str
    sentence: str
    value: float
    unit: str
    ratio: float | None = None
    baseline: float | None = None
    period: str | None = None
    sample: int | None = None
    # 분모가 작을 수 있는 배수 (숏츠 조회 중앙값 배수): 상한을 넘으면 표본 적음
    small_base: bool = False
    show_sample: bool = False  # 문장 끝에 "표본 N개"를 늘 붙인다 (숏츠)


def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:g}"


def _few(c: _Cand, rule: Rule) -> bool:
    return c.small_base and c.ratio is not None and c.ratio > rule.max_ratio


def _small(c: _Cand, rule: Rule) -> bool:
    return c.sample is not None and c.sample < rule.min_sample


def _tier(c: _Cand, rule: Rule) -> tuple[int, float]:
    """0 = 배수(표본 충분), 1 = 배수(표본 적음), 2 = 값만, 3 = 숏츠 배수 상한 넘음, 4 = 1배 아래."""
    if c.ratio is None:
        return (2 if not _small(c, rule) else 3, 0.0)
    if c.ratio < 1:
        return (4, -c.ratio)
    if _few(c, rule):
        return (3, -c.ratio)
    return (1 if _small(c, rule) else 0, -c.ratio)


def _sentence(c: _Cand, rule: Rule) -> str:
    if _few(c, rule):
        return f"{c.sentence} ({FEW})"
    if _small(c, rule) or (c.show_sample and c.sample is not None):
        return f"{c.sentence} (표본 {c.sample}개)"
    return c.sentence


def pick(cands: Sequence[_Cand], facts: Sequence[DataFact],
         rule: Rule = DEFAULT_RULE) -> list[EvidenceHighlight]:  # fmt: skip
    """후보 → 센 순서로 최대 3개 (같은 이름은 한 번). facts에 같은 이름이 있으면 그 id.

    표본이 기준보다 적은 근거는 뒤로 보내고 "표본 N개"를 붙인다. 숏츠 조회 배수처럼 분모가 작을 수
    있는 값이 상한을 넘으면 "표본 적음"으로 더 뒤로.
    """
    ids = {f.label: f.fact_id for f in facts}
    out: list[EvidenceHighlight] = []
    seen: set[str] = set()
    for c in sorted(cands, key=lambda c: _tier(c, rule)):
        if c.label in seen:
            continue
        seen.add(c.label)
        out.append(EvidenceHighlight(
            source=c.source,  # type: ignore[arg-type]
            label=c.label, value=c.value, unit=c.unit, baseline_value=c.baseline,
            ratio=c.ratio, period=c.period, sample=c.sample, sentence=_sentence(c, rule),
            fact_id=ids.get(c.label),
        ))  # fmt: skip
        if len(out) == MAX:
            break
    return out


def _naver(label: str, ratio: float, who: str = "네이버 검색", *, latest: float | None = None,
           baseline: float | None = None, sample: int | None = None) -> _Cand:  # fmt: skip
    r = round(ratio, 2)
    return _Cand("naver_search", label, f"{who}이 평소의 {r:g}배예요 ({NAVER_PERIOD})",
                 latest if latest is not None else r, "배" if latest is None else "지수",
                 ratio=r, baseline=baseline, period=NAVER_PERIOD, sample=sample)  # fmt: skip


def _ages(bands: Sequence[object]) -> list[_Cand]:
    out = []
    for b in bands:
        ratio = getattr(b, "ratio", None)
        if ratio is None:
            continue
        band = getattr(b, "band", "")
        out.append(_naver(f"{band} 평소 대비 배수", ratio, f"{band} 네이버 검색",
                          latest=getattr(b, "latest_value", None),
                          baseline=getattr(b, "baseline_value", None)))  # fmt: skip
    return out


def insight_highlights(i: TrendInsight, facts: Sequence[DataFact],
                       rule: Rule = DEFAULT_RULE) -> list[EvidenceHighlight]:  # fmt: skip
    e = i.evidence
    cands: list[_Cand] = []
    if e.naver_ratio is not None:
        cands.append(_naver("네이버 평소 대비 배수", e.naver_ratio))
    cands += _ages(i.age_bands)
    if e.season_ratio is not None and e.season_month is not None:
        r = round(e.season_ratio, 2)
        cands.append(_Cand(
            "naver_search", "시즌 배율 (기간 평균 대비)",
            f"작년 {e.season_month.month}월 네이버 검색이 기간 평균의 {r:g}배예요",
            r, "배", ratio=r, period=e.season_month.isoformat(),
        ))  # fmt: skip
    s = i.shorts_fit
    if s is not None and s.ratio is not None and s.median_views is not None:
        cands.append(_Cand(
            "youtube", "이 분류 숏츠 중앙값 전체 대비 배수",
            f"이 분류 숏츠 조회가 전체 숏츠의 {s.ratio:g}배예요",
            s.median_views, "회", ratio=s.ratio, baseline=s.overall_median_views,
            period=s.snapshot_at.isoformat() if s.snapshot_at else None, sample=s.samples,
            small_base=True, show_sample=True,
        ))  # fmt: skip
    at = e.youtube_at.isoformat() if e.youtube_at else None
    if e.google_rank is not None:
        cands.append(_Cand("google", "Google 인기 검색어 순위",
                           f"Google 인기 검색어 {e.google_rank}위예요", e.google_rank, "위",
                           period=e.google_at.isoformat() if e.google_at else None))  # fmt: skip
    if e.youtube_views_per_hour is not None:
        v = round(e.youtube_views_per_hour)
        cands.append(_Cand("youtube", "지금 시간당 조회",
                           f"YouTube 인기 영상이 시간당 {_num(v)}회 조회되고 있어요", v,
                           "회/시간", period=at, sample=e.youtube_videos))  # fmt: skip
    if e.youtube_videos:
        cands.append(_Cand("youtube", "YouTube 인기 영상 수",
                           f"YouTube 인기 영상 {e.youtube_videos}개에 나와요", e.youtube_videos,
                           "개", period=at))  # fmt: skip  # 값이 곧 영상 수 (표본 칸 없음)
    return pick(cands, facts, rule)


def card_highlights(c: DomainOpportunity, facts: Sequence[DataFact],
                    rule: Rule = DEFAULT_RULE) -> list[EvidenceHighlight]:  # fmt: skip
    cands: list[_Cand] = []
    for e in [*c.search_interest, *c.consumption]:
        if e.status != "known" or e.value is None:
            continue
        period = (f"{e.period_start.isoformat()}~{e.period_end.isoformat()}"
                  if e.period_start and e.period_end else None)  # fmt: skip
        v = round(e.value, 2)
        if e.source_id == "naver_search_trend" and e.unit == "배":
            cands.append(_naver(e.label, e.value, sample=e.samples))
        elif e.source_id == "naver_shopping" and e.unit == "배":
            cands.append(_Cand("naver_shopping", e.label,
                               f"네이버 쇼핑 클릭이 평소의 {v:g}배예요", v, "배", ratio=v,
                               period=period, sample=e.samples))  # fmt: skip
    cands += _ages(c.age_bands)
    s = c.shorts_fit
    if s is not None and s.ratio is not None and s.median_views is not None:
        cands.append(_Cand(
            "youtube", "숏츠 조회 중앙값 전체 대비 배수",
            f"이 주제 숏츠 조회가 기준 숏츠의 {s.ratio:g}배예요",
            s.median_views, "회", ratio=s.ratio, baseline=s.overall_median_views,
            period=s.snapshot_at.isoformat() if s.snapshot_at else None, sample=s.samples,
            small_base=True, show_sample=True,
        ))  # fmt: skip
    elif s is not None and s.median_views is not None:
        cands.append(_Cand("youtube", "숏츠 조회 중앙값",
                           f"이 주제 숏츠 조회 중앙값은 {_num(s.median_views)}회예요",
                           s.median_views, "회", sample=s.samples, show_sample=True))  # fmt: skip
    return pick(cands, facts, rule)


def evidence_shorts(links: Sequence[VideoLink]) -> list[VideoLink]:
    """근거 숏츠 최대 3개: 썸네일·제목이 있는 것만, 조회수 순 (모르면 뒤)."""
    usable = [v for v in links if v.thumbnail_url and v.title]
    return sorted(usable, key=lambda v: (v.views is None, -(v.views or 0)))[:MAX]
