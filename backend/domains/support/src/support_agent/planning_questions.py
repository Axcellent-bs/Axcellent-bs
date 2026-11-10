"""기획 → 문의 서비스 되묻기 (`answer_planning_question`, #264).

기획이 문의에서 보낸 변경 요청(`create_ticket`)의 기획서를 쓰다가 근거가 모자라면 문의 서비스에
묻는다. 문의 서비스는 **자기가 보낸 요청의 기록**으로만 답한다. 지어내지 않는다.

- 기록은 `ChangeRequestStore`의 연결(`ChangeRequestLink`)이다. 요청을 보낼 때 코드가 정리한 근거
  요약(`sent_evidence`)과 근거 문의 ID(`sent_evidence_ids`)를 함께 남겨 둔다 (#281 2단계 a).
- 답하는 규칙 (코드 규칙, LLM 없음). 질문 문구는 판단에 쓰지 않는다 (`about`·`options`만 본다).
  - 이 사건(`ctx_req_…`)과 요청 번호(`REQ-n`)와 테넌트가 모두 맞는 연결이 없으면 `unknown`
  - 근거(`about: evidence`) 질문이고 고를 답(`options`)이 없고 보낸 근거 요약이 있으면 `answered`
  - 우리가 보낸 요청이지만 기록으로 답할 수 없으면 `needs_human`
    (다른 칸을 묻거나, 고를 답이 있거나, 보낸 근거가 없는 예전 기록, 답에 개인정보가 섞일 때)
- 상태를 바꾸지 않는 read skill이라 `execute()`를 거치지 않는다.
- 사람이 답하는 화면과 `receive_planning_answer` 전송은 후속이다. 그래서 `needs_human`은 상태만
  돌려주고, 기획은 멈추지 않고 기획서를 이어 쓴다. 다만 질문이 열려 있는 동안 그 기획서는
  확정되지 않고 "답 대기"로 남아 개발로 넘어가지 못하니, 사람이 되도록 빨리 답해야 한다.

기록에 있는 사실만 쓴다. 우리가 가진 시각은 요청을 **보낸** 시각이라 "보낸 시각"으로 적고, 문의가
몰린 시각(발생 시각)처럼 쓰지 않는다.
"""

from __future__ import annotations

from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
)
from support_agent import guard
from support_agent.change_requests import ChangeRequestLink, ChangeRequestStore

ANSWERABLE_ABOUT = "evidence"
MAX_LINES = 5  # 한 요청에 묶인 문의가 많아도 답은 이만큼만 이어 붙인다
MAX_EVIDENCE_IDS = 20


def sent_links(
    changes: ChangeRequestStore, tenant_id: str, context_id: str, request_id: str
) -> list[ChangeRequestLink]:
    """우리가 보낸 요청의 연결. 사건 번호·요청 번호·테넌트가 모두 맞아야 우리 요청이다."""
    return [
        link
        for link in changes.for_context(context_id)
        if link.request_id == request_id and link.tenant_id == tenant_id
    ]


def _lines(links: list[ChangeRequestLink]) -> list[str]:
    """보낸 근거 요약이 있는 연결마다 한 줄 (같은 문의·같은 근거는 한 번만)."""
    seen: set[tuple[str, int]] = set()
    lines: list[str] = []
    for link in links:
        if not link.sent_evidence:
            continue
        key = (link.sent_evidence, link.inquiry_id)
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"{link.sent_evidence} (대표 문의 {link.source_ref}, 보낸 시각 {link.created_at})"
        )
    return lines


def _evidence_ids(links: list[ChangeRequestLink]) -> list[str]:
    ids: list[str] = []
    for link in links:
        for ref in (link.source_ref, *link.sent_evidence_ids):
            if ref not in ids:
                ids.append(ref)
    return ids[:MAX_EVIDENCE_IDS]


def answer_question(
    q: AnswerPlanningQuestionInput,
    tenant_id: str,
    context_id: str,
    changes: ChangeRequestStore,
) -> AnswerPlanningQuestionOutput:
    """기록으로 답한다. 우리 요청이 아니면 `unknown`, 기록으로 못 답하면 `needs_human`."""
    links = sent_links(changes, tenant_id, context_id, q.request_id)
    if not links:
        return AnswerPlanningQuestionOutput(status="unknown")
    if q.about != ANSWERABLE_ABOUT or q.options:
        return AnswerPlanningQuestionOutput(status="needs_human")
    lines = _lines(links)
    if not lines:
        return AnswerPlanningQuestionOutput(status="needs_human")
    shown = lines[:MAX_LINES]
    answer = "; ".join(shown)
    if len(lines) > len(shown):
        answer += f" (외 {len(lines) - len(shown)}건)"
    if guard.find_pii(answer):  # 기록에 개인정보가 섞였다면 사람이 확인하게 둔다
        return AnswerPlanningQuestionOutput(status="needs_human")
    return AnswerPlanningQuestionOutput(
        status="answered",
        answer=answer,
        answered_by="agent",
        evidence_ids=_evidence_ids(links),
    )
