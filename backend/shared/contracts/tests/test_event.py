import json

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    EVENT_DATA_MODELS,
    CampaignCreatedData,
    CouponIssuedData,
    EventEnvelope,
    EventType,
    IncidentData,
    InfraAnomalyData,
    MetricAnomalyData,
    NoticePublishedData,
    OrderCancelledData,
    OrderCreatedData,
    OrderDeliveredData,
    PaymentFailedData,
    PaymentRefundedData,
    PaymentSucceededData,
    ProductCreatedData,
    ReturnReceivedData,
    SellerRegisteredData,
    ShipmentDelayedData,
    ShopDegradedData,
    SupportIncidentCompensationData,
    SupportInquiryReceivedData,
    SupportInquiryResolvedData,
    SupportInquirySurgeData,
)

ENVELOPE_JSON = json.dumps(
    {
        "event_id": "evt_0001",
        "type": "shop.degraded",
        "occurred_at": "2026-10-22T05:41:00Z",
        "context_id": None,
        "source": "shop-api",
        "idempotency_key": "shop-api:shop.degraded:1",
        "data": {
            "component": "payment",
            "metric_code": "p95_latency_ms",
            "value": 1450.0,
            "threshold": 1000.0,
        },
    }
)


def test_envelope_example_json_validates() -> None:
    event = EventEnvelope.model_validate_json(ENVELOPE_JSON)
    assert event.type == EventType.SHOP_DEGRADED
    assert event.version == 1
    assert event.tenant_id == "shop-demo"
    assert event.untrusted == {}
    assert ShopDegradedData.model_validate(event.data).metric_code == "p95_latency_ms"


def test_unknown_event_type_is_rejected() -> None:
    raw = json.loads(ENVELOPE_JSON)
    raw["type"] = "shop.exploded"
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(raw)


def test_event_type_catalog() -> None:
    values = {t.value for t in EventType}
    assert len(values) == 28
    for expected in (
        "order.created",
        "payment.failed",
        "shop.recovered",
        "support.inquiry_received",
        "support.inquiry_resolved",
        "support.incident_compensation_decided",
        "metric.anomaly",
        "infra.anomaly_detected",
        "finance.anomaly_detected",
        "incident.resolved",
        "coupon.issued",
        "safety.review_decided",
        "handoff.completed",
    ):
        assert expected in values
    assert set(EVENT_DATA_MODELS) <= set(EventType)


def test_representative_data_models() -> None:
    assert (
        PaymentFailedData.model_validate(
            {"payment_id": "pay_1", "order_id": "ord_1", "amount": 30000, "failure_code": "TIMEOUT"}
        ).amount
        == 30000
    )
    anomaly = MetricAnomalyData.model_validate(
        {
            "metric": "payment_failure_rate",
            "window_from": "2026-10-22T05:00:00Z",
            "window_to": "2026-10-22T05:05:00Z",
            "observed": 0.31,
            "baseline": 0.02,
            "severity": "high",
        }
    )
    assert anomaly.severity == "high"
    incident = IncidentData.model_validate(
        {"component": "payment", "signal_source": "shop", "opened_at": "2026-10-22T05:41:00Z"}
    )
    assert incident.resolved_at is None
    assert NoticePublishedData(notice_id="ntc_1", channel_code="banner", context_id="ctx_inc_1")


def test_amount_must_be_integer_won() -> None:
    with pytest.raises(ValidationError):
        PaymentFailedData.model_validate(
            {"payment_id": "p", "order_id": "o", "amount": 100.5, "failure_code": "X"}
        )


def test_campaign_with_promises() -> None:
    campaign = CampaignCreatedData.model_validate(
        {
            "campaign_id": "cmp_1",
            "target_codes": ["BOOK"],
            "starts_at": "2026-10-22T00:00:00Z",
            "ends_at": "2026-10-25T00:00:00Z",
            "promises": [
                {
                    "type": "points",
                    "amount": 1000,
                    "condition_code": "LATE_3D",
                    "condition": "3일 지연",
                }
            ],
        }
    )
    assert campaign.promises[0].condition_code == "LATE_3D"


