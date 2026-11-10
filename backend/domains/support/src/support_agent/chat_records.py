"""일어난 일을 고객지원 AI 대화방에 한 줄 요약으로 남긴다 (예진님 형식, 2026-11-10).

지금은 기획이 되물은 질문만 남긴다. 두 시점에 같은 대화(질문 하나에 대화 하나)에 적는다.
- 기록으로 답하지 못해 사람 답을 기다리게 됐을 때: "Q-REQ-n-k 고객지원에 근거 질문 → 사람 답 대기"
- 사람이 답해 기획에 보냈을 때: "Q-REQ-n-k → 답 받음"

규칙:
- 문장은 기록된 값(요청·질문·기획서 번호, 묻는 칸, 상태, 답한 쪽)으로만 코드가 만든다.
  질문·답 문구는 싣지 않는다 (원문은 질문 목록 쪽에만 있다).
- 사람이 연 대화가 아니라 `ref`로 이어진 기록이고 `who`는 AI다. 그래서 다른 사람은 요약만 본다.
- 남기는 데 실패해도 질문 받기·답 보내기는 멈추지 않는다 (호출하는 쪽이 감싼다).
- 이미 남긴 시점은 다시 남기지 않는다 (같은 질문이 다시 와도 한 번).
"""

from __future__ import annotations

from support_agent import chat, guard
from support_agent.planning_question_inbox import PlanningQuestionRecord

WHO = "고객지원 AI"
MAX_VALUE_CHARS = 30
WAITING = "사람 답 대기"
GOT_ANSWER = "답 받음"
CLOSED_TEXT = {
    "applied": GOT_ANSWER,
    "duplicate": "답 받음 (이미 받은 답)",
    "unknown_question": "기획이 모르는 질문이라 닫음",
}


def _value(text: str | None) -> str:
    """기획이 보낸 값에서 개인정보를 가리고 짧게 자른다."""
    return guard.mask_pii(" ".join((text or "").split()))[0][:MAX_VALUE_CHARS]


def _ref(record: PlanningQuestionRecord) -> str:
    return f"planning-question:{record.tenant_id}:{record.question_id}"


def _received_text(record: PlanningQuestionRecord) -> str:
    parts = [f"요청 {_value(record.request_id)}"]
    if record.spec_id:
        parts.append(f"기획서 {_value(record.spec_id)}")
    if record.about:
        parts.append(f"묻는 칸 {_value(record.about)}")
    return f"{_value(record.question_id)} 고객지원에 근거 질문 → {WAITING} ({', '.join(parts)})"


def planning_question_received(
    chats: chat.ChatStore, record: PlanningQuestionRecord
) -> chat.ChatThread:
    """사람 답을 기다리게 된 질문을 남긴다. 이미 남겼으면 그대로 돌려준다."""
    found = chats.find_by_ref(_ref(record))
    if found is not None:
        return found
    text = _received_text(record)
    thread = chats.create(WHO, record.question_id, record.context_id, ref=_ref(record))
    chats.add_message(thread.id, "assistant", text)
    return chats.close(thread.id, text[: chat.MAX_SUMMARY_CHARS])


def planning_question_answered(
    chats: chat.ChatStore, record: PlanningQuestionRecord, planning_outcome: str
) -> chat.ChatThread:
    """사람이 답해 기획이 응답한 시점을 남긴다. 대화가 없으면 먼저 받은 시점부터 만든다."""
    thread = planning_question_received(chats, record)
    done = CLOSED_TEXT.get(planning_outcome, GOT_ANSWER)
    if any(m.text.startswith(f"{_value(record.question_id)} → ") for m in thread.messages):
        return thread  # 이미 남겼다
    who = "사람"  # 이 길로 답하는 건 사람뿐이다 (기록으로 답한 건 대화방에 남기지 않는다)
    text = f"{_value(record.question_id)} → {done} (답한 쪽: {who})"
    chats.add_message(thread.id, "assistant", text)
    summary = f"{_value(record.question_id)} {WAITING} → {done} (답한 쪽: {who})"
    return chats.close(thread.id, summary[: chat.MAX_SUMMARY_CHARS])
