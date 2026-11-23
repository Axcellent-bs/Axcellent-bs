"""③-쓰기 판정 2 (W2): 돈이 걸린 쓰기는 safety 실행 기록으로 관문 통과를 확인한다.

설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md` "변환기는 관문을 대신하지
않는다 — 어떻게 막나" 2번.

- 요청 본문의 `context_id`로 safety 실행 기록(`GET /api/safety/executions?context_id=`)을 읽고,
  같은 `idempotency_key`·테넌트의 기록을 찾는다. 도메인은 `execute()`와 shop-api 쓰기에 같은 키를
  쓴다(고객지원 `claim_handlers.process_claim_queue`, 마케팅 `handlers.issue_coupon`).
  테넌트는 경로의 회사로 본다(`request_tenant`): 다른 회사 요청의 기록이면 `tenant_mismatch`로 거절.
  비교는 양쪽을 정식 ID로 바꿔서 한다(`same_company`, `shop-demo` = `catbooks`)
  본문에 `execution_id`가 없어 하나를 바로 찾는 경로(`/executions/{execution_id}`)로는 찾지 못한다
- 통과 조건: 기록의 `action`이 동작에 맞고(`refund_request`·`issue_coupon`), 기록을 낸 서비스가
  보낸 서비스와 같고, `decision.status == "executed"`, 금액이 기록 `params`와 같음
  (환불 `refund_amount`, 쿠폰 `count`·`unit_value`·그 곱(`total_value`가 있으면)·`buyer_id`)
- 하나라도 아니면 403 `GATE_NOT_PASSED`. safety에 닿지 못하거나 응답이 이상하면 503
  `GATE_UNAVAILABLE` (둘 다 통과 아님: 고객사에도 할 일함에도 넣지 않는다)
- safety GET은 지금 토큰 없이 받지만 변환기는 dashboard-api 에이전트 토큰을 붙여 부른다
- 로그에는 동작·서비스·까닭 코드·`execution_id`만 남긴다(멱등 키·본문 원문은 남기지 않는다)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ax_agent_template import company_scope, config
from ax_agent_template.companies import canonical_company_id
from ax_contracts.schemas import TENANT_ID_DEFAULT
from ax_dashboard_api.envelope import ApiError

TOKEN_NAME = "dashboard-api"
TIMEOUT = 10.0
# 시험이 safety 응답을 흉내 낼 때 바꾼다 (httpx.MockTransport)
TRANSPORT: httpx.AsyncBaseTransport | None = None

# 쓰기 동작 → 그 쓰기를 허락하는 safety 실행 동작 (도메인의 `execute()` 호출 이름)
SAFETY_ACTION: dict[str, str] = {"refund": "refund_request", "coupon_issue": "issue_coupon"}

logger = logging.getLogger("ax.site_adapter")


def _not_passed(action: str, service: str, why: str, execution_id: str | None = None) -> ApiError:
    logger.warning(
        "관문 기록 확인 실패: action=%s service=%s why=%s execution=%s",
        action,
        service,
        why,
        execution_id,
    )
    return ApiError(
        403,
        "GATE_NOT_PASSED",
        "안전 관문을 통과한 실행 기록과 맞지 않아 사이트에 쓰지 않았어요",
    )


def _unavailable(action: str, service: str, why: str) -> ApiError:
    logger.warning("관문 기록을 확인하지 못함: action=%s service=%s why=%s", action, service, why)
    return ApiError(
        503,
        "GATE_UNAVAILABLE",
        "안전 관문 기록을 확인하지 못해 사이트에 쓰지 않았어요. 잠시 뒤 다시 보내 주세요",
    )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def amount_matches(action: str, claimed: dict[str, Any], params: dict[str, Any]) -> bool:
    """보낸 금액이 실행 기록 `params`와 같은지."""
    if action == "refund":
        amount = _int(claimed.get("refund_amount"))
        return amount is not None and amount == _int(params.get("refund_amount"))
    count, unit = _int(claimed.get("count")), _int(claimed.get("unit_value"))
    if count is None or unit is None:
        return False
    if (count, unit) != (_int(params.get("count")), _int(params.get("unit_value"))):
        return False
    if "total_value" in params and _int(params.get("total_value")) != count * unit:
        return False
    return claimed.get("buyer_id") == params.get("buyer_id")


def request_tenant() -> str:
    """이 변환기 요청의 회사가 safety 기록·할 일함에 남는 테넌트 값.

    캣북스(경로에 회사 없음)는 예전 그대로 `TENANT_ID`(기본 shop-demo). 다른 회사
    (`/site-adapter/<회사 ID>`)는 그 회사 ID = 그 회사 AI 요청의 `meta.tenant_id`
    (`execute()`가 safety 기록에 그대로 싣는다).
    """
    cid = company_scope.active()
    if cid is None or cid == company_scope.CATBOOKS:
        return os.getenv("TENANT_ID", TENANT_ID_DEFAULT)
    return cid


def same_company(recorded: Any, tenant: str) -> bool:
    """기록의 테넌트와 경로의 회사가 같은 회사인지. 별칭(`shop-demo`)은 정식 ID로 바꿔 비교.

    캣북스 AI가 정식 ID로 보내도, 설치 값 `TENANT_ID`가 옛 별칭이어도 같은 회사로 본다.
    형식이 틀린 값은 어느 회사와도 같지 않다.
    """
    left, right = canonical_company_id(recorded), canonical_company_id(tenant)
    return left is not None and left == right


async def _records(context_id: str, action: str, service: str) -> list[Any]:
    try:
        token = config.agent_token(TOKEN_NAME)
        url = f"{config.agent_url('safety').rstrip('/')}/api/safety/executions"
    except (RuntimeError, KeyError):
        raise _unavailable(action, service, "no_token_or_url") from None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT) as client:
            response = await client.get(
                url,
                params={"context_id": context_id},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        raise _unavailable(action, service, "unreachable") from None
    if response.status_code != 200:
        raise _unavailable(action, service, f"status_{response.status_code}")
    try:
        data = response.json().get("data")
    except (ValueError, AttributeError):
        data = None
    if not isinstance(data, list):
        raise _unavailable(action, service, "bad_body")
    return data


async def confirm(
    action: str,
    service: str,
    context_id: str | None,
    idempotency_key: str,
    claimed: dict[str, Any],
) -> str:
    """판정 2: 통과면 확인한 safety `execution_id`. 아니면 403·503 `ApiError`."""
    if not context_id:
        raise _not_passed(action, service, "no_context_id")
    tenant = request_tenant()
    found: dict[str, Any] | None = None
    other_company = False
    for record in await _records(context_id, action, service):
        request = record.get("request") if isinstance(record, dict) else None
        if not isinstance(request, dict):
            continue
        if (
            request.get("idempotency_key") == idempotency_key
            and request.get("context_id") == context_id
        ):
            if same_company(request.get("tenant_id", TENANT_ID_DEFAULT), tenant):
                found = record  # 테넌트마다 키는 하나다 (safety `by_key`)
            else:
                other_company = True
    if found is None:
        # 같은 키의 기록이 다른 회사 요청(meta.tenant_id)이면: 경로의 회사와 달라 거절
        raise _not_passed(action, service, "tenant_mismatch" if other_company else "no_record")
    request = found["request"]
    decision = found.get("decision")
    execution_id = found.get("execution_id")
    execution_id = execution_id if isinstance(execution_id, str) else None
    if request.get("action") != SAFETY_ACTION[action]:
        raise _not_passed(action, service, "action_mismatch", execution_id)
    if request.get("requester") != service:
        raise _not_passed(action, service, "requester_mismatch", execution_id)
    if not isinstance(decision, dict) or decision.get("status") != "executed":
        raise _not_passed(action, service, "not_executed", execution_id)
    params = request.get("params")
    if not isinstance(params, dict) or not amount_matches(action, claimed, params):
        raise _not_passed(action, service, "amount_mismatch", execution_id)
    if execution_id is None:
        raise _not_passed(action, service, "no_execution_id")
    return execution_id
