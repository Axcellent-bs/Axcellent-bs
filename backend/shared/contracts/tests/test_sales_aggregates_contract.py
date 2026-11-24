"""일별 집계 계약 (3단계 3-1, 설계 2026-11-24-daily-aggregate-design.md 7절·4.3절).

- 집계 행은 회사·날짜·차원 → 숫자만: 어떤 행에도 구매자 id·주문 id 칸이 없다
- 사람 수 칸은 HiddenCount (k 미만이면 value None + hidden)
- 판매 기록 내보내기 범위 칸은 추가만: 예전 요청·응답은 그대로 읽힌다
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import BaseModel, ValidationError

from ax_contracts.data_access import sales_aggregates as agg
from ax_contracts.data_access.shop_buyer_summaries import (
    BuyerSummaryRow,
    ShopBuyerSummariesQuery,
    ShopBuyerSummariesResult,
)
from ax_contracts.data_access.shop_sales import ShopSalesExportQuery
from ax_contracts.schemas.common import TENANT_ID_DEFAULT

NOW = dt.datetime(2026, 11, 24, 0, 30, tzinfo=dt.UTC)
BASE = {"run_id": "run_1", "computed_at": NOW}

AGG_ROWS: list[type[BaseModel]] = [
    agg.SalesDailyRow,
    agg.BuyerPeriodRow,
    agg.CohortRow,
    agg.AtRiskRow,
    agg.PairRow,
    agg.SegmentProfileRow,
    agg.ChannelValueRow,
    agg.QualityDailyRow,
    agg.AggRun,
    agg.AggCompareRow,
]
ID_FIELDS = {"buyer_id", "order_id", "customer_id", "buyer_ids", "order_ids", "ordered_at"}


@pytest.mark.parametrize("model", AGG_ROWS, ids=lambda m: m.__name__)
def test_aggregate_rows_have_no_person_or_order_id(model: type[BaseModel]) -> None:
    assert not ID_FIELDS & set(model.model_fields)
    assert "tenant_id" in model.model_fields  # 회사마다 나뉜다


def test_sales_daily_row_defaults_and_tenant() -> None:
    row = agg.SalesDailyRow.model_validate({**BASE, "day": "2026-11-23", "grain": "order"})
    assert row.tenant_id == TENANT_ID_DEFAULT and row.product_id == "" and row.orders == 0
    assert row.coupon_used is False and row.refund_amount == 0
    with pytest.raises(ValidationError):
        agg.SalesDailyRow.model_validate({**BASE, "day": "2026-11-23", "grain": "week"})


def test_hidden_count_keeps_number_or_hides_it() -> None:
    shown = agg.HiddenCount(value=41)
    hidden = agg.HiddenCount(hidden=True)
    assert (shown.value, shown.hidden) == (41, False)
    assert (hidden.value, hidden.hidden) == (None, True)
    row = agg.BuyerPeriodRow.model_validate(
        {
            **BASE,
            "as_of": "2026-11-23",
            "period": "90d",
            "dim_kind": "genre",
            "dim_code": "mystery",
            "attr_cell": {"age_band": "10s"},
            "buyers": {"hidden": True},
            "new_buyers": {"hidden": True},
            "repeat_buyers": {"hidden": True},
        }
    )
    assert row.buyers.value is None and row.buyers.hidden
    with pytest.raises(ValidationError):  # 정해진 기간만
        agg.BuyerPeriodRow.model_validate({**row.model_dump(), "period": "45d"})


def test_cohort_row_month_and_not_due() -> None:
    row = agg.CohortRow.model_validate(
        {
            **BASE,
            "as_of": "2026-11-23",
            "cohort_month": "2026-10",
            "first_buyers": {"value": 96},
            "repeat": {"30": {"value": 12}, "60": None, "90": None},
        }
    )
    assert row.repeat["60"] is None  # 아직 다 안 지남
    with pytest.raises(ValidationError):
        agg.CohortRow.model_validate({**row.model_dump(), "cohort_month": "2026/10"})


def test_agg_run_status_and_input() -> None:
    run = agg.AggRun(
        run_id="run_1", as_of=dt.date(2026, 11, 23), input="sales_db", started_at=NOW,
        status="done", rows={"agg_sales_daily": 120},
    )  # fmt: skip
    assert run.tenant_id == TENANT_ID_DEFAULT and run.finished_at is None
    with pytest.raises(ValidationError):
        agg.AggRun.model_validate({**run.model_dump(), "input": "csv"})


def test_buyer_summaries_query_and_result() -> None:
    q = ShopBuyerSummariesQuery(as_of=NOW)
    assert q.limit == 500 and q.cursor is None and q.changed_since is None
    with pytest.raises(ValidationError):
        ShopBuyerSummariesQuery(as_of=NOW, limit=1001)
    res = ShopBuyerSummariesResult.model_validate(
        {
            "as_of": NOW,
            "buyers": [
                {
                    "buyer_id": "b_1",
                    "first_paid_at": NOW,
                    "paid_orders": 2,
                    "attributes": {"age_band": "20s"},
                    "items": [
                        {"level": "genre", "code": "essay", "first_paid_at": NOW,
                         "last_paid_at": NOW, "paid_orders": 1},
                    ],
                },
                {"buyer_id": "b_2"},  # 결제 완료 주문이 없는 사람
            ],
        }
    )  # fmt: skip
    assert res.has_more is False and res.next_cursor is None
    assert res.buyers[1] == BuyerSummaryRow(buyer_id="b_2")


def test_sales_export_query_range_fields_are_optional() -> None:
    old = ShopSalesExportQuery.model_validate({"cursor": "c1", "limit": 100})
    assert old.created_from is None and old.refunded_to is None  # 예전 요청 그대로
    ranged = ShopSalesExportQuery(created_from=NOW, created_to=NOW + dt.timedelta(days=1))
    assert ranged.created_to and ranged.cursor is None
