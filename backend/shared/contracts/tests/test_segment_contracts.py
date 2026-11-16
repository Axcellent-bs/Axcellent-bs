"""고객·시장 분석 계약: 고객 내보내기, 장르 칸, CatalogSales 확장, 분석 입출력 4쌍.

캣북스형(연령대·성별·직업, 장르 소설·에세이·AI 활용)과 게임사형(연령대·플랫폼, 장르 RPG·퍼즐)
설정이 같은 모델로 통과하는지 본다. 이름은 시험 자료일 뿐 계약 코드에는 없다.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ax_contracts.data_access.shop_customers import (
    CustomerRow,
    ShopCustomersExportQuery,
    ShopCustomersExportResult,
)
from ax_contracts.data_access.shop_sales import SalesOrderLine
from ax_contracts.schemas.data import (
    CatalogSales,
    EventProposalInput,
    EventProposalItem,
    EventProposalOutput,
    EventProposalSendInput,
    EventProposalSendOutput,
    MarketCoverageInput,
    MarketCoverageOutput,
    MetricCatalog,
    SalesQuality,
    SegmentFavoritesInput,
    SegmentFavoritesOutput,
    SegmentProfileInput,
    SegmentProfileOutput,
)

AGE = {"key": "age_band", "label": "연령대",
       "values": {"10s": "10대", "20s": "20대", "30s": "30대", "40s": "40대 이상"}}  # fmt: skip

BOOKSTORE = {
    "genres": {"novel": "소설", "essay": "에세이", "ai": "AI 활용"},
    "genre_keywords": {"ai": ["AI 업무", "챗GPT"], "novel": ["소설 추천"]},
    "attributes": [
        AGE,
        {"key": "gender", "label": "성별", "values": {"f": "여성", "m": "남성"}},
        {"key": "job", "label": "직업", "values": {"office": "직장인", "student": "학생"}},
    ],
    "external_filters": [
        {"source_id": "data4library", "attribute_key": "age_band", "value_code": "20s",
         "params": {"from_age": 20, "to_age": 29}},
        {"source_id": "data4library", "attribute_key": "gender", "value_code": "f",
         "params": {"gender": 1}},
    ],  # fmt: skip
    "genre_external": [{"source_id": "data4library", "genre_code": "novel", "params": {"kdc": 8}}],
    "population": [
        {"attribute_keys": ["age_band", "gender"], "source_id": "kosis",
         "table_ref": {"org_id": "101", "tbl_id": "DT_X"}, "base_year": 2025},
    ],  # fmt: skip
    "event_kinds": [
        {"code": "coupon", "label": "쿠폰", "discount_rate": 0.15,
         "conversion_low": 0.03, "conversion_high": 0.06},
    ],  # fmt: skip
    "quick_questions": [
        {"label": "20대 여성이 좋아하는 책", "filters": {"age_band": "20s", "gender": "f"}},
    ],
}

GAME = {
    "genres": {"rpg": "RPG", "puzzle": "퍼즐"},
    "genre_keywords": {"rpg": ["RPG 신작"]},
    "attributes": [
        AGE,
        {"key": "platform", "label": "플랫폼", "values": {"ios": "iOS", "aos": "안드로이드"}},
    ],
    "population": [{"attribute_keys": ["age_band"], "source_id": "kosis", "table_ref": {}}],
    "event_kinds": [
        {"code": "pack", "label": "아이템 패키지", "discount_rate": 0.2,
         "conversion_low": 0.01, "conversion_high": 0.04},
        {"code": "attend", "label": "출석 보상", "discount_rate": 0, "conversion_low": 0.05,
         "conversion_high": 0.1},
    ],  # fmt: skip
    "quick_questions": [{"label": "iOS 20대", "filters": {"platform": "ios", "age_band": "20s"}}],
    "min_group_size": 10,
    "active_days": 90,
}


@pytest.mark.parametrize("config", [BOOKSTORE, GAME], ids=["bookstore", "game"])
def test_both_company_shapes_pass(config: dict) -> None:
    sales = CatalogSales.model_validate(config)
    assert sales.attributes[0].key == "age_band"
    again = CatalogSales.model_validate(sales.model_dump(mode="json"))
    assert again == sales


def test_new_fields_default_empty_and_old_config_still_valid() -> None:
    old = CatalogSales.model_validate({"categories": {"book": "도서"}, "min_product_orders": 5})
    assert old.genres == {} and old.attributes == [] and old.event_kinds == []
    assert old.min_group_size == 5 and old.active_days == 365


BAD = [
    ({"attributes": [AGE, AGE]}, "key가 겹친다"),
    ({"attributes": [{"key": "x", "label": "x", "values": {}}]}, "values"),
    ({"genre_keywords": {"ghost": ["x"]}}, "없는 장르"),
    ({"genres": {"a": "A"},
      "genre_external": [{"source_id": "s", "genre_code": "b"}]}, "없는 장르"),
    ({"attributes": [AGE], "external_filters": [
        {"source_id": "s", "attribute_key": "age_band", "value_code": "99s"}]}, "없는 값"),
    ({"attributes": [AGE], "external_filters": [
        {"source_id": "s", "attribute_key": "job", "value_code": "x"}]}, "없는 속성"),
    ({"attributes": [AGE], "population": [
        {"attribute_keys": ["gender"], "source_id": "kosis"}]}, "없는 속성"),
    ({"attributes": [AGE], "population": [
        {"attribute_keys": ["age_band"], "source_id": "kosis"},
        {"attribute_keys": ["age_band"], "source_id": "kosis"}]}, "조합이 겹친다"),
    ({"attributes": [AGE], "quick_questions": [
        {"label": "q", "filters": {"age_band": "50s"}}]}, "없는 값"),
    ({"attributes": [AGE],
      "quick_questions": [{"label": "q", "filters": {"job": "x"}}]}, "없는 속성"),
    ({"event_kinds": [{"code": "c", "label": "c", "discount_rate": 0.1,
                       "conversion_low": 0.2, "conversion_high": 0.1}]}, "conversion_low"),
    ({"event_kinds": [{"code": "c", "label": "c", "discount_rate": 1.5,
                       "conversion_low": 0, "conversion_high": 0}]}, "discount_rate"),
    ({"event_kinds": [
        {"code": "c", "label": "c", "discount_rate": 0, "conversion_low": 0, "conversion_high": 0},
        {"code": "c", "label": "d", "discount_rate": 0, "conversion_low": 0, "conversion_high": 0},
    ]}, "code가 겹친다"),
    ({"min_group_size": 0}, "min_group_size"),
]  # fmt: skip


@pytest.mark.parametrize(("config", "message"), BAD)
def test_invalid_configs_are_rejected(config: dict, message: str) -> None:
    with pytest.raises(ValidationError) as error:
        CatalogSales.model_validate(config)
    assert message in str(error.value)


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        CatalogSales.model_validate({"unknown_field": 1})
    with pytest.raises(ValidationError):
        CatalogSales.model_validate({"attributes": [{**AGE, "extra": 1}]})
    with pytest.raises(ValidationError):  # 고객 행에 이름·생년월일 칸은 없다
        CustomerRow.model_validate(
            {"buyer_id": "b", "joined_at": "2026-01-01T00:00:00Z",
             "updated_at": "2026-01-01T00:00:00Z", "birth_date": "1999-01-01"}
        )  # fmt: skip


def test_metric_catalog_accepts_extended_sales() -> None:
    base = {
        "id": "x", "name": "x",
        "metrics": [{"id": "m", "label": "m", "bad_direction": "up", "unit": "건"}],
        "impact": {"unit_label": "주문", "customer_label": "고객", "money_field": "amount"},
    }  # fmt: skip
    catalog = MetricCatalog.model_validate({**base, "sales": GAME})
    assert catalog.sales is not None and catalog.sales.min_group_size == 10


def test_customers_export_contract() -> None:
    assert ShopCustomersExportQuery().limit == 500
    for bad in (0, 1001):
        with pytest.raises(ValidationError):
            ShopCustomersExportQuery(limit=bad)
    row = CustomerRow.model_validate(
        {"buyer_id": "buy_0001", "joined_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-11-01T00:00:00Z", "attributes": {"age_band": "20s"}}
    )  # fmt: skip
    result = ShopCustomersExportResult(customers=[row], next_cursor="c", has_more=False)
    assert ShopCustomersExportResult.model_validate(result.model_dump(mode="json")) == result
    with pytest.raises(ValidationError):
        naive = {**row.model_dump(mode="json"), "joined_at": "2026-01-01T00:00:00"}
        CustomerRow.model_validate(naive)


def test_sales_line_genre_is_optional() -> None:
    line = SalesOrderLine(product_id="p", qty=1, unit_price=1)
    assert line.genre_code is None
    assert SalesOrderLine(product_id="p", qty=1, unit_price=1, genre_code="ai").genre_code == "ai"


def test_quality_new_fields_default_empty() -> None:
    q = SalesQuality()
    assert q.unknown_genres == [] and q.unmapped_attribute_values == {}


def test_segment_inputs() -> None:
    assert SegmentProfileInput(target_kind="genre", target_code="ai").period == "90d"
    with pytest.raises(ValidationError):
        SegmentProfileInput(target_kind="seller", target_code="x")
    with pytest.raises(ValidationError):
        SegmentFavoritesInput(filters={})
    with pytest.raises(ValidationError):
        SegmentFavoritesInput(filters={"a": "b"}, period="7d")
    with pytest.raises(ValidationError):
        MarketCoverageInput(attribute_keys=[])
    for bad in ("2026-13", "2026-1", "26-12"):
        with pytest.raises(ValidationError):
            EventProposalInput(month=bad)
    with pytest.raises(ValidationError):
        EventProposalInput(month="2026-12", max_items=6)


def _proposal(**over) -> dict:
    base = {
        "proposal_id": "evp_abc",
        "target_filters": {"job": "office"},
        "genre_code": "ai",
        "event_kind": "coupon",
        "timing_basis": {"month": "2026-12", "keywords": ["AI 업무"], "last_year_multiple": 1.3},
        "reasons": [
            {"kind": "segment", "text": "1.5배", "value": 1.5, "unit": "배", "source": "주문"},
            {"kind": "season", "text": "1.3배", "value": 1.3, "unit": "배", "source": "검색"},
            {"kind": "conversion", "text": "3~6%", "value": 0.03, "unit": "%", "source": "가정값"},
        ],
        "estimate": {
            "target_customers": 2000, "conversion_low": 0.03, "conversion_high": 0.06,
            "conversion_source": "assumption", "assumption": True, "aov": 22700,
            "orders_low": 60, "orders_high": 120, "revenue_low": 1362000,
            "revenue_high": 2724000, "discount_cost_low": 204300, "discount_cost_high": 408600,
            "formula": "2,000명 × 3~6% × 22,700원",
        },
    }  # fmt: skip
    base.update(over)
    return base


def test_event_proposal_needs_exactly_three_reasons() -> None:
    item = EventProposalItem.model_validate(_proposal())
    assert item.sent is None and item.past_results == [] and item.title is None
    for reasons in (item.reasons[:2], item.reasons * 2):
        with pytest.raises(ValidationError):
            EventProposalItem.model_validate(
                _proposal(reasons=[r.model_dump() for r in reasons])
            )


def test_outputs_round_trip_and_suppressed_rows() -> None:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    basis = {"period_from": now, "period_to": now, "previous_from": now, "previous_to": now,
             "catalog_id": "x"}  # fmt: skip
    outputs = [
        SegmentProfileOutput(
            target_kind="genre", target_code="ai", period="90d", basis=basis,
            attributes=[{"key": "job", "label": "직업", "values": [
                {"value_code": "office", "label": "직장인", "target_share": 0.58,
                 "overall_share": 0.39, "lift": 1.49, "buyers": 58},
                {"value_code": "student", "label": "학생", "suppressed": True},
            ]}],
        ),
        SegmentFavoritesOutput(
            filters={"age_band": "20s"}, period="90d",
            internal=[{"kind": "genre", "code": "essay", "label": "에세이", "share": 0.18,
                       "lift": 1.9, "buyers": 74}],
            external=[{"source_id": "data4library", "status": "key_missing",
                       "reason": "키 필요"}],
        ),
        MarketCoverageOutput(
            attribute_keys=["age_band", "gender"],
            rows=[{"values": {"age_band": "20s", "gender": "f"}, "label": "20대 · 여성",
                   "buyers": 1180, "population": 3000000, "coverage": 0.0004},
                  {"values": {"age_band": "10s", "gender": "m"}, "label": "10대 · 남성",
                   "status": "population_missing", "reason": "통계표를 읽지 못함"}],
        ),
        EventProposalOutput(month="2026-12", proposals=[_proposal()]),
        EventProposalOutput(month="2026-12", status="insufficient",
                            insufficient_reasons=["데이터 부족: x"]),
    ]  # fmt: skip
    for out in outputs:
        assert type(out).model_validate(out.model_dump(mode="json")) == out
    assert outputs[0].attributes[0].values[1].lift is None


def test_ranges_are_checked() -> None:
    with pytest.raises(ValidationError):
        MarketCoverageOutput(rows=[{"label": "x", "coverage": 1.5}])
    with pytest.raises(ValidationError):
        EventProposalItem.model_validate(
            _proposal(estimate={**_proposal()["estimate"], "conversion_high": 2})
        )


def test_send_contract() -> None:
    assert EventProposalSendInput(proposal_id="evp_abc").proposal_id == "evp_abc"
    out = EventProposalSendOutput(outcome="duplicate", proposal_ref="PROP-12")
    assert out.reason == ""
    with pytest.raises(ValidationError):
        EventProposalSendOutput(outcome="sent")
