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
- 읽기(11/17): 주문을 파이썬으로 올리지 않는다. 관문(고객 기록 없음·고객 내보내기 오류)을 먼저
  보고 걸리면 주문 수 COUNT 하나만 읽고 끝낸다. 숫자는 `SalesStore.segment_session`의 SQL 집계
  (GROUP BY·COUNT(DISTINCT buyer_id))로 받은 "속성 값 셀 → 구매자 수"에서 계산한다. 숨김 규칙
  (`protect`·`determined_cells`)은 그대로이고 입력만 집계 숫자다
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
- 고객 구성 지수(11/17, 화면 "우리 고객은 어느 층에 몰려 있나"): 커버 비율은 근거로 남기고
  - buyer_share = 그 묶음 구매자 ÷ 모든 속성 값을 답한 구매자 합 (응답 안 함처럼 표 `codes`가 빈
    값이 든 묶음은 분모에서도 빠진다)
  - population_share = 그 묶음 인구 ÷ 인구가 붙은 모든 묶음 인구 합
  - index = buyer_share ÷ population_share × 100. 숨긴 줄·응답 안 함·인구 없는 줄은 셋 다 None
  - 비중의 분모(답한 구매자 합)가 buyers ÷ buyer_share로 드러나므로 "답한 묶음 전체 합"도 공개
    합계로 보고 보완 숨김(`protect`)에 넣는다
  - population_note: 묶음마다 합산한 구간을 KOSIS 응답의 분류 이름(C1_NM·C2_NM·ITM_NM)으로 적는다
    ("20~24세 + 25~29세", 끝이 열린 구간이 들면 "50세 이상 합산"). 설정에 나이 범위를 적지 않는다
  - population_source = "<표 이름> · 2026년 10월 말"(월별) 또는 "· 2025년"(연별). "기준"을 붙이지
    않고 화면은 이 문자열 하나만 쓴다
  - 요약 = 지수가 가장 높은 묶음과 가장 낮은 묶음
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
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
from data_agent.aggregate import read_buyers as rb
from data_agent.aggregate.store import AggregateStore, cell_text
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.data4library import Data4LibraryConnector
from data_agent.connectors.kosis import KosisConnector, KosisDataPoint
from data_agent.connectors.naver_shopping import NaverShoppingConnector
from data_agent.sales_quality import (
    INSUFFICIENT,
    KST,
    Window,
    gate,
    quality_brief,
    quality_counted,
)
from data_agent.sales_store import (
    FAKE_SOURCE,
    Cells,
    SalesStore,
    SegmentSession,
    customers_source,
)
from data_agent.skills.sales_analysis import FAKE_DATA, NO_PRODUCT, category_label

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
# 고객은 있는데 속성(연령대·성별 등) 값이 한 명도 없음: 회사 사이트가 속성을 주지 않는다(11/26).
# 다른 회사(캣북스) 값으로 채우지 않는다
NO_ATTRIBUTES = f"{INSUFFICIENT}: 이 회사 사이트에서 받은 고객 속성이 없어요"
EXTERNAL_LABEL: dict[str, str] = {
    "data4library": "도서관 정보나루 인기대출",
    "naver_shopping": "네이버 쇼핑인사이트 분야 연령 비중",
}
D4L_PARAMS = ("kdc", "from_age", "to_age", "gender")
MAX_GENRE_CALLS = 2  # 장르(KDC) 조건을 더한 정보나루 호출 수 상한 (요청 하나당)
POPULATION_MISSING = "인구 통계표 설정 필요"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- 기간·읽기·관문


def segment_window(period: SegmentPeriod, now: datetime) -> Window:
    """한국 시간 오늘 0시에서 N-1일 전 0시 ~ now. 직전 기간 없음(시작 시각과 같게)."""
    local = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = (local - timedelta(days=SEGMENT_DAYS[period] - 1)).astimezone(UTC)
    return Window(start=start, end=now.astimezone(UTC), previous_start=start, previous_end=start)


@dataclass
class Snapshot:
    """한 기간의 관문 결과(품질 보고·이유·근거). 숫자는 `session`으로 집계해 받는다.

    주문을 파이썬으로 올리지 않는다. `SalesStore.segment_session`이 SQL GROUP BY·
    COUNT(DISTINCT buyer_id)로 구매자 수만 돌려준다. 구매자는 기간 안 결제 완료 주문이 있는 가명
    buyer_id, 셀은 구매자마다 속성 값을 `keys` 순서로 묶은 것이다(없으면 None).
    """

    window: Window
    report: SalesQuality
    reasons: list[str]
    basis: SalesBasis
    store: SalesStore
    tenant_id: str

    def session(self, keys: Sequence[str]) -> AbstractContextManager[SegmentSession]:
        return self.store.segment_session(self.tenant_id, self.window.start, self.window.end, keys)


