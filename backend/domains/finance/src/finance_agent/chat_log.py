"""재무 AI가 한 일을 공통 로그에 남겨 AI 대화방에 보이게 한다.

재무 AI가 하는 장부 기록은 다른 AI가 한 일에서 시작하므로 대화로 보여 준다.

- 장부 기록: 지급·환불 이벤트를 받아 장부에 적으면 "재무 AI → 보낸 쪽: 장부에 적었어요"
  (`result_sent`, completed) 한 줄을 남긴다. skill 이름은 `ledger_record`.
  요청 줄은 남기지 않는다(대화가 두 배로 길어진다).

payload에는 코드가 만든 사실(종류·금액·규칙 코드)만 담는다. 고객 문장은 넣지 않는다.
"""

from __future__ import annotations

from typing import Any

from ax_agent_template.logging import log_event

AGENT = "finance"
LEDGER_SKILL = "ledger_record"


def ledger_recorded(
    context_id: str | None, tenant_id: str, sender: str, kind: str, amount: int, count: int = 1
) -> None:
    """지급·환불 한 건을 장부에 적은 일을 대화 한 줄로 남긴다.

    AI 대화방은 사건마다 방이 있다. 사건 번호가 없는 기록(쇼핑몰 환불 이벤트 등)은
    대화로 남기지 않는다 (장부에는 적힌다).
    """
    if not context_id:
        return
    payload: dict[str, Any] = {"ledger_kind": kind, "amount": amount, "count": count}
    log_event(
        context_id,
        tenant_id,
        "result_sent",
        payload,
        source=AGENT,
        skill=LEDGER_SKILL,
        task_status="completed",
        requester=sender,
    )