def test_coupon_purpose_and_campaign_id() -> None:
    compensation = CouponIssuedData(
        coupon_id="cpn_1", purpose_code="compensation", count=3, total_value=9000
    )
    assert compensation.campaign_id is None
    with pytest.raises(ValidationError, match="campaign_id"):
        CouponIssuedData(
            coupon_id="cpn_1",
            campaign_id="cmp_1",
            purpose_code="compensation",
            count=1,
            total_value=1000,
        )
    with pytest.raises(ValidationError, match="campaign_id"):
        CouponIssuedData(coupon_id="cpn_1", purpose_code="campaign", count=1, total_value=1000)


def test_support_inquiry_received_data() -> None:
    assert EVENT_DATA_MODELS[EventType.SUPPORT_INQUIRY_RECEIVED] is SupportInquiryReceivedData
    first = SupportInquiryReceivedData(inquiry_id=7, message_count=1)
    assert first.order_id is None
    assert first.category is None
    assert first.urgency is None
    full = SupportInquiryReceivedData(
        inquiry_id=7, order_id="ord_1", category="delivery", urgency="high", message_count=2
    )
    assert full.urgency == "high"
    with pytest.raises(ValidationError):
        SupportInquiryReceivedData(inquiry_id=7, urgency="urgent", message_count=1)
    with pytest.raises(ValidationError):
        SupportInquiryReceivedData(inquiry_id=7, message_count=0)


def test_support_inquiry_resolved_data() -> None:
    assert EVENT_DATA_MODELS[EventType.SUPPORT_INQUIRY_RESOLVED] is SupportInquiryResolvedData
    answered = SupportInquiryResolvedData(inquiry_id=7, status="resolved", resolution="answered")
    assert answered.order_id is None and answered.coupon_id is None
    full = SupportInquiryResolvedData(
        inquiry_id=7,
        order_id="ord_1",
        status="resolved",
        resolution="compensated",
        claim_id=3,
        claim_decision="approve",
        coupon_id="cpn_1",
        refund_amount=2000,
    )
    assert full.model_dump(mode="json")["refund_amount"] == 2000
    with pytest.raises(ValidationError):
        SupportInquiryResolvedData(inquiry_id=7, status="closed", resolution="answered")
    with pytest.raises(ValidationError):
        SupportInquiryResolvedData(inquiry_id=7, status="resolved", resolution="refunded")
    with pytest.raises(ValidationError):
        SupportInquiryResolvedData(
            inquiry_id=7, status="resolved", resolution="answered", refund_amount=-1
        )
    with pytest.raises(ValidationError):  # 고객 원문 같은 모르는 필드는 거부
        SupportInquiryResolvedData(
            inquiry_id=7, status="resolved", resolution="answered", message="원문"
        )


def test_support_inquiry_surge_data() -> None:
    assert EVENT_DATA_MODELS[EventType.SUPPORT_INQUIRY_SURGE] is SupportInquirySurgeData
    plain = SupportInquirySurgeData(
        topic_code="payment",
        count=6,
        window_minutes=10,
        window_start="2026-11-09T06:00:00Z",
        baseline_per_window=1.2,
        ratio=5.0,
        inquiry_ids=[3, 5, 9],
    )
    assert plain.triage_reason is None  # 일반 분류 급증은 triage_reason이 없다
    no_baseline = SupportInquirySurgeData(
        topic_code="payment",
        count=5,
        window_minutes=10,
        window_start="2026-11-09T06:00:00Z",
        baseline_per_window=0,
    )
    assert no_baseline.ratio is None and no_baseline.inquiry_ids == []
    triage = SupportInquirySurgeData(
        topic_code="payment",
        count=5,
        window_minutes=10,
        window_start="2026-11-09T06:00:00Z",
        baseline_per_window=0,
        triage_reason="no_error_code",
    )
    assert triage.model_dump(mode="json")["triage_reason"] == "no_error_code"


def test_support_inquiry_surge_data_rejects_bad_values_and_free_text() -> None:
    base = {
        "topic_code": "payment",
        "count": 5,
        "window_minutes": 10,
        "window_start": "2026-11-09T06:00:00Z",
        "baseline_per_window": 1.0,
    }
    assert SupportInquirySurgeData.model_validate(base)
    for bad in (
        {"count": 0},
        {"window_minutes": 0},
        {"baseline_per_window": -1},
        {"ratio": -0.5},
        {"topic_code": ""},
        {"topic_code": "x" * 41},
        {"triage_reason": "manual"},  # 상담원이 직접 올린 표시는 급증의 근거가 아니다
        {"inquiry_ids": list(range(21))},
        {"window_start": "not-a-time"},
    ):
        with pytest.raises(ValidationError):
            SupportInquirySurgeData.model_validate({**base, **bad})
    free_texts = ({"message": "원문"}, {"label": "장바구니 화면 오류"}, {"customer_name": "김하늘"})
    for free_text in free_texts:
        with pytest.raises(ValidationError):  # 고객 원문·분류 이름 같은 글은 data에 못 넣는다
            SupportInquirySurgeData.model_validate({**base, **free_text})


