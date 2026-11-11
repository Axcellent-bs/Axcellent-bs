"""알림 받는 주소: 고객사 판매 서버(shop-api 규격)가 이벤트를 보내는 웹훅.

- `POST /api/dashboard/events`: 본문은 공통 봉투(`EventEnvelope`) 그대로다. 확인을 모두 통과하면
  이벤트 버스(`events.publish`)에 넣는다. 총괄·재무 등 받는 쪽은 지금처럼 버스를 구독하면 된다.
- 인증은 `Authorization: Bearer <회사 키>`. 회사 키는 `AX_COMPANY_KEYS="<tenant_id>:<키>,..."`
  (회사마다 하나, `.env`에만 둔다). 설정이 없으면 모두 거부한다(fail-closed). 사람 토큰·에이전트
  토큰은 회사 키가 아니다.
- 고객사가 보내는 값은 외부 입력이라 아래만 받는다.
  - 봉투의 `tenant_id`가 키의 회사와 같아야 한다(403 `TENANT_MISMATCH`).
  - 판매 서버 이벤트 종류만(`ALLOWED_TYPES`, 422 `EVENT_TYPE_NOT_ALLOWED`).
    사건·안전 등 내부 이벤트는 못 넣는다.
  - `source`는 `shop-api`만(422 `EVENT_SOURCE_NOT_ALLOWED`).
  - 사건 번호(`context_id`)는 총괄이 발급하므로 비어 있어야 한다(422 `CONTEXT_ID_NOT_ALLOWED`).
  - `data`는 종류별 계약 모델에 맞아야 한다(422 `EVENT_DATA_INVALID`).
- 같은 `event_id`는 버스가 한 번만 쌓는다(`events.publish`). 그래서 판매 서버가 다시 보내도 된다.
- 버스에 넣지 못하면 503을 돌려준다. 판매 서버 outbox는 미발행으로 남겨 다음에 다시 보낸다.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Body, Header
from pydantic import ValidationError

from ax_agent_template import events
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import EVENT_DATA_MODELS
from ax_dashboard_api.auth import bearer
from ax_dashboard_api.envelope import ApiError, ok

KEYS_ENV = "AX_COMPANY_KEYS"
SHOP_SOURCE = "shop-api"

# SHOP-05: 판매 서버가 발행하는 이벤트 (1차·2차)
ALLOWED_TYPES = frozenset(
    {
        EventType.ORDER_CREATED,
        EventType.ORDER_CANCELLED,
        EventType.ORDER_DELIVERED,
        EventType.PAYMENT_SUCCEEDED,
        EventType.PAYMENT_FAILED,
        EventType.PAYMENT_REFUNDED,
        EventType.SHIPMENT_DELAYED,
        EventType.RETURN_RECEIVED,
        EventType.SELLER_REGISTERED,
        EventType.PRODUCT_CREATED,
        EventType.SHOP_DEGRADED,
        EventType.SHOP_RECOVERED,
    }
)

# 회사 키로 들어오는 창구: 사람 토큰 대신 회사 키를 본다
router = APIRouter(prefix="/api/dashboard")


def _configured() -> list[tuple[str, str]]:
    """(키, 회사) 목록. 형식이 틀린 항목은 버린다."""
    pairs: list[tuple[str, str]] = []
    for item in (os.getenv(KEYS_ENV) or "").split(","):
        tenant, sep, key = item.strip().partition(":")
        if sep and tenant.strip() and key.strip():
            pairs.append((key.strip(), tenant.strip()))
    return pairs


def tenant_from_authorization(authorization: str | None) -> str:
    """회사 키를 확인해 회사(tenant_id)를 돌려준다. 키가 없으면 401, 모르는 키면 403."""
    token = bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "회사 키가 필요하다 (Authorization: Bearer 회사 키)")
    found: str | None = None
    for key, tenant in _configured():
        if hmac.compare_digest(key.encode(), token.encode()):
            found = tenant
    if found is None:
        raise ApiError(403, "COMPANY_KEY_INVALID", "회사 키를 확인할 수 없다")
    return found


def _check(tenant: str, body: dict[str, Any]) -> EventEnvelope:
    try:
        envelope = EventEnvelope.model_validate(body)
    except ValidationError as error:
        raise ApiError(
            422, "EVENT_INVALID", f"이벤트 봉투 형식이 아니다: {error.error_count()}곳"
        ) from None
    if envelope.tenant_id != tenant:
        raise ApiError(403, "TENANT_MISMATCH", "회사 키와 이벤트의 회사가 다르다")
    if envelope.type not in ALLOWED_TYPES:
        raise ApiError(
            422, "EVENT_TYPE_NOT_ALLOWED", f"판매 서버 이벤트가 아니다: {envelope.type.value}"
        )
    if envelope.source != SHOP_SOURCE:
        raise ApiError(422, "EVENT_SOURCE_NOT_ALLOWED", f"source는 {SHOP_SOURCE}만 받는다")
    if envelope.context_id is not None:
        raise ApiError(422, "CONTEXT_ID_NOT_ALLOWED", "사건 번호는 총괄이 발급한다 (비워서 보낸다)")
    model = EVENT_DATA_MODELS.get(envelope.type)
    if model is not None:
        try:
            model.model_validate(envelope.data)
        except ValidationError as error:
            raise ApiError(
                422,
                "EVENT_DATA_INVALID",
                f"{envelope.type.value}의 data 형식이 아니다: {error.error_count()}곳",
            ) from None
    return envelope


@router.post("/events", status_code=202)
async def receive_event(
    body: dict[str, Any] = Body(...),  # noqa: B008 - FastAPI 본문 선언
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    tenant = tenant_from_authorization(authorization)
    envelope = _check(tenant, body)
    try:
        await events.publish(envelope)
    except Exception as error:  # noqa: BLE001 - 버스가 없으면 판매 서버가 다시 보내게 알린다
        raise ApiError(
            503, "EVENT_BUS_UNAVAILABLE", f"이벤트 버스에 넣지 못했다: {type(error).__name__}"
        ) from None
    return ok({"event_id": envelope.event_id, "accepted": True})