def cell_count(cells: Cells, keys: Sequence[str], filters: dict[str, str]) -> int:
    """셀 중 속성 조건(`filters`, 값이 그대로 같아야 함)이 모두 맞는 구매자 수."""
    at = [(keys.index(k), v) for k, v in filters.items()]
    return sum(n for cell, n in cells.items() if all(cell[i] == v for i, v in at))


def data_source(store: SalesStore, tenant_id: str) -> str | None:
    """저장소를 가짜 출처(데모 전용)가 채웠으면 "가짜 데이터(데모용)" (sales_analysis와 같다)."""
    return FAKE_DATA if store.sync_state(tenant_id).source == FAKE_SOURCE else None


def customer_reasons(
    store: SalesStore, tenant_id: str, cfg: CatalogSales | None = None
) -> list[str]:
    """고객 기록 관문: 고객 내보내기 출처의 마지막 오류, 저장된 고객 0명, 속성 값 0개."""
    reasons: list[str] = []
    source = store.sync_state(tenant_id).source
    if source:
        error = store.sync_state(tenant_id, customers_source(source)).last_error
        if error:
            reasons.append(f"{INSUFFICIENT}: {error}")
    if store.customer_count(tenant_id) == 0:
        reasons.append(NO_CUSTOMERS)
    elif cfg is not None and cfg.attributes and not store.attribute_values(tenant_id):
        reasons.append(NO_ATTRIBUTES)
    return reasons


def snapshot(
    store: SalesStore,
    tenant_id: str,
    period: SegmentPeriod,
    cfg: CatalogSales | None,
    catalog_id: str,
    now: datetime,
) -> Snapshot:
    """관문을 먼저 본다. 고객 기록 관문(고객 없음·고객 내보내기 오류)에 걸리면 주문 집계를 하지
    않고 짧은 품질 보고(기간 안 주문 수 COUNT 하나·버린 행·마지막 수집)와 이유만 돌려준다.
    이유 순서는 예전과 같다(품질 관문 → 고객 기록 관문)."""
    w = segment_window(period, now)
    created, paid = store.order_counts(tenant_id, w.start, w.end)
    early = customer_reasons(store, tenant_id, cfg) if cfg is not None else []
    if early:
        report = quality_brief(store, tenant_id, w, created)
    else:
        report = quality_counted(store, tenant_id, w, created, cfg)
    ready = rb.from_snapshot(tenant_id, now, lambda agg, day: True) is True  # 3-2 ⑨
    reasons = gate(report, paid, cfg, now, aggregate_ready=ready) + early
    basis = SalesBasis(
        period_from=w.start,
        period_to=w.end,
        previous_from=w.start,
        previous_to=w.start,
        rows_read=created + store.customer_count(tenant_id),
        last_synced_at=report.last_synced_at,
        catalog_id=catalog_id,
        catalog_version=cfg.version if cfg else None,
        data_source=data_source(store, tenant_id),
    )
    return Snapshot(w, report, reasons, basis, store, tenant_id)


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


def by_value(cells: Cells, at: int, values: dict[str, str]) -> dict[str, int]:
    """셀의 `at`번째 속성 값별 구매자 수. 카탈로그에 없는 값·모름(None)은 빼고 센다."""
    out: dict[str, int] = defaultdict(int)
    for cell, n in cells.items():
        if cell[at] in values:
            out[cell[at]] += n
    return dict(out)


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


def options(
    cfg: CatalogSales | None,
    catalog_id: str,
    source: str | None,
    site_categories: Sequence[str] = (),
) -> SegmentOptionsOutput:
    """화면 선택지. 카탈로그 `[sales]`를 순서 그대로 내려 준다 (숫자 없음).

    분류 표가 없는 회사(사이트 변환기로 연결한 회사)는 판매 기록에 나온 사이트 분류 값을
    그대로 이름으로 쓴다(`site_categories`, #1841과 같은 규칙: 누비투어 JP·스터디온 DEV)."""
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
        categories=(
            [OptionItem(code=c, label=n) for c, n in cfg.categories.items()]
            if cfg.categories
            else [OptionItem(code=c, label=c) for c in site_categories]
        ),
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
    found, name = rb.run_source(tenant_id)  # 3-2 ②: 집계 계산 기록의 출처 (원본을 열지 않는다)
    codes = site_category_codes(store, tenant_id, cfg, aggregate=found)
    if found:
        return options(cfg, catalog_id, FAKE_DATA if name == FAKE_SOURCE else None, codes)
    return options(cfg, catalog_id, data_source(store, tenant_id), codes)


