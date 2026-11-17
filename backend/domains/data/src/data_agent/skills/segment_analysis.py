"""고객·시장 분석 (11/16): `segment_options` · `segment_profile` · `segment_favorites` ·
`market_coverage`.

표준 판매 저장소(`sales_store`: 주문·줄·고객·고객 속성)만 읽는다. 판매 서버를 직접 읽지 않는다.
LLM을 쓰지 않고 회사 상태를 바꾸지 않는다(grade read). 숫자는 모두 이 파일의 코드가 계산한다.
회사마다 다른 이름(속성·값·장르·분류·바깥 출처 조건·인구 통계표)은 카탈로그 `[sales]`에서만 온다.

공통
- 기간: 한국 시간 오늘 0시에서 N-1일 전 0시 ~ 기준 시각(now). 30d·90d·365d. 직전 비교가 없어
  `basis.previous_*`는 기간 시작 시각과 같다(계약 주석)
- 구매자 = 기간 안 결제 완료 주문(`sales_clean.counts_as_revenue`)이 있는 가명 buyer_id. 환불은
  구매 여부를 바꾸지 않는다
- 속성 값이 없거나 카탈로그에 없는 값이면 "모름"이다. 비율의 분모는 그 속성 값을 아는 구매자다
  (모름은 `unknown_buyers`로 따로 센다). 그래서 한 속성의 값 비율 합은 1이다
- 개인정보: 집단 통계만 낸다. 묶음이 `min_group_size`(기본 5)명 미만이면 `suppressed=True`이고
  그 묶음의 숫자 칸은 None. "모름" 수도 0보다 크고 기준 미만이면 None. buyer_id 목록은 내지 않는다
- 2차 숨김(complementary suppression): 숨긴 칸 값을 공개된 합계·다른 칸·비율로 거꾸로 계산할 수
  없게 한다. 규칙
  - 합이 공개된(또는 계산되는) 칸 묶음을 정한다. 숨긴 칸 값이 그 합계들과 보이는 칸으로 하나로
    정해지면(선형 방정식으로 풀리면 — 묶음 안에 숨긴 칸이 하나뿐인 경우 등) 그 칸이 든 묶음의
    보이는 칸 중 값이 가장 작은 칸(같으면 앞 칸)부터, 숨기면 풀리지 않게 되는 칸을 하나 더 숨긴다
    (`suppressed=True`, 숫자 칸 None). 더 풀리는 칸이 없을 때까지 되풀이한다(`protect`)
  - 0명인 칸도 기준 미만이라 숨긴 칸으로 센다(0도 알려 주지 않는다)
  - segment_profile, 속성 하나마다: 값 칸들 + "모름" 칸의 합 = target_buyers(공개). 값 칸들의
    합(그 속성을 아는 대상 구매자)은 보이는 칸이 하나라도 있으면 buyers ÷ target_share로 계산되므로
    공개된 합계로 본다. 그래서 "모름"을 숨기면(0 < 모름 < 기준) 그 속성의 값 칸을 모두 숨긴다
    (하나라도 보이면 모름 = target_buyers − buyers ÷ target_share). 비율은 분모가 묶음 합계라
    같은 묶음 규칙으로 함께 막힌다: 보이는 target_share·overall_share 합이 1에서 빠지는 몫은
    숨긴 칸 둘 이상에 나뉘어 있다. lift = target_share ÷ overall_share는 같은 칸 안의 값이라
    숨긴 칸에는 내지 않는다
  - market_coverage: 화면에 합계 칸은 없지만 같은 365일 구매자 수가 다른 곳(segment_favorites의
    한 속성 조건 구매자 수 등)에서 나올 수 있어, 조합 표의 한 줄(다른 속성 값 하나를 고정한 칸들)과
    전체 합을 공개된 합계로 보고 같은 규칙을 쓴다. 인구는 공공 통계라 숨기지 않고, 숨긴 줄의
    커버 비율은 None이다(커버 비율 × 인구 = 구매자라서)
- 품질 관문: `sales_quality.gate` 그대로 + 고객 기록 관문(고객 내보내기 출처 `<출처>:customers`의
  `last_error`가 있으면 그 문장, 저장된 고객이 0명이면 "저장된 고객 기록이 없음"). 걸리면
  status "insufficient"와 이유, 숫자 칸은 비운다
- 설정에 없는 입력(속성 key·값, 분류·장르 code, 인구 조합)도 invalid_input으로 실패시키지 않고
  status "insufficient"와 이유 문장으로 돌려준다(화면이 이유를 그대로 보여 줄 수 있게)
- 근거: `basis`(기간·읽은 행 = 기간 안 주문 행 + 저장된 고객 수·마지막 수집·카탈로그 판·
  data_source)와 evidence. 가짜 출처로 채운 저장소면 data_source "가짜 데이터(데모용)"
- 요약은 코드 템플릿 문장이다(숫자는 출력값만)

바깥 출처(`segment_favorites`)
- 카탈로그 `external_filters`·`genre_external`에 적힌 출처만 부른다
- 도서관 정보나루(`data4library`): 필터에 맞는 조건(예: 연령)을 합쳐 인기대출 1번, 그 집단이
  많이 산 장르(숨김 아님) 중 `genre_external`에 KDC가 있는 것 최대 2개를 KDC 조건을 더해 1번씩.
  설정에 그 출처가 있지만 이번 필터에 맞는 조건이 하나도 없으면 status "error", 이유 "설정 필요: …"
- 네이버 쇼핑인사이트(`naver_shopping`): 설정 params에 `cid`·`group`(10~60)이 있을 때만
  분야 안 연령 비중(그 연령 클릭 상대값 ÷ 전 연령 합)을 낸다. 없으면 부르지 않는다
- 오류: UNAUTHORIZED(키 없음) → key_missing, RATE_LIMITED → limit, 그 밖 → error. 이유만 담고
  숫자를 지어내지 않는다. 하루 캐시·호출 한도·record/replay는 커넥터 것을 그대로 쓴다

시장 커버 비율(`market_coverage`)
- 입력 속성 조합은 카탈로그 `population`의 조합 하나와 같은 집합이어야 한다(순서는 카탈로그 순서로
  맞춘다). 아니면 insufficient
- 구매자 = 최근 365일 결제 완료 주문이 있고 그 속성 값을 모두 아는 사람. 인구 = KOSIS 표
  (`table_ref`: org_id·tbl_id·itm_id·prd_se, 선택 obj_l1·obj_l2(기본 ALL)·new_est_prd_cnt(기본 1)·
  unit_scale(기본 1)·label, `codes` = 속성 key → 값 code → 표 분류 코드(문자열 또는 목록)).
  가장 최근 시점 값을 더한다. 표 설정이 비면 모든 줄 population_missing "인구 통계표 설정 필요".
  선택 `dims`(속성 key → c1·c2·itm): 속성을 항목(ITM_ID, 예: 남자·여자 인구수)에도 맞출 수 있다.
  없으면 첫 속성 c1, 둘째 속성 c2. 쓰지 않는 칸(예: 지역)은 obj_l1 등으로 한 코드만 고른다.
  코드 목록이 빈 값(예: 응답 안 함)은 인구가 없어 비율을 내지 않는다. 월 단위 표면
  population_source에 기준 달("YYYY-MM 기준")을 붙이고 근거에도 남긴다
  커넥터 오류는 status error와 이유. 인구를 지어내지 않는다
"""

