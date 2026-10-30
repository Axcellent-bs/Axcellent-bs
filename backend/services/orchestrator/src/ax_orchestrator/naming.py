"""이벤트 이름: 신호의 구성요소·지표 코드를 사람이 읽는 한 문장으로 바꾼다.

총괄 화면과 인사 AI의 결정 카드 제목이 같은 문장을 쓰도록 여기서 한 번만 만든다. 대응표에 없으면
영어 코드를 그대로 보이지 않고 "…에 문제가 생겼어요"로 둔다(LLM으로 지어내지 않는다).
같은 종류의 이벤트인지 가르는 값(`event_type`)은 코드 그대로 쓴다.
"""

from __future__ import annotations

COMPONENT_KO = {
    "payment": "결제",
    "api": "판매 사이트",
    "db": "주문 DB",
    "search": "검색",
    "shipping": "배송",
}
SUBJECT_KO = {
    "payment": "결제가",
    "api": "쇼핑몰이",
    "db": "주문 DB가",
    "search": "검색이",
    "shipping": "배송이",
}
METRIC_KO = {
    "error_rate_5xx": "서버 오류로 자꾸 실패해요",
    "p95_latency_ms": "응답이 느려졌어요",
    "order_fail_rate": "주문 실패가 늘었어요",
    "payment_fail_rate": "실패가 늘었어요",
}
FINANCE_KO = {
    "repeat_refund": "같은 고객이 환불을 반복해요",
    "refund_rate_jump": "환불이 갑자기 많아졌어요",
    "split_under_limit": "한도 바로 아래로 쪼갠 지급이 있어요",
    "duplicate_coupon": "같은 이벤트 보상 쿠폰이 두 번 나갔어요",
}
FINANCE_DEFAULT = "돈 흐름에서 이상한 점을 찾았어요"
PLAN_KO = {
    "scenario_a": "결제 장애 대응",
    "finance_anomaly": "재무 이상 대응",
    "support_resolved": "고객 문의 처리 결과 기록",
}


def event_title(component: str, metric: str | None) -> str:
    """이벤트 제목. 예: `결제가 응답이 느려졌어요`."""
    if component == "finance":
        return FINANCE_KO.get(metric or "", FINANCE_DEFAULT)
    comp = COMPONENT_KO.get(component, component)
    if metric and metric in METRIC_KO:
        return f"{SUBJECT_KO.get(component, f'{comp}이(가)')} {METRIC_KO[metric]}"
    return f"{comp}에 문제가 생겼어요"


def event_type(component: str, metric: str | None) -> str:
    """같은 유형의 이벤트인지 가르는 값. 예: `payment:p95_latency_ms`."""
    return f"{component}:{metric or 'unknown'}"
