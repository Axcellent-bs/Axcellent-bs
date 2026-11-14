"""문의 집계·급증 감지 skill 핸들러 (읽기 전용).

상태를 바꾸지 않으므로 `execute()`를 거치지 않는다. 숫자는 `insights`가 코드로 센다.
급증 신호를 `support.inquiry_surge` 이벤트로 발행하는 일은 contracts에 data 모델이 생긴 뒤에 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ax_contracts.schemas import AgentRequest, AgentResult
from support_agent import handlers as h
from support_agent import inquiry_order_counts, insights
from support_agent.book_issue_schemas import InquiryOrderCountsInput
from support_agent.insight_schemas import SurgeInput


async def inquiry_overview(request: AgentRequest) -> AgentResult:
    """KPI·분류별 건수·답변 대기 문의."""
    overview = insights.overview(h.STORE, datetime.now(UTC))
    return h._done(request, overview.model_dump(mode="json"))


async def detect_inquiry_surge(request: AgentRequest) -> AgentResult:
    """분류별 문의 급증을 코드로 감지한다 (DEP-CS-05)."""
    parsed = h._parse(request, SurgeInput)
    if isinstance(parsed, AgentResult):
        return parsed
    report = insights.detect_surge(
        h.STORE,
        datetime.now(UTC),
        window_minutes=parsed.window_minutes,
        baseline_hours=parsed.baseline_hours,
        min_count=parsed.min_count,
        ratio=parsed.ratio,
        baseline_method=parsed.baseline_method,
    )
    return h._done(request, report.model_dump(mode="json"))


async def inquiry_order_counts_skill(request: AgentRequest) -> AgentResult:
    """기간 안의 문의를 주문별로 센다 (책별 집계가 문의를 상품에 붙일 때 부른다)."""
    parsed = h._parse(request, InquiryOrderCountsInput)
    if isinstance(parsed, AgentResult):
        return parsed
    tenant = request.meta.tenant_id  # 요청한 고객사의 문의만 센다
    counts = inquiry_order_counts.inquiry_order_counts(
        [c for c in h.STORE.all() if c.tenant_id == tenant], datetime.now(UTC), parsed.period
    )
    return h._done(request, counts.model_dump(mode="json"))


INSIGHT_HANDLERS = {
    "inquiry_overview": inquiry_overview,
    "detect_inquiry_surge": detect_inquiry_surge,
    "inquiry_order_counts": inquiry_order_counts_skill,
}
