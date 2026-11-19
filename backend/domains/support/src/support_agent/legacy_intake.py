"""판매 서버 자료를 다시 만들기 전에 받은 문의("옛 자료") 표시.

판매 서버 자료를 다시 만든 날(`SUPPORT_LEGACY_INTAKE_BEFORE`, 예: `2026-11-14`) 0시(한국 시간)
이전에 받은 문의는 문의에 든 주문이 판매 서버에 없을 수 있다. 목록은 이 문의를 기본으로 숨기고
필터로만 보여 주기 위해 표시만 붙인다. 판매 서버를 부르지 않고 접수일과 설정값만 비교한다.

- 설정이 없거나 읽을 수 없으면 아무것도 표시하지 않는다.
- 문의 번호는 다시 매겨질 수 있어 번호가 아니라 접수일로 가른다.
- 표시일 뿐이다: 상태(열림)와 재무 기록은 바꾸지 않는다.
  주문이 실제로 있는지는 상세의 주문 조회가 말해 준다.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from support_agent.schemas import Conversation
from support_agent.store import InquiryStore

ENV_BEFORE = "SUPPORT_LEGACY_INTAKE_BEFORE"
FIELD = "legacy_intake"
_KST = ZoneInfo("Asia/Seoul")


def cutoff() -> datetime | None:
    """설정한 날 0시(한국 시간). 설정이 없거나 `YYYY-MM-DD`가 아니면 None."""
    raw = (os.getenv(ENV_BEFORE) or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=_KST)
    except ValueError:
        return None


def _parse(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_KST)


def received_at(conversation: Conversation) -> datetime | None:
    """문의를 받은 시각: 가장 이른 메시지 시각. 메시지가 없으면 마지막 변경 시각."""
    times = [t for m in conversation.messages if (t := _parse(m.created_at)) is not None]
    return min(times) if times else _parse(conversation.updated_at)


def is_legacy(conversation: Conversation, before: datetime | None = None) -> bool:
    before = before or cutoff()
    if before is None:
        return False
    received = received_at(conversation)
    return received is not None and received < before


def legacy_ids(store: InquiryStore) -> set[int]:
    """옛 자료로 표시할 문의 번호 (설정이 없으면 빈 집합)."""
    before = cutoff()
    if before is None:
        return set()
    return {c.id for c in store.all() if is_legacy(c, before)}
