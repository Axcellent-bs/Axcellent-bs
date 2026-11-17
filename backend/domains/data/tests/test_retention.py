"""재구매·이탈 (`retention`, 11/17).

기준 시각 2026-11-16 12:00(한국)이면 코호트는 8·9·10월(지금 달 11월은 뺀다).
- 8월: 20대 여성 10명이 8/10 첫 구매. 6명은 8/20(30일 안), 2명은 9/25(60일 안) 다시 산다
  → 30일 6/10, 60일 8/10, 90일은 아직(9/1 + 90일 = 11/30)
- 9월: 10대 남성 7명이 9/5 첫 구매, 3명이 9/15 다시 산다 → 30일 칸은 다 지났지만 3명(기준 5
  미만)이라 숨김, 60일은 아직(10/1 + 60일 = 11/30)
- 10월: 4명 → 표본 부족
떠날 위험(60일): 8/20에 마지막으로 산 단골 6명(20대 여성), 9/15의 3명(10대 남성, 단골 3명이라 숨김)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.data import AttributeDef, CatalogSales, RetentionOutput
from data_agent import catalog, handlers
from data_agent.sales_quality import KST
from data_agent.sales_store import SalesStore, customers_source
from data_agent.skills import retention as rt
from data_agent.skills import segment_analysis as seg

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)  # 한국 11/16 12:00
CFG = CatalogSales(
    min_period_orders=1,
    categories={"book": "도서"},
    genres={"g1": "장르1", "g2": "장르2"},
    attributes=[
        AttributeDef(key="age_band", label="연령대", values={"10s": "10대", "20s": "20대"}),
        AttributeDef(key="gender", label="성별", values={"female": "여성", "male": "남성"}),
    ],
)


def kst(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 12, 0, tzinfo=KST).astimezone(UTC)


def order(n: str, buyer: str, when: datetime, genre: str = "g1",
          status: str = "paid") -> SalesOrderRow:  # fmt: skip
    return SalesOrderRow(
        order_id=n, buyer_id=buyer, status=status, created_at=when, updated_at=when,
        items_amount=10000, payment_status="succeeded",
        lines=[SalesOrderLine(product_id=f"p_{genre}", category_code="book", title=f"책 {genre}",
                              qty=1, unit_price=10000, genre_code=genre)],
    )  # fmt: skip


def make_store(customers: bool = True) -> SalesStore:
    store = SalesStore(":memory:")
    rows: list[SalesOrderRow] = []
    people: list[tuple[str, dict[str, str]]] = []
    for i in range(10):  # 8월 20대 여성
        b = f"aug{i}"
        people.append((b, {"age_band": "20s", "gender": "female"}))
        rows.append(order(f"{b}-1", b, kst(8, 10)))
        if i < 6:
            rows.append(order(f"{b}-2", b, kst(8, 20), "g2"))
        elif i < 8:
            rows.append(order(f"{b}-2", b, kst(9, 25)))
    for i in range(7):  # 9월 10대 남성
        b = f"sep{i}"
        people.append((b, {"age_band": "10s", "gender": "male"}))
        rows.append(order(f"{b}-1", b, kst(9, 5)))
        if i < 3:
            rows.append(order(f"{b}-2", b, kst(9, 15)))
    for i in range(4):  # 10월 4명
        b = f"oct{i}"
        people.append((b, {"age_band": "20s", "gender": "male"}))
        rows.append(order(f"{b}-1", b, kst(10, 3)))
    rows.append(order("cancel-1", "aug9", kst(8, 12), status="cancelled"))  # 취소는 재구매가 아님
    rows.append(order("recent-1", "late", NOW - timedelta(days=1)))  # 11월 첫 구매 (코호트 밖)
    store.upsert(TENANT, rows, NOW)
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    if customers:
        day = NOW - timedelta(days=200)
        store.upsert_customers(TENANT, [CustomerRow(buyer_id=b, joined_at=day, updated_at=day,
                                                    attributes=a) for b, a in people], NOW)
        store.finish_sync(TENANT, customers_source("shop-api"), NOW, None)
    return store


def run_rt(store: SalesStore, **kw: Any) -> RetentionOutput:
    args: dict[str, Any] = {"cohorts": 3, "filters": {}, "cfg": CFG}
    args.update(kw)
    return rt.retention(store, TENANT, args["cohorts"], args["filters"], args["cfg"], "test", NOW)


def test_cohort_months_skip_current_month_and_cross_year() -> None:
    assert [m.label for m in rt.cohort_months(3, NOW)] == ["2026-08", "2026-09", "2026-10"]
    jan = datetime(2027, 1, 5, tzinfo=UTC)
    months = rt.cohort_months(2, jan)
    assert [m.label for m in months] == ["2026-11", "2026-12"]
    assert months[1].end == datetime(2027, 1, 1, tzinfo=KST).astimezone(UTC)  # 한국 1일 0시


def test_cohorts_repeat_rates_not_yet_and_hidden() -> None:
    out = run_rt(make_store())
    assert out.status == "ok", out.insufficient_reasons
    aug, sep, oct_ = out.cohorts
    assert (aug.cohort_month, aug.customers, aug.r30, aug.r60, aug.r90) == (
        "2026-08", 10, 0.6, 0.8, None)  # 90일은 아직 (9/1 + 90일 = 11/30)
    assert sep.customers == 7 and sep.r30 is None and sep.r60 is None  # 3명 숨김 · 60일 아직
    assert not sep.suppressed
    assert oct_.suppressed and oct_.customers is None  # 4명
    assert out.churn_days == 60


def test_at_risk_groups_hidden_and_top_genre() -> None:
    out = run_rt(make_store())
    assert len(out.at_risk) == 1  # 10대 남성(단골 3명)은 숨김
    row = out.at_risk[0]
    assert row.filters == {"age_band": "20s", "gender": "female"} and row.label == "20대 · 여성"
    assert (row.regulars, row.at_risk, row.share) == (8, 6, 0.75)  # 8/20에 마지막으로 산 6명
    assert row.top_item_label == "장르1"  # 단골 8명 모두 장르1, 장르2는 6명
    assert "떠날 것 같은 단골이 가장 많은 묶음은 20대 · 여성 (6명, 단골의 75.0%)" in out.summary
    assert "(가정)" in out.summary


def test_complementary_hiding_keeps_totals_from_solving() -> None:
    """묶음 합계가 보이면 하나만 숨긴 칸은 풀리므로 하나를 더 숨긴다."""
    reg, risk = [10, 3, 9, 12], [6, 3, 5, 7]
    hidden = rt.hide(reg, risk, 5)
    assert 1 in hidden and len(hidden) >= 2  # 3명 칸 + 보완 숨김 한 칸
    everything = [list(range(4))]
    assert not seg.determined_cells(hidden, everything)


def test_filters_and_bad_filters() -> None:
    women = run_rt(make_store(), filters={"gender": "female"})
    assert [c.customers for c in women.cohorts] == [10, None, None]
    assert women.cohorts[1].suppressed  # 9월에 여성 첫 구매 고객 0명
    bad = run_rt(make_store(), filters={"job": "x"})
    assert bad.status == "insufficient"
    assert any("설정에 없는 속성: job" in r for r in bad.insufficient_reasons)


class NoAggregates(SalesStore):
    def segment_session(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("고객 기록 관문에 걸리면 집계하지 않는다")

    def orders_created(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError("주문을 파이썬으로 읽지 않는다")


def test_no_customers_stops_before_reading_orders() -> None:
    store = NoAggregates(":memory:")
    store.upsert(TENANT, [order("o1", "b1", kst(9, 1))], NOW)
    store.finish_sync(TENANT, "shop-api", NOW - timedelta(minutes=1), None)
    out = run_rt(store)
    assert out.status == "insufficient" and seg.NO_CUSTOMERS in out.insufficient_reasons
    assert out.quality.rows_in_period == 1  # 365일 안 주문 수만 셌다 (COUNT 하나)
    assert out.summary.startswith("재구매·이탈 숫자를 내지 않음")


def _request(**inp: Any) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_req_dashboard", token="mock:dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill="retention", input=inp)


def test_handler_registered_and_checks_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    assert handlers.HANDLERS["retention"] is handlers.retention_skill
    catalog.use_catalog(catalog.load_catalog("catbooks"))
    handlers.use_sales_store(make_store())
    try:
        ok = asyncio.run(handlers.retention_skill(_request(cohorts=2)))
        other = asyncio.run(handlers.retention_skill(_request(tenant_id="other")))
        too_many = asyncio.run(handlers.retention_skill(_request(cohorts=7)))
    finally:
        handlers.use_sales_store(None)
        catalog.use_catalog(None)
    assert ok.status == TaskStatus.COMPLETED and ok.evidence
    out = RetentionOutput.model_validate(ok.output)
    assert out.churn_days == 60  # catbooks 기본값
    assert "떠남 기준 60일(가정)" in ok.evidence.data_summary
    assert other.status == TaskStatus.FAILED and other.outcome == "invalid_input"
    assert too_many.status == TaskStatus.FAILED