def site_category_codes(
    store: SalesStore, tenant_id: str, cfg: CatalogSales | None, aggregate: bool = False
) -> list[str]:
    """분류 표가 없는 회사의 선택지: 그 회사 판매 기록에 나온 분류 code (빈 값 제외, 정렬).

    분류 표가 있으면(캣북스) 읽지 않는다. 집계 경로면 집계(`agg_sales_daily`)에서, 아니면
    판매 저장소에서 읽는다. 못 읽으면 빈 목록(다른 회사 값으로 채우지 않는다)."""
    if cfg is None or cfg.categories:
        return []
    try:
        if aggregate:
            return rb.category_codes(tenant_id)
        return store.category_codes(tenant_id)
    except Exception:  # noqa: BLE001 - 선택지는 비워 두고 화면은 그대로 연다
        logger.warning("[segment] 사이트 분류 목록 읽기 실패 (%s)", tenant_id, exc_info=True)
        return []


# ---------------------------------------------------------------- segment_profile


def target_label(
    kind: SegmentTargetKind, code: str, cfg: CatalogSales | None, titles: dict[str, str]
) -> str:
    if kind == "category":
        return category_label(cfg, code)
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
    titles: dict[str, str] = {}
    if target_kind == "product":
        w = snap.window
        found = store.product_title(tenant_id, w.start, w.end, target_code)
        titles = {target_code: found} if found else {}
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
        if target_kind == "category" and not cfg.categories:
            known = None  # 분류 표 없는 회사: 사이트 분류 값 그대로(#1841)
        if known is not None and target_code not in known:
            problems.append(f"설정에 없는 {KIND_LABEL[target_kind]}: {target_code}")
    reasons = problems + snap.reasons
    if reasons or cfg is None:
        out = SegmentProfileOutput(**common, status="insufficient", insufficient_reasons=reasons)
        return out.model_copy(update={"summary": profile_summary(out)})

    least = cfg.min_group_size
    ordered = sorted(keys, key=[a.key for a in cfg.attributes].index)
    sales_cfg = cfg
    got = rb.from_snapshot(tenant_id, now, lambda agg, day: profile_from_snapshot(
        agg, tenant_id, target_kind, target_code, ordered, period, sales_cfg, day))  # fmt: skip
    if got is not None:
        fields, as_of = got
        common["basis"] = rb.basis_at(snap.basis, rb.window_of(period, as_of))
        out = SegmentProfileOutput(**common, **fields)
        return out.model_copy(update={"summary": rb.with_note(profile_summary(out, least), as_of)})
    with snap.session(ordered) as s:
        overall_cells = s.buyer_cells()
        target_cells = s.item_cells(target_kind, target_code)
    overall_n, target_n = sum(overall_cells.values()), sum(target_cells.values())
    suppressed = target_n < least
    breakdowns = []
    for at, key in enumerate(ordered):
        attr = next(a for a in cfg.attributes if a.key == key)
        t_by = by_value(target_cells, at, attr.values)
        o_by = by_value(overall_cells, at, attr.values)
        t_known, o_known = sum(t_by.values()), sum(o_by.values())
        unknown = target_n - t_known
        counts = [t_by.get(code, 0) for code in attr.values]
        hidden, hide_unknown = profile_hidden(counts, unknown, least)  # 1차 + 2차 숨김
        shares = []
        for i, (code, label) in enumerate(attr.values.items()):
            n = counts[i]
            if suppressed or i in hidden:
                shares.append(SegmentShare(value_code=code, label=label, suppressed=True))
                continue
            ts, os_ = ratio(n, t_known), ratio(o_by.get(code, 0), o_known)
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
        target_buyers=None if suppressed else target_n,
        target_suppressed=suppressed,
        overall_buyers=overall_n,
        attributes=breakdowns,
    )
    return out.model_copy(update={"summary": profile_summary(out, least)})


