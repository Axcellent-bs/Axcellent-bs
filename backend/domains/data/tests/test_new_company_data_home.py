"""새 회사(사이트 변환기, 공통 카탈로그) 데이터 홈 현황·고객 분석 선택지 (11/26 누비투어 실화면).

- 데이터 홈 주문 수: 공통 카탈로그엔 결제 비율 지표가 없고 변환기는 5분 지표를 주지 않는다 →
  그 회사 판매 기록에서 센다. 결제 성공률은 사이트가 주지 않는 값이라 이유를 적는다
  (0으로 두지 않는다).
- 고객 분석 분류 선택지: 집계가 비어도 그 회사 판매 기록의 분류를 같이 본다.
- 공통 카탈로그: 인구 비교(연령대 × 성별)·이벤트 종류·자주 묻는 질문.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from data_agent import catalog, handlers
from data_agent.monitor import days_to_snapshot
from data_agent.report_store import ReportStore, Snapshot
from data_agent.sales_store import SalesStore
from data_agent.skills import activity_report as ar
from data_agent.skills import segment_analysis as seg

NUBI = "c-cf2f0d"
CATBOOKS = "shop-demo"
NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)
DAY = datetime(2026, 11, 25, 3, 0, tzinfo=UTC)


def _order(n: int, buyer: str, category: str, at: datetime = DAY) -> SalesOrderRow:
    return SalesOrderRow(
        order_id=f"o{n}", buyer_id=buyer, status="paid", created_at=at, updated_at=at,
        items_amount=10000, payment_status="succeeded",
        lines=[SalesOrderLine(product_id=f"t_{category}", category_code=category,
                              title=f"투어 {category}", qty=1, unit_price=10000)],
    )  # fmt: skip


@pytest.fixture
def store():
    s = SalesStore(":memory:")
    s.upsert(NUBI, [_order(i, f"n{i}", "japan") for i in range(7)], NOW)
    s.finish_sync(NUBI, "site", NOW, None)
    s.upsert(CATBOOKS, [_order(100 + i, f"c{i}", "book") for i in range(40)], NOW)
    s.finish_sync(CATBOOKS, "shop-api", NOW, None)
    handlers.use_sales_store(s)
    yield s
    handlers.use_sales_store(None)


class _NoMetrics:
    """변환기처럼 5분 지표를 주지 않는 커넥터. 불리면 실패한다(판매 기록으로 세야 한다)."""

    async def metric_series(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("변환기 회사는 5분 지표를 부르지 않는다")

    async def transactions_volume(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("변환기 회사는 5분 지표를 부르지 않는다")


def _common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog, "current", lambda *a, **k: catalog.load_catalog("common"))


def test_new_company_orders_come_from_its_own_sales(store, monkeypatch) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(ar, "as_connector", lambda source: source)
    got = asyncio.run(ar.shop_counts(_NoMetrics(), (date(2026, 11, 24), date(2026, 11, 26)), NUBI))
    assert got.orders == 7.0  # 캣북스 40건 안 섞임
    assert (got.attempts, got.failed) == (None, None)
    assert got.shop_reason == ar.NO_PAYMENT_ATTEMPTS


def test_new_company_never_synced_is_unknown_not_zero(store, monkeypatch) -> None:
    _common(monkeypatch)
    monkeypatch.setattr(ar, "as_connector", lambda source: source)
    got = asyncio.run(
        ar.shop_counts(_NoMetrics(), (date(2026, 11, 24), date(2026, 11, 26)), "c-abc123")
    )
    assert got.orders is None
    assert got.shop_reason == ar.NO_SALES_YET


def test_catbooks_keeps_catalog_reason_path(store, monkeypatch) -> None:
    """캣북스는 판매 기록으로 바꾸지 않는다 (카탈로그 지표가 없으면 예전 이유 그대로)."""
    _common(monkeypatch)
    monkeypatch.setattr(ar, "as_connector", lambda source: source)
    days = (date(2026, 11, 24), date(2026, 11, 26))
    got = asyncio.run(ar.shop_counts(_NoMetrics(), days, CATBOOKS))
    assert got.orders is None
    assert got.shop_reason and got.shop_reason.startswith("카탈로그에 거래량·비율 지표가 없어")


def test_old_catalog_reason_rows_are_rebuilt_once() -> None:
    reports = ReportStore(":memory:")
    reason = "카탈로그에 거래량·비율 지표가 없어 기본 거래 수를 셀 수 없음"
    old = {"company": {"shop_reason": reason}}
    day = date(2026, 11, 24)
    reports.put(Snapshot(NUBI, day, NOW - timedelta(hours=30), old, True, retried=True))
    assert (day, True) in days_to_snapshot(reports, NUBI, NOW, backfill=3)
    new = {"company": {"shop_reason": ar.NO_PAYMENT_ATTEMPTS}}
    reports.put(Snapshot(NUBI, day, NOW, new, True, retried=True))
    assert day not in [d for d, _ in days_to_snapshot(reports, NUBI, NOW, backfill=3)]


def test_options_read_sales_categories_when_aggregate_is_empty(store, monkeypatch) -> None:
    """집계가 분류 이름이 채워지기 전에 만들어져 비어 있어도 판매 기록의 분류가 선택지에 나온다."""
    monkeypatch.setattr(seg.rb, "run_source", lambda tenant: (True, "site"))
    monkeypatch.setattr(seg.rb, "category_codes", lambda tenant: [])
    out = seg.read_options(store, NUBI, catalog.load_catalog("common").sales, "common")
    assert [c.code for c in out.categories] == ["japan"]  # 캣북스 book 없음


def test_common_catalog_has_population_event_kinds_and_questions() -> None:
    for name in ("common", "nubi", "studyon"):
        sales = catalog.load_catalog(name).sales
        assert sales is not None
        assert [list(p.attribute_keys) for p in sales.population] == [["age_band", "gender"]]
        assert [e.code for e in sales.event_kinds] == ["bundle_discount", "genre_coupon"]
        assert sales.quick_questions
        assert not sales.genres  # 장르 표는 없다 (사이트가 주지 않는다)
