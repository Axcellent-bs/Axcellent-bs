"""매출 분석 계약: 판매 기록 내보내기(shop_sales)와 sales_analysis 입출력, 카탈로그 [sales]."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ax_contracts.data_access.shop_sales import (
    SalesOrderRow,
    ShopSalesExportQuery,
    ShopSalesExportResult,
)
from ax_contracts.schemas.data import (
    CatalogSales,
    MetricCatalog,
    SalesAnalysisInput,
    SalesAnalysisOutput,
    SalesBasis,
)

ROW = {
    "order_id": "ord_0001",
    "buyer_id": "buy_0001",
    "status": "paid",
    "created_at": "2026-11-15T01:00:00Z",
    "updated_at": "2026-11-15T01:00:05Z",
    "items_amount": 36000,
    "shipping_fee": 3000,
    "points_used": 1000,
    "coupon_discount": 2000,
    "src_source": "naver",
    "src_medium": "cpc",
    "payment_status": "succeeded",
    "lines": [
        {"product_id": "prd_0001", "category_code": "book", "qty": 2, "unit_price": 18000,
         "coupon_share": 2000, "points_share": 1000},
    ],
    "refunds": [{"amount": 5000, "refunded_at": "2026-11-16T00:00:00Z"}],
}  # fmt: skip


def test_export_query_defaults_and_limit_bounds() -> None:
    query = ShopSalesExportQuery()
    assert query.cursor is None and query.limit == 500 and query.tenant_id == "shop-demo"
    assert ShopSalesExportQuery(limit=1).limit == 1
    assert ShopSalesExportQuery(limit=1000).limit == 1000
    for bad in (0, 1001):
        with pytest.raises(ValidationError):
            ShopSalesExportQuery(limit=bad)


def test_order_row_parses_utc_and_keeps_raw_amounts() -> None:
    row = SalesOrderRow.model_validate(ROW)
    assert row.created_at == datetime(2026, 11, 15, 1, 0, tzinfo=UTC)
    assert row.lines[0].category_code == "book"
    assert row.refunds[0].refunded_at.tzinfo is not None
    # 정제는 data가 한다: 음수 금액·0 수량도 형식으로는 받는다(버린 수를 세려고)
    raw = SalesOrderRow.model_validate(
        {**ROW, "items_amount": -1, "lines": [{**ROW["lines"][0], "qty": 0}]}
    )
    assert raw.items_amount == -1 and raw.lines[0].qty == 0


def test_order_row_new_fields_are_optional() -> None:
    minimal = SalesOrderRow.model_validate(
        {k: ROW[k] for k in ("order_id", "buyer_id", "status", "created_at", "updated_at")}
    )
    assert minimal.lines == [] and minimal.refunds == [] and minimal.payment_status is None


def test_order_row_rejects_naive_time_and_personal_fields() -> None:
    with pytest.raises(ValidationError):
        SalesOrderRow.model_validate({**ROW, "created_at": "2026-11-15T01:00:00"})
    with pytest.raises(ValidationError):  # 이름·연락처·주소 칸은 없다
        SalesOrderRow.model_validate({**ROW, "buyer_name": "홍길동"})


def test_export_result_round_trip() -> None:
    result = ShopSalesExportResult.model_validate(
        {"orders": [ROW], "next_cursor": "abc", "has_more": True}
    )
    assert result.orders[0].order_id == "ord_0001" and result.has_more


def test_catalog_sales_section_is_optional_and_defaults() -> None:
    base = {
        "id": "x",
        "name": "x",
        "metrics": [{"id": "m", "label": "m", "bad_direction": "up", "unit": "건"}],
        "impact": {"unit_label": "주문", "customer_label": "고객", "money_field": "amount"},
    }
    assert MetricCatalog.model_validate(base).sales is None
    catalog = MetricCatalog.model_validate({**base, "sales": {"categories": {"book": "도서"}}})
    assert catalog.sales == CatalogSales(categories={"book": "도서"})
    assert catalog.sales.min_product_orders == 5
    assert catalog.sales.max_staleness_minutes == 30
    assert catalog.sales.min_period_orders == 20
    with pytest.raises(ValidationError):
        CatalogSales(min_product_orders=0)


def test_sales_analysis_input_period() -> None:
    assert SalesAnalysisInput().period == "7d"
    assert SalesAnalysisInput(period="today").period == "today"
    with pytest.raises(ValidationError):
        SalesAnalysisInput.model_validate({"period": "90d"})


def test_sales_analysis_output_insufficient_has_no_numbers() -> None:
    now = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
    basis = SalesBasis(
        period_from=now, period_to=now, previous_from=now, previous_to=now, catalog_id="catbooks"
    )
    out = SalesAnalysisOutput(
        period="today",
        as_of=now,
        status="insufficient",
        insufficient_reasons=["데이터 부족: 기간 안 결제 완료 주문 3건 (20건 미만)"],
        basis=basis,
    )
    assert out.kpis == [] and out.customers is None and out.quality.rows_in_period == 0
    dumped = SalesAnalysisOutput.model_validate(out.model_dump(mode="json"))
    assert dumped == out
