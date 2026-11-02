"""급증 감지 결과 → 기획에 보낼 변경 요청안 (코드 규칙, LLM 없음).

- 급증한 분류마다 제안 하나를 만든다. 제목·근거는 템플릿이고 고객 원문이나 개인정보를 쓰지 않는다.
- 대표 문의는 급증 샘플 중 번호가 가장 작은 문의다. 나머지 샘플은 근거 ID(`inq_<번호>`)로 담는다.
- 만들기만 한다. 아무것도 보내거나 저장하지 않는다 (보내는 일은 사람이 고른 뒤 `request_change`).
"""

from __future__ import annotations

from collections.abc import Callable

from support_agent import guard
from support_agent.change_requests import source_ref_for
from support_agent.insight_schemas import SurgeReport, TopicSurge
from support_agent.proposal_schemas import ChangeProposal, ProposeChangesReport

# 분류 코드 → 사람이 읽는 말 (화면의 TOPIC_KO와 같은 값). 모르는 코드는 "기타"로 쓴다
TOPIC_KO: dict[str, str] = {
    "payment": "결제",
    "shipping": "배송",
    "refund": "환불·반품",
    "cancel": "주문 취소",
    "product": "상품",
    "account": "계정",
    "review": "후기",
    "privacy_deletion": "개인정보 삭제 요청",
    "general": "일반",
}
FALLBACK_LABEL = "기타"


def _label(topic_code: str) -> str:
    return TOPIC_KO.get(topic_code, FALLBACK_LABEL)


def _times(ratio: float | None, baseline: float) -> str:
    if ratio is None:
        return "평소 기록 없음"
    return f"평소 창당 {baseline:g}건의 {ratio:g}배"


def propose_one(
    surge: TopicSurge, report: SurgeReport, *, has_request: Callable[[int], bool]
) -> ChangeProposal | None:
    """급증 하나를 변경 요청안으로 바꾼다. 샘플 문의가 없으면 대표 문의를 정할 수 없어 None."""
    ids = sorted(set(surge.sample_inquiry_ids))
    if not ids:
        return None
    label = _label(surge.topic_code)
    representative, rest = ids[0], ids[1:]
    title = f"{label} 문의 급증 — 안내·화면 점검 검토"
    evidence = (
        f"최근 {report.window_minutes}분 {label} 문의 {surge.count}건 "
        f"({_times(surge.ratio, surge.baseline_per_window)})"
    )
    # 보내기 단계에서 `request_change`가 개인정보를 거부하므로, 만들 때 미리 걸러 둔다
    if guard.find_pii(title) or guard.find_pii(evidence):
        return None
    return ChangeProposal(
        topic_code=surge.topic_code,
        title=title,
        evidence=evidence,
        inquiry_id=representative,
        evidence_ids=[source_ref_for(i) for i in rest],
        count=surge.count,
        ratio=surge.ratio,
        already_requested=has_request(representative),
    )


def build(report: SurgeReport, *, has_request: Callable[[int], bool]) -> ProposeChangesReport:
    proposals = [p for s in report.surges if (p := propose_one(s, report, has_request=has_request))]
    return ProposeChangesReport(
        window_minutes=report.window_minutes,
        baseline_hours=report.baseline_hours,
        evaluated_at=report.evaluated_at,
        proposals=proposals,
    )
