"""이벤트 제안 (11/16): `event_proposal`(grade read) · `event_proposal_send`(기획에 보내기, A).

`event_proposal`은 이벤트 달(`month`)에 "누구에게 · 무엇을 · 어떤 이벤트로" 할지 후보를
코드 규칙으로 고르고 점수 순으로 최대 `max_items`개를 낸다. 숫자는 모두 이 파일의 코드가 계산한다.
LLM은 제안마다 제목(title)과 요약(summary)만 쓴다. 회사마다 다른 이름(속성·장르·이벤트 종류·
검색어)은 카탈로그 `[sales]`에서만 온다.

후보 (코드 규칙)
- 분석 기간: 최근 90일(`segment_analysis`와 같은 창·품질 관문·고객 기록 관문). 관문에 걸리면 status
  "insufficient"와 이유, 제안 없음. 설정에 속성·장르·이벤트 종류가 없어도 insufficient
- 묶음 = 카탈로그 속성 1개 또는 2개의 값 조합 전부(카탈로그 순서). 묶음 구매자(기간 안 결제 완료 +
  속성이 모두 맞음)가 `min_group_size` 미만이면 버린다(대상이 되지 않는다)
- 묶음 × 장르: 묶음 구매자 중 그 장르를 산 사람 n이 `min_group_size` 이상이고 배수(lift = 묶음 안
  비율 ÷ 전체 구매자 중 비율, `segment_favorites`와 같은 정의)가 `MIN_LIFT` 이상이면 후보
- 대상 고객 = 그 묶음 고객 중 최근 `active_days`일 결제 완료 주문이 1번 이상인 사람. 이것도
  `min_group_size` 미만이면 버린다
- 시즌: 장르의 대표 검색어(`genre_keywords`)를 네이버 검색어 트렌드 월 단위로 한 번 부른다(장르마다
  한 그룹). 시즌 배수 = 작년 같은 달 값 ÷ 그 달까지 12개월(작년 같은 달 포함) 평균. 상대값이라
  그룹 안 비율이 요청 크기와 상관없다
- 시즌을 못 재면(키 없음·한도·자료 없음·검색어 설정 없음·작년 같은 달이 아직 안 끝남) 후보는 남기고
  시즌 배수 1로 **가정**한다: 근거 value 1.0, source "가정값 (검색 트렌드 확인 불가)",
  문장에 "가정"과 이유, timing_basis.last_year_multiple은 None, 한계에 한 줄
- 이벤트 종류: 후보마다 카탈로그 `event_kinds` 중 (전환율 가운데 × (1 − 할인율))이 가장 큰 것
  (같으면 카탈로그 순서)

점수 (순서용, 코드 상수)
- score = 배수 × 시즌 배수(못 재면 1) × min(1, n ÷ `FULL_SAMPLE`)
  (n이 `FULL_SAMPLE`명보다 적은 묶음은 표본이 작아 깎는다)
- 점수 큰 순(같으면 배수 큰 순, 묶음 이름, 장르 code). 한 장르·한 묶음은 한 번만 고른다
  (비슷한 제안이 겹치지 않게)
- 기준 미달 후보 제외(카탈로그, 가정값): 잰 시즌 배수 < `min_season_multiple`이거나 예상 주문 낮은
  쪽 < `min_orders_low`이면 제안에서 빼고 `excluded`에 이유와 함께 둔다(점수 순 최대
  `EXCLUDED_MAX`). 시즌을 못 잰 후보(1배로 가정)는 시즌 기준으로 빼지 않는다. 뺀 후보는 장르·묶음을
  차지하지 않아 다음 후보가 그 자리를 쓸 수 있다
- 금액은 LLM 사실·템플릿 문장 모두 쉼표 표기(`won`, 예: 61,572원). 숫자 검증은 쉼표를 지우고
  비교한다

예상 효과
- 전환율: 지난 캠페인 쿠폰 사용률(사용 ÷ 발급, 캠페인 쿠폰이 있는 캠페인만)이 있으면 그
  최솟값~최댓값(`conversion_source` past_campaign). 없으면 이벤트 종류의 가정값
  (assumption True, 문장에 "가정")
- 객단가 = 최근 90일 그 장르가 든 결제 완료 주문의 장르 줄 순매출(할인 몫 뺀 값) 합 ÷ 그 주문 수
- 주문 = 반올림(대상 고객 × 전환율), 예상 매출 = 주문 × 객단가,
  할인 비용 = 반올림(예상 매출 × 할인율)
- 근거(reasons)는 꼭 3개: 묶음 배수(segment) · 시즌 배수(season) · 전환율(conversion). 숫자와 출처

같은 입력이면 같은 제안
- proposal_id = "evp-<달>-" + sha256(고객사·달·묶음 조건·장르·이벤트 종류) 앞 12자. 데이터가 새로
  들어와도 조건이 같으면 같은 id다(숫자는 새 값으로 덮어쓰고 보낸 기록은 남는다). 그래서 같은 제안을
  기획에 두 번 만들지 않는다
- 제안은 `event_proposal_store`에 저장한다(보내기가 id로 찾는다). 제목·요약은 숫자가 같고 전에 LLM
  문장이 검증을 통과했으면 다시 쓰지 않는다

LLM 문장
- 제목·요약만. 문장 속 숫자는 이 제안의 계산 값에서 나온 표기여야 한다(`explain._bad_numbers`와 같은
  규칙). 길이(제목 `TITLE_MAX`자·요약 `SUMMARY_MAX`자)를 넘거나, 전환율이 가정인데 요약에 "가정"이
  없거나, LLM이 없거나 실패하면 코드 템플릿 문장

고객 반응
- 고객지원 `book_issue_stats`(#1125, 최근 한 달)를 그 장르 상위 상품으로 한 번 읽어, 판매 수량이
  `customer_reaction.MIN_SOLD` 이상이고 클레임 비율이 `CLAIM_RATE_HIGH` 이상인 상품은 한계에 적는다
  (제안을 빼지 않는다). 못 읽으면 한계에 이유 한 줄

기획에 보내기 (`event_proposal_send`)
- 저장된 제안을 기획 `receive_insight`로 보낸다. 먼저 data 자신의 `execute("send_event_proposal")`
  (safety registry 항목 필요), executed일 때만 `call_agent("planning", "receive_insight")`.
  idempotency_key = "<proposal_id>:send", insight_id = proposal_id(기획은 같은 요청자·같은
  insight_id면 duplicate)
- facts = 근거 3개 + 예상 숫자(대상 고객·전환율·객단가·주문·매출·할인 비용) (+ 가짜 데이터면 출처),
  최대 12개. products·product_ids = 그 장르 상위 상품
- 결과(created·duplicate·rejected(기획이 최근 지운 제안과 같음))와 제안 번호·시각을 저장한다.
  safety가 실행하지 않거나 기획에 닿지 못하면 보내지 않은 것으로 두고 저장하지 않는다
  (다시 누를 수 있다).
  이미 created·duplicate로 보낸 제안은 기획을 다시 부르지 않고 duplicate
- 마케팅에는 보내지 않는다(기획이 사람 확인 뒤 넘긴다)

성과 되돌리기 (`past_results`, 예상 vs 실제)
- 고르는 제안: 같은 고객사에서 기획에 보낸(created·duplicate) 제안 중 이번 제안과 묶음·장르·이벤트
  종류가 같은 것. 다른 달의 제안(proposal_id가 다름)은 캠페인이 없어도 예상 범위만으로 보이고,
  이번 제안 자신(같은 proposal_id, 같은 달에 다시 부른 경우)은 캠페인이 연결됐을 때만 보인다
  (보낸 사실은 이미 `sent`에 있다)
- 연결: 제안 → 보낸 기록의 기획 제안 번호(`sent.proposal_ref`, PROP-n) → 캠페인. 캠페인 성과 결과
  (`campaign_result`)의 proposal_id가 그 PROP-n이거나 insight_id가 이 제안의 proposal_id(보낼 때
  insight_id = proposal_id)이면 연결. 시험 캠페인은 뺀다
- 한 줄 = 제안 하나 × 연결된 캠페인 하나. expected_low·high = 그 제안의 예상 매출 범위,
  actual = 캠페인 기간 매출 증분(`campaign_result` 판정의 incremental_gross_krw, 마이너스 그대로,
  측정 불가면 None), period = 캠페인 측정 기간. 연결된 캠페인이 없으면 campaign_id·actual·기간은
  비운다
- 순서: 제안 달 최근 것부터, 같은 제안 안에서는 캠페인 기간 최근 것부터. 최대 `PAST_RESULTS_MAX`줄
- 캠페인 결과는 보낸 제안이 있을 때만 읽는다(`campaign_results` 주입, 핸들러는 영상 저장소 →
  campaign_result). 못 읽으면 예상 범위만 두고 근거에 이유 한 줄
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ax_agent_template.llm import LLMClient, LLMFormatError, LLMNotConfiguredError
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.data import (
    CampaignResultOutput,
    CatalogSales,
    EventKind,
    EventProposalItem,
    EventProposalOutput,
    EventProposalSendOutput,
    ExcludedProposal,
    ProposalEstimate,
    ProposalPastResult,
    ProposalReason,
    ProposalSent,
    SalesBasis,
    SalesQuality,
    TimingBasis,
    TrendKeywordGroup,
    TrendScanInput,
)
from ax_contracts.schemas.planning import InsightFact, ReceiveInsightInput, ReceiveInsightOutput
from data_agent.clients.customer_reaction import MIN_SOLD, Reactions
from data_agent.connectors import ConnectorUnavailable
from data_agent.event_proposal_store import EventProposalStore, StoredProposal
from data_agent.explain import (
    FORMAT_ERROR,
    LLM_DAILY_CAP,
    LLM_ERROR,
    LLM_UNAVAILABLE,
    NUMBER_INVALID,
    _bad_numbers,
)
from data_agent.llm_budget import LLMDailyCapReached
from data_agent.sales_quality import KST
from data_agent.sales_store import SalesStore, StoredOrder
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import NO_PRODUCT, line_nets
from data_agent.skills.trend_history import add_months, first_of_month, last_day

SKILL = "event_proposal"
SEND_SKILL = "event_proposal_send"
ANALYSIS_PERIOD = "90d"  # 묶음 배수·객단가를 재는 기간
ANALYSIS_LABEL = "최근 90일"
MIN_LIFT = 1.2  # 묶음 배수가 이보다 작으면 후보가 아니다
FULL_SAMPLE = 30  # 묶음 안 장르 구매자가 이보다 적으면 점수를 (n ÷ 30)만큼 깎는다
MAX_GROUP_ATTRS = 2  # 묶음은 속성 1개 또는 2개의 값 조합
SEASON_ASSUMED = 1.0  # 시즌 배수를 못 재면 1로 가정
SEASON_MIN_POINTS = 6  # 12개월 중 값이 이보다 적으면 시즌을 못 잰 것으로 본다
CLAIM_RATE_HIGH = 0.05  # 최근 한 달 클레임 비율이 이 이상이면 한계에 적는다
TOP_PRODUCTS = 3  # 기획에 넘기는 그 장르 상위 상품 수 (구매자 수 순)
PAST_RESULTS_MAX = 3  # 같은 조건으로 전에 보낸 제안 (예상 범위)
TITLE_MAX, SUMMARY_MAX = 60, 300  # LLM 문장 길이 (기획 계약 120·400보다 짧게)
EXCLUDED_MAX = 10  # 기준 미달로 뺀 후보를 보여 주는 수 (점수 순)
SEASON_ASSUMED_SOURCE = "가정값 (검색 트렌드 확인 불가)"
NAVER_SOURCE = "네이버 검색어 트렌드"
QUERIES = [*seg.QUERIES, "event_proposal_store.event_proposals"]

# 보내기
ACTION = "send_event_proposal"  # data 자신의 execute() 액션 (safety registry 항목 필요)
TARGET, PLANNING_SKILL = "planning", "receive_insight"
FACT_SOURCE = "data.event_proposal"
FACTS_MAX, LABEL_MAX, VALUE_MAX, SOURCE_MAX = 12, 120, 80, 60
DEADLINE = timedelta(seconds=30)
NOT_STORED = "저장된 제안이 없음 (먼저 이벤트 제안을 불러야 함)"

Send = Callable[[str, AgentRequest], Awaitable[AgentResult]]
Gate = Callable[..., Awaitable[Any]]
ReactionReader = Callable[[list[str]], Awaitable[Reactions]]
# 성과 되돌리기: 보낸 제안의 id·기획 제안 번호 → 연결된 캠페인 성과 결과 (campaign_result)
ResultReader = Callable[[set[str]], Awaitable[Sequence[CampaignResultOutput]]]
SendOutcome = Literal["created", "duplicate", "rejected"]


# ---------------------------------------------------------------- 데이터 모양


@dataclass(frozen=True)
class PastCampaign:
    """지난 캠페인 하나의 전환율(쿠폰 사용 ÷ 발급, 0~1)."""

    campaign_id: str
    rate: float


@dataclass(frozen=True)
class Season:
    keywords: tuple[str, ...]
    multiple: float | None  # None = 못 잼 (1로 가정)
    note: str  # 잰 출처 또는 못 잰 이유
    same_month: date | None = None  # 작년 같은 달
    first_month: date | None = None


@dataclass
class Candidate:
    filters: dict[str, str]
    label: str
    genre: str
    genre_label: str
    segment_size: int  # 묶음 구매자 (분석 기간)
    buyers: int  # 묶음 구매자 중 그 장르를 산 사람
    share: float
    base_share: float
    lift: float
    target_customers: int
    aov: int
    products: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Base:
    reasons: list[str]
    report: SalesQuality
    basis: SalesBasis | None
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Draft:
    """고른 제안 하나와 계산 재료 (문장·저장에 쓴다)."""

    item: EventProposalItem
    candidate: Candidate
    kind: EventKind
    facts: dict[str, int | float | str]


# ---------------------------------------------------------------- 저장소 읽기 (스레드에서)


def groups(cfg: CatalogSales) -> list[dict[str, str]]:
    """속성 1개·2개 값 조합 전부 (카탈로그 순서)."""
    out: list[dict[str, str]] = []
    for size in range(1, MAX_GROUP_ATTRS + 1):
        for attrs in itertools.combinations(cfg.attributes, size):
            for codes in itertools.product(*(list(a.values) for a in attrs)):
                out.append({a.key: c for a, c in zip(attrs, codes, strict=True)})
    return out


def active_window(days: int, now: datetime) -> tuple[datetime, datetime]:
    """대상 고객 기간: 최근 `days`일(한국 0시 기준) ~ now (UTC)."""
    local = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return (local - timedelta(days=days - 1)).astimezone(UTC), now.astimezone(UTC)


def genre_aov(totals: dict[str, tuple[int, int]], uneven: Iterable[StoredOrder]) -> dict[str, int]:
    """장르 → 그 장르가 든 주문의 장르 줄 순매출 합 ÷ 그 주문 수 (반올림, 0 미만이면 0).

    `totals`는 줄 할인 몫이 주문 할인과 맞는 주문을 저장소가 더한 (합, 주문 수)
    (`SalesStore.genre_line_nets`), `uneven`은 맞지 않아 `line_nets`로 다시 나눠야 하는 주문."""
    total: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    for genre, (net, orders) in totals.items():
        total[genre] += net
        count[genre] += orders
    for order in uneven:
        if not order.lines:
            continue
        seen: set[str] = set()
        for line, net in zip(order.lines, line_nets(order), strict=True):
            if line.genre_code:
                total[line.genre_code] += net.net
                seen.add(line.genre_code)
        for genre in seen:
            count[genre] += 1
    return {g: max(0, round(total[g] / count[g])) for g in count}


def genre_products(
    buyers: dict[str, dict[str, int]], titles: dict[str, str]
) -> dict[str, list[tuple[str, str]]]:
    """장르 → 상위 상품 (산 사람 수 큰 순, 같으면 이름·id 순) 최대 `TOP_PRODUCTS`개.

    `buyers` = 장르 → 상품 → 산 구매자 수, `titles` = 장르 줄에서 처음 나온 상품 이름."""
    out: dict[str, list[tuple[str, str]]] = {}
    for genre, products in buyers.items():
        ranked = sorted(products, key=lambda p: (-products[p], titles.get(p, ""), p))
        out[genre] = [(p, titles.get(p, NO_PRODUCT)) for p in ranked[:TOP_PRODUCTS]]
    return out


def read_base(
    store: SalesStore, tenant_id: str, cfg: CatalogSales | None, catalog_id: str, now: datetime
) -> Base:
    """관문을 보고, 묶음 × 장르 후보(배수·대상 고객·객단가·상위 상품)를 센다."""
    snap = seg.snapshot(store, tenant_id, ANALYSIS_PERIOD, cfg, catalog_id, now)
    problems: list[str] = []
    if cfg is not None:
        if not cfg.attributes:
            problems.append("설정에 고객 속성(attributes)이 없음")
        if not cfg.genres:
            problems.append("설정에 장르(genres)가 없음")
        if not cfg.event_kinds:
            problems.append("설정에 이벤트 종류(event_kinds)가 없음")
    reasons = problems + snap.reasons
    if reasons or cfg is None:
        return Base(reasons, snap.report, snap.basis)
    least = cfg.min_group_size
    keys = [a.key for a in cfg.attributes]
    with snap.session(keys) as s:
        cells = s.buyer_cells()
        by_genre = {code: c for code, c in s.genre_cells().items() if code in cfg.genres}
        active = s.active_cells(*active_window(cfg.active_days, now))
        aov = genre_aov(*s.genre_nets())
        products = genre_products(s.genre_products(), s.titles(genre_only=True))
    overall_n = sum(cells.values())
    candidates: list[Candidate] = []
    for filters in groups(cfg):
        segment = seg.cell_count(cells, keys, filters)
        if segment < least:
            continue
        target = seg.cell_count(active, keys, filters)
        if target < least:
            continue
        for genre in sorted(by_genre):
            who = by_genre[genre]
            n = seg.cell_count(who, keys, filters)
            if n < least or genre not in aov:
                continue
            share = seg.ratio(n, segment)
            base = seg.ratio(sum(who.values()), overall_n)
            lift = seg.lift(share, base)
            if share is None or base is None or lift is None or lift < MIN_LIFT:
                continue
            candidates.append(Candidate(
                filters=filters, label=seg.filter_label(filters, cfg), genre=genre,
                genre_label=cfg.genres[genre], segment_size=segment, buyers=n, share=share,
                base_share=base, lift=lift, target_customers=target, aov=aov[genre],
                products=products.get(genre, []),
            ))  # fmt: skip
    return Base([], snap.report, snap.basis, candidates)


# ---------------------------------------------------------------- 시즌 (네이버, 장르마다 한 번)


def month_start(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


async def season_of(
    connector: Any | None, genre: str, keywords: Sequence[str], month: str, now: datetime
) -> Season:
    kw = tuple(keywords)
    if not kw:
        return Season(kw, None, "설정에 이 장르의 검색어(genre_keywords)가 없음")
    same = add_months(month_start(month), -12)
    first = add_months(same, -11)
    if same >= first_of_month(now.astimezone(KST).date()):
        return Season(kw, None, f"작년 같은 달({same:%Y-%m})이 아직 끝나지 않음")
    if connector is None:
        return Season(kw, None, "네이버 검색어 트렌드 커넥터가 없음")
    query = TrendScanInput(
        keyword_groups=[TrendKeywordGroup(group_name=genre, keywords=list(kw[:20]))],
        start_date=first, end_date=last_day(same), time_unit="month",
    )  # fmt: skip
    try:
        response = await connector.search_trend(query)
    except ConnectorUnavailable as error:
        return Season(kw, None, f"네이버 검색어 트렌드를 읽지 못함 [{error.code}]")
    series = next((s for s in response.series if s.group_name == genre), None)
    values = {
        first_of_month(d): float(v)
        for d, v in (series.points if series else ())
        if first <= d <= last_day(same)
    }
    if same not in values or len(values) < SEASON_MIN_POINTS:
        return Season(kw, None, "네이버가 이 기간 값을 충분히 주지 않음")
    mean = sum(values.values()) / len(values)
    if mean <= 0:
        return Season(kw, None, "네이버 값이 모두 0")
    note = f"{NAVER_SOURCE} ({first:%Y-%m}~{same:%Y-%m})"
    return Season(kw, round(values[same] / mean, 2), note, same, first)


async def seasons_for(
    connector: Any | None, cfg: CatalogSales, genres: Iterable[str], month: str, now: datetime
) -> dict[str, Season]:
    out: dict[str, Season] = {}
    for genre in sorted(set(genres)):
        out[genre] = await season_of(connector, genre, cfg.genre_keywords.get(genre, []), month,
                                     now)  # fmt: skip
    return out


# ---------------------------------------------------------------- 점수·예상·근거 (순수 함수)


def score_of(c: Candidate, season: Season | None) -> float:
    multiple = season.multiple if season and season.multiple is not None else SEASON_ASSUMED
    return round(c.lift * multiple * min(1.0, c.buyers / FULL_SAMPLE), 4)


def conversion(kind: EventKind, past: Sequence[PastCampaign]) -> tuple[float, float, bool]:
    """(낮음, 높음, 가정인지). 지난 캠페인이 있으면 그 사용률 최솟값~최댓값."""
    if past:
        rates = sorted(p.rate for p in past)
        return rates[0], rates[-1], False
    return kind.conversion_low, kind.conversion_high, True


def pick_kind(kinds: Sequence[EventKind], past: Sequence[PastCampaign]) -> EventKind:
    """(전환율 가운데 × (1 − 할인율))이 가장 큰 이벤트 종류. 같으면 카탈로그 앞쪽."""

    def value(i: int) -> tuple[float, int]:
        low, high, _ = conversion(kinds[i], past)
        return ((low + high) / 2 * (1 - kinds[i].discount_rate), -i)

    return kinds[max(range(len(kinds)), key=value)]


def pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def num(value: float) -> str:
    return f"{value:.2f}"


def estimate(c: Candidate, kind: EventKind, past: Sequence[PastCampaign]) -> ProposalEstimate:
    low, high, assumed = conversion(kind, past)
    orders_low, orders_high = round(c.target_customers * low), round(c.target_customers * high)
    revenue_low, revenue_high = orders_low * c.aov, orders_high * c.aov
    cost_low = round(revenue_low * kind.discount_rate)
    cost_high = round(revenue_high * kind.discount_rate)
    formula = (
        f"대상 고객 {c.target_customers:,}명 × 전환율 {pct(low)}~{pct(high)}"
        f"{' (가정)' if assumed else ''} = 주문 {orders_low:,}~{orders_high:,}건; "
        f"주문 × 객단가 {c.aov:,}원 = 예상 매출 {revenue_low:,}~{revenue_high:,}원; "
        f"할인 비용 = 예상 매출 × 할인율 {pct(kind.discount_rate)} = {cost_low:,}~{cost_high:,}원"
    )
    return ProposalEstimate(
        target_customers=c.target_customers, conversion_low=low, conversion_high=high,
        conversion_source="assumption" if assumed else "past_campaign", assumption=assumed,
        aov=c.aov, orders_low=orders_low, orders_high=orders_high, revenue_low=revenue_low,
        revenue_high=revenue_high, discount_cost_low=cost_low, discount_cost_high=cost_high,
        formula=formula,
    )  # fmt: skip


def reasons_of(
    c: Candidate,
    season: Season,
    kind: EventKind,
    past: Sequence[PastCampaign],
    data_source: str | None,
) -> list[ProposalReason]:
    """근거 3개: 묶음 배수 · 시즌 배수 · 전환율. 숫자 하나와 출처씩."""
    origin = data_source or "판매 저장소"
    lift = round(c.lift, 2)
    segment = ProposalReason(
        kind="segment",
        text=(f"{c.label} 구매자 중 {c.genre_label} 구매 비율 {pct(c.share)} — 전체 구매자 "
              f"{pct(c.base_share)}의 {num(lift)}배"),
        value=lift, unit="배",
        source=f"{origin} 주문 기록 {ANALYSIS_LABEL} (묶음 구매자 {c.segment_size:,}명)",
    )  # fmt: skip
    if season.multiple is not None and season.same_month is not None:
        same = season.same_month
        words = ", ".join(season.keywords[:2])
        season_reason = ProposalReason(
            kind="season",
            text=(f"'{words}' 검색량: 작년 {same.year}년 {same.month}월이 그때까지 12개월 평균의 "
                  f"{num(season.multiple)}배"),
            value=season.multiple, unit="배", source=season.note,
        )  # fmt: skip
    else:
        season_reason = ProposalReason(
            kind="season",
            text=f"시즌 배수 확인 불가 ({season.note}) — 1배로 가정",
            value=SEASON_ASSUMED, unit="배", source=SEASON_ASSUMED_SOURCE,
        )  # fmt: skip
    low, high, assumed = conversion(kind, past)
    mid = round((low + high) / 2 * 100, 2)
    if assumed:
        conv = ProposalReason(
            kind="conversion",
            text=(f"가정 전환율 {pct(low)}~{pct(high)} (가운데 {mid:g}%) — 지난 캠페인 기록이 "
                  f"없어 {kind.label} 가정값을 씀"),
            value=mid, unit="%", source="가정값 (카탈로그 event_kinds)",
        )  # fmt: skip
    else:
        conv = ProposalReason(
            kind="conversion",
            text=(f"지난 캠페인 {len(past)}건 쿠폰 사용률 {pct(low)}~{pct(high)} "
                  f"(가운데 {mid:g}%)를 전환율로 씀"),
            value=mid, unit="%", source=f"지난 캠페인 {len(past)}건 (쿠폰 사용 ÷ 발급)",
        )  # fmt: skip
    return [segment, season_reason, conv]


def proposal_id(tenant_id: str, month: str, filters: dict[str, str], genre: str, kind: str) -> str:
    key = json.dumps(
        {"tenant": tenant_id, "month": month, "filters": dict(sorted(filters.items())),
         "genre": genre, "event_kind": kind},
        sort_keys=True, ensure_ascii=False,
    )  # fmt: skip
    return f"evp-{month}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


def limits_of(
    est: ProposalEstimate, season: Season, past: Sequence[PastCampaign], data_source: str | None
) -> list[str]:
    lines = [
        f"묶음 배수는 {ANALYSIS_LABEL} 구매 기록의 연관이며 이벤트 효과를 보장하지 않음",
        f"객단가는 {ANALYSIS_LABEL} 이 장르가 든 주문의 장르 금액(할인 뺀 값) 평균",
    ]
    if est.assumption:
        lines.append("전환율은 카탈로그 가정값 (지난 캠페인 기록이 없음)")
    else:
        lines.append(
            f"전환율은 지난 캠페인 {len(past)}건의 쿠폰 사용률 (대상·상품이 이번과 달랐을 수 있음)"
        )
    if season.multiple is None:
        lines.append(f"시즌 배수를 재지 못해 1배로 가정함 ({season.note})")
    if data_source:
        lines.append(f"{data_source}로 계산함 (실제 판매 기록이 아님)")
    return lines


def text_facts(
    c: Candidate, kind: EventKind, month: str, est: ProposalEstimate, reasons: list[ProposalReason]
) -> dict[str, int | float | str]:
    """LLM 문장에 쓸 수 있는 값 (이 밖의 숫자는 검증에서 버린다)."""
    return {
        "대상 묶음": c.label, "장르": c.genre_label, "이벤트": kind.label, "이벤트 달": month,
        "묶음 배수": reasons[0].value or 0.0, "시즌 배수": reasons[1].value or 0.0,
        "대상 고객 수": est.target_customers,
        "전환율 낮음(%)": round(est.conversion_low * 100, 2),
        "전환율 높음(%)": round(est.conversion_high * 100, 2),
        "전환율 출처": "가정값" if est.assumption else "지난 캠페인",
        "객단가": won(est.aov or 0), "예상 주문 낮음": est.orders_low,
        "예상 주문 높음": est.orders_high, "예상 매출 낮음": won(est.revenue_low),
        "예상 매출 높음": won(est.revenue_high), "할인율(%)": round(kind.discount_rate * 100, 2),
        "할인 비용 낮음": won(est.discount_cost_low),
        "할인 비용 높음": won(est.discount_cost_high),
    }  # fmt: skip


def won(amount: int) -> str:
    """금액 표기: 쉼표 + 원 (예: 61,572원). LLM 사실과 템플릿 문장이 같은 표기를 쓴다."""
    return f"{amount:,}원"


def build(  # noqa: PLR0913 - 계산 재료를 그대로 받는다
    base: Base,
    cfg: CatalogSales,
    tenant_id: str,
    month: str,
    max_items: int,
    seasons: dict[str, Season],
    past: Sequence[PastCampaign],
    data_source: str | None,
) -> tuple[list[Draft], list[ExcludedProposal]]:
    """점수 순으로 고르고 제안을 만든다 (제목·요약은 템플릿, 문장은 나중에).

    기준 미달(시즌 배수 < `min_season_multiple`, 예상 주문 낮은 쪽 < `min_orders_low`) 후보는
    제안에서 빼고 이유와 함께 돌려준다. 뺀 후보는 장르·묶음을 차지하지 않는다.
    """
    scored = sorted(
        ((score_of(c, seasons.get(c.genre)), c) for c in base.candidates),
        key=lambda x: (-x[0], -x[1].lift, x[1].label, x[1].genre,
                       json.dumps(sorted(x[1].filters.items()))),
    )  # fmt: skip
    kind = pick_kind(cfg.event_kinds, past)
    drafts: list[Draft] = []
    excluded: list[ExcludedProposal] = []
    used_genres: set[str] = set()
    used_groups: set[tuple[tuple[str, str], ...]] = set()
    for score, c in scored:
        group = tuple(sorted(c.filters.items()))
        if c.genre in used_genres or group in used_groups:
            continue
        season = seasons.get(c.genre) or Season((), None, "시즌을 보지 않음")
        est = estimate(c, kind, past)
        why = exclusion_reasons(season, est, cfg)
        if why:
            if len(excluded) < EXCLUDED_MAX:
                excluded.append(ExcludedProposal(target_label=c.label, item_label=c.genre_label,
                                                 reason=" · ".join(why)))  # fmt: skip
            continue
        used_genres.add(c.genre)
        used_groups.add(group)
        reasons = reasons_of(c, season, kind, past, data_source)
        item = EventProposalItem(
            proposal_id=proposal_id(tenant_id, month, c.filters, c.genre, kind.code),
            target_filters=dict(c.filters), target_label=c.label, genre_code=c.genre,
            item_label=c.genre_label, event_kind=kind.code, event_label=kind.label,
            timing_basis=TimingBasis(
                month=month, keywords=list(season.keywords), last_year_multiple=season.multiple,
                source=season.note,
            ),
            reasons=reasons, estimate=est, limits=limits_of(est, season, past, data_source),
            score=score,
        )  # fmt: skip
        facts = text_facts(c, kind, month, est, reasons)
        item = item.model_copy(
            update={"title": template_title(item), "summary": template_summary(item)}
        )
        drafts.append(Draft(item, c, kind, facts))
        if len(drafts) >= max_items:
            break
    return drafts, excluded


def exclusion_reasons(season: Season, est: ProposalEstimate, cfg: CatalogSales) -> list[str]:
    """기준 미달 이유. 시즌을 못 잰 후보(1배로 가정)는 시즌 기준으로 빼지 않는다."""
    why: list[str] = []
    if season.multiple is not None and season.multiple < cfg.min_season_multiple:
        why.append(f"작년 같은 달 검색 배수 {num(season.multiple)}배 "
                   f"(기준 {num(cfg.min_season_multiple)}배 미만)")  # fmt: skip
    if est.orders_low < cfg.min_orders_low:
        why.append(f"예상 주문(낮은 쪽) {est.orders_low:,}건 (기준 {cfg.min_orders_low:,}건 미만)")
    return why


# ---------------------------------------------------------------- 문장 (템플릿 · LLM)


def month_label(month: str) -> str:
    return f"{int(month[:4])}년 {int(month[5:7])}월"


def template_title(item: EventProposalItem) -> str:
    when = month_label(item.timing_basis.month)
    return f"{when} {item.target_label} 대상 {item.item_label} {item.event_label}"


def template_summary(item: EventProposalItem) -> str:
    est = item.estimate
    lift, season = item.reasons[0], item.reasons[1]
    season_text = (
        f"작년 같은 달 검색량이 평균의 {num(season.value or 0)}배"
        if item.timing_basis.last_year_multiple is not None
        else "시즌 배수는 확인하지 못해 1배로 가정"
    )
    conv = "가정 전환율" if est.assumption else "지난 캠페인 전환율"
    return (
        f"{item.target_label} 고객은 {item.item_label} 구매 비율이 전체의 "
        f"{num(lift.value or 0)}배이고 {season_text}입니다. "
        f"대상 {est.target_customers:,}명에 {item.event_label} 이벤트를 하면 {conv} "
        f"{pct(est.conversion_low)}~{pct(est.conversion_high)} 기준 예상 매출 "
        f"{est.revenue_low:,}~{est.revenue_high:,}원, 할인 비용 "
        f"{est.discount_cost_low:,}~{est.discount_cost_high:,}원입니다."
    )


_PROMPT = (
    "이벤트 제안 하나의 제목과 요약을 한국어로 써라.\n"
    "규칙: 제목은 {title_max}자 이내 한 줄, 요약은 두 문장 {summary_max}자 이내. "
    "facts에 있는 숫자만 그대로 쓰고 숫자를 바꾸거나 새로 계산하지 말 것. "
    "효과를 단정하지 말고 예상 범위로 쓸 것. "
    "'전환율 출처'가 가정값이면 요약에 '가정'이라는 말을 꼭 쓸 것.\n"
    "facts: {facts}"
)


class _Text(BaseModel):
    title: str
    summary: str


@dataclass(frozen=True)
class Text:
    title: str
    summary: str
    by_llm: bool
    reason: str | None = None


def facts_key(facts: dict[str, int | float | str]) -> str:
    raw = json.dumps(facts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def write_text(draft: Draft, llm: LLMClient | None) -> Text:
    """LLM 제목·요약. 숫자 검증·길이·"가정" 확인에 실패하면 템플릿과 이유."""
    item = draft.item

    def fallback(reason: str) -> Text:
        return Text(item.title or template_title(item), item.summary or template_summary(item),
                    False, reason)  # fmt: skip

    if llm is None:
        return fallback(LLM_UNAVAILABLE)
    prompt = _PROMPT.format(title_max=TITLE_MAX, summary_max=SUMMARY_MAX, facts=draft.facts)
    try:
        result = await llm.structured(prompt, _Text)
        title, summary = result.title.strip(), result.summary.strip()
    except LLMDailyCapReached:
        return fallback(LLM_DAILY_CAP)
    except LLMNotConfiguredError:
        return fallback(LLM_UNAVAILABLE)
    except (LLMFormatError, ValidationError):
        return fallback(FORMAT_ERROR)
    except Exception:  # noqa: BLE001 - 호출 실패(HTTP 오류·시간 초과 등)는 모두 템플릿
        return fallback(LLM_ERROR)
    if not title or not summary or len(title) > TITLE_MAX or len(summary) > SUMMARY_MAX:
        return fallback(FORMAT_ERROR)
    if "\n" in title or (item.estimate.assumption and "가정" not in summary):
        return fallback(FORMAT_ERROR)
    if _bad_numbers(f"{title}\n{summary}", draft.facts):
        return fallback(NUMBER_INVALID)
    return Text(title, summary, True)


# ---------------------------------------------------------------- 고객 반응 (한계에만)


async def add_reactions(drafts: list[Draft], reader: ReactionReader | None) -> str | None:
    """상위 상품의 최근 한 달 클레임 비율이 높으면 한계에 적는다. 읽지 못한 이유를 돌려준다."""
    if reader is None or not drafts:
        return None
    ids = list(dict.fromkeys(p for d in drafts for p, _ in d.candidate.products))
    if not ids:
        return None
    got = await reader(ids)
    for d in drafts:
        lines = list(d.item.limits)
        if got.reason is not None:
            lines.append(f"고객 반응(클레임 비율)을 읽지 못함 ({got.reason})")
        else:
            for pid, name in d.candidate.products:
                row = got.rows.get(pid)
                if row is None or row.sold_qty is None or row.sold_qty < MIN_SOLD:
                    continue
                if row.claim_rate is not None and row.claim_rate >= CLAIM_RATE_HIGH:
                    lines.append(
                        f"고객 반응: {name} 최근 한 달 클레임 비율 {pct(row.claim_rate)} "
                        f"(클레임 {row.claim_count}건 / 판매 {row.sold_qty}권) — "
                        "이벤트 전에 확인 필요"
                    )
        d.item = d.item.model_copy(update={"limits": lines})
    return got.reason


# ---------------------------------------------------------------- 지난 캠페인 (쿠폰 사용률)


async def past_from_coupons(
    campaign_ids: Sequence[str], connector: Any
) -> tuple[list[PastCampaign], str | None]:
    """캠페인마다 쿠폰 사용 ÷ 발급. 발급 0·쿠폰 없음은 건너뛴다. 못 읽으면 읽은 것까지와 이유."""
    if connector is None or not hasattr(connector, "campaign_coupons"):
        return [], None
    out: list[PastCampaign] = []
    for cid in campaign_ids:
        try:
            coupons = await connector.campaign_coupons(cid)
        except ConnectorUnavailable as error:
            return out, f"지난 캠페인 쿠폰을 읽지 못함 [{error.code}]"
        issued = sum(c.count for c in coupons)
        if issued > 0:
            used = sum(c.used_units for c in coupons)
            out.append(PastCampaign(cid, round(min(used / issued, 1.0), 4)))
    return out, None


def was_sent(item: EventProposalItem) -> bool:
    return item.sent is not None and item.sent.outcome in ("created", "duplicate")


def link_keys(stored: Iterable[StoredProposal]) -> set[str]:
    """보낸 제안의 proposal_id와 기획 제안 번호(PROP-n). 캠페인 결과를 이 값으로 찾는다."""
    keys: set[str] = set()
    for s in stored:
        if was_sent(s.item):
            keys.add(s.item.proposal_id)
            if s.item.sent and s.item.sent.proposal_ref:
                keys.add(s.item.sent.proposal_ref)
    return keys


def linked(
    item: EventProposalItem, results: Sequence[CampaignResultOutput]
) -> list[CampaignResultOutput]:
    """보낸 제안 하나에 연결된 캠페인 결과 (시험 캠페인 뺌, 기간 최근 것부터, campaign_id 한 번)."""
    ref = item.sent.proposal_ref if item.sent else None
    found = {
        r.campaign_id: r for r in results
        if not r.test and ((ref is not None and r.proposal_id == ref)
                           or r.insight_id == item.proposal_id)
    }  # fmt: skip
    return sorted(
        found.values(),
        key=lambda r: (r.window_from or datetime.min.replace(tzinfo=UTC), r.campaign_id),
        reverse=True,
    )


def actual_of(result: CampaignResultOutput) -> int | None:
    """캠페인 기간 매출 증분 (campaign_result 판정 값 그대로, 측정 불가면 None)."""
    verdict = result.sales.verdict if result.sales else None
    return verdict.incremental_gross_krw if verdict else None


def past_results(
    stored: Sequence[StoredProposal],
    item: EventProposalItem,
    results: Sequence[CampaignResultOutput] = (),
) -> list[ProposalPastResult]:
    """같은 묶음·장르·이벤트로 기획에 보낸 제안의 예상 vs 실제 (최근 것부터 최대 3줄).

    다른 달의 제안은 캠페인이 없어도 예상 범위만 보인다. 이번 제안 자신은 캠페인이 연결됐을 때만.
    """
    found = [
        s.item for s in stored
        if was_sent(s.item)
        and s.item.genre_code == item.genre_code and s.item.event_kind == item.event_kind
        and s.item.target_filters == item.target_filters
    ]  # fmt: skip
    found.sort(key=lambda i: (i.timing_basis.month, i.proposal_id), reverse=True)
    rows: list[ProposalPastResult] = []
    for i in found:
        common: dict[str, Any] = {
            "proposal_id": i.proposal_id,
            "proposal_ref": i.sent.proposal_ref if i.sent else None,
            "expected_low": i.estimate.revenue_low,
            "expected_high": i.estimate.revenue_high,
        }
        campaigns = linked(i, results)
        if not campaigns and i.proposal_id != item.proposal_id:
            rows.append(ProposalPastResult(**common))
        rows += [
            ProposalPastResult(
                **common, campaign_id=r.campaign_id, actual=actual_of(r),
                period_from=r.window_from, period_to=r.window_to,
            )  # fmt: skip
            for r in campaigns
        ]
    return rows[:PAST_RESULTS_MAX]


# ---------------------------------------------------------------- event_proposal 전체


@dataclass
class ProposalRun:
    output: EventProposalOutput
    evidence: Evidence
    fallback: bool = False
    fallback_reason: str | None = None


def summary_of(out: EventProposalOutput, candidates: int, cfg: CatalogSales | None) -> str:
    label = month_label(out.month)
    if out.status != "ok":
        text = f"{label} 이벤트 제안을 내지 않음 — " + "; ".join(out.insufficient_reasons)
        return seg.with_source(text, out.basis)
    if not out.proposals and out.excluded:
        text = (f"{label} 이벤트 제안 없음: 후보 {len(out.excluded)}건이 기준(시즌 배수·예상 "
                "주문)에 못 미쳐 제외함.")  # fmt: skip
        return seg.with_source(text, out.basis)
    if not out.proposals:
        least = cfg.min_group_size if cfg else 0
        text = (f"{label} 이벤트 제안 없음: 묶음 배수 {MIN_LIFT}배 이상이고 {least}명 이상인 "
                "묶음 × 장르가 없음.")  # fmt: skip
        return seg.with_source(text, out.basis)
    best = out.proposals[0]
    text = (
        f"{label} 이벤트 제안 {len(out.proposals)}건 (후보 {candidates}개 중 점수 순). 1순위는 "
        f"{best.target_label} 대상 {best.item_label} {best.event_label} — 예상 매출 "
        f"{best.estimate.revenue_low:,}~{best.estimate.revenue_high:,}원"
        f"{' (가정 전환율)' if best.estimate.assumption else ''}."
    )
    return seg.with_source(text, out.basis)


async def propose(  # noqa: PLR0913 - 저장소·커넥터를 바꿔 끼울 수 있게 다 받는다
    store: SalesStore,
    proposals: EventProposalStore,
    tenant_id: str,
    month: str,
    max_items: int,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
    *,
    trend: Any | None = None,
    llm: LLMClient | None = None,
    past: Sequence[PastCampaign] = (),
    past_note: str | None = None,
    reactions: ReactionReader | None = None,
    read: Callable[..., Awaitable[Base]] | None = None,
    campaign_results: ResultReader | None = None,
) -> ProposalRun:
    """이벤트 제안. 저장소 읽기는 `read`(핸들러는 스레드)로, 나머지는 여기서."""
    if read is None:
        base = read_base(store, tenant_id, cfg, catalog_id, now)
    else:
        base = await read(store, tenant_id, cfg, catalog_id, now)
    common: dict[str, Any] = {"month": month, "quality": base.report, "basis": base.basis}
    queries = list(QUERIES)
    if base.reasons or cfg is None:
        out = EventProposalOutput(**common, status="insufficient",
                                  insufficient_reasons=base.reasons)  # fmt: skip
        out = out.model_copy(update={"summary": summary_of(out, 0, cfg)})
        return ProposalRun(out, evidence_of(out, now, queries, []))
    data_source = base.basis.data_source if base.basis else None
    genres = {c.genre for c in base.candidates}
    seasons = await seasons_for(trend, cfg, genres, month, now)
    queries += [
        f"naver search-trend group={g} {s.first_month:%Y-%m}~{s.same_month:%Y-%m} month"
        for g, s in sorted(seasons.items()) if s.first_month and s.same_month
    ]  # fmt: skip
    drafts, excluded = build(base, cfg, tenant_id, month, max_items, seasons, past, data_source)
    notes: list[str] = []
    if past_note:
        notes.append(past_note)
    if drafts and reactions is not None:
        queries.append(f"book_issue_stats period=month products={len(drafts)}개 제안 상위 상품")
        if (why := await add_reactions(drafts, reactions)) is not None:
            notes.append(f"고객 반응 읽지 못함: {why}")
    stored = {s.item.proposal_id: s for s in proposals.all(tenant_id)}
    results: Sequence[CampaignResultOutput] = ()
    keys = link_keys(stored.values())
    if drafts and keys and campaign_results is not None:
        try:
            results = await campaign_results(keys)
        except Exception as error:  # noqa: BLE001 - 성과를 못 읽어도 제안은 낸다 (예상 범위만)
            notes.append(f"지난 제안 성과(campaign_result)를 읽지 못함 ({type(error).__name__})")
        queries.append(f"campaign_result 보낸 제안 연결 (캠페인 {len(results)}개)")
    fallbacks: list[str] = []
    items: list[EventProposalItem] = []
    for d in drafts:
        key = facts_key(d.facts)
        before = stored.get(d.item.proposal_id)
        if before is not None and before.text_by_llm and before.facts_key == key:
            text = Text(before.item.title or "", before.item.summary or "", True)
        else:
            text = await write_text(d, llm)
        if not text.by_llm:
            fallbacks.append(text.reason or "unknown")
        item = d.item.model_copy(update={"title": text.title, "summary": text.summary})
        proposals.save(tenant_id, StoredProposal(item, list(d.candidate.products), key,
                                                 text.by_llm))  # fmt: skip
        sent = before.item.sent if before is not None else None
        items.append(item.model_copy(update={
            "sent": sent, "past_results": past_results(list(stored.values()), item, results),
        }))  # fmt: skip
    if fallbacks:
        notes.append(f"제목·요약을 템플릿으로 대체함 {len(fallbacks)}건: "
                     + ", ".join(sorted(set(fallbacks))))  # fmt: skip
    out = EventProposalOutput(**common, proposals=items, excluded=excluded)
    out = out.model_copy(update={"summary": summary_of(out, len(base.candidates), cfg)})
    return ProposalRun(
        out, evidence_of(out, now, queries, notes), bool(fallbacks),
        fallbacks[0] if fallbacks else None,
    )  # fmt: skip


def evidence_of(
    out: EventProposalOutput, now: datetime, queries: list[str], notes: list[str]
) -> Evidence:
    ev = seg.evidence(out.basis, now, "; ".join(notes))
    return ev.model_copy(update={
        "queries": queries,
        "ref_ids": [f"event_proposal:{p.proposal_id}" for p in out.proposals],
    })  # fmt: skip


# ---------------------------------------------------------------- event_proposal_send


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _value(reason: ProposalReason) -> str:
    v = reason.value
    shown = "확인 불가" if v is None else (f"{v:g}" if reason.unit == "%" else num(v))
    return f"{shown}{reason.unit}"


REASON_LABEL = {
    "segment": "묶음 구매 배수 (전체 구매자 대비)",
    "season": "시즌 배수 (작년 같은 달 ÷ 12개월 평균)",
    "conversion": "전환율 가운데",
}


def facts_of(item: EventProposalItem) -> list[InsightFact]:
    """근거 3개 + 예상 숫자 (최대 12개, 길이 제한)."""
    est = item.estimate
    assumed = " (가정)" if est.assumption else ""
    rows: list[tuple[str, str, str]] = []
    for r in item.reasons:
        tail = " (가정)" if r.source.startswith("가정") else ""
        rows.append((f"{REASON_LABEL[r.kind]} · {item.target_label} {item.item_label}",
                     _value(r) + tail, r.source))  # fmt: skip
    rows += [
        ("대상 고객 (최근 결제 고객)", f"{est.target_customers:,}명", FACT_SOURCE),
        ("전환율 범위", f"{pct(est.conversion_low)}~{pct(est.conversion_high)}{assumed}",
         FACT_SOURCE),
        ("객단가 (장르)", f"{est.aov or 0:,}원", FACT_SOURCE),
        ("예상 주문", f"{est.orders_low:,}~{est.orders_high:,}건", FACT_SOURCE),
        ("예상 매출", f"{est.revenue_low:,}~{est.revenue_high:,}원", FACT_SOURCE),
        ("할인 비용", f"{est.discount_cost_low:,}~{est.discount_cost_high:,}원", FACT_SOURCE),
    ]  # fmt: skip
    fake = next((line for line in item.limits if "가짜 데이터" in line), None)
    if fake is not None:
        rows.append(("데이터 출처", "가짜 데이터(데모용)", FACT_SOURCE))
    return [
        InsightFact(label=clip(a, LABEL_MAX), value=clip(b, VALUE_MAX), source=clip(c, SOURCE_MAX))
        for a, b, c in rows
    ][:FACTS_MAX]


def body_of(item: EventProposalItem) -> str:
    lines = [f"## 계산\n{item.estimate.formula}", "## 근거"]
    lines += [f"- {r.text} (출처: {r.source})" for r in item.reasons]
    lines.append("## 한계")
    lines += [f"- {line}" for line in item.limits]
    return clip("\n".join(lines), 8000)


def build_insight(stored: StoredProposal, now: datetime) -> ReceiveInsightInput:
    """기획에 보낼 인사이트. 계약 모델로 만들어 길이·필수 칸을 검증한다."""
    item = stored.item
    products = stored.products[:10]
    return ReceiveInsightInput(
        insight_id=item.proposal_id,
        title=clip(item.title or template_title(item), 120),
        summary=clip(item.summary or template_summary(item), 400),
        skill=SKILL,
        facts=facts_of(item),
        products=[name for _, name in products],
        product_ids=[pid for pid, _ in products],
        audience=clip(item.target_label, 60) or None,
        generated_at=now,
        body_md=body_of(item),
    )


@dataclass(frozen=True)
class SendResult:
    status: TaskStatus
    outcome: str
    output: EventProposalSendOutput


def _rejected(status: TaskStatus, outcome: str, reason: str) -> SendResult:
    return SendResult(status, outcome, EventProposalSendOutput(outcome="rejected", reason=reason))


async def send_proposal(  # noqa: PLR0911 - 결과 갈래마다 바로 돌려준다
    proposals: EventProposalStore,
    tenant_id: str,
    pid: str,
    meta: RequestMeta,
    now: datetime,
    *,
    send: Send,
    gate: Gate,
) -> SendResult:
    """저장된 제안 하나를 기획 receive_insight로 보낸다 (safety 관문 먼저)."""
    stored = proposals.get(tenant_id, pid)
    if stored is None:
        return _rejected(TaskStatus.FAILED, "not_found", NOT_STORED)
    sent = stored.item.sent
    if sent is not None and sent.outcome in ("created", "duplicate"):
        return SendResult(TaskStatus.COMPLETED, "duplicate", EventProposalSendOutput(
            outcome="duplicate", proposal_ref=sent.proposal_ref,
            reason=f"이미 기획에 보냄 ({sent.proposal_ref or '번호 없음'})",
        ))  # fmt: skip
    try:
        insight = build_insight(stored, now)
    except (ValidationError, ValueError) as error:
        return _rejected(TaskStatus.FAILED, "invalid_output",
                         f"계약 검증 실패: {str(error)[:120]}")  # fmt: skip
    call_meta = meta.model_copy(update={
        "token": "internal:data",  # call_agent가 data 토큰으로 바꿔 보낸다
        "tenant_id": tenant_id,
        "deadline": min(meta.deadline, datetime.now(UTC) + DEADLINE),
        "idempotency_key": f"{pid}:send",
        "requester": "data",
    })  # fmt: skip
    item = stored.item
    decision = await gate(
        ACTION,
        {"proposal_id": pid, "month": item.timing_basis.month, "event_kind": item.event_kind,
         "genre_code": item.genre_code},  # fmt: skip
        call_meta,
        Evidence(
            ref_ids=[f"event_proposal:{pid}"],
            data_summary=f"이벤트 제안 {pid} → 기획 ({insight.title})",
            collected_at=now,
        ),
    )
    if decision.status != "executed":
        return _rejected(TaskStatus.REJECTED, "rejected",
                         f"safety가 실행하지 않음 ({decision.status})")  # fmt: skip
    reply = await send(TARGET, AgentRequest(meta=call_meta, skill=PLANNING_SKILL,
                                            input=insight.model_dump(mode="json")))  # fmt: skip
    if reply.status != TaskStatus.COMPLETED:
        why = reply.outcome or reply.status.value
        return _rejected(TaskStatus.FAILED, why, f"기획에 보내지 못함 ({why})")
    try:
        got = ReceiveInsightOutput.model_validate(reply.output or {})
    except ValidationError:
        return _rejected(TaskStatus.FAILED, "invalid_output", "기획 응답 형식이 계약과 다름")
    outcome: SendOutcome = "created"
    reason = ""
    if got.outcome == "duplicate":
        outcome = "duplicate"
    elif got.outcome == "deleted_recently":
        outcome = "rejected"
        reason = f"기획에서 최근 지운 제안({got.proposal_id})과 같아 만들지 않음"
    proposals.mark_sent(tenant_id, pid, ProposalSent(sent_at=now, outcome=outcome,
                                                     proposal_ref=got.proposal_id))  # fmt: skip
    return SendResult(TaskStatus.COMPLETED, outcome, EventProposalSendOutput(
        outcome=outcome, proposal_ref=got.proposal_id, reason=reason,
    ))  # fmt: skip
