"""판매자 정산 (고객지원 > 판매자 정산 화면).

판매자 정산(정산금 계산 · 지급 승인 요청 · 묶음 상태 · 지급 기한 점검)은 고객지원(support,
settlement 서비스)이 한다. 재무는 결과를 받아 보여 주기만 한다.
이 창구는 support skill을 A2A로 부른다.

- `GET /api/dashboard/settlements?period=YYYY-Www`: `seller_settlement` (생략하면 지난주)
- `GET /api/dashboard/settlements/deadlines`: `seller_settlement_deadlines`
- `POST /api/dashboard/settlements/batch` `{period}`: `request_seller_settlement`. 사람 토큰이
  필요하고 요청한 사람(`requested_by`)은 토큰의 이름이다(본문에서 받지 않는다). support가
  `execute("confirm_settlement")`로 안전 장치에 지급 승인을 요청하고 안전 장치는 항상 사람에게
  보류한다(C). 돈은 여기서 움직이지 않는다. 평소에는 고객지원 AI가 지급일이 된 주를 스스로 요청하고
  (`settlement_auto.py`), 이 버튼은 사람이 먼저 올리고 싶을 때 쓴다. 승인은 고객지원 담당이 한다.

응답: `data`는 support 출력에서 `meta`를 뺀 것, support의 `meta`(수집 시각·출처·받지 못한 값)는
응답 `meta`에 합친다. 꺼진 서비스는 빈 값으로 바꾸지 않고 503을 그대로 낸다(정산할 판매자가 없다고
지어내지 않는다).

오류: `invalid_input`→400 `INVALID_INPUT`, `nothing_to_settle`→409 `NOTHING_TO_SETTLE`,
`period_not_closed`(기간이 아직 끝나지 않은 주)→409 `PERIOD_NOT_CLOSED`,
`orders_partial`(판매 서버 주문 일부를 못 읽음, 지급 요청만 막는다)→503 `ORDERS_PARTIAL`,
`safety_unavailable`→503 `SAFETY_UNAVAILABLE`, `settlement_preparing`(서버를 켠 뒤 첫 장부를
모으는 중)→503 `SETTLEMENT_PREPARING`. 그 밖은 `support_client.unwrap`과 같다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ax_dashboard_api import support_client
from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.envelope import ok

router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(require_human_for_writes)])

# support 실패 코드 → (HTTP 상태, 화면 오류 코드)
ERRORS: dict[str, tuple[int, str]] = {
    "invalid_input": (400, "INVALID_INPUT"),
    "nothing_to_settle": (409, "NOTHING_TO_SETTLE"),
    "period_not_closed": (409, "PERIOD_NOT_CLOSED"),
    "orders_partial": (503, "ORDERS_PARTIAL"),
    "safety_unavailable": (503, "SAFETY_UNAVAILABLE"),
    "settlement_preparing": (503, "SETTLEMENT_PREPARING"),
}
# "2026-W45" 모양. 형식은 support가 확인하고, 여기서는 지나치게 긴 값만 막는다
PERIOD_MAX = 16


class SettlementBatchBody(BaseModel):
    period: str | None = Field(default=None, max_length=PERIOD_MAX)


def _envelope(out: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in out.items() if k != "meta"}
    env = ok(data)
    env["meta"] = {**(out.get("meta") or {}), **env["meta"]}
    return env


@router.get("/settlements")
async def settlements(period: str | None = Query(default=None, max_length=PERIOD_MAX)) -> dict:
    out = await support_client.call("seller_settlement", {"period": period}, errors=ERRORS)
    return _envelope(out)


@router.get("/settlements/deadlines")
async def settlement_deadlines() -> dict:
    out = await support_client.call("seller_settlement_deadlines", {}, errors=ERRORS)
    return _envelope(out)


@router.post("/settlements/batch")
async def request_settlement(request: Request, body: SettlementBatchBody | None = None) -> dict:
    period = (body or SettlementBatchBody()).period
    out = await support_client.call(
        "request_seller_settlement",
        {"period": period, "requested_by": request.state.human},
        target=f"settlement:{period or 'last_week'}",
        errors=ERRORS,
    )
    settlement = dict(out.get("settlement") or {})
    meta = settlement.pop("meta", None) or {}
    env = ok({"settlement": settlement, "requested": bool(out.get("requested"))})
    env["meta"] = {**meta, **env["meta"]}
    return env