def profile_from_snapshot(  # noqa: PLR0913 - 입력 칸 그대로
    agg: AggregateStore,
    tenant_id: str,
    target_kind: SegmentTargetKind,
    target_code: str,
    ordered: list[str],
    period: SegmentPeriod,
    cfg: CatalogSales,
    as_of: date,
) -> tuple[dict[str, Any], date] | None:
    """어제 기준 고객 프로필 스냅샷(대상 × 속성 값, 숨김은 저장 때 끝남)을 화면 칸으로 (3-2 ⑥).

    분모(그 속성을 아는 사람)를 숨긴 칸 때문에 알 수 없거나, 전체 구매자 속성 칸이 숨겨져 있으면
    None(원본). 대상 구매자가 k 미만이면 줄이 없거나 숨겨져 있어 전체를 숨긴다."""
    rows = [r for r in agg.buyer_rows(tenant_id, as_of) if r["period"] == period]
    whole = next((r for r in rows if r["dim_kind"] == "all"), None)
    if whole is None or whole["buyers_hidden"]:
        return None
    mine = {r["attr_cell"]: r for r in rows
            if r["dim_kind"] == target_kind and r["dim_code"] == target_code}  # fmt: skip
    overall = {r["attr_cell"]: r for r in rows if r["dim_kind"] == "attr"}
    target = mine.get("")
    suppressed = target is None or bool(target["buyers_hidden"])
    breakdowns = []
    for key in ordered:
        attr = next(a for a in cfg.attributes if a.key == key)
        o = [overall.get(cell_text({key: v})) for v in attr.values]
        if any(r is None or r["buyers_hidden"] for r in o):
            return None
        o_known = sum(r["buyers"] for r in o if r is not None)
        if suppressed:
            shares = [SegmentShare(value_code=c, label=lb, suppressed=True)
                      for c, lb in attr.values.items()]  # fmt: skip
            breakdowns.append(SegmentAttributeBreakdown(key=key, label=attr.label, values=shares))
            continue
        t = [mine.get(cell_text({key: v})) for v in attr.values]
        unk = mine.get(cell_text({key: ""}))
        if any(r is None for r in t) or unk is None:
            return None
        unknown = None if unk["buyers_hidden"] else unk["buyers"]
        if unknown is not None:
            t_known = target["buyers"] - unknown
        elif all(not r["buyers_hidden"] for r in t):
            t_known = sum(r["buyers"] for r in t)
        elif all(r["buyers_hidden"] for r in t):
            t_known = 0  # 보이는 칸이 없어 분모를 쓰지 않는다
        else:
            return None  # 보이는 칸의 분모를 알 수 없다
        shares = []
        for (c, lb), r, orow in zip(attr.values.items(), t, o, strict=True):
            if r["buyers_hidden"]:
                shares.append(SegmentShare(value_code=c, label=lb, suppressed=True))
                continue
            ts, os_ = ratio(r["buyers"], t_known), ratio(orow["buyers"], o_known)
            shares.append(SegmentShare(value_code=c, label=lb, target_share=ts, overall_share=os_,
                                       lift=lift(ts, os_), buyers=r["buyers"]))  # fmt: skip
        breakdowns.append(SegmentAttributeBreakdown(key=key, label=attr.label, values=shares,
                                                    unknown_buyers=unknown))  # fmt: skip
    fields = {
        "target_buyers": None if suppressed else target["buyers"],
        "target_suppressed": suppressed,
        "overall_buyers": whole["buyers"],
        "attributes": breakdowns,
    }
    return fields, as_of


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
    sales_cfg = cfg
    got = rb.from_snapshot(tenant_id, now, lambda agg, day: favorites_from_snapshot(
        agg, tenant_id, filters, period, top, sales_cfg, day))  # fmt: skip
    if got is not None:
        segment, items, as_of = got
        common["basis"] = rb.basis_at(snap.basis, rb.window_of(period, as_of))
        out = SegmentFavoritesOutput(
            **common, segment_buyers=segment, segment_suppressed=segment is None, internal=items
        )
        text = favorites_summary(out, cfg.min_group_size)
        return out.model_copy(update={"summary": rb.with_note(text, as_of)})
    least = cfg.min_group_size
    keys = list(filters)
    with snap.session(keys) as s:
        cells = s.buyer_cells()
        overall_n = sum(cells.values())
        segment_n = cell_count(cells, keys, filters)
        suppressed = segment_n < least
        bought: list[tuple[str, str, int, int]] = []
        titles: dict[str, str] = {}
        if not suppressed:
            bought = s.item_counts(filters)
            # 상품 이름은 순위에 나갈 수 있는 상품만 찾는다 (11/18: 기간 줄 전체를 다시 잇지 않게)
            titles = s.titles(ids=products_to_name(bought, segment_n, overall_n, least, top))
    internal: list[FavoriteItem] = []
    if not suppressed:
        names: dict[str, Callable[[str], str | None]] = {
            "genre": cfg.genres.get,
            "category": lambda c: category_label(cfg, c),
            "product": lambda c: titles.get(c, NO_PRODUCT),
        }
        for kind in ("genre", "category", "product"):
            internal += ranked(kind, bought, segment_n, overall_n, names[kind], least, top)
    out = SegmentFavoritesOutput(
        **common,
        segment_buyers=None if suppressed else segment_n,
        segment_suppressed=suppressed,
        internal=internal,
    )
    return out.model_copy(update={"summary": favorites_summary(out, least)})