def test_support_inquiry_surge_envelope_round_trip() -> None:
    envelope = EventEnvelope.model_validate(
        {
            "event_id": "evt_surge_1",
            "type": "support.inquiry_surge",
            "occurred_at": "2026-11-09T06:10:00Z",
            "context_id": None,
            "source": "support",
            "idempotency_key": "support:support.inquiry_surge:payment:2026-11-09T06:00:00Z",
            "data": {
                "topic_code": "payment",
                "count": 5,
                "window_minutes": 10,
                "window_start": "2026-11-09T06:00:00Z",
                "baseline_per_window": 0,
                "inquiry_ids": [3, 5],
                "triage_reason": "no_error_code",
            },
        }
    )
    assert envelope.type == EventType.SUPPORT_INQUIRY_SURGE
    assert SupportInquirySurgeData.model_validate(envelope.data).count == 5


def test_infra_anomaly_event_has_data_model() -> None:
    assert EVENT_DATA_MODELS[EventType.INFRA_ANOMALY_DETECTED] is InfraAnomalyData
    envelope = EventEnvelope.model_validate(
        {
            "event_id": "evt_infra_1",
            "type": "infra.anomaly_detected",
            "occurred_at": "2026-10-22T05:40:00+00:00",
            "context_id": None,
            "source": "infra",
            "idempotency_key": "infra:infra.anomaly_detected:zbx-1001",
            "data": {
                "component": "payment",
                "metric_code": "error_rate_5xx",
                "value": 0.18,
                "threshold": 0.05,
                "detected_at": "2026-10-22T05:39:30Z",
                "source_ref": "zabbix:trigger:1001",
            },
        }
    )
    data = InfraAnomalyData.model_validate(envelope.data)
    assert data.component == "payment"
    assert data.source_ref == "zabbix:trigger:1001"
    # 값·임계값·근거 참조는 없어도 된다 (없는 값은 None, 지어내지 않는다)
    minimal = InfraAnomalyData.model_validate(
        {"component": "db", "metric_code": "cpu_util", "detected_at": "2026-10-22T05:39:30Z"}
    )
    assert (minimal.value, minimal.threshold, minimal.source_ref) == (None, None, None)


def test_infra_anomaly_data_rejects_unknown_and_naive_time() -> None:
    base = {"component": "db", "metric_code": "cpu_util", "detected_at": "2026-10-22T05:39:30Z"}
    with pytest.raises(ValidationError):
        InfraAnomalyData.model_validate({**base, "cause": "deploy"})  # 원인 판단 필드는 없다
    with pytest.raises(ValidationError):
        InfraAnomalyData.model_validate({**base, "detected_at": "2026-10-22T05:39:30"})


SHOP_EVENT_SAMPLES: dict[EventType, dict] = {
    EventType.ORDER_CREATED: {
        "order_id": "ord_1",
        "buyer_id": "buy_a1",
        "items": [{"product_id": "prd_1", "seller_id": "sel_1", "qty": 2, "unit_price": 12000}],
        "total_amount": 24000,
    },
    EventType.ORDER_CANCELLED: {
        "order_id": "ord_1",
        "reason_code": "buyer",
        "cancelled_amount": 24000,
    },
    EventType.ORDER_DELIVERED: {"order_id": "ord_1", "delivered_at": "2026-10-22T05:00:00Z"},
    EventType.PAYMENT_SUCCEEDED: {
        "payment_id": "pay_1",
        "order_id": "ord_1",
        "amount": 24000,
        "method_code": "card",
    },
    EventType.PAYMENT_REFUNDED: {
        "payment_id": "pay_1",
        "order_id": "ord_1",
        "refund_id": "rfd_1",
        "refund_amount": 24000,
    },
    EventType.SHIPMENT_DELAYED: {"order_id": "ord_1", "delay_minutes": 1440},
    EventType.RETURN_RECEIVED: {
        "return_id": "ret_1",
        "order_id": "ord_1",
        "received_at": "2026-10-22T05:00:00Z",
    },
    EventType.SELLER_REGISTERED: {"seller_id": "sel_1", "category_codes": ["book", "stationery"]},
    EventType.PRODUCT_CREATED: {
        "product_id": "prd_1",
        "seller_id": "sel_1",
        "category_code": "book",
        "price": 12000,
    },
}


