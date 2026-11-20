"""비밀값 저장소 요청 적용 스킬(키 관리 화면). 값은 이 스킬에 오지 않는다.

- 화면(대시보드 API)이 새 값을 암호화해 대기 요청(`request_id`)으로 저장해 둔다.
  이 스킬은 요청 번호만 받아 안전 관문을 거치고, 통과하면 그 요청을 적용한다.
  안전 서버·A2A·이력에는 지문과 끝 글자만 간다.
- `add_secret`(A): 추가. 키 담당자가 바로. `change_secret`(C): 교체·삭제.
  요청자와 다른 사람의 승인이 필요하다(안전 관문이 막는다).
  승인 뒤 같은 요청을 다시 보내면 적용된다('승인됨 → 적용').
- 권한(secret_editor·담당 tenant)·로그인 세션 검사는 대시보드 API가 하고
  사람 이름(`updated_by`)은 토큰에서 채운다. 쓰기는 요청자가 dashboard-api일 때만(Agent Card).
- 결과·로그에는 사유 코드와 tenant·이름만 있다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, ValidationError

from ax_agent_template.execute import execute
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from ax_secret_store import errors, resolver
from ax_secret_store.errors import SecretStoreError

ADD = "add_secret"
CHANGE = "change_secret"


class ApplyInput(AxModel):
    request_id: str = Field(min_length=1, max_length=40)
    updated_by: str = Field(min_length=1, max_length=64)


def _result(request: AgentRequest, status: TaskStatus, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, outcome=outcome, output=dict(output))


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return _result(request, TaskStatus.FAILED, outcome, **output)


def _evidence(action: str, summary: str) -> Evidence:
    return Evidence(
        queries=[action],
        data_summary=summary,
        collected_at=datetime.now(UTC),
        ref_ids=["secret_store"],
    )


async def apply_request(request: AgentRequest, *, wanted: str, from_decision: Any) -> AgentResult:
    """`wanted`: ADD | CHANGE. 요청 번호의 동작과 스킬이 맞아야 한다."""
    try:
        data = ApplyInput.model_validate(request.input)
    except ValidationError as error:
        errs = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
        return _fail(request, "invalid_input", errors=errs)
    if not resolver.is_db_mode():
        return _fail(request, "store_disabled")
    try:
        svc = resolver.service()
        pending = svc.pending(data.request_id)
    except SecretStoreError as error:
        return _fail(request, error.code)
    expected = ADD if pending.op == "add" else CHANGE
    if expected != wanted:
        return _fail(request, errors.REQUEST_MISMATCH)
    params = svc.gate_params(pending)  # 평문·암호문 없음
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
    key = f"{request.meta.context_id}:{wanted}:{digest[:24]}"
    summary = f"비밀 저장소 {pending.op} {pending.tenant}/{pending.name} ({pending.requested_by})"
    ev = _evidence(wanted, summary)
    decision = await execute(
        wanted, params, request.meta.model_copy(update={"idempotency_key": key}), ev
    )
    blocked = from_decision(request, decision, ev)
    if blocked:
        review_id = (blocked.output or {}).get("review_id")
        if blocked.outcome == "held" and isinstance(review_id, str):
            try:
                svc.record_review(pending.id, review_id)
            except SecretStoreError as error:
                return _fail(request, error.code)
        return blocked
    try:
        done = svc.apply(pending.id, data.updated_by)
    except SecretStoreError as error:
        return _fail(request, error.code)
    resolver.invalidate()  # 이 프로세스 캐시는 바로 갱신, 다른 프로세스는 TTL 안에
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="applied",
        output=done,
        evidence=ev,
    )
