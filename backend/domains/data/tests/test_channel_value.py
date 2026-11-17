"""첫 구매 유입 경로별 고객 가치 (`channel_value`, 11/17).

기준 시각 2026-11-16 12:00(한국), 기간 365d, 가치 기간 90일.
- 유튜브: 6/1 첫 구매 6명(1만 원 − 쿠폰 1천 원) + 6/20 2만 원. 한 명은 7/1에 5천 원 환불, 한 명은
  10/1에 또 사지만 90일 밖(6/1 + 90일 = 8/30). 한 명은 5/1에 네이버로 주문했다 취소(결제 완료 아님)
  → 1인 (6 × 29,000 − 5,000) ÷ 6 = 28,167원, 1인 주문 2.0건, 재구매 100%
- 네이버: 7/1 첫 구매 5명, 1만 5천 원 한 번 → 1인 15,000원, 재구매 0%
- 직접 유입 3명·모르는 출처(tiktok) 2명 → 숨김(5명 미만), 숨긴 칸이 둘이라 합계로 풀리지 않음
- 11/1 유튜브 첫 구매 4명 → 90일이 안 지나 뺌. 작년 10월 첫 구매 1명 → 기간 밖
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRefund, SalesOrderRow
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import CatalogSales, ChannelValueOutput
from data_agent import catalog, handlers
from data_agent.sales_quality import KST
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import channel_value as cv
from data_agent.skills import segment_analysis as seg

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
CFG = CatalogSales(
    min_period_orders=1,
    categories={"book": "도서"},
    source_labels={"youtube": "유튜브", "naver": "네이버"},
)


def kst(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=KST).astimezone(UTC)


def order(n: str, buyer: str, when: datetime, amount: int, source: str | None = None,
          **kw: Any) -> SalesOrderRow:  # fmt: skip
    base = {
        "order_id": n, "buyer_id": buyer, "status": "paid", "created_at": when, "updated_at": when,
        "items_amount": amount, "payment_status": "succeeded", "src_source": source,
        "lines": [SalesOrderLine(product_id="p1", category_code="book", title="책", qty=1,
                                 unit_price=amount)],
    }  # fmt: skip
    return SalesOrderRow.model_validate({**base, **kw})


def make_store(customers: bool = True) -> SalesStore:
    rows: list[SalesOrderRow] = []
    buyers: list[str] = []
    for i in range(6):
        b = f"yt{i}"
        buyers.append(b)
        refunds = ([SalesOrderRefund(amount=5000, refunded_at=kst(2026, 7, 1))] if i == 0 else [])
        line = SalesOrderLine(product_id="p1", category_code="book", title="책", qty=1,
                              unit_price=10000, coupon_share=1000)  # fmt: skip
        rows.append(order(f"{b}-1", b, kst(2026, 6, 1), 10000, "youtube", coupon_discount=1000,
                          lines=[line]))  # fmt: skip
        rows.append(order(f"{b}-2", b, kst(2026, 6, 20), 20000, refunds=refunds))
    rows.append(order("yt1-late", "yt1", kst(2026, 10, 1), 50000))  # 90일 밖
    rows.append(order("yt2-cancel", "yt2", kst(2026, 5, 1), 9000, "naver", status="cancelled",
                      payment_status=None))  # fmt: skip
    for i in range(5):
        b = f"nv{i}"
        buyers.append(b)
        rows.append(order(f"{b}-1", b, kst(2026, 7, 1), 15000, "naver"))
    for i in range(3):
        b = f"dir{i}"
        buyers.append(b)
        rows.append(order(f"{b}-1", b, kst(2026, 7, 5), 8000))
    for i in range(2):
        b = f"tt{i}"
        buyers.append(b)
        rows.append(order(f"{b}-1", b, kst(2026, 7, 6), 8000, "tiktok"))
    for i in range(4):
        b = f"new{i}"
        buyers.append(b)
        rows.append(order(f"{b}-1", b, kst(2026, 11, 1), 8000, "youtube"))
    buyers.append("old")
    rows.append(order("old-1", "old", kst(2025, 10, 1), 8000, "naver"))
    rows.append(order("old-2", "old", kst(2026, 6, 2), 8000, "youtube"))
    store = SalesStore(":memory:")
    store.upsert(TENANT, rows, NOW)
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    if customers:
        day = NOW - timedelta(days=400)
        store.upsert_customers(TENANT, [CustomerRow(buyer_id=b, joined_at=day, updated_at=day)
                                        for b in buyers], NOW)  # fmt: skip
        store.finish_sync(TENANT, customers_source("shop-api"), NOW, None)
    return store


def run_cv(store: SalesStore, cfg: CatalogSales = CFG) -> tuple[ChannelValueOutput, int]:
    return cv.channel_value(store, TENANT, "365d", cfg, "test", NOW)


def test_value_per_first_source() -> None:
    out, excluded = run_cv(make_store())
    assert out.status == "ok", out.insufficient_reasons
    assert out.horizon_days == 90 and out.cost_note == "재무 연결 대기"
    assert excluded == 4  # 11/1 첫 구매 4명은 90일이 안 지남
    yt, nv, *hidden = out.rows
    assert (yt.label, yt.source, yt.new_customers) == ("유튜브", "youtube", 6)
    assert yt.revenue_per_customer == round((6 * 29000 - 5000) / 6)  # 28,167원
    assert (yt.orders_per_customer, yt.repeat_rate, yt.cost_krw) == (2.0, 1.0, None)
    assert (nv.label, nv.new_customers, nv.revenue_per_customer) == ("네이버", 5, 15000)
    assert (nv.orders_per_customer, nv.repeat_rate) == (1.0, 0.0)
    assert [(r.label, r.suppressed, r.new_customers) for r in hidden] == [
        ("기타 경로", True, None), ("직접 유입", True, None)]
    assert all(r.source is None for r in hidden)  # 원본 코드를 화면에 내지 않는다
    assert "유튜브로 들어온 고객이 90일 동안 1인 28,167원으로 가장 높음" in out.summary
    assert "재무 연결 대기" in out.summary


def test_one_hidden_channel_hides_another() -> None:
    """숨긴 경로가 하나뿐이면 합계로 풀리므로 다음으로 작은 경로도 숨긴다."""
    store = make_store()
    direct = [order(f"tt{i}-1", f"tt{i}", kst(2026, 7, 6), 8000) for i in range(2)]
    store.upsert(TENANT, direct, NOW)
    out, _ = run_cv(store)  # 모르는 출처가 없어져 숨긴 경로는 직접 유입(5명이 됨)…
    labels = {r.label: r for r in out.rows}
    assert "기타 경로" not in labels and labels["직접 유입"].new_customers == 5
    three = CFG.model_copy(update={"min_group_size": 6})  # 유튜브 6명만 보이고 나머지는 숨김
    out6, _ = run_cv(make_store(), three)
    shown = [r.label for r in out6.rows if not r.suppressed]
    assert shown == ["유튜브"]
    counts = [6, 5, 3, 2]
    hidden = seg.protect(counts, {1, 2, 3}, [list(range(4))])
    assert not seg.determined_cells(hidden, [list(range(4))])


class NoAggregates(SalesStore):
    def segment_session(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("고객 기록 관문에 걸리면 집계하지 않는다")


def test_no_customers_stops_before_reading_orders() -> None:
    store = NoAggregates(":memory:")
    store.upsert(TENANT, [order("o1", "b1", kst(2026, 7, 1), 8000, "naver")], NOW)
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    out, excluded = run_cv(store)
    assert out.status == "insufficient" and seg.NO_CUSTOMERS in out.insufficient_reasons
    assert out.rows == [] and excluded == 0 and out.quality.rows_in_period == 1


def _request(**inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill="channel_value", input=inp)


def test_handler_evidence_keeps_excluded_count(monkeypatch: pytest.MonkeyPatch) -> None:
    assert handlers.HANDLERS["channel_value"] is handlers.channel_value_skill
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    store = make_store()
    store.finish_sync(TENANT, "shop-api", datetime.now(UTC), None)  # 핸들러는 실제 지금 시각
    handlers.use_sales_store(store)
    try:
        ok = asyncio.run(handlers.channel_value_skill(_request()))
        other = asyncio.run(handlers.channel_value_skill(_request(tenant_id="other")))
    finally:
        handlers.use_sales_store(None)
        catalog.use_catalog(None)
    assert ok.status == TaskStatus.COMPLETED and ok.evidence
    out = ChannelValueOutput.model_validate(ok.output)
    assert out.horizon_days == 90 and any(r.label == "유튜브" for r in out.rows)  # catbooks 이름
    assert "아직 안 지나 뺀 새 고객" in ok.evidence.data_summary
    assert other.status == TaskStatus.FAILED and other.outcome == "invalid_input"
