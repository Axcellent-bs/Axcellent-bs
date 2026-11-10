"""이벤트 공통 봉투와 대표 이벤트 data 모델 (공통 구성안 6장).

원칙: 이벤트에는 사실만 담는다(원인·판단 문장 금지). 외부 텍스트는 `untrusted`,
개인정보는 가명 ID만 쓴다. 경로·마감·근거는 이벤트가 아니라 A2A Task에 둔다.
받는 쪽은 같은 `event_id`를 두 번 처리하지 않는다(최소 한 번 전달).
필드를 추가하면 `version`은 그대로 두고, 삭제·이름 변경은 `version`을 올린다.

대표 이벤트의 data 모델만 여기에 둔다. 나머지는 발행 담당이 PR로 추가한다
(safety 이벤트는 태윤). shop 이벤트 data 모델은 이슈 #11 요청으로 준하·태윤이 추가했다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime
from ax_contracts.schemas.execute import HoldReason


class EventType(StrEnum):
    """`<대상>.<동사>` 형식의 이벤트 종류."""

    # 판매 사이트
    ORDER_CREATED = "order.created"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_DELIVERED = "order.delivered"
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_REFUNDED = "payment.refunded"
    SHIPMENT_DELAYED = "shipment.delayed"
    RETURN_RECEIVED = "return.received"
    SELLER_REGISTERED = "seller.registered"
    PRODUCT_CREATED = "product.created"
    SHOP_DEGRADED = "shop.degraded"
    SHOP_RECOVERED = "shop.recovered"
    # 고객지원
    SUPPORT_INQUIRY_RECEIVED = "support.inquiry_received"
    SUPPORT_INQUIRY_RESOLVED = "support.inquiry_resolved"
    SUPPORT_INQUIRY_SURGE = "support.inquiry_surge"
    SUPPORT_INCIDENT_COMPENSATION_DECIDED = "support.incident_compensation_decided"
    # 데이터분석
    METRIC_ANOMALY = "metric.anomaly"
    # 인프라(DejaFix 어댑터)
    INFRA_ANOMALY_DETECTED = "infra.anomaly_detected"
    # 재무: 더 이상 발행하지 않는다 (재무 감사를 없앴다). 값은 계약 호환으로만 남긴다
    FINANCE_ANOMALY_DETECTED = "finance.anomaly_detected"
    # 통합 에이전트
    INCIDENT_OPENED = "incident.opened"
    INCIDENT_RESOLVED = "incident.resolved"
    # 마케팅
    NOTICE_PUBLISHED = "notice.published"
    CAMPAIGN_CREATED = "campaign.created"
    COUPON_ISSUED = "coupon.issued"
    # 안전 레이어
    SAFETY_ACTION_EXECUTED = "safety.action_executed"
    SAFETY_ACTION_HELD = "safety.action_held"
    SAFETY_REVIEW_DECIDED = "safety.review_decided"
    # 사람 할 일함 (dashboard-api, 업무 요청 라우터)
    HANDOFF_COMPLETED = "handoff.completed"


class EventEnvelope(AxModel):
    """모든 이벤트의 공통 봉투.

    - `context_id`: 사건이 아니면 None이다.
    - `source`: 발행한 서비스·에이전트 이름이다.
    - `data`: 이벤트별 사실 필드(아래 `EVENT_DATA_MODELS`에 모델이 있으면 그 형식을 따른다).
    - `untrusted`: 외부 텍스트. 지시로 해석하지 않는다.
    """

    event_id: str
    type: EventType
    version: int = 1
    occurred_at: UtcDatetime
    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str | None = None
    source: str
    idempotency_key: str
    data: dict[str, Any] = Field(default_factory=dict)
    untrusted: dict[str, Any] = Field(default_factory=dict)


class ShopDegradedData(AxModel):
    """`shop.degraded` / `shop.recovered`. shop-api가 직접 측정해 발행한다.

    `metric_code`는 `p95_latency_ms`, `error_rate_5xx` 등이다.
    """

    component: Literal["api", "db", "payment"]
    metric_code: str
    value: float | None = None
    threshold: float | None = None
    # 기준을 넘기 시작한 첫 표본의 시각 (UTC). 영향 분석이 이번 장애 구간만 세도록 총괄이 쓴다
    since: UtcDatetime | None = None
    # `shop.recovered`에만: 어떻게 정상으로 돌아왔는지. `fault_released`(장애 상태를 해제함),
    # `metrics_normal`(따로 해제하지 않았는데 지표가 정상으로 돌아옴). 모르면 비운다
    recovery: Literal["fault_released", "metrics_normal"] | None = None
    # `fault_released`일 때 해제한 쪽(쇼핑몰 서버 토큰 이름).
    # `infra`=인프라 AI 롤백, `admin`=사람 운영자
    released_by: str | None = None


class PaymentFailedData(AxModel):
    """`payment.failed`. 금액은 정수 원이다. (`payment.succeeded`의 data는 재헌이 추가한다)"""

    payment_id: str
    order_id: str
    amount: int
    failure_code: str


class OrderItem(AxModel):
    """`order.created`의 주문 항목. 금액은 정수 원이다."""

    product_id: str
    seller_id: str
    qty: int = Field(ge=1)
    unit_price: int


class OrderCreatedData(AxModel):
    """`order.created`. `buyer_id`는 가명 ID다."""

    order_id: str
    buyer_id: str
    items: list[OrderItem]
    total_amount: int


class OrderCancelledData(AxModel):
    """`order.cancelled`."""

    order_id: str
    reason_code: Literal["buyer", "seller", "system"]
    cancelled_amount: int


class OrderDeliveredData(AxModel):
    """`order.delivered`."""

    order_id: str
    delivered_at: UtcDatetime


class PaymentSucceededData(AxModel):
    """`payment.succeeded`."""

    payment_id: str
    order_id: str
    amount: int
    method_code: str


class PaymentRefundedData(AxModel):
    """`payment.refunded`."""

    payment_id: str
    order_id: str
    refund_id: str
    refund_amount: int


class ShipmentDelayedData(AxModel):
    """`shipment.delayed`. 배송 예정일은 이벤트가 아니라 `shop_get_order` 응답으로 준다."""

    order_id: str
    delay_minutes: int = Field(ge=0)


class ReturnReceivedData(AxModel):
    """`return.received`. 클레임 ID(`clm_`)는 이벤트에 넣지 않고 `return_id`(`ret_`)로 통일한다."""

    return_id: str
    order_id: str
    received_at: UtcDatetime


class SellerRegisteredData(AxModel):
    """`seller.registered`. 판매자 소개 문장은 `untrusted`로 준다."""

    seller_id: str
    category_codes: list[str]
    # 제출 서류를 운영자가 확인했는가. 예전 이벤트(필드 없음)는 확인 안 함으로 본다
    documents_verified: bool = False


class ProductCreatedData(AxModel):
    """`product.created`. 상품명·설명은 `untrusted`로 준다."""

    product_id: str
    seller_id: str
    category_code: str
    price: int


class MetricAnomalyData(AxModel):
    """`metric.anomaly`. data 에이전트가 업무 지표 이상을 알린다."""

    metric: str
    window_from: UtcDatetime
    window_to: UtcDatetime
    observed: float
    baseline: float
    severity: Literal["low", "mid", "high"]


class InfraAnomalyData(AxModel):
    """`infra.anomaly_detected`. infra(DejaFix 어댑터)가 인프라 이상을 감지해 알린다.

    사실만 담는다. 원인 판단은 넣지 않고 infra `diagnose_incident` 결과로 따로 준다.
    `source_ref`는 근거 참조(Zabbix 이벤트·트리거 ID 등)다. `context_id`는 None이고
    사건 번호는 통합 에이전트가 발급한다.
    """

    component: str
    metric_code: str
    value: float | None = None
    threshold: float | None = None
    detected_at: UtcDatetime
    source_ref: str | None = None


class IncidentData(AxModel):
    """`incident.opened` / `incident.resolved`. 통합 에이전트만 발행한다."""

    component: str
    signal_source: Literal["shop", "data", "infra", "support"]
    opened_at: UtcDatetime
    resolved_at: UtcDatetime | None = None


class NoticePublishedData(AxModel):
    """`notice.published`.

    공지 본문·사실 필드·약속은 이벤트에 넣지 않는다. 마케팅의 `get_notice` 응답으로만 준다.
    """

    notice_id: str
    channel_code: str
    context_id: str


class Promise(AxModel):
    """마케팅이 고객에게 한 약속. 계산에는 `condition_code`만 쓴다."""

    type: Literal["points", "coupon"]
    amount: int
    condition_code: str
    condition: str  # 사람이 읽는 설명. 계산에 쓰지 않는다


class CampaignCreatedData(AxModel):
    """`campaign.created`."""

    campaign_id: str
    target_codes: list[str]
    starts_at: UtcDatetime
    ends_at: UtcDatetime
    promises: list[Promise] = Field(default_factory=list)


class CouponIssuedData(AxModel):
    """`coupon.issued`. `purpose_code`가 보상이면 `campaign_id`는 None이다.

    `buyer_id`·`execution_id`는 재무 사건 장부에 남긴다. 안전 서비스 실행 기록은 재시작하면
    비워진다.
    """

    coupon_id: str
    campaign_id: str | None = None
    purpose_code: Literal["campaign", "compensation"]
    count: int
    total_value: int
    buyer_id: str | None = None
    execution_id: str | None = None

    @model_validator(mode="after")
    def _campaign_id_matches_purpose(self) -> CouponIssuedData:
        if self.purpose_code == "compensation" and self.campaign_id is not None:
            raise ValueError("보상 쿠폰은 campaign_id가 None이어야 한다")
        if self.purpose_code == "campaign" and self.campaign_id is None:
            raise ValueError("캠페인 쿠폰에는 campaign_id가 필요하다")
        return self


class SafetyActionExecutedData(AxModel):
    """`safety.action_executed`. 안전 레이어가 발행한다. 금액은 정수 원이다."""

    execution_id: str
    action: str
    requester: str
    grade: Literal["A", "B", "C"]
    computed_amount: int | None = None
    threshold_version: int | None = None


class SafetyActionHeldData(SafetyActionExecutedData):
    """`safety.action_held`. C 보류 건. `review_id`는 승인 대기열 ID다."""

    hold_reason: HoldReason
    review_id: str


class SafetyReviewDecidedData(AxModel):
    """`safety.review_decided`. `decided_by`는 사람 이름이고 자동 거절이면 `system`이다."""

    review_id: str
    execution_id: str
    action: str
    requester: str
    decision: Literal["approved", "rejected"]
    decided_by: str


class SupportInquiryReceivedData(AxModel):
    """`support.inquiry_received`. 고객지원이 문의 접수·후속 고객 메시지마다 발행한다.

    사실 필드만 담는다. 문의 원문은 봉투의 `untrusted`로 주고,
    `category`·`urgency`는 분류 코드값이다.
    `message_count`는 문의에 쌓인 고객 메시지 수이며 첫 접수는 1이다.
    `order_id`는 다른 주문 이벤트와 같은 `ord_` 문자열 ID다.
    """

    inquiry_id: int
    order_id: str | None = None
    category: str | None = None
    urgency: Literal["low", "medium", "high"] | None = None
    message_count: int = Field(ge=1)


class SupportInquiryResolvedData(AxModel):
    """`support.inquiry_resolved`. 고객지원이 문의를 닫을 때(해결·이관) 한 번 발행한다.

    사실 필드만 담는다. 고객 원문과 판단 문장은 담지 않고, 사유는 열거형 코드뿐이다.
    멱등 키는 `<context_id>:support.inquiry_resolved:<inquiry_id>`라 같은 문의는 한 번만 나간다.
    닫은 뒤 환불이 끝나면 `refund_amount`를 채워 한 번 더 나간다. 이때 키 끝에
    `:refund:<claim_id>`가 붙어서 같은 환불은 한 번만 나가고, 받는 쪽은 같은 `inquiry_id`의
    기록에 금액을 갱신하면 된다. 보상 쿠폰이 닫은 뒤 지급되면 `coupon_id`를 채워
    `:coupon:<coupon_id>` 키로 한 번 더 나간다. 클레임 판정 없이 쿠폰만 나갔으면 `resolution`은
    `compensated`다.
    `order_id`는 다른 주문 이벤트와 같은 `ord_` 문자열 ID, `coupon_id`는 마케팅 `coupon.issued`와
    이어 보는 `cpn_` ID, `refund_amount`는 정수 원이다.
    """

    inquiry_id: int
    order_id: str | None = None
    status: Literal["resolved", "escalated"]
    resolution: Literal["answered", "claim_decided", "compensated", "escalated"]
    claim_id: int | None = None
    claim_decision: Literal["approve", "reject"] | None = None
    coupon_id: str | None = None
    refund_amount: int | None = Field(default=None, ge=0)


class SupportInquirySurgeData(AxModel):
    """`support.inquiry_surge`. 고객지원이 한 분류의 문의가 짧은 시간에 몰렸다고 판단할 때 발행한다.

    알리기만 한다. 장애 후보로 열지, 어디에 붙일지는 받는 쪽(총괄)이 정한다. 모니터링 증거(Zabbix·
    결제 감시) 없이 고객 문의만으로 만든 신호라 확신이 낮다. 코드 값만 담고 고객 이름·원문과
    분류 이름 같은 사람이 쓴 글은 담지 않는다.
    `topic_code`는 문의 분류 코드(예: `payment`), `count`는 최근 창(`window_minutes`분,
    `window_start`부터)에 들어온 그 분류 문의 수, `baseline_per_window`는 평소 같은 길이 창의
    건수(평균 또는 중앙값)다. 평소가 0이면 `ratio`는 None이다. `inquiry_ids`는 대표 문의 번호
    목록으로 최대 20개다. `triage_reason`은 "분류 필요"에 올라간 문의의 급증일 때만 채운다
    (`no_error_code`: 오류 코드 없는 결제 문의, `unclassified`: 분류가 안 맞는 문의).
    멱등 키는 `<source>:support.inquiry_surge:<topic_code>:<window_start>`라 같은 창의 같은 급증은
    한 번만 나간다. `window_start`는 멱등용 고정 구간(창 길이로 나눈 구간)의 시작이라, 실제로 센
    범위(확인한 시각까지 최근 창)의 시작과는 최대 창 길이만큼 다를 수 있다.
    """

    topic_code: str = Field(min_length=1, max_length=40)
    count: int = Field(ge=1)
    window_minutes: int = Field(ge=1)
    window_start: UtcDatetime
    baseline_per_window: float = Field(ge=0)
    ratio: float | None = Field(default=None, ge=0)
    inquiry_ids: list[int] = Field(default_factory=list, max_length=20)
    triage_reason: Literal["no_error_code", "unclassified"] | None = None


class SupportIncidentCompensationData(AxModel):
    """`support.incident_compensation_decided`. 사람이 사건(장애) 보상 대기 고객을 승인·거절한 뒤,
    그 사건의 보상 큐가 지금 어디까지 왔는지를 한 번 알린다.

    알리기만 한다. 총괄은 타임라인 기록과 단계 상태 표시만 하고 승인은 하지 않는다.
    사건(`ctx_inc_…`) 단위 요약이라 고객별 목록·구매자 ID·결정한 사람의 이름·고객 원문은
    담지 않는다.
    `pending`은 아직 사람이 보지 못한 고객, `approved`는 승인했지만 마케팅에 아직 못 보낸 고객,
    `held`는 마케팅 한도 초과로 사람 승인을 기다리는 고객, `sent`는 쿠폰이 나간 고객,
    `rejected`는 거절한 고객 수다(이 사건 큐 전체의 지금 상태).
    `coupons_sent`는 나간 쿠폰 수, `sent_value`는 그 합계(원·적립금 합)이다.
    `remaining`은 아직 끝나지 않은 고객 수(`pending`+`approved`+`held`)로, 0이 아니면 사람이 더
    처리해야 한다. 멱등 키는
    `<source>:support.incident_compensation_decided:<사건 번호>:<상태 지문>`이라 같은 상태를 다시
    알려도 한 번만 처리되고, 상태가 바뀌면 새 이벤트가 된다.
    """

    incident_context_id: str = Field(pattern=r"^ctx_inc_", min_length=9, max_length=80)
    pending: int = Field(ge=0)
    approved: int = Field(ge=0)
    held: int = Field(ge=0)
    sent: int = Field(ge=0)
    rejected: int = Field(ge=0)
    remaining: int = Field(ge=0)
    coupons_sent: int = Field(ge=0)
    sent_value: int = Field(ge=0)


class HandoffCompletedData(AxModel):
    """`handoff.completed`. 사람이 할 일함의 일을 끝내면 dashboard-api가 한 번 발행한다.

    맡긴 쪽 에이전트는 `task_id`로 처음 받은 `handed_to_human` 결과와 이어 붙인다.
    사람이 적은 결과 문장은 사실 필드가 아니라서 봉투의 `untrusted.result`로 준다.
    멱등 키는 `handoff.completed:<task_id>`라 같은 일은 한 번만 나간다.
    """

    task_id: str
    status: Literal["done", "rejected"]
    from_service: str
    to_service: str
    skill: str


# 이벤트 종류 → data 모델. 발행 담당이 새 data 모델을 추가할 때 여기에 한 줄 더한다.
EVENT_DATA_MODELS: dict[EventType, type[BaseModel]] = {
    EventType.SHOP_DEGRADED: ShopDegradedData,
    EventType.SHOP_RECOVERED: ShopDegradedData,
    EventType.PAYMENT_FAILED: PaymentFailedData,
    EventType.PAYMENT_SUCCEEDED: PaymentSucceededData,
    EventType.PAYMENT_REFUNDED: PaymentRefundedData,
    EventType.ORDER_CREATED: OrderCreatedData,
    EventType.ORDER_CANCELLED: OrderCancelledData,
    EventType.ORDER_DELIVERED: OrderDeliveredData,
    EventType.SHIPMENT_DELAYED: ShipmentDelayedData,
    EventType.RETURN_RECEIVED: ReturnReceivedData,
    EventType.SELLER_REGISTERED: SellerRegisteredData,
    EventType.PRODUCT_CREATED: ProductCreatedData,
    EventType.METRIC_ANOMALY: MetricAnomalyData,
    EventType.INFRA_ANOMALY_DETECTED: InfraAnomalyData,
    EventType.INCIDENT_OPENED: IncidentData,
    EventType.INCIDENT_RESOLVED: IncidentData,
    EventType.NOTICE_PUBLISHED: NoticePublishedData,
    EventType.CAMPAIGN_CREATED: CampaignCreatedData,
    EventType.COUPON_ISSUED: CouponIssuedData,
    EventType.SAFETY_ACTION_EXECUTED: SafetyActionExecutedData,
    EventType.SAFETY_ACTION_HELD: SafetyActionHeldData,
    EventType.SAFETY_REVIEW_DECIDED: SafetyReviewDecidedData,
    EventType.SUPPORT_INQUIRY_RECEIVED: SupportInquiryReceivedData,
    EventType.SUPPORT_INQUIRY_RESOLVED: SupportInquiryResolvedData,
    EventType.SUPPORT_INQUIRY_SURGE: SupportInquirySurgeData,
    EventType.SUPPORT_INCIDENT_COMPENSATION_DECIDED: SupportIncidentCompensationData,
    EventType.HANDOFF_COMPLETED: HandoffCompletedData,
}