GROUP = ""  # 스냅샷의 묶음 크기 줄 (item_code, build_more.GROUP과 같다)


def favorites_from_snapshot(  # noqa: PLR0913 - 입력 칸 그대로
    agg: AggregateStore,
    tenant_id: str,
    filters: dict[str, str],
    period: SegmentPeriod,
    top: int,
    cfg: CatalogSales,
    as_of: date,
) -> tuple[int | None, list[FavoriteItem], date] | None:
    """어제 기준 즐겨 사는 것 스냅샷을 화면 순위로 (3-2 ⑥).
    (묶음 크기 또는 숨김 None, 순위, 기준일)

    조건은 속성 1~2개만 저장돼 있다. 그 밖이거나 묶음 크기 줄이 없는 옛 스냅샷이면 None(원본).
    숨긴 항목은 숫자 없이 저장돼 있어 순위에서는 "기준 미만"으로만 쓴다(이름 순으로 뒤)."""
    ordered = {a.key: filters[a.key] for a in cfg.attributes if a.key in filters}
    if len(ordered) != len(filters) or not 1 <= len(ordered) <= 2:  # noqa: PLR2004 - 속성 1~2개
        return None
    cell = cell_text(ordered)
    rows = [r for r in agg.more_rows("agg_segment_profile", tenant_id, as_of)
            if r["period"] == period and r["attr_cell"] == cell]  # fmt: skip
    size = next((r for r in rows if r["item_code"] == GROUP), None)
    if size is None:
        return None
    if size["buyers_hidden"]:
        return None, [], as_of
    segment_n, overall_n = size["buyers"], size["item_buyers"]
    least = cfg.min_group_size
    bought = [
        (r["item_level"], r["item_code"], r["item_buyers"],
         1 if r["buyers_hidden"] else r["buyers"])  # 숨김 = 0 < n < k
        for r in rows if r["item_code"] != GROUP
    ]  # fmt: skip
    titles = {r["item_code"]: r["title"] for r in rows if r["item_level"] == "product"}
    names: dict[str, Callable[[str], str | None]] = {
        "genre": cfg.genres.get,
        "category": lambda c: category_label(cfg, c),
        "product": lambda c: titles.get(c) or NO_PRODUCT,
    }
    items: list[FavoriteItem] = []
    for kind in ("genre", "category", "product"):
        items += ranked(kind, bought, segment_n, overall_n, names[kind], least, top)
    return segment_n, items, as_of


def products_to_name(
    bought: Sequence[tuple[str, str, int, int]], segment_n: int, overall_n: int, least: int,
    top: int,
) -> set[str]:  # fmt: skip
    """`ranked`가 상품 순위에 넣을 수 있는 상품 id: 보이는 상품 중 (구매 비율, 배수)로 상위
    `top`개와 그 경계와 같은 값인 상품(이름으로 순서가 갈릴 수 있음). 보이는 상품이 `top`개보다
    적으면 숨긴 상품(이름 순으로 뒤에 붙는다)도 모두. 나머지 상품은 이름 없이도 순위가 같다."""
    shown: list[tuple[float, float, str]] = []
    hidden: list[str] = []
    for kind, code, total, n in bought:
        if kind != "product" or n == 0:
            continue
        if n < least:
            hidden.append(code)
            continue
        share = ratio(n, segment_n)
        shown.append((share or 0, lift(share, ratio(total, overall_n)) or 0, code))
    shown.sort(key=lambda x: (-x[0], -x[1]))
    if len(shown) <= top:
        return {c for *_, c in shown} | set(hidden)
    edge = shown[top - 1][:2]
    return {c for share, lf, c in shown if (share, lf) >= edge}