from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    CatalogSales,
    CoverageRow,
    ExternalItem,
    ExternalResult,
    FavoriteItem,
    MarketCoverageOutput,
    OptionAttribute,
    OptionItem,
    OptionQuestion,
    PopulationSource,
    SalesBasis,
    SalesQuality,
    SegmentAttributeBreakdown,
    SegmentFavoritesOutput,
    SegmentOptionsOutput,
    SegmentPeriod,
    SegmentProfileOutput,
    SegmentShare,
    SegmentTargetKind,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.data4library import Data4LibraryConnector
from data_agent.connectors.kosis import KosisConnector, KosisDataPoint
from data_agent.connectors.naver_shopping import NaverShoppingConnector
from data_agent.sales_clean import counts_as_revenue
from data_agent.sales_quality import INSUFFICIENT, KST, Window, gate, quality
from data_agent.sales_store import FAKE_SOURCE, SalesStore, StoredOrder, customers_source
from data_agent.skills.sales_analysis import FAKE_DATA, NO_CATEGORY, NO_PRODUCT

SEGMENT_DAYS: dict[str, int] = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}
SEGMENT_LABEL: dict[str, str] = {
    "1d": "최근 1일",
    "7d": "최근 7일",
    "30d": "최근 30일",
    "90d": "최근 90일",
    "365d": "최근 365일",
}
# 화면(물어보기·누가 사나)이 고르는 기간. 365d는 시장 커버가 따로 쓴다
SCREEN_PERIODS: tuple[SegmentPeriod, ...] = ("1d", "7d", "30d", "90d")
KIND_LABEL: dict[str, str] = {"category": "분류", "genre": "장르", "product": "상품"}
QUERIES = [
    "sales_store.sales_orders",
    "sales_store.sales_order_lines",
    "sales_store.sales_customers",
    "sales_store.sales_customer_attributes",
]
NO_CUSTOMERS = f"{INSUFFICIENT}: 저장된 고객 기록이 없음 (고객 내보내기를 아직 받지 않음)"
EXTERNAL_LABEL: dict[str, str] = {
    "data4library": "도서관 정보나루 인기대출",
    "naver_shopping": "네이버 쇼핑인사이트 분야 연령 비중",
}
D4L_PARAMS = ("kdc", "from_age", "to_age", "gender")
MAX_GENRE_CALLS = 2  # 장르(KDC) 조건을 더한 정보나루 호출 수 상한 (요청 하나당)
POPULATION_MISSING = "인구 통계표 설정 필요"


# ---------------------------------------------------------------- 기간·읽기·관문


def segment_window(period: SegmentPeriod, now: datetime) -> Window:
    """한국 시간 오늘 0시에서 N-1일 전 0시 ~ now. 직전 기간 없음(시작 시각과 같게)."""
    local = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = (local - timedelta(days=SEGMENT_DAYS[period] - 1)).astimezone(UTC)
    return Window(start=start, end=now.astimezone(UTC), previous_start=start, previous_end=start)


@dataclass
class Snapshot:
    """한 기간을 저장소에서 읽은 것: 결제 완료 주문, 구매자 속성, 품질 보고, 관문 이유, 근거."""

    window: Window
    paid: list[StoredOrder]
    attrs: dict[str, dict[str, str]]  # buyer_id → 속성 (없는 key = 모름)
    report: SalesQuality
    reasons: list[str]
    basis: SalesBasis

    @property
    def buyers(self) -> set[str]:
        return {o.buyer_id for o in self.paid}


def data_source(store: SalesStore, tenant_id: str) -> str | None:
    """저장소를 가짜 출처(데모 전용)가 채웠으면 "가짜 데이터(데모용)" (sales_analysis와 같다)."""
    return FAKE_DATA if store.sync_state(tenant_id).source == FAKE_SOURCE else None


def customer_reasons(store: SalesStore, tenant_id: str) -> list[str]:
    """고객 기록 관문: 고객 내보내기 출처의 마지막 오류, 저장된 고객 0명."""
    reasons: list[str] = []
    source = store.sync_state(tenant_id).source
    if source:
        error = store.sync_state(tenant_id, customers_source(source)).last_error
        if error:
            reasons.append(f"{INSUFFICIENT}: {error}")
    if store.customer_count(tenant_id) == 0:
        reasons.append(NO_CUSTOMERS)
    return reasons


