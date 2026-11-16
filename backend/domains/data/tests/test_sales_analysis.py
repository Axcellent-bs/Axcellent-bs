"""매출 분석 skill (`sales_analysis`): 금액·기간 경계·환불·재구매·상품 표본·유입·관문·핸들러."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import CatalogSales, SalesAnalysisOutput
from data_agent import handlers
from data_agent.sales_quality import window
from data_agent.sales_store import SalesStore
from data_agent.skills.sales_analysis import allocate, analyze, channel_label, line_nets

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00
TODAY = window("today", NOW)  # 한국 11/16 0시 = UTC 11/15 15:00
CFG = CatalogSales(
    categories={"book": "도서", "stationery": "문구"},
    source_labels={"naver": "네이버"},
    medium_labels={"cpc": "검색 광고"},
    min_product_orders=2,
    min_period_orders=1,
)


def line(pid="prd_1", code="book", qty=1, price=10000, coupon=0, points=0, title="책"):
    return SalesOrderLine(product_id=pid, category_code=code, title=title, qty=qty,
                          unit_price=price, coupon_share=coupon, points_share=points)  # fmt: skip


def order(  # noqa: PLR0913 - 시험 주문을 짧게 만드는 도우미
    oid, at, *, buyer="buy_1", lines=None, coupon=0, points=0, status="paid",
    pay="succeeded", refunds=(), source=None, medium=None, shipping=0,
) -> SalesOrderRow:  # fmt: skip
    lines = lines if lines is not None else [line()]
    return SalesOrderRow(
        order_id=oid, buyer_id=buyer, status=status, created_at=at, updated_at=at,
        items_amount=sum(x.qty * x.unit_price for x in lines), shipping_fee=shipping,
        points_used=points, coupon_discount=coupon, src_source=source, src_medium=medium,
        payment_status=pay, lines=lines, refunds=list(refunds),
    )  # fmt: skip


def store_with(*rows: SalesOrderRow, synced: datetime = NOW - timedelta(minutes=1)) -> SalesStore:
    store = SalesStore(":memory:")
    store.upsert(TENANT, rows, NOW)
    store.finish_sync(TENANT, "shop-api", synced, None)
    return store


def run(store: SalesStore, period="today", cfg: CatalogSales | None = CFG) -> SalesAnalysisOutput:
    return analyze(store, TENANT, period, cfg, "catbooks", NOW)


def kpi(out: SalesAnalysisOutput, key: str):
    return next(k for k in out.kpis if k.key == key)


# ---------------------------------------------------------------- 금액


def test_revenue_with_coupon_points_refund_and_shipping_excluded() -> None:
    at = TODAY.start + timedelta(hours=1)
    rows = [
        # 상품 30,000 − 쿠폰 2,000 − 포인트 1,000, 배송비 3,000은 빼지 않음 → 27,000
        order("o1", at, lines=[line(qty=3)], coupon=2000, points=1000, shipping=3000,
              refunds=[SalesOrderRefund(amount=4000, refunded_at=at + timedelta(hours=1))]),
        order("o2", at, lines=[line(pid="prd_2", code="stationery", price=5000)]),
        order("o3", at, status="cancelled"),  # 취소: 매출 아님
        order("o4", at, status="payment_failed", pay="failed"),  # 결제 실패: 매출 아님
        order("o5", at, status="pending_payment", pay=None),  # 결제 대기
    ]  # fmt: skip
    out = run(store_with(*rows))
    assert out.status == "ok"
    # 27,000 − 기간 안 환불 4,000 + 5,000 = 28,000
    assert kpi(out, "revenue").value == 28000
    assert kpi(out, "order_count").value == 2
    assert kpi(out, "aov").value == 14000
    by_code = {c.code: c for c in out.categories}
    assert by_code["book"].revenue == 23000 and by_code["stationery"].revenue == 5000
    assert sum(c.revenue for c in out.categories) == kpi(out, "revenue").value
    assert by_code["book"].label == "도서" and by_code["book"].share == pytest.approx(0.8214, 1e-3)


def test_kst_midnight_boundary() -> None:
    rows = [
        # 어제 23:59:59 (한국 시간)
        order("before", TODAY.start - timedelta(seconds=1), lines=[line(price=1000)]),
        order("at", TODAY.start, lines=[line(price=2000)]),  # 오늘 0:00:00
    ]
    store = store_with(*rows)
    out = run(store)
    assert kpi(out, "revenue").value == 2000  # 어제 23:59:59 주문은 오늘에 들지 않는다
    # 오늘의 직전 기간은 어제 0시 ~ 어제 12시라 어제 23:59:59 주문도 들지 않는다
    assert kpi(out, "revenue").previous == 0
    week = run(store, period="7d")
    assert kpi(week, "revenue").value == 3000  # 7일 안에는 둘 다 든다
    assert week.quality.empty_days == [f"2026-11-{d}" for d in range(10, 15)]


def test_refund_in_next_period_is_subtracted_there_not_in_order_period() -> None:
    yesterday = TODAY.previous_start + timedelta(hours=2)
    refund = SalesOrderRefund(amount=3000, refunded_at=TODAY.start + timedelta(hours=1))
    rows = [order("o1", yesterday, refunds=[refund]), order("o2", TODAY.start + timedelta(hours=2))]
    out = run(store_with(*rows))
    assert kpi(out, "revenue").previous == 10000  # 어제 주문은 어제 매출 그대로
    assert kpi(out, "revenue").value == 10000 - 3000  # 오늘 환불은 오늘 매출에서
    assert kpi(out, "order_count").value == 1  # 환불은 주문 수를 늘리지 않는다


def test_refund_of_cancelled_order_is_not_subtracted_twice() -> None:
    refund = SalesOrderRefund(amount=10000, refunded_at=TODAY.start + timedelta(hours=2))
    rows = [
        order("o1", TODAY.start + timedelta(hours=1), status="cancelled", refunds=[refund]),
        order("o2", TODAY.start + timedelta(hours=1)),
    ]
    assert kpi(run(store_with(*rows)), "revenue").value == 10000


def test_change_rates_and_points() -> None:
    prev_t, cur_t = TODAY.previous_start + timedelta(hours=1), TODAY.start + timedelta(hours=1)
    rows = [
        order("p1", prev_t, buyer="a", lines=[line(price=10000)]),
        order("c1", cur_t, buyer="b", lines=[line(price=12000)]),
    ]
    out = run(store_with(*rows))
    assert kpi(out, "revenue").change == pytest.approx(0.2)
    assert kpi(out, "order_count").change == 0.0
    assert kpi(out, "repeat_rate").change_kind == "points"


def test_discount_shares_fall_back_to_proportional_when_they_do_not_add_up() -> None:
    rows = [
        order("o1", TODAY.start + timedelta(hours=1), coupon=3001,
              lines=[line(price=20000), line(pid="p2", code="stationery", price=10000)]),
    ]  # fmt: skip
    store = store_with(*rows)
    stored = store.orders_created(TENANT, TODAY.start, TODAY.end)[0]
    nets = line_nets(stored)
    assert sum(n.net for n in nets) == 30000 - 3001
    assert [n.net for n in nets] == [20000 - 2001, 10000 - 1000]


def test_allocate_keeps_total() -> None:
    assert allocate(10, [1, 1, 1]) == [4, 3, 3]
    assert allocate(-10, [1, 1, 1]) == [-4, -3, -3]
    assert allocate(5, [0, 0]) == [5, 0]
    assert allocate(7, []) == []


# ---------------------------------------------------------------- 고객·상품·유입


def test_repeat_customer_has_paid_order_before_period() -> None:
    old = TODAY.start - timedelta(days=10)
    rows = [
        order("old_a", old, buyer="a"),
        order("old_c", old, buyer="c", status="cancelled"),  # 취소된 주문만 있으면 신규
        order("t_a", TODAY.start + timedelta(hours=1), buyer="a", lines=[line(price=6000)]),
        order("t_b", TODAY.start + timedelta(hours=1), buyer="b", lines=[line(price=3000)],
              coupon=1000),
        order("t_c", TODAY.start + timedelta(hours=2), buyer="c", lines=[line(price=2000)]),
    ]  # fmt: skip
    out = run(store_with(*rows))
    assert out.customers is not None
    assert out.customers.buyers == 3 and out.customers.repeat_buyers == 1
    assert out.customers.repeat_revenue == 6000 and out.customers.new_revenue == 2000 + 2000
    assert out.customers.repeat_share == pytest.approx(0.6)
    assert kpi(out, "repeat_rate").value == pytest.approx(0.3333, 1e-3)
    assert out.customers.coupon_orders == 1
    assert out.customers.coupon_order_rate == pytest.approx(0.3333, 1e-3)


def test_product_moves_need_min_sample_in_both_periods() -> None:
    prev_t, cur_t = TODAY.previous_start + timedelta(hours=1), TODAY.start + timedelta(hours=1)
    rows = []
    for i in range(2):  # 상품 A: 두 기간 2건씩 → 비교 (10,000 → 15,000)
        rows.append(order(f"pa{i}", prev_t, lines=[line(pid="A", title="상품A", price=5000)]))
        rows.append(order(f"ca{i}", cur_t, lines=[line(pid="A", title="상품A", price=7500)]))
    for i in range(2):  # 상품 B: 줄어듦
        rows.append(order(f"pb{i}", prev_t, lines=[line(pid="B", title="상품B", price=8000)]))
        rows.append(order(f"cb{i}", cur_t, lines=[line(pid="B", title="상품B", price=4000)]))
    rows.append(order("cc", cur_t, lines=[line(pid="C", title="상품C")]))  # 표본 부족
    out = run(store_with(*rows))
    assert [(m.label, m.change) for m in out.products_up] == [("상품A", 0.5)]
    assert [(m.label, m.change) for m in out.products_down] == [("상품B", -0.5)]
    assert out.products_low_sample == 1 and out.min_product_orders == 2
    assert out.products_up[0].orders == 2 and out.products_up[0].category_label == "도서"


def test_channels_labels_without_codes() -> None:
    at = TODAY.start + timedelta(hours=1)
    rows = [
        order("o1", at, source="naver", medium="cpc", lines=[line(price=5000)]),
        order("o2", at, lines=[line(price=3000)]),
        order("o3", at, source="tiktok", medium="social", lines=[line(price=1000)]),
        order("o4", at, source="zzz", lines=[line(price=1000)]),
    ]
    out = run(store_with(*rows))
    assert [(c.label, c.revenue, c.orders) for c in out.channels] == [
        ("네이버 · 검색 광고", 5000, 1), ("직접 유입", 3000, 1), ("기타 경로", 2000, 2)
    ]  # fmt: skip
    other = next(c for c in out.channels if c.label == "기타 경로")
    assert other.source is None and other.medium is None
    assert channel_label("naver", "unknown", CFG) == "네이버"


def test_unknown_category_label_and_quality_list() -> None:
    at = TODAY.start + timedelta(hours=1)
    out = run(store_with(order("o1", at, lines=[line(code="toy")])))
    assert out.categories[0].label == "이름 없는 분류"
    assert out.quality.unknown_category_codes == ["toy"]


# ---------------------------------------------------------------- 빈 데이터·관문


def test_empty_store_is_insufficient_without_numbers() -> None:
    out = run(SalesStore(":memory:"))
    assert out.status == "insufficient"
    assert out.insufficient_reasons == [
        "데이터 부족: 판매 기록을 아직 끝까지 받은 적이 없음",
        "데이터 부족: 기간 안 결제 완료 주문 0건 (기준 1건 이상)",
    ]
    assert out.kpis == [] and out.categories == [] and out.channels == []
    assert out.customers is None and out.products_up == []
    assert out.summary.startswith("오늘 매출 숫자를 내지 않음")


def test_gate_stale_collection_hides_numbers() -> None:
    at = TODAY.start + timedelta(hours=1)
    out = run(store_with(order("o1", at), synced=NOW - timedelta(minutes=31)))
    assert out.status == "insufficient" and out.kpis == []
    assert out.insufficient_reasons == ["데이터 부족: 마지막 수집이 31분 전 (기준 30분 안)"]
    assert out.quality.rows_in_period == 1  # 품질 보고는 그대로 준다


def test_gate_too_few_orders_hides_numbers() -> None:
    cfg = CFG.model_copy(update={"min_period_orders": 20})
    at = TODAY.start + timedelta(hours=1)
    out = run(store_with(*[order(f"o{i}", at) for i in range(19)]), cfg=cfg)
    assert out.insufficient_reasons == ["데이터 부족: 기간 안 결제 완료 주문 19건 (기준 20건 이상)"]
    assert out.kpis == []
    ok = run(store_with(*[order(f"o{i}", at) for i in range(20)]), cfg=cfg)
    assert ok.status == "ok" and kpi(ok, "order_count").value == 20


def test_no_sales_config_is_insufficient() -> None:
    out = run(store_with(order("o1", TODAY.start + timedelta(hours=1))), cfg=None)
    assert out.status == "insufficient" and out.definition == ""


def test_summary_and_basis_use_output_values_only() -> None:
    at = TODAY.start + timedelta(hours=1)
    out = run(store_with(order("o1", at, lines=[line(price=12345)])))
    assert out.summary == (
        "오늘 매출 12,345원 (직전 대비 비교 없음), 결제 완료 주문 1건, 객단가 12,345원."
        " 가장 많이 팔린 분류는 도서 (매출 비중 100.0%)."
    )
    assert out.basis.period_from == TODAY.start and out.basis.period_to == NOW
    assert out.basis.catalog_id == "catbooks" and out.basis.catalog_version == "1"
    assert out.basis.rows_read == 1
    assert out.definition.startswith("매출 = 결제 완료 주문")


def test_7d_period_and_empty_days() -> None:
    w = window("7d", NOW)
    out = run(store_with(order("o1", w.start + timedelta(hours=1))), period="7d")
    assert out.period == "7d" and len(out.quality.empty_days) == 6


# ---------------------------------------------------------------- 핸들러


def _request(**inp) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill="sales_analysis", input=inp)


def test_handler_reads_store_and_returns_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    store = SalesStore(":memory:")
    store.finish_sync(TENANT, "shop-api", now, None)
    handlers.use_sales_store(store)
    try:
        result = asyncio.run(handlers.sales_analysis(_request(period="7d")))
    finally:
        handlers.use_sales_store(None)
    assert result.status == TaskStatus.COMPLETED
    out = SalesAnalysisOutput.model_validate(result.output)
    assert out.status == "insufficient"  # 주문 0건 → 숫자 없음
    assert out.basis.catalog_id == "catbooks"
    assert "읽은 행 0" in result.evidence.data_summary
    assert "sales_store.sales_orders" in result.evidence.queries


def test_handler_rejects_bad_period_and_other_tenant() -> None:
    bad = asyncio.run(handlers.sales_analysis(_request(period="90d")))
    assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"
    other = asyncio.run(handlers.sales_analysis(_request(period="7d", tenant_id="other")))
    assert other.status == TaskStatus.FAILED and other.outcome == "invalid_input"


def test_skill_is_registered() -> None:
    assert "sales_analysis" in handlers.HANDLERS
