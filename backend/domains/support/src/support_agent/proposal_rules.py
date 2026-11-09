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
from support_agent.proposal_decisions import ProposalDecision
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
EVIDENCE_LIMIT = 500  # `request_change`의 근거 글자 수 상한 (change_schemas)


def _label(topic_code: str) -> str:
    return TOPIC_KO.get(topic_code, FALLBACK_LABEL)


def _times(ratio: float | None, baseline: float) -> str:
    if ratio is None:
        return "평소 기록 없음"
    return f"평소 창당 {baseline:g}건의 {ratio:g}배"


def _no_decision(_topic: str, _inquiry_id: int) -> ProposalDecision | None:
    return None


def _no_labels(_topic: str) -> list[tuple[str, int]]:
    return []


def _cause_names(labels: list[tuple[str, int]]) -> str:
    """상담원이 정한 원인 이름과 건수 (예: "상담원이 정한 원인: 장바구니 화면 오류 3건")."""
    return "상담원이 정한 원인: " + ", ".join(f"{name} {count}건" for name, count in labels)


def propose_one(
    surge: TopicSurge,
    report: SurgeReport,
    *,
    has_request: Callable[[int], bool],
    decision_for: Callable[[str, int], ProposalDecision | None] = _no_decision,
    labels_for: Callable[[str], list[tuple[str, int]]] = _no_labels,
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
    labels = labels_for(surge.topic_code)
    if labels:  # 상담원이 "분류 필요"에서 정한 원인 이름이 있으면 근거에 붙인다 (이름과 건수만)
        with_causes = f"{evidence} · {_cause_names(labels)}"
        if len(with_causes) <= EVIDENCE_LIMIT and not guard.find_pii(with_causes):
            evidence = with_causes
    # 보내기 단계에서 `request_change`가 개인정보를 거부하므로, 만들 때 미리 걸러 둔다
    if guard.find_pii(title) or guard.find_pii(evidence):
        return None
    decision = decision_for(surge.topic_code, representative)
    return ChangeProposal(
        topic_code=surge.topic_code,
        title=title,
        evidence=evidence,
        inquiry_id=representative,
        evidence_ids=[source_ref_for(i) for i in rest],
        count=surge.count,
        ratio=surge.ratio,
        already_requested=has_request(representative),
        declined=decision is not None,
        decline_reason=decision.reason if decision else None,
    )


def build(
    report: SurgeReport,
    *,
    has_request: Callable[[int], bool],
    decision_for: Callable[[str, int], ProposalDecision | None] = _no_decision,
    labels_for: Callable[[str], list[tuple[str, int]]] = _no_labels,
) -> ProposeChangesReport:
    proposals = [
        p
        for s in report.surges
        if (
            p := propose_one(
                s,
                report,
                has_request=has_request,
                decision_for=decision_for,
                labels_for=labels_for,
            )
        )
    ]
    return ProposeChangesReport(
        window_minutes=report.window_minutes,
        baseline_hours=report.baseline_hours,
        evaluated_at=report.evaluated_at,
        proposals=proposals,
    )
