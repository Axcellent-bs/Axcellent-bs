"""이벤트 이름: 신호의 구성요소·지표 코드를 사람이 읽는 한 문장으로 바꾼다.

총괄 화면과 인사 AI의 결정 카드 제목이 같은 문장을 쓰도록 여기서 한 번만 만든다. 대응표에 없으면
영어 코드를 그대로 보이지 않고 "…에 문제가 생겼어요"로 둔다(LLM으로 지어내지 않는다).
같은 종류의 이벤트인지 가르는 값(`event_type`)은 코드 그대로 쓴다.
"""

from __future__ import annotations

import re

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
# 부서 코드 → 사람이 읽는 이름 (결정 카드·화면 문장에 쓴다)
DEPT_KO = {
    "infra": "인프라 AI",
    "data": "데이터분석 AI",
    "planning": "기획 AI",
    "dev": "개발 AI",
    "marketing": "마케팅 AI",
    "support": "고객지원 AI",
    "finance": "재무 AI",
    "hr": "인사 AI",
    "orchestrator": "총괄 AI",
}
# 단계 메모의 코드 → 사람이 읽는 말 (코드가 아닌 메모는 그대로 둔다)
NOTE_KO = {
    "gate_rejected": "안전 확인 단계에서 막힘",
    "failed: gate_rejected": "안전 확인 단계에서 막힘",
    "metric_not_supported": "아직 분석하지 못하는 항목",
    "service_disabled": "해당 서비스가 꺼져 있음",
    "human_input": "사람 확인이 필요함",
    "cause_confident": "원인이 충분히 뚜렷함",
    "no_added_customers": "새로 확인된 고객 없음",
    "timeout": "응답이 늦어 중단됨",
}
# 문의 처리 결과 → 사람이 읽는 말
RESOLUTION_KO = {
    "answered": "답변 완료",
    "claim_decided": "클레임 결정",
    "compensated": "보상 완료",
    "escalated": "상담원에게 넘김",
}
# 원인 설명 글에 섞여 나오는 영어 지표·구성요소 이름 → 쉬운 말 (긴 이름부터 바꾼다)
TERM_KO = {
    "payment_fail_rate": "결제 실패율",
    "error_rate_5xx": "서버 오류율",
    "p95_latency_ms": "응답 시간",
    "order_fail_rate": "주문 실패율",
    "shop-api": "쇼핑몰 서버",
    "payment": "결제",
    "shipping": "배송",
    "search": "검색",
    "api": "판매 사이트",
    "db": "주문 DB",
}


def plain_terms(text: str) -> str:
    """글 속 영어 지표·구성요소 이름을 한국어로 바꾼다(다른 단어 속 글자는 그대로 둔다)."""
    pattern = "|".join(re.escape(k) for k in sorted(TERM_KO, key=len, reverse=True))
    return re.sub(
        rf"(?<![A-Za-z0-9_-])({pattern})(?![A-Za-z0-9_-])", lambda m: TERM_KO[m.group(1)], text
    )


PLAN_KO = {
    "scenario_a": "결제 장애 대응",
    "support_resolved": "고객 문의 처리 결과 기록",
}


def event_title(component: str, metric: str | None) -> str:
    """이벤트 제목. 예: `결제가 응답이 느려졌어요`."""
    comp = COMPONENT_KO.get(component, component)
    if metric and metric in METRIC_KO:
        return f"{SUBJECT_KO.get(component, f'{comp}이(가)')} {METRIC_KO[metric]}"
    return f"{comp}에 문제가 생겼어요"


def event_type(component: str, metric: str | None) -> str | None:
    """같은 유형의 이벤트인지 가르는 값. 예: `payment:p95_latency_ms`. 지표를 모르면 None."""
    return f"{component}:{metric}" if metric else None
