"""매출 분석 기간(한국 시간)과 품질 보고·품질 관문. 표준 판매 저장소만 읽는다.

- 기간: 기준 시각(now)까지. today = 한국 시간 오늘 0시부터, 7d·30d = 6일·29일 전 0시부터.
  직전 기간 = 같은 길이로 1일·7일·30일 앞(오늘이면 어제 0시 ~ 어제 같은 시각)
- 저장소 시각은 UTC이고 여기서 한국 시간으로 바꿔 날짜·자정 경계를 정한다
- 품질 보고: 기간 안 주문 행 수, 주문이 하나도 없는 날, 버린 행 수·이유, 마지막 수집 시각,
  카탈로그에 이름이 없는 category_code, 카탈로그 genres에 없는 genre_code, 카탈로그 attributes에
  없는 고객 속성 key·값(저장된 고객 전체 기준)
- 품질 관문: 설정이 없거나, 끝까지 받은 적이 없거나, 마지막 수집이 `max_staleness_minutes`보다
  오래됐거나, 기간 안 결제 완료 주문이 `min_period_orders`보다 적으면 숫자를 내지 않고
  "데이터 부족: 이유"를 돌려준다
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

from ax_contracts.schemas.data import CatalogSales, SalesPeriod, SalesQuality
from data_agent.sales_clean import counts_as_revenue
from data_agent.sales_store import SalesStore, StoredOrder

KST = timezone(timedelta(hours=9), "KST")
PERIOD_DAYS: dict[str, int] = {"today": 1, "7d": 7, "30d": 30}
PERIOD_LABEL: dict[str, str] = {"today": "오늘", "7d": "최근 7일", "30d": "최근 30일"}
INSUFFICIENT = "데이터 부족"


@dataclass(frozen=True)
class Window:
    start: datetime  # UTC
    end: datetime
    previous_start: datetime
    previous_end: datetime


def window(period: SalesPeriod, now: datetime) -> Window:
    days = PERIOD_DAYS[period]
    local = now.astimezone(KST)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    shift = timedelta(days=days)
    end = now.astimezone(UTC)
    return Window(
        start=start.astimezone(UTC),
        end=end,
        previous_start=(start - shift).astimezone(UTC),
        previous_end=end - shift,
    )


def kst_day(moment: datetime) -> date:
    return moment.astimezone(KST).date()


def days_in(start: datetime, end: datetime) -> list[date]:
    """[start, end)가 걸친 한국 날짜 (끝이 그날 0시 정각이면 그날은 넣지 않는다)."""
    first = kst_day(start)
    last = kst_day(end - timedelta(microseconds=1)) if end > start else first
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def quality(
    store: SalesStore,
    tenant_id: str,
    w: Window,
    orders: Sequence[StoredOrder],
    cfg: CatalogSales | None,
) -> SalesQuality:
    """기간 [w.start, w.end)의 품질 보고. `orders`는 그 기간에 접수한 주문(상태 무관)."""
    with_orders = {kst_day(o.created_at) for o in orders}
    known = cfg.categories if cfg else {}
    unknown = sorted(
        {
            line.category_code
            for o in orders
            for line in o.lines
            if line.category_code and line.category_code not in known
        }
    )
    genres = cfg.genres if cfg else {}
    unknown_genres = sorted(
        {
            line.genre_code
            for o in orders
            for line in o.lines
            if line.genre_code and line.genre_code not in genres
        }
    )
    dropped = store.dropped(tenant_id, w.start, w.end)
    return SalesQuality(
        rows_in_period=len(orders),
        empty_days=[d.isoformat() for d in days_in(w.start, w.end) if d not in with_orders],
        dropped_rows=sum(dropped.values()),
        dropped_reasons=dropped,
        last_synced_at=store.sync_state(tenant_id).last_synced_at,
        unknown_category_codes=unknown,
        unknown_genres=unknown_genres,
        unmapped_attribute_values=unmapped_attributes(store, tenant_id, cfg),
    )


def quality_counted(
    store: SalesStore,
    tenant_id: str,
    w: Window,
    rows_in_period: int,
    cfg: CatalogSales | None,
) -> SalesQuality:
    """`quality`와 같은 보고를 주문을 파이썬으로 읽지 않고 저장소 집계로 만든다 (고객·시장 분석).
    `rows_in_period`는 기간 안 주문 수(상태 무관, `SalesStore.order_counts`)."""
    have = store.order_days(tenant_id, w.start, w.end)
    categories, genres = store.unknown_line_codes(
        tenant_id, w.start, w.end, cfg.categories if cfg else {}, cfg.genres if cfg else {}
    )
    dropped = store.dropped(tenant_id, w.start, w.end)
    return SalesQuality(
        rows_in_period=rows_in_period,
        empty_days=[d.isoformat() for d in days_in(w.start, w.end) if d.isoformat() not in have],
        dropped_rows=sum(dropped.values()),
        dropped_reasons=dropped,
        last_synced_at=store.sync_state(tenant_id).last_synced_at,
        unknown_category_codes=sorted(categories),
        unknown_genres=sorted(genres),
        unmapped_attribute_values=unmapped_attributes(store, tenant_id, cfg),
    )


def quality_brief(
    store: SalesStore, tenant_id: str, w: Window, rows_in_period: int
) -> SalesQuality:
    """관문에 먼저 걸려 숫자를 내지 않을 때의 짧은 보고: 기간 안 주문 수(COUNT 하나)·버린 행·
    마지막 수집만 (빈 날·이름 없는 코드는 세지 않는다)."""
    dropped = store.dropped(tenant_id, w.start, w.end)
    return SalesQuality(
        rows_in_period=rows_in_period,
        dropped_rows=sum(dropped.values()),
        dropped_reasons=dropped,
        last_synced_at=store.sync_state(tenant_id).last_synced_at,
    )


def unmapped_attributes(
    store: SalesStore, tenant_id: str, cfg: CatalogSales | None
) -> dict[str, list[str]]:
    """저장된 고객 속성 중 카탈로그 `attributes`에 없는 것: key → 값 code 목록.
    카탈로그에 없는 key는 그 key의 값을 모두 넣는다."""
    known = {a.key: a.values for a in cfg.attributes} if cfg else {}
    out: dict[str, list[str]] = {}
    for key, codes in sorted(store.attribute_values(tenant_id).items()):
        missing = sorted(c for c in codes if c not in known.get(key, {}))
        if missing:
            out[key] = missing
    return out


def paid_orders(orders: Sequence[StoredOrder]) -> list[StoredOrder]:
    return [o for o in orders if counts_as_revenue(o.status, o.payment_status)]


def gate(
    report: SalesQuality, paid_count: int, cfg: CatalogSales | None, now: datetime
) -> list[str]:
    """품질 관문. 걸리면 이유 문장 목록(비었으면 통과)."""
    if cfg is None:
        return [f"{INSUFFICIENT}: 회사 카탈로그에 매출 분석 설정([sales])이 없음"]
    reasons: list[str] = []
    synced = report.last_synced_at
    if synced is None:
        reasons.append(f"{INSUFFICIENT}: 판매 기록을 아직 끝까지 받은 적이 없음")
    else:
        age = now - synced
        if age > timedelta(minutes=cfg.max_staleness_minutes):
            minutes = int(age.total_seconds() // 60)
            reasons.append(
                f"{INSUFFICIENT}: 마지막 수집이 {minutes}분 전 "
                f"(기준 {cfg.max_staleness_minutes}분 안)"
            )
    if paid_count < cfg.min_period_orders:
        reasons.append(
            f"{INSUFFICIENT}: 기간 안 결제 완료 주문 {paid_count}건 "
            f"(기준 {cfg.min_period_orders}건 이상)"
        )
    return reasons
