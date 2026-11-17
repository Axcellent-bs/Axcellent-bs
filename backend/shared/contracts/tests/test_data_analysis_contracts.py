"""데이터 분석 확장 계약 (11/17): basket_pairs · retention · channel_value, CatalogSales 칸 3개,
EventProposalItem.bundle. 직렬화 왕복, 기본값, 예전 설정·응답이 그대로 읽히는지 본다."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    BasketPairsInput,
    BasketPairsOutput,
    BundleBasis,
    CatalogSales,
    ChannelValueInput,
    ChannelValueOutput,
    EventProposalItem,
    EventProposalOutput,
    RetentionInput,
    RetentionOutput,
)

NOW = datetime(2026, 11, 17, tzinfo=UTC)
BASIS = {"period_from": NOW, "period_to": NOW, "previous_from": NOW, "previous_to": NOW,
         "catalog_id": "catbooks"}  # fmt: skip

PROPOSAL = {
    "proposal_id": "evp_abc",
    "event_kind": "coupon",
    "timing_basis": {"month": "2026-12"},
    "reasons": [
        {"kind": "segment", "text": "1.5배", "value": 1.5, "source": "주문"},
        {"kind": "season", "text": "1.3배", "value": 1.3, "source": "검색"},
        {"kind": "conversion", "text": "3~6%", "value": 0.03, "source": "가정값"},
    ],
    "estimate": {"target_customers": 100, "conversion_low": 0.03, "conversion_high": 0.06,
                 "conversion_source": "assumption"},
}  # fmt: skip


def _round_trip(model):
    assert type(model).model_validate(model.model_dump(mode="json")) == model


def test_catalog_new_fields_default_and_old_config_still_valid() -> None:
    old = CatalogSales.model_validate({"categories": {"book": "도서"}, "min_product_orders": 5})
    assert old.churn_days == 60 and old.repeat_min_orders == 2 and old.value_horizon_days == 90
    custom = CatalogSales(churn_days=45, repeat_min_orders=3, value_horizon_days=180)
    _round_trip(custom)
    for bad in ({"churn_days": 0}, {"repeat_min_orders": 1}, {"value_horizon_days": 0}):
        with pytest.raises(ValidationError):
            CatalogSales.model_validate(bad)


def test_basket_pairs_contract() -> None:
    inp = BasketPairsInput()
    assert (inp.level, inp.period, inp.filters, inp.max_pairs) == ("genre", "90d", {}, 10)
    assert inp.tenant_id == "shop-demo"
    assert BasketPairsInput(level="product", period="7d", filters={"age_band": "20s"}).level == (
        "product"
    )
    for bad in ({"max_pairs": 0}, {"max_pairs": 21}, {"level": "order"}, {"period": "2d"}):
        with pytest.raises(ValidationError):
            BasketPairsInput.model_validate(bad)

    out = BasketPairsOutput(
        basis=BASIS,
        pairs=[
            {"a_code": "novel", "a_label": "소설", "b_code": "essay", "b_label": "에세이",
             "together_buyers": 42, "a_buyers": 120, "b_buyers": 90, "confidence": 0.35,
             "lift": 1.8},
            {"a_code": "ai", "b_code": "essay", "suppressed": True},
        ],
    )  # fmt: skip
    _round_trip(out)
    hidden = out.pairs[1]
    assert hidden.together_buyers is None and hidden.confidence is None and hidden.lift is None
    with pytest.raises(ValidationError):
        BasketPairsOutput(pairs=[{"a_code": "a", "b_code": "b", "confidence": 1.2}])
    bad = BasketPairsOutput(status="insufficient", insufficient_reasons=["데이터 부족: x"])
    assert bad.pairs == [] and bad.basis is None
    _round_trip(bad)


def test_retention_contract() -> None:
    inp = RetentionInput()
    assert inp.cohorts == 3 and inp.filters == {}
    for bad in (0, 7):
        with pytest.raises(ValidationError):
            RetentionInput(cohorts=bad)

    out = RetentionOutput(
        basis=BASIS,
        cohorts=[
            {"cohort_month": "2026-08", "customers": 210, "r30": 0.21, "r60": 0.3, "r90": 0.36},
            {"cohort_month": "2026-10", "customers": 180, "r30": 0.18},  # 60·90일 아직 안 지남
            {"cohort_month": "2026-11", "suppressed": True},
        ],
        at_risk=[
            {"filters": {"age_band": "30s"}, "label": "30대", "regulars": 80, "at_risk": 20,
             "share": 0.25, "top_item_label": "에세이"},
            {"filters": {"age_band": "10s"}, "label": "10대", "suppressed": True},
        ],
    )  # fmt: skip
    _round_trip(out)
    assert out.churn_days == 60
    assert out.cohorts[1].r60 is None and out.cohorts[1].r90 is None
    assert out.at_risk[1].at_risk is None and out.at_risk[1].top_item_label is None
    for month in ("2026-13", "2026-1"):
        with pytest.raises(ValidationError):
            RetentionOutput(cohorts=[{"cohort_month": month}])
    with pytest.raises(ValidationError):
        RetentionOutput(at_risk=[{"share": 1.5}])


def test_channel_value_contract() -> None:
    assert ChannelValueInput().period == "365d"
    out = ChannelValueOutput(
        basis=BASIS,
        rows=[
            {"source": "naver", "label": "네이버", "new_customers": 300,
             "revenue_per_customer": 41000, "repeat_rate": 0.28, "orders_per_customer": 1.4},
            {"source": None, "label": "직접 유입", "suppressed": True},
        ],
    )  # fmt: skip
    _round_trip(out)
    assert out.horizon_days == 90 and out.cost_note == "재무 연결 대기"
    assert all(r.cost_krw is None for r in out.rows)
    with pytest.raises(ValidationError):
        ChannelValueOutput(rows=[{"label": "x", "repeat_rate": 2}])
    with pytest.raises(ValidationError):
        ChannelValueOutput(rows=[{"label": "x", "revenue_per_customer": -1}])


def test_event_proposal_bundle_is_optional() -> None:
    old = EventProposalItem.model_validate(PROPOSAL)  # 예전 응답 (bundle 없음)
    assert old.bundle is None
    item = EventProposalItem.model_validate(
        {**PROPOSAL, "bundle": {"a_code": "novel", "a_label": "소설", "b_code": "essay",
                                "b_label": "에세이", "lift": 1.8, "together_buyers": 42}}
    )  # fmt: skip
    assert item.bundle == BundleBasis(
        a_code="novel", a_label="소설", b_code="essay", b_label="에세이", lift=1.8,
        together_buyers=42,
    )  # fmt: skip
    _round_trip(EventProposalOutput(month="2026-12", proposals=[item, old]))
    with pytest.raises(ValidationError):
        BundleBasis(a_code="a", b_code="b", lift=-1)