def snapshot(
    store: SalesStore,
    tenant_id: str,
    period: SegmentPeriod,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> Snapshot:
    w = segment_window(period, now)
    created = store.orders_created(tenant_id, w.start, w.end)
    paid = [o for o in created if counts_as_revenue(o.status, o.payment_status)]
    customers = store.customers(tenant_id)
    report = quality(store, tenant_id, w, created, cfg)
    reasons = gate(report, len(paid), cfg, now)
    if cfg is not None:
        reasons += customer_reasons(store, tenant_id)
    basis = SalesBasis(
        period_from=w.start,
        period_to=w.end,
        previous_from=w.start,
        previous_to=w.start,
        rows_read=len(created) + len(customers),
        last_synced_at=report.last_synced_at,
        catalog_id=catalog_id,
        catalog_version=cfg.version if cfg else None,
        data_source=data_source(store, tenant_id),
    )
    return Snapshot(w, paid, {c.buyer_id: c.attributes for c in customers}, report, reasons, basis)


# ---------------------------------------------------------------- 계산 도우미


def ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole > 0 else None


def lift(target: float | None, overall: float | None) -> float | None:
    if target is None or overall is None or overall <= 0:
        return None
    return round(target / overall, 4)


def small(count: int, least: int) -> int | None:
    """0이거나 기준 이상이면 그 수, 0보다 크고 기준 미만이면 숨김(None)."""
    return count if count == 0 or count >= least else None


# ---------------------------------------------------------------- 2차 숨김


def _rank(rows: list[list[Fraction]]) -> int:
    """행렬 계수 (가우스 소거, 분수로 정확히)."""
    work = [list(r) for r in rows]
    rank = 0
    width = len(work[0]) if work else 0
    for col in range(width):
        pivot = next((i for i in range(rank, len(work)) if work[i][col] != 0), None)
        if pivot is None:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        for i in range(len(work)):
            if i != rank and work[i][col] != 0:
                factor = work[i][col] / work[rank][col]
                work[i] = [a - factor * b for a, b in zip(work[i], work[rank], strict=True)]
        rank += 1
    return rank


def _equations(hidden: set[int], groups: Sequence[Sequence[int]]) -> tuple[list[int], list]:
    order = sorted(hidden)
    rows = [
        [Fraction(1 if cell in g else 0) for cell in order]
        for g in groups
        if any(cell in hidden for cell in g)
    ]
    return order, rows


def _solved(pos: int, order: list[int], rows: list, base: int) -> bool:
    unit = [Fraction(1 if k == pos else 0) for k in range(len(order))]
    return _rank([*rows, unit]) == base


def determined_cells(hidden: set[int], groups: Sequence[Sequence[int]]) -> list[int]:
    """숨긴 칸 중 합계가 공개된 묶음(groups)과 보이는 칸으로 값이 하나로 정해지는 칸.

    묶음마다 "숨긴 칸들의 합 = 공개 합계 − 보이는 칸 합"이 알려진다. 숨긴 칸 i는 단위 벡터 e_i가
    그 방정식들(숨긴 칸 표시 벡터)이 펼치는 공간에 있을 때만 정해진다.
    """
    order, rows = _equations(hidden, groups)
    if not rows:
        return []
    base = _rank(rows)
    return [cell for pos, cell in enumerate(order) if _solved(pos, order, rows, base)]


def is_determined(cell: int, hidden: set[int], groups: Sequence[Sequence[int]]) -> bool:
    order, rows = _equations(hidden, groups)
    return bool(rows) and _solved(order.index(cell), order, rows, _rank(rows))


MAX_EXTRA_CELLS = 3  # 한 번에 더 숨겨 볼 칸 수 상한 (2차원 표는 직사각형을 만드는 3칸이면 된다)


def protect(values: Sequence[int], hidden: set[int], groups: Sequence[Sequence[int]]) -> set[int]:
    """2차 숨김: 숨긴 칸이 계산되지 않을 때까지 보이는 칸을 더 숨긴다 (모듈 docstring 규칙).

    값이 정해지는 숨긴 칸마다, 숨기면 그 칸이 풀리지 않게 되는 보이는 칸 묶음을 칸 수가 적은 것 →
    값 합이 작은 것(같으면 앞 칸) 순으로 골라 숨긴다. 한 묶음 안에서는 가장 작은 보이는 칸 하나로
    충분하고, 2차원 표에서는 직사각형을 만드는 칸이 필요하다. 3칸으로도 안 되면 그 칸이 든 묶음의
    보이는 칸을 모두 숨긴다.
    """
    hidden = set(hidden)
    while True:
        stuck = determined_cells(hidden, groups)
        if not stuck:
            return hidden
        cell = stuck[0]
        visible = [c for c in range(len(values)) if c not in hidden]
        best: tuple[int, ...] | None = None
        for size in range(1, min(MAX_EXTRA_CELLS, len(visible)) + 1):
            tries = sorted(
                itertools.combinations(visible, size), key=lambda e: (sum(values[c] for c in e), e)
            )
            best = next(
                (e for e in tries if not is_determined(cell, hidden | set(e), groups)), None
            )
            if best is not None:
                break
        if best is None:
            best = tuple(c for g in groups if cell in g for c in g if c not in hidden)
        if not best:  # 더 숨길 보이는 칸이 없다 (호출자가 합계 쪽을 처리)
            return hidden
        hidden |= set(best)


def profile_hidden(counts: Sequence[int], unknown: int, least: int) -> tuple[set[int], bool]:
    """segment_profile 속성 하나의 숨길 값 칸(위치)과 "모름"을 숨길지.

    칸 = 값 칸들(0..k-1) + 모름(k). 합계: 전체(target_buyers, 공개)와 값 칸 합(보이는 값 칸이
    하나라도 있으면 buyers ÷ target_share로 계산됨).
    """
    k = len(counts)
    cells = [*counts, unknown]
    hidden = {i for i, n in enumerate(counts) if n < least}
    if small(unknown, least) is None:
        hidden.add(k)
    while True:
        groups: list[list[int]] = [list(range(k + 1))]
        if any(i not in hidden for i in range(k)):
            groups.append(list(range(k)))
        more = protect(cells, hidden, groups)
        if more == hidden:
            break
        hidden = more
    return {i for i in hidden if i < k}, k in hidden


def coverage_groups(sizes: Sequence[int]) -> list[list[int]]:
    """조합 표(itertools.product 순서)의 합계 묶음: 속성마다 다른 속성 값을 고정한 줄 + 전체."""
    combos = list(itertools.product(*(range(n) for n in sizes)))
    groups: list[list[int]] = [list(range(len(combos)))]
    for p in range(len(sizes)):
        lines: dict[tuple[int, ...], list[int]] = defaultdict(list)
        for idx, combo in enumerate(combos):
            lines[combo[:p] + combo[p + 1 :]].append(idx)
        groups += [g for g in lines.values() if len(g) > 1]
    return groups


def known_value(attrs: dict[str, str] | None, key: str, values: dict[str, str]) -> str | None:
    """속성 값. 없거나 카탈로그에 없는 값이면 None(모름)."""
    code = (attrs or {}).get(key)
    return code if code in values else None


def attribute_values(cfg: CatalogSales) -> dict[str, dict[str, str]]:
    return {a.key: a.values for a in cfg.attributes}


def filter_problems(filters: dict[str, str], cfg: CatalogSales) -> list[str]:
    values = attribute_values(cfg)
    problems = []
    for key, code in filters.items():
        if key not in values:
            problems.append(f"설정에 없는 속성: {key} (설정된 속성: {', '.join(values) or '없음'})")
        elif code not in values[key]:
            problems.append(f"속성 {key}에 없는 값: {code}")
    return problems


def filter_label(filters: dict[str, str], cfg: CatalogSales) -> str:
    """사람이 읽는 조건 (카탈로그 순서, 이름만). 예: "20대 · 여성"."""
    parts = []
    for a in cfg.attributes:
        code = filters.get(a.key)
        if code is not None:
            parts.append(a.values.get(code, code))
    return " · ".join(parts)


def item_buyers(paid: Iterable[StoredOrder]) -> dict[tuple[str, str], set[str]]:
    """(종류, code) → 그것을 산 구매자. 종류는 genre·category·product."""
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for order in paid:
        for line in order.lines:
            if line.genre_code:
                found[("genre", line.genre_code)].add(order.buyer_id)
            if line.category_code:
                found[("category", line.category_code)].add(order.buyer_id)
            if line.product_id:
                found[("product", line.product_id)].add(order.buyer_id)
    return found


def product_titles(paid: Iterable[StoredOrder]) -> dict[str, str]:
    titles: dict[str, str] = {}
    for order in paid:
        for line in order.lines:
            if line.product_id and line.title and line.product_id not in titles:
                titles[line.product_id] = line.title
    return titles


def kst_text(moment: datetime) -> str:
    return f"{moment.astimezone(KST):%Y-%m-%d %H:%M}"


def pct(value: float | None) -> str:
    return "확인할 수 없음" if value is None else f"{value * 100:.1f}%"


def with_source(text: str, basis: SalesBasis | None) -> str:
    return f"[{basis.data_source}] {text}" if basis and basis.data_source else text


def evidence(basis: SalesBasis | None, now: datetime, extra: str = "") -> Evidence:
    """기간·읽은 행·마지막 수집·카탈로그 판·출처를 한 줄로."""
    if basis is None:
        summary = "판매 저장소를 읽지 않음"
    else:
        synced = kst_text(basis.last_synced_at) if basis.last_synced_at else "없음"
        summary = (
            f"기간 {kst_text(basis.period_from)}~{kst_text(basis.period_to)} (한국 시간), "
            f"읽은 행 {basis.rows_read}, 마지막 수집 {synced}, "
            f"카탈로그 {basis.catalog_id} 판 {basis.catalog_version or '없음'}"
            + (f", 출처 {basis.data_source}" if basis.data_source else "")
        )
    return Evidence(
        queries=list(QUERIES),
        data_summary=summary + (f", {extra}" if extra else ""),
        collected_at=now,
        ref_ids=[],
    )


# ---------------------------------------------------------------- segment_options


def options(cfg: CatalogSales | None, catalog_id: str, source: str | None) -> SegmentOptionsOutput:
    """화면 선택지. 카탈로그 `[sales]`를 순서 그대로 내려 준다 (숫자 없음)."""
    if cfg is None:
        return SegmentOptionsOutput(
            periods=list(SCREEN_PERIODS), data_source=source, catalog_id=catalog_id
        )
    return SegmentOptionsOutput(
        periods=list(SCREEN_PERIODS),
        attributes=[
            OptionAttribute(
                key=a.key,
                label=a.label,
                values=[OptionItem(code=c, label=n) for c, n in a.values.items()],
            )
            for a in cfg.attributes
        ],
        genres=[OptionItem(code=c, label=n) for c, n in cfg.genres.items()],
        categories=[OptionItem(code=c, label=n) for c, n in cfg.categories.items()],
        quick_questions=[
            OptionQuestion(label=q.label, filters=dict(q.filters)) for q in cfg.quick_questions
        ],
        event_kinds=[OptionItem(code=e.code, label=e.label) for e in cfg.event_kinds],
        population_keys=[list(p.attribute_keys) for p in cfg.population],
        min_group_size=cfg.min_group_size,
        data_source=source,
        catalog_id=catalog_id,
        catalog_version=cfg.version,
        item_noun=cfg.item_noun,
    )


def read_options(
    store: SalesStore, tenant_id: str, cfg: CatalogSales | None, catalog_id: str
) -> SegmentOptionsOutput:
    return options(cfg, catalog_id, data_source(store, tenant_id))


# ---------------------------------------------------------------- segment_profile


def target_label(
    kind: SegmentTargetKind, code: str, cfg: CatalogSales | None, titles: dict[str, str]
) -> str:
    if kind == "category":
        return (cfg.categories if cfg else {}).get(code, NO_CATEGORY)
    if kind == "genre":
        return (cfg.genres if cfg else {}).get(code, code)
    return titles.get(code, NO_PRODUCT)


def profile(  # noqa: PLR0913 - 입력 칸을 그대로 받는다
    store: SalesStore,
    tenant_id: str,
    target_kind: SegmentTargetKind,
    target_code: str,
    attribute_keys: Sequence[str],
    period: SegmentPeriod,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> SegmentProfileOutput:
    """대상(분류·장르·상품)을 산 구매자 vs 기간 전체 구매자의 속성 구성비."""
    snap = snapshot(store, tenant_id, period, cfg, catalog_id, now)
    titles = product_titles(snap.paid)
    common: dict[str, Any] = {
        "target_kind": target_kind,
        "target_code": target_code,
        "target_label": target_label(target_kind, target_code, cfg, titles),
        "period": period,
        "quality": snap.report,
        "basis": snap.basis,
    }
    problems: list[str] = []
    keys: list[str] = []
    if cfg is not None:
        values = attribute_values(cfg)
        keys = list(attribute_keys) or list(values)
        problems += [
            f"설정에 없는 속성: {k} (설정된 속성: {', '.join(values) or '없음'})"
            for k in keys
            if k not in values
        ]
        if not keys:
            problems.append("설정에 고객 속성(attributes)이 없음")
        known = {"category": cfg.categories, "genre": cfg.genres}.get(target_kind)
        if known is not None and target_code not in known:
            problems.append(f"설정에 없는 {KIND_LABEL[target_kind]}: {target_code}")
    reasons = problems + snap.reasons
    if reasons or cfg is None:
        out = SegmentProfileOutput(**common, status="insufficient", insufficient_reasons=reasons)
        return out.model_copy(update={"summary": profile_summary(out)})

    least = cfg.min_group_size
    overall = snap.buyers
    target = item_buyers(snap.paid).get((target_kind, target_code), set())
    suppressed = len(target) < least
    breakdowns = []
    for key in sorted(keys, key=[a.key for a in cfg.attributes].index):
        attr = next(a for a in cfg.attributes if a.key == key)
        t_vals = [known_value(snap.attrs.get(b), key, attr.values) for b in target]
        o_vals = [known_value(snap.attrs.get(b), key, attr.values) for b in overall]
        t_known = sum(v is not None for v in t_vals)
        o_known = sum(v is not None for v in o_vals)
        unknown = len(target) - t_known
        counts = [t_vals.count(code) for code in attr.values]
        hidden, hide_unknown = profile_hidden(counts, unknown, least)  # 1차 + 2차 숨김
        shares = []
        for i, (code, label) in enumerate(attr.values.items()):
            n = counts[i]
            if suppressed or i in hidden:
                shares.append(SegmentShare(value_code=code, label=label, suppressed=True))
                continue
            ts, os_ = ratio(n, t_known), ratio(o_vals.count(code), o_known)
            shares.append(
                SegmentShare(value_code=code, label=label, target_share=ts, overall_share=os_,
                             lift=lift(ts, os_), buyers=n)
            )  # fmt: skip
        breakdowns.append(
            SegmentAttributeBreakdown(
                key=key,
                label=attr.label,
                values=shares,
                unknown_buyers=None if suppressed or hide_unknown else unknown,
            )
        )
    out = SegmentProfileOutput(
        **common,
        target_buyers=None if suppressed else len(target),
        target_suppressed=suppressed,
        overall_buyers=len(overall),
        attributes=breakdowns,
    )
    return out.model_copy(update={"summary": profile_summary(out, least)})


def profile_summary(out: SegmentProfileOutput, least: int | None = None) -> str:
    label = SEGMENT_LABEL[out.period]
    if out.status != "ok":
        text = f"{label} {out.target_label} 구매자 구성 숫자를 내지 않음 — " + "; ".join(
            out.insufficient_reasons
        )
        return with_source(text, out.basis)
    if out.target_suppressed:
        text = f"{label} {out.target_label} 구매자가 {least}명 미만이라 숫자를 숨김."
        return with_source(text, out.basis)
    text = (
        f"{label} {out.target_label} 구매자 {out.target_buyers}명 "
        f"(전체 구매자 {out.overall_buyers}명)."
    )
    best = max(
        (
            (s.lift, b.label, s)
            for b in out.attributes
            for s in b.values
            if s.lift is not None and not s.suppressed
        ),
        key=lambda x: x[0],
        default=None,
    )
    if best is not None:
        s = best[2]
        text += (
            f" 가장 두드러진 묶음은 {best[1]} {s.label} "
            f"(대상 {pct(s.target_share)} vs 전체 {pct(s.overall_share)}, {s.lift:.2f}배)."
        )
    return with_source(text, out.basis)


# ---------------------------------------------------------------- segment_favorites


def favorites(  # noqa: PLR0913
    store: SalesStore,
    tenant_id: str,
    filters: dict[str, str],
    period: SegmentPeriod,
    top: int,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> SegmentFavoritesOutput:
    """속성 묶음의 우리 쇼핑몰 안 순위 (바깥 출처는 `favorites_with_external`이 붙인다)."""
    snap = snapshot(store, tenant_id, period, cfg, catalog_id, now)
    common: dict[str, Any] = {
        "filters": dict(filters),
        "filter_label": filter_label(filters, cfg) if cfg else "",
        "period": period,
        "quality": snap.report,
        "basis": snap.basis,
    }
    reasons = (filter_problems(filters, cfg) if cfg else []) + snap.reasons
    if reasons or cfg is None:
        out = SegmentFavoritesOutput(**common, status="insufficient", insufficient_reasons=reasons)
        return out.model_copy(update={"summary": favorites_summary(out)})
    least = cfg.min_group_size
    overall = snap.buyers
    segment = {
        b for b in overall if all(snap.attrs.get(b, {}).get(k) == v for k, v in filters.items())
    }
    suppressed = len(segment) < least
    internal: list[FavoriteItem] = []
    if not suppressed:
        bought = item_buyers(snap.paid)
        titles = product_titles(snap.paid)
        names: dict[str, Callable[[str], str | None]] = {
            "genre": cfg.genres.get,
            "category": lambda c: cfg.categories.get(c, NO_CATEGORY),
            "product": lambda c: titles.get(c, NO_PRODUCT),
        }
        for kind in ("genre", "category", "product"):
            internal += ranked(kind, bought, segment, overall, names[kind], least, top)
    out = SegmentFavoritesOutput(
        **common,
        segment_buyers=None if suppressed else len(segment),
        segment_suppressed=suppressed,
        internal=internal,
    )
    return out.model_copy(update={"summary": favorites_summary(out, least)})


def ranked(  # noqa: PLR0913
    kind: str,
    bought: dict[tuple[str, str], set[str]],
    segment: set[str],
    overall: set[str],
    name: Callable[[str], str | None],
    least: int,
    top: int,
) -> list[FavoriteItem]:
    """한 종류(장르·분류·상품)의 순위: 집단 구매자 중 산 비율 큰 순. 기준 미만은 뒤에 숨김으로."""
    shown: list[FavoriteItem] = []
    hidden: list[FavoriteItem] = []
    for (k, code), who in bought.items():
        label = name(code)
        if k != kind or label is None:  # 카탈로그에 없는 장르는 품질 보고(unknown_genres)에만
            continue
        n = len(who & segment)
        if n == 0:
            continue
        if n < least:
            hidden.append(FavoriteItem(kind=kind, code=code, label=label, suppressed=True))
            continue
        share = ratio(n, len(segment))
        base = ratio(len(who & overall), len(overall))
        shown.append(
            FavoriteItem(kind=kind, code=code, label=label, share=share, lift=lift(share, base),
                         buyers=n)
        )  # fmt: skip
    shown.sort(key=lambda i: (-(i.share or 0), -(i.lift or 0), i.label, i.code))
    hidden.sort(key=lambda i: (i.label, i.code))
    return (shown + hidden)[:top]


def favorites_summary(out: SegmentFavoritesOutput, least: int | None = None) -> str:
    label = SEGMENT_LABEL[out.period]
    who = out.filter_label or "선택한 묶음"
    if out.status != "ok":
        text = f"{label} {who} 고객 선호 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
        return with_source(text, out.basis)
    if out.segment_suppressed:
        return with_source(f"{label} {who} 구매자가 {least}명 미만이라 숫자를 숨김.", out.basis)
    text = f"{label} {who} 구매자 {out.segment_buyers}명."
    genres = [i for i in out.internal if i.kind == "genre" and not i.suppressed]
    # 첫 문장 = 배수(전체 대비) 1위 장르, 둘째 문장 = 구매 비율 1위 장르 (같은 정렬 규칙: 큰 값 →
    # 이름 → code)
    by_lift = min(
        (g for g in genres if g.lift is not None),
        key=lambda g: (-(g.lift or 0), -(g.share or 0), g.label, g.code),
        default=None,
    )
    by_share = genres[0] if genres else None  # internal은 구매 비율 큰 순
    if by_lift is not None:
        text += (
            f" 특히 많이 사는 장르는 {by_lift.label} (전체 대비 {by_lift.lift:.1f}배, "
            f"구매자 중 {pct(by_lift.share)})."
        )
    if by_share is not None:
        text += f" 가장 많이 산 장르는 {by_share.label} (구매자 중 {pct(by_share.share)})."
    ok = [e for e in out.external if e.status == "ok"]
    if out.external:
        text += f" 바깥 출처 {len(out.external)}곳 중 {len(ok)}곳 확인."
    return with_source(text, out.basis)


def _kst_date(moment: datetime) -> date:
    return moment.astimezone(KST).date()


def _external_error(base: ExternalResult, exc: ConnectorUnavailable) -> ExternalResult:
    status = {"UNAUTHORIZED": "key_missing", "RATE_LIMITED": "limit"}.get(exc.code, "error")
    return base.model_copy(update={"status": status, "reason": f"{exc.code}: {exc}"})


def _conditions(filters: dict[str, str], cfg: CatalogSales, source_id: str) -> tuple[dict, list]:
    """필터 중 그 출처 조건으로 바꿀 수 있는 것의 params 합과, 바꾼 (key, code) 목록."""
    params: dict[str, Any] = {}
    used: list[tuple[str, str]] = []
    for f in cfg.external_filters:
        if f.source_id == source_id and filters.get(f.attribute_key) == f.value_code:
            params.update(f.params)
            used.append((f.attribute_key, f.value_code))
    return params, used


def _condition_label(
    filters: dict[str, str], used: list[tuple[str, str]], cfg: CatalogSales, extra: str = ""
) -> str:
    applied = filter_label(dict(used), cfg)
    skipped = filter_label({k: v for k, v in filters.items() if (k, v) not in used}, cfg)
    text = applied or "조건 없음"
    if extra:
        text += f" · {extra}"
    if skipped:
        text += f" (바깥 출처에 걸지 않은 조건: {skipped})"
    return text


async def _data4library(  # noqa: PLR0913
    conn: Data4LibraryConnector,
    base: ExternalResult,
    params: dict[str, Any],
    start: date,
    end: date,
    top: int,
) -> ExternalResult:
    kwargs = {k: params[k] for k in D4L_PARAMS if k in params}
    try:
        result = await conn.popular_loans(start, end, **kwargs)
    except ConnectorUnavailable as exc:
        return _external_error(base, exc)
    items = [
        ExternalItem(name=i.title, value=float(i.loan_count), unit="회", rank=i.ranking)
        for i in sorted(result.items, key=lambda i: i.ranking)[:top]
        if i.ranking >= 1
    ]
    return base.model_copy(update={"items": items})


async def _naver_age_share(  # noqa: PLR0913
    conn: NaverShoppingConnector,
    base: ExternalResult,
    params: dict[str, Any],
    start: date,
    end: date,
    who: str,
) -> ExternalResult:
    cid, group = params.get("cid"), params.get("group")
    if not cid or not group:
        return base.model_copy(
            update={"status": "error", "reason": "설정 필요: 네이버 쇼핑인사이트 cid·group 없음"}
        )
    try:
        series = await conn.category_age(str(cid), start, end, "month")
    except ConnectorUnavailable as exc:
        return _external_error(base, exc)
    total = sum(v for points in series.values() for _, v in points)
    mine = sum(v for _, v in series.get(str(group), []))
    if total <= 0 or str(group) not in series:
        return base.model_copy(update={"status": "error", "reason": "쇼핑인사이트에 자료 없음"})
    name = str(params.get("category_label") or cid)
    item = ExternalItem(name=f"{name} 클릭 중 {who} 비중", value=round(mine / total * 100, 1),
                        unit="%", rank=1)  # fmt: skip
    return base.model_copy(update={"items": [item]})


async def external_results(  # noqa: PLR0913
    out: SegmentFavoritesOutput,
    cfg: CatalogSales,
    top: int,
    now: datetime,
    d4l: Data4LibraryConnector,
    naver: NaverShoppingConnector | None,
) -> list[ExternalResult]:
    """설정에 적힌 바깥 출처만 부른다. 숫자는 응답에서 읽은 것만."""
    start, end = _kst_date(out.basis.period_from if out.basis else now), _kst_date(now)
    period_label = f"{SEGMENT_LABEL[out.period]} ({start}~{end})"
    filters = out.filters
    sources = [f.source_id for f in cfg.external_filters] + [
        g.source_id for g in cfg.genre_external
    ]
    results: list[ExternalResult] = []
    for source_id in dict.fromkeys(sources):  # 설정 순서, 중복 없이
        params, used = _conditions(filters, cfg, source_id)
        base = ExternalResult(
            source_id=source_id,
            label=EXTERNAL_LABEL.get(source_id, source_id),
            period_label=period_label,
            condition_label=_condition_label(filters, used, cfg),
        )
        if source_id == "data4library":
            if used:
                results.append(await _data4library(d4l, base, params, start, end, top))
            else:
                results.append(base.model_copy(update={
                    "status": "error",
                    "reason": f"설정 필요: '{out.filter_label}' 조건을 도서관 정보나루 조건으로 "
                              "바꾸는 설정(external_filters)이 없음",
                }))  # fmt: skip
            results += await _genre_loans(out, cfg, d4l, params, used, start, end, top, base)
        elif source_id == "naver_shopping":
            if used and naver is not None:  # 설정에 맞는 연령 조건이 있을 때만
                who = filter_label(dict(used), cfg)
                results.append(await _naver_age_share(naver, base, params, start, end, who))
        else:
            results.append(base.model_copy(update={
                "status": "error", "reason": f"아직 연결하지 않은 바깥 출처: {source_id}",
            }))  # fmt: skip
    return results


async def _genre_loans(  # noqa: PLR0913
    out: SegmentFavoritesOutput,
    cfg: CatalogSales,
    d4l: Data4LibraryConnector,
    params: dict[str, Any],
    used: list[tuple[str, str]],
    start: date,
    end: date,
    top: int,
    base: ExternalResult,
) -> list[ExternalResult]:
    """집단이 많이 산 장르(숨김 아님) 중 KDC 설정이 있는 것 최대 2개를 KDC 조건으로 더 부른다."""
    kdc_of = {g.genre_code: g.params for g in cfg.genre_external if g.source_id == "data4library"}
    results: list[ExternalResult] = []
    seen: set[str] = set()
    for item in out.internal:
        if len(results) >= MAX_GENRE_CALLS:
            break
        if item.kind != "genre" or item.suppressed or item.code not in kdc_of:
            continue
        extra = kdc_of[item.code]
        if str(extra.get("kdc")) in seen:
            continue
        seen.add(str(extra.get("kdc")))
        genre_base = base.model_copy(update={
            "label": f"{base.label} · {item.label}",
            "condition_label": _condition_label(
                out.filters, used, cfg, f"{item.label}(KDC {extra.get('kdc')})"
            ),
        })  # fmt: skip
        results.append(await _data4library(d4l, genre_base, {**params, **extra}, start, end, top))
    return results


async def favorites_with_external(  # noqa: PLR0913
    store: SalesStore,
    tenant_id: str,
    filters: dict[str, str],
    period: SegmentPeriod,
    top: int,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
    d4l: Data4LibraryConnector | None = None,
    naver: NaverShoppingConnector | None = None,
) -> SegmentFavoritesOutput:
    """저장소 읽기는 스레드에서, 바깥 출처는 status ok일 때만 부른다."""
    out = await asyncio.to_thread(
        favorites, store, tenant_id, filters, period, top, cfg, catalog_id, now
    )
    if out.status != "ok" or cfg is None:
        return out
    external = await external_results(
        out, cfg, top, now, d4l or Data4LibraryConnector(),
        naver or NaverShoppingConnector(caller="segment_favorites"),
    )  # fmt: skip
    out = out.model_copy(update={"external": external})
    return out.model_copy(update={"summary": favorites_summary(out, cfg.min_group_size)})


# ---------------------------------------------------------------- market_coverage


def find_population(keys: Sequence[str], cfg: CatalogSales) -> PopulationSource | None:
    return next((p for p in cfg.population if set(p.attribute_keys) == set(keys)), None)


def coverage_buyers(
    store: SalesStore,
    tenant_id: str,
    attribute_keys: Sequence[str],
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> MarketCoverageOutput:
    """구매자 수만 채운 줄 (인구는 `coverage_with_population`이 붙인다)."""
    snap = snapshot(store, tenant_id, "365d", cfg, catalog_id, now)
    common: dict[str, Any] = {
        "attribute_keys": list(attribute_keys),
        "quality": snap.report,
        "basis": snap.basis,
    }
    problems: list[str] = []
    pop = find_population(attribute_keys, cfg) if cfg else None
    if cfg is not None and pop is None:
        allowed = " / ".join("·".join(p.attribute_keys) for p in cfg.population) or "없음"
        problems.append(
            f"설정에 없는 속성 조합: {'·'.join(attribute_keys)} (가능한 조합: {allowed})"
        )
    reasons = problems + snap.reasons
    if reasons or cfg is None or pop is None:
        out = MarketCoverageOutput(**common, status="insufficient", insufficient_reasons=reasons)
        return out.model_copy(update={"summary": coverage_summary(out)})
    values = attribute_values(cfg)
    keys = list(pop.attribute_keys)  # 카탈로그 순서
    counts: dict[tuple[str, ...], int] = defaultdict(int)
    for buyer in snap.buyers:
        found = [known_value(snap.attrs.get(buyer), k, values[k]) for k in keys]
        if all(found):  # 하나라도 모름이면 세지 않는다
            counts[tuple(str(v) for v in found)] += 1
    least = cfg.min_group_size
    combos = list(itertools.product(*(list(values[k]) for k in keys)))
    found = [counts.get(combo, 0) for combo in combos]
    hidden = protect(  # 1차(기준 미만) + 2차 숨김 (줄·전체 합계로 계산되지 않게)
        found,
        {i for i, n in enumerate(found) if n < least},
        coverage_groups([len(values[k]) for k in keys]),
    )
    rows = []
    for i, combo in enumerate(combos):
        rows.append(
            CoverageRow(
                values=dict(zip(keys, combo, strict=True)),
                label=" · ".join(values[k][c] for k, c in zip(keys, combo, strict=True)),
                buyers=None if i in hidden else found[i],
                suppressed=i in hidden,
                base_year=pop.base_year,
            )
        )
    out = MarketCoverageOutput(**{**common, "attribute_keys": keys}, rows=rows)
    return out.model_copy(update={"summary": coverage_summary(out)})


def _codes(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(c) for c in raw]
    return [str(raw)] if raw not in (None, "") else []


async def population_for(
    pop: PopulationSource, rows: list[CoverageRow], kosis: KosisConnector
) -> list[CoverageRow]:
    """인구를 붙인다. 표 설정이 없거나 못 읽으면 줄마다 이유만 (인구를 지어내지 않는다)."""
    ref = pop.table_ref

    def all_rows(status: str, reason: str) -> list[CoverageRow]:
        return [r.model_copy(update={"status": status, "reason": reason}) for r in rows]

    if not ref:
        return all_rows("population_missing", POPULATION_MISSING)
    if pop.source_id != "kosis":
        return all_rows("error", f"아직 연결하지 않은 인구 출처: {pop.source_id}")
    missing = [k for k in ("org_id", "tbl_id", "itm_id", "prd_se") if not ref.get(k)]
    if missing or len(pop.attribute_keys) > 2 or not isinstance(ref.get("codes"), dict):
        need = ", ".join(missing + ([] if isinstance(ref.get("codes"), dict) else ["codes"]))
        return all_rows(
            "population_missing", f"{POPULATION_MISSING} (빠진 칸: {need or '속성 2개까지'})"
        )
    dims = population_dims(pop)
    if dims is None:
        return all_rows("population_missing", f"{POPULATION_MISSING} (dims 설정 확인)")
    use_l2 = "c2" in dims.values() or "obj_l2" in ref
    try:
        points = await kosis.statistics_data(
            str(ref["org_id"]), str(ref["tbl_id"]), str(ref["itm_id"]),
            str(ref.get("obj_l1", "ALL")), str(ref["prd_se"]),
            new_est_prd_cnt=int(ref.get("new_est_prd_cnt", 1)),
            obj_l2=str(ref.get("obj_l2", "ALL")) if use_l2 else None,
        )  # fmt: skip
    except ConnectorUnavailable as exc:
        return all_rows("error", f"KOSIS를 읽지 못함 ({exc.code}: {exc})")
    return _attach(pop, rows, points)


DIMS = ("c1", "c2", "itm")  # 속성을 맞출 수 있는 통계표 칸: 첫째 분류·둘째 분류·항목(ITM_ID)
DOUBLED = "통계표 설정 확인 필요: 같은 칸 값이 여러 개 (지역 등을 한 곳으로)"


def population_dims(pop: PopulationSource) -> dict[str, str] | None:
    """속성 key → 통계표 칸. `table_ref.dims`가 없으면 예전처럼 첫 속성 c1, 둘째 속성 c2.

    예: 행정구역 × 연령 표에서 성별이 항목(남자·여자 인구수)이면 {"age_band": "c2", "gender":
    "itm"}이고 행정구역은 `obj_l1`로 한 곳(예: 전국)만 고른다. 잘못된 설정이면 None.
    """
    raw = pop.table_ref.get("dims")
    if raw is None:
        return dict(zip(pop.attribute_keys, ("c1", "c2"), strict=False))
    if not isinstance(raw, dict) or set(raw) != set(pop.attribute_keys):
        return None
    dims = {str(k): str(v) for k, v in raw.items()}
    if any(v not in DIMS for v in dims.values()) or len(set(dims.values())) != len(dims):
        return None
    return dims


def base_month(latest: str, prd_se: str) -> str | None:
    """월 단위 표의 기준 달 "YYYY-MM" (예: "202610" → "2026-10"). 아니면 None."""
    if prd_se.upper() == "M" and len(latest) == 6 and latest.isdigit():
        return f"{latest[:4]}-{latest[4:]}"
    return None


def _attach(
    pop: PopulationSource, rows: list[CoverageRow], points: list[KosisDataPoint]
) -> list[CoverageRow]:
    """최근 시점 값을 속성 값 코드로 더한다. 코드 목록이 비어 있는 값(예: 응답 안 함)은 인구가
    없어 커버 비율을 내지 않는다. 쓰지 않는 칸(예: 지역)은 한 코드로 골라야 한다(겹치면 이유만)."""
    ref = pop.table_ref
    codes: dict[str, dict[str, Any]] = ref["codes"]
    scale = float(ref.get("unit_scale", 1) or 1)
    dims = population_dims(pop) or {}
    dated = [p for p in points if p.value is not None]
    if not dated:
        return [
            r.model_copy(update={"status": "population_missing", "reason": "통계표에 값이 없음"})
            for r in rows
        ]
    latest = max(p.prd_de for p in dated)
    used = set(dims.values())

    def cell(c1: str, c2: str, itm: str) -> tuple[str, str, str]:
        return (c1 if "c1" in used else "", c2 if "c2" in used else "",
                itm if "itm" in used else "")  # fmt: skip

    table: dict[tuple[str, str, str], float] = {}
    doubled = False
    for p in dated:
        if p.prd_de != latest or p.value is None:
            continue
        key = cell(p.c1, p.c2, p.itm_id)
        doubled = doubled or key in table
        table[key] = p.value
    label = str(ref.get("label") or dated[0].tbl_nm or "KOSIS")
    month = base_month(latest, str(ref.get("prd_se", "")))
    if month:
        label = f"{label} ({month} 기준)"
    year = pop.base_year or (int(latest[:4]) if latest[:4].isdigit() else None)
    out = []
    for r in rows:
        update: dict[str, Any] = {"population_source": label, "base_year": year}
        per_dim = {d: [""] for d in DIMS}
        for key, dim in dims.items():
            per_dim[dim] = _codes(codes.get(key, {}).get(r.values[key]))
        cells = [cell(*c) for c in itertools.product(per_dim["c1"], per_dim["c2"], per_dim["itm"])]
        if doubled:
            update |= {"status": "population_missing", "reason": DOUBLED}
        elif not cells:
            update |= {
                "status": "population_missing",
                "reason": "인구 통계에 없는 값이라 비율을 내지 않음",
            }
        elif any(c not in table for c in cells):
            update |= {"status": "population_missing", "reason": "통계표에 이 조합의 값이 없음"}
        else:
            people = round(sum(table[c] for c in cells) * scale)
            update["population"] = people
            if r.buyers is not None and people > 0:
                if r.buyers > people:
                    update |= {"status": "error", "reason": "구매자가 인구보다 많음 (설정 확인)"}
                else:
                    update["coverage"] = round(r.buyers / people, 8)
        out.append(r.model_copy(update=update))
    return out


def population_note(out: MarketCoverageOutput) -> str:
    """근거 한 줄: 인구 출처(기준 달 포함). 인구를 붙이지 않았으면 빈 문자열."""
    sources = sorted({r.population_source for r in out.rows if r.population_source})
    return f"인구 {', '.join(sources)}" if sources else ""


def coverage_summary(out: MarketCoverageOutput) -> str:
    if out.status != "ok":
        text = "시장 커버 비율 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
        return with_source(text, out.basis)
    measured = [r for r in out.rows if r.coverage is not None]
    if not measured:
        reasons = sorted({r.reason for r in out.rows if r.reason})
        text = f"최근 365일 구매자 묶음 {len(out.rows)}개, 인구 비교는 못 함"
        text += f" — {'; '.join(reasons)}." if reasons else "."
        return with_source(text, out.basis)
    best = max(measured, key=lambda r: r.coverage or 0)
    text = (
        f"최근 365일 구매자 기준 {len(measured)}개 묶음의 시장 커버 비율을 계산. 가장 높은 묶음은 "
        f"{best.label} ({best.buyers}명 / 인구 {best.population or 0:,}명 = "
        f"{(best.coverage or 0) * 100:.3f}%)."
    )
    return with_source(text, out.basis)


async def coverage_with_population(  # noqa: PLR0913
    store: SalesStore,
    tenant_id: str,
    attribute_keys: Sequence[str],
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
    kosis: KosisConnector | None = None,
) -> MarketCoverageOutput:
    out = await asyncio.to_thread(
        coverage_buyers, store, tenant_id, attribute_keys, cfg, catalog_id, now
    )
    pop = find_population(out.attribute_keys, cfg) if cfg else None
    if out.status != "ok" or pop is None:
        return out
    rows = await population_for(pop, out.rows, kosis or KosisConnector())
    out = out.model_copy(update={"rows": rows})
    return out.model_copy(update={"summary": coverage_summary(out)})