def ranked(  # noqa: PLR0913
    kind: str,
    bought: Sequence[tuple[str, str, int, int]],
    segment_n: int,
    overall_n: int,
    name: Callable[[str], str | None],
    least: int,
    top: int,
) -> list[FavoriteItem]:
    """한 종류(장르·분류·상품)의 순위: 집단 구매자 중 산 비율 큰 순. 기준 미만은 뒤에 숨김으로.

    `bought` = (종류, code, 산 구매자 수, 그중 집단 구매자 수) — `SalesStore.item_buyer_counts`."""
    shown: list[FavoriteItem] = []
    hidden: list[FavoriteItem] = []
    for k, code, total, n in bought:
        label = name(code)
        if k != kind or label is None:  # 카탈로그에 없는 장르는 품질 보고(unknown_genres)에만
            continue
        if n == 0:
            continue
        if n < least:
            hidden.append(FavoriteItem(kind=kind, code=code, label=label, suppressed=True))
            continue
        share = ratio(n, segment_n)
        base = ratio(total, overall_n)
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
    internal: SegmentFavoritesOutput | None = None,
) -> SegmentFavoritesOutput:
    """저장소 읽기는 스레드에서, 바깥 출처는 status ok일 때만 부른다.

    `internal`(11/18): 이미 계산한 우리 쇼핑몰 순위(결과 캐시). 주면 저장소를 다시 읽지 않는다."""
    out = internal or await asyncio.to_thread(
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


def coverage_cells(
    pop: PopulationSource, cfg: CatalogSales, counts: Cells
) -> tuple[list[tuple[str, ...]], list[int], set[int], list[int]]:
    """인구 속성 조합 칸별 구매자 수와 숨길 칸 (조합, 수, 숨김, 답한 조합 위치).
    1차(기준 미만, 0 포함) + 2차 숨김(줄·열·전체 합, 답한 묶음 합으로 계산되지 않게)."""
    values = attribute_values(cfg)
    keys = list(pop.attribute_keys)
    combos = list(itertools.product(*(list(values[k]) for k in keys)))
    found = [counts.get(combo, 0) for combo in combos]
    answered = answered_combos(pop, keys, combos)
    groups = coverage_groups([len(values[k]) for k in keys])
    if 0 < len(answered) < len(combos):
        # 구성 지수(11/17): 비중의 분모(답한 구매자 합)가 buyers ÷ buyer_share로 계산되므로
        # "답한 묶음 전체 합"도 공개된 합계로 본다 (보이는 비중 합으로 숨긴 칸을 풀 수 없게)
        groups.append(list(answered))
    least = cfg.min_group_size
    hidden = protect(found, {i for i, n in enumerate(found) if n < least}, groups)
    return combos, found, hidden, list(answered)


def coverage_rows(  # noqa: PLR0913
    pop: PopulationSource,
    cfg: CatalogSales,
    combos: list[tuple[str, ...]],
    shown: list[int | None],
    answered_total: int,
    answered: Sequence[int],
) -> list[CoverageRow]:
    """칸 수(숨긴 칸은 None) → 화면 줄."""
    values = attribute_values(cfg)
    keys = list(pop.attribute_keys)
    rows = []
    for i, combo in enumerate(combos):
        n = shown[i]
        rows.append(CoverageRow(
            values=dict(zip(keys, combo, strict=True)),
            label=" · ".join(values[k][c] for k, c in zip(keys, combo, strict=True)),
            buyers=n, suppressed=n is None, base_year=pop.base_year,
            buyer_share=ratio(n, answered_total) if i in answered and n is not None else None,
        ))  # fmt: skip
    return rows


ANSWERED = "answered"  # 스냅샷: 답한 묶음 전체 구매자 수 줄 (dim_code 끝)


def coverage_key(pop: PopulationSource) -> str:
    return ",".join(pop.attribute_keys)


def coverage_from_snapshot(
    agg: AggregateStore, tenant_id: str, pop: PopulationSource, cfg: CatalogSales, as_of: date
) -> tuple[list[CoverageRow], date] | None:
    """어제 기준 365일 인구 조합 칸 스냅샷(숨김은 저장 때 끝남)을 화면 줄로 (3-2 ⑥)."""
    key = coverage_key(pop)
    rows = [r for r in agg.buyer_rows(tenant_id, as_of)
            if r["period"] == "365d" and r["dim_kind"] == "attr_combo"]  # fmt: skip
    cells = {r["attr_cell"]: r for r in rows if r["dim_code"] == key}
    total = next((r for r in rows if r["dim_code"] == f"{key}:{ANSWERED}"), None)
    values = attribute_values(cfg)
    keys = list(pop.attribute_keys)
    combos = list(itertools.product(*(list(values[k]) for k in keys)))
    texts = [cell_text(dict(zip(keys, c, strict=True))) for c in combos]
    if total is None or any(t not in cells for t in texts):
        return None
    shown = [None if cells[t]["buyers_hidden"] else cells[t]["buyers"] for t in texts]
    answered = answered_combos(pop, keys, combos)
    return coverage_rows(pop, cfg, combos, shown, total["buyers"] or 0, list(answered)), as_of


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
    keys = list(pop.attribute_keys)  # 카탈로그 순서
    sales_cfg, population = cfg, pop
    got = rb.from_snapshot(tenant_id, now, lambda agg, day: coverage_from_snapshot(
        agg, tenant_id, population, sales_cfg, day))  # fmt: skip
    if got is not None:
        rows, as_of = got
        common["basis"] = rb.basis_at(snap.basis, rb.window_of("365d", as_of))
        out = MarketCoverageOutput(**{**common, "attribute_keys": keys}, rows=rows)
        return out.model_copy(update={"summary": rb.with_note(coverage_summary(out), as_of)})
    # 셀 = 구매자의 속성 값 묶음. 아래 조합(카탈로그 값만)으로 찾으므로 하나라도 모름이거나
    # 카탈로그에 없는 값인 구매자는 세지 않는다
    with snap.session(keys) as s:
        counts = s.buyer_cells()
    combos, found, hidden, answered = coverage_cells(pop, cfg, counts)
    rows = coverage_rows(pop, cfg, combos, [None if i in hidden else n for i, n in
                                            enumerate(found)],
                         sum(found[i] for i in answered), answered)  # fmt: skip
    out = MarketCoverageOutput(**{**common, "attribute_keys": keys}, rows=rows)
    return out.model_copy(update={"summary": coverage_summary(out)})


def _codes(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(c) for c in raw]
    return [str(raw)] if raw not in (None, "") else []


def answered_combos(
    pop: PopulationSource, keys: Sequence[str], combos: Sequence[tuple[str, ...]]
) -> set[int]:
    """인구 통계에 있는 값으로만 된 묶음(위치). 표 설정 `codes`에서 코드 목록이 빈 값(예: 응답
    안 함)이 하나라도 든 묶음은 뺀다. `codes`가 없으면 모든 묶음."""
    codes = pop.table_ref.get("codes")
    if not isinstance(codes, dict):
        return set(range(len(combos)))
    return {
        i
        for i, combo in enumerate(combos)
        if all(_codes(codes.get(k, {}).get(c)) for k, c in zip(keys, combo, strict=True))
    }


async def population_for(
    pop: PopulationSource, rows: list[CoverageRow], kosis: KosisConnector
) -> list[CoverageRow]:
    """인구를 붙인다. 표 설정이 없거나 못 읽으면 줄마다 이유만 (인구를 지어내지 않는다)."""
    ref = pop.table_ref

    def all_rows(status: str, reason: str) -> list[CoverageRow]:
        # 인구가 없으면 비중·지수도 내지 않는다
        return [
            r.model_copy(update={"status": status, "reason": reason, "buyer_share": None})
            for r in rows
        ]

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
    names: dict[str, dict[str, str]] = {d: {} for d in DIMS}  # 칸 → 코드 → KOSIS 분류 이름
    doubled = False
    for p in dated:
        if p.prd_de != latest or p.value is None:
            continue
        key = cell(p.c1, p.c2, p.itm_id)
        doubled = doubled or key in table
        table[key] = p.value
        for dim, code, name in (("c1", p.c1, p.c1_nm), ("c2", p.c2, p.c2_nm),
                                ("itm", p.itm_id, p.itm_nm)):  # fmt: skip
            if name:
                names[dim].setdefault(code, name)
    label = source_text(str(ref.get("label") or dated[0].tbl_nm or "KOSIS"), latest,
                        str(ref.get("prd_se", "")))  # fmt: skip
    year = pop.base_year or (int(latest[:4]) if latest[:4].isdigit() else None)
    out = []
    for r in rows:
        update: dict[str, Any] = {"population_source": label, "base_year": year}
        per_dim = {d: [""] for d in DIMS}
        for key, dim in dims.items():
            per_dim[dim] = _codes(codes.get(key, {}).get(r.values[key]))
        bands = " · ".join(
            note for key, dim in dims.items()
            if len(per_dim[dim]) > 1
            and (note := band_note([names[dim].get(c, "") for c in per_dim[dim]]))
        )  # fmt: skip
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
            update["population_note"] = bands  # 합산한 구간 (인구를 붙인 줄만)
            if r.buyers is not None and people > 0:
                if r.buyers > people:
                    update |= {"status": "error", "reason": "구매자가 인구보다 많음 (설정 확인)"}
                else:
                    update["coverage"] = round(r.buyers / people, 8)
        if update.get("status", r.status) != "ok" or update.get("population") is None:
            update["buyer_share"] = None  # 응답 안 함·인구 없는 줄은 비중·지수를 내지 않는다
        out.append(r.model_copy(update=update))
    return with_index(out)


def source_text(label: str, latest: str, prd_se: str) -> str:
    """출처 한 줄: "<표 이름> · 2026년 10월 말"(월별) 또는 "<표 이름> · 2025년"(연별). "기준"을
    붙이지 않는다(화면이 기준 해를 따로 붙이지 않고 이 문자열 하나만 쓴다)."""
    month = base_month(latest, prd_se)
    if month:
        return f"{label} · {int(month[:4])}년 {int(month[5:])}월 말"
    if len(latest) >= 4 and latest[:4].isdigit():
        return f"{label} · {latest[:4]}년"
    return label


_BAND = re.compile(r"^\s*(\d+)\s*[-~]\s*(\d+)\s*세?\s*$")
_OPEN = re.compile(r"^\s*(\d+)\s*(?:세\s*이상|세?\s*\+)\s*$")


def band_note(names: Sequence[str]) -> str:
    """합산한 인구 구간 이름 (KOSIS 분류 이름에서). "20 - 24세", "25 - 29세" → "20~24세 + 25~29세".
    끝이 열린 구간(예: "100세 이상", "100+")이 들면 "50세 이상 합산". 이름이 없으면 빈 문자열."""
    if not names or any(not n for n in names):
        return ""
    lows: list[int] = []
    parts: list[str] = []
    opened = False
    for n in names:
        if m := _BAND.match(n):
            lows.append(int(m[1]))
            parts.append(f"{int(m[1])}~{int(m[2])}세")
        elif m := _OPEN.match(n):
            lows.append(int(m[1]))
            opened = True
        else:
            parts.append(n.strip())
    if opened and lows:
        return f"{min(lows)}세 이상 합산"
    return " + ".join(parts)


def with_index(rows: list[CoverageRow]) -> list[CoverageRow]:
    """고객 구성 지수: population_share = 그 묶음 인구 ÷ 인구가 있는 묶음 인구 합,
    index = buyer_share ÷ population_share × 100. 숨긴 줄·응답 안 함·인구 없는 줄은 셋 다 None.
    분모는 인구가 붙은 모든 묶음(숨긴 묶음 포함, 응답 안 함은 인구가 없어 빠진다)의 합이다."""
    total = sum(r.population for r in rows if r.population is not None)
    out = []
    for r in rows:
        share = idx = None
        if total > 0 and r.buyer_share is not None and r.population is not None:
            share = round(r.population / total, 6)
            idx = round(r.buyer_share / share * 100, 1) if share > 0 else None
        out.append(
            r.model_copy(
                update={
                    "population_share": share,
                    "index": idx,
                    "buyer_share": r.buyer_share if share else None,
                }
            )
        )
    return out


def population_note(out: MarketCoverageOutput) -> str:
    """근거 한 줄: 인구 출처(기준 달 포함). 인구를 붙이지 않았으면 빈 문자열."""
    sources = sorted({r.population_source for r in out.rows if r.population_source})
    return f"인구 {', '.join(sources)}" if sources else ""


def topic(word: str) -> str:
    """주제 조사: 마지막 글자에 받침이 있으면 "은", 없으면 "는". 한글이 아니면 "은(는)"."""
    last = word.strip()[-1:] or " "
    if "가" <= last <= "힣":
        return f"{word}{'은' if (ord(last) - 0xAC00) % 28 else '는'}"
    return f"{word}은(는)"


def coverage_summary(out: MarketCoverageOutput) -> str:
    if out.status != "ok":
        text = "고객 구성 지수 숫자를 내지 않음 — " + "; ".join(out.insufficient_reasons)
        return with_source(text, out.basis)
    ranked = sorted((r for r in out.rows if r.index is not None),
                    key=lambda r: (-(r.index or 0), r.label))  # fmt: skip
    if ranked:
        top = ranked[0]
        text = (f"최근 365일 구매자 기준 고객 구성 지수: {topic(top.label)} 인구 비중보다 우리 "
                f"고객 비중이 {(top.index or 0) / 100:.1f}배(지수 {top.index:.0f})")  # fmt: skip
        if len(ranked) > 1:
            low = ranked[-1]
            text += (f", {topic(low.label)} {(low.index or 0) / 100:.1f}배(지수 "
                     f"{low.index:.0f})로 가장 적음")  # fmt: skip
        return with_source(text + ".", out.basis)
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
    buyers: MarketCoverageOutput | None = None,
) -> MarketCoverageOutput:
    """`buyers`(11/18): 이미 계산한 구매자 수 줄(결과 캐시). 주면 저장소를 다시 읽지 않는다."""
    out = buyers or await asyncio.to_thread(
        coverage_buyers, store, tenant_id, attribute_keys, cfg, catalog_id, now
    )
    pop = find_population(out.attribute_keys, cfg) if cfg else None
    if out.status != "ok" or pop is None:
        return out
    rows = await population_for(pop, out.rows, kosis or KosisConnector())
    out = out.model_copy(update={"rows": rows})
    return out.model_copy(update={"summary": coverage_summary(out)})