@pytest.mark.parametrize("event_type", list(SHOP_EVENT_SAMPLES))
def test_shop_event_data_models_accept_samples(event_type: EventType) -> None:
    model = EVENT_DATA_MODELS[event_type]
    data = SHOP_EVENT_SAMPLES[event_type]
    assert model.model_validate(data).model_dump(mode="json", exclude_none=True)
    with pytest.raises(ValidationError):
        model.model_validate({**data, "unknown_field": 1})  # 모르는 필드는 거부


def test_shop_event_models_are_the_expected_classes() -> None:
    assert EVENT_DATA_MODELS[EventType.ORDER_CREATED] is OrderCreatedData
    assert EVENT_DATA_MODELS[EventType.ORDER_CANCELLED] is OrderCancelledData
    assert EVENT_DATA_MODELS[EventType.ORDER_DELIVERED] is OrderDeliveredData
    assert EVENT_DATA_MODELS[EventType.PAYMENT_SUCCEEDED] is PaymentSucceededData
    assert EVENT_DATA_MODELS[EventType.PAYMENT_REFUNDED] is PaymentRefundedData
    assert EVENT_DATA_MODELS[EventType.SHIPMENT_DELAYED] is ShipmentDelayedData
    assert EVENT_DATA_MODELS[EventType.RETURN_RECEIVED] is ReturnReceivedData
    assert EVENT_DATA_MODELS[EventType.SELLER_REGISTERED] is SellerRegisteredData
    assert EVENT_DATA_MODELS[EventType.PRODUCT_CREATED] is ProductCreatedData


def test_shop_event_models_reject_bad_values() -> None:
    cancelled = SHOP_EVENT_SAMPLES[EventType.ORDER_CANCELLED]
    with pytest.raises(ValidationError):
        OrderCancelledData.model_validate({**cancelled, "reason_code": "customer"})
    created = SHOP_EVENT_SAMPLES[EventType.ORDER_CREATED]
    with pytest.raises(ValidationError):
        OrderCreatedData.model_validate({**created, "total_amount": 100.5})  # 금액은 정수 원
    with pytest.raises(ValidationError):
        OrderCreatedData.model_validate({**created, "items": [{**created["items"][0], "qty": 0}]})
    with pytest.raises(ValidationError):
        ShipmentDelayedData.model_validate({"order_id": "ord_1", "delay_minutes": -1})
    with pytest.raises(ValidationError):  # 시간대 없는 시각은 거부
        OrderDeliveredData.model_validate(
            {"order_id": "ord_1", "delivered_at": "2026-10-22T05:00:00"}
        )
    with pytest.raises(ValidationError):  # claim_id는 이벤트에 넣지 않는다
        ReturnReceivedData.model_validate(
            {**SHOP_EVENT_SAMPLES[EventType.RETURN_RECEIVED], "claim_id": "clm_1"}
        )


def test_support_incident_compensation_data() -> None:
    assert (
        EVENT_DATA_MODELS[EventType.SUPPORT_INCIDENT_COMPENSATION_DECIDED]
        is SupportIncidentCompensationData
    )
    data = SupportIncidentCompensationData(
        incident_context_id="ctx_inc_0001",
        pending=2,
        approved=0,
        held=1,
        sent=4,
        rejected=1,
        remaining=3,
        coupons_sent=4,
        sent_value=12000,
    )
    assert data.remaining == data.pending + data.approved + data.held
    for bad in (
        {"incident_context_id": "ctx_req_0001"},  # 사건(ctx_inc_)만 받는다
        {"pending": -1},
        {"buyer_ids": ["buy_0001"]},  # 고객별 값은 담지 않는다
        {"decided_by": "김하늘"},  # 사람 이름도 담지 않는다
    ):
        with pytest.raises(ValidationError):
            SupportIncidentCompensationData(**{**data.model_dump(), **bad})
