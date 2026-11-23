"""기획 → 개발 넘기기: planning `start_dev` → dev 서비스, dev `receive_dev_result` → planning.

기획(planning 8101)과 개발(dev 8111)은 따로 뜬 서비스라 상태(메모리)가 나뉜다. 그래서 확정된
기획서와 작업을 dev로 보내고, dev가 만든 PR·작업 상태·요청 상태를 planning으로 돌려준다.

- 기획(`PlanningService`)과 개발(`DevService`) 사이에서 보낸다 (분리 4단계부터 합친 모드는 없다).
- 결과 통지(`result_notify`)와 달리 목 모드에서도 보낸다. 두 서비스가 같이 떠 있어야 이어진다.
- 받는 서비스는 `call_agent`가 skill로 정한다(`route_target`: start_dev → dev, receive_dev_result
  → planning). 원래 요청 meta를 쓰지 않고 `path`를 비운 새 요청으로 보낸다(LOOP_DETECTED 방지).
- 실패는 상태 변경을 되돌리지 않는다. 서비스가 결과 한 줄을 활동에 남긴다.
- 실패는 둘로 나눈다(`classify`). 받는 쪽이 답하고 거절한 것(`invalid_input`·`rejected` 등)은
  다시 보내도 같아서 자동으로 다시 보내지 않고(사람이 고친 뒤 다시 보내기), 닿지 못한 것
  (연결 안 됨·시간 초과·5xx)만 간격을 늘려 가며 다시 보낸다 (11/23, 메신저에 "start dev: failed"가
  30초마다 거듭 쌓이던 문제).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from ax_agent_template.client import call_agent
from ax_agent_template.services import BY_ID
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus

START_DEV = "start_dev"
RECEIVE_DEV_RESULT = "receive_dev_result"
ROLES = ("planning", "dev")
# 개발 AI 대화가 기획에 넘긴 변경 요청의 source_ref 머리 (뒤에 대화 id). 기획은 이 요청을
# 간단 요청·자동 확정으로 돌리지 않고 사람이 기획서를 확정해야 개발로 넘긴다 (11/09)
DEV_CHAT_REF = "dev_chat:"
CALL_TIMEOUT_SECONDS = 10

SENT, TRANSIENT, PERMANENT, HANDED = "sent", "transient", "permanent", "handed"
# 닿지 못한 실패 (call_agent가 만든 코드·받는 쪽 처리 중 오류·할 일함에 못 넣음). 다시 보낸다
TRANSIENT_OUTCOMES = frozenset(
    {"unreachable", "handler_error", "timeout", "service_disabled", "inbox_unreachable"}
)
# 받는 쪽이 꺼져 있어 사람 할 일함으로 넘어감 등 (거절이 아니다). 주기적으로 다시 보내지는 않고
# 다시 켰을 때 맞추기가 다시 보낸다 (받는 쪽·할 일함은 멱등 키로 거른다)
ACCEPTED_STATUSES = frozenset({"input-required", "submitted", "working"})
# 받는 쪽 거절 중 원인이 보내는 쪽 밖에 있을 수 있는 것: 활동에 확인할 곳을 덧붙인다
PERMANENT_HINTS = {
    "invalid_output": "받는 쪽 응답 형식이 맞지 않음, 두 서비스 버전 확인",
    "unauthorized": "토큰·권한 확인",
}
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_LINE = re.compile(r"^보내지 못함 \((\S+)(?: (.*))?\)$")


class SendResult(str):
    """보낸 결과 한 줄(활동 기록용 글)에 받는 쪽 status·outcome을 붙인 값. 글자로 그대로 쓴다."""

    status: str
    outcome: str

    def __new__(cls, text: str, status: str = "", outcome: str = "") -> SendResult:
        obj = super().__new__(cls, text)
        obj.status = status
        obj.outcome = outcome
        return obj


def classify(how: str) -> str:
    """보낸 결과 → `SENT` · `TRANSIENT`(다시 보냄) · `PERMANENT`(받는 쪽이 거절, 다시 보내지 않음) ·
    `HANDED`(사람 할 일함으로 넘어감, 다시 켰을 때 맞추기만 다시 보냄).

    `SendResult`면 status·outcome으로, 그냥 글이면 `보내지 못함 (status outcome)`을 읽어 정한다.
    읽지 못하는 글(보내다 난 예외 `보내지 못함 (오류)` 등)은 닿지 못한 것으로 본다."""
    if how.startswith("보냄"):
        return SENT
    if isinstance(how, SendResult) and how.status:
        status, outcome = how.status, how.outcome
    else:
        m = _LINE.match(how.strip())
        if m is None:
            return TRANSIENT
        status, outcome = m.group(1), (m.group(2) or "").strip()
    return _classify(status, outcome)


def _classify(status: str, outcome: str) -> str:
    code = outcome.lower()
    if status == "completed":
        return SENT
    if status == "canceled" or code in TRANSIENT_OUTCOMES:
        return TRANSIENT  # 시간 초과·연결 안 됨·받는 쪽 처리 중 오류·꺼져 있음
    if code.startswith("http_"):
        n = code.removeprefix("http_")
        return TRANSIENT if n.startswith("5") or n in ("408", "429") else PERMANENT
    if status in ACCEPTED_STATUSES or code == "handed_to_human":
        return HANDED
    if status == "rejected":
        return PERMANENT
    if _CODE.match(outcome):
        return PERMANENT  # 받는 쪽이 코드로 답함 (invalid_input, gate_rejected, not_found …)
    return TRANSIENT  # 코드가 아닌 모르는 실패는 간격을 늘려 가며 다시 본다


def permanent_hint(how: str) -> str:
    """거절 사유에 덧붙일 확인할 곳 (없으면 빈 글)."""
    outcome = how.outcome if isinstance(how, SendResult) else how
    low = outcome.lower()
    return next((v for k, v in PERMANENT_HINTS.items() if k in low), "")


def fingerprint(body: dict) -> str:
    """넘기기 입력의 지문. 다시 켰을 때 맞추기가 같은 입력으로 거절된 건을 건너뛰는 데 쓴다."""
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# (skill, context_id, tenant_id, 입력, idempotency_key) → 보낸 결과 한 줄 (활동 기록용)
DevSender = Callable[[str, str, str, BaseModel, str], Awaitable[str]]


def start_dev_key(context_id: str, spec_id: str) -> str:
    return f"{context_id}:start_dev:{spec_id}"


def dev_result_key(context_id: str, spec_id: str, fingerprint: str) -> str:
    return f"{context_id}:dev_result:{spec_id}:{fingerprint}"


async def send_to_service(
    skill: str, context_id: str, tenant_id: str, payload: BaseModel, idempotency_key: str
) -> str:
    """skill을 맡은 쪽으로 보낸다. 개발 skill(start_dev 등)은 dev, 나머지는 기획(planning)."""
    meta = RequestMeta(
        context_id=context_id,
        tenant_id=tenant_id,
        token="replaced-by-call_agent",
        path=[],
        deadline=datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS),
        idempotency_key=idempotency_key,
    )
    target = "dev" if skill in BY_ID["dev"].skills else "planning"
    out = await call_agent(
        target, AgentRequest(meta=meta, skill=skill, input=payload.model_dump(mode="json"))
    )
    if out.status == TaskStatus.COMPLETED:
        return SendResult("보냄", out.status.value, out.outcome or "")
    text = f"보내지 못함 ({out.status.value} {out.outcome or ''})".strip()
    return SendResult(text, out.status.value, out.outcome or "")
