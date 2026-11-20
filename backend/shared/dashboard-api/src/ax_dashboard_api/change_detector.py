"""변화 감지기: 고객사 판매 기록을 주기적으로 읽어, 바뀐 것만 내부 이벤트로 만든다.

설계: `docs/superpowers/specs/2026-11-19-customer-change-polling-design.md` (PR 1: 뼈대).
고객사가 웹훅을 보내지 않아도(고객사는 가만히 있어요) 같은 모양의 이벤트가 버스에 들어가게 한다.

한 회차: `GET {agent_url("shop-api")}/api/shop/sales-export?cursor&limit`를 `has_more=false`가
될 때까지 읽고, 줄마다 직전 상태 표의 같은 주문과 비교한다.

| 이벤트 | 감지 규칙 (직전 → 지금) | 지금 |
|---|---|---|
| `payment.refunded` | `refunds`에 직전에 없던 `refund_id` / id 없는 환불은 누적 금액 증가분 | 켬 |
| `order.cancelled` | `status`가 취소 아님 → 취소. `cancel_reason_code`가 있을 때만 | 켬 |
| `order.created` | 직전 표에 없던 주문 (`items[].seller_id`를 판매 기록으로 못 채움) | 꺼 둠 |
| `payment.succeeded` | `payment_status`가 `succeeded`로 | 꺼 둠 |
| `payment.failed` | `payment_status`가 `failed`로 (`failure_code`를 못 채움) | 꺼 둠 |

- 스위치 (모두 기본 꺼짐, 지금 동작이 바뀌지 않는다)
  - `AX_CHANGE_DETECTOR_SECONDS`: 주기(초). 비우거나 `0`이면 돌지 않는다. 최소 30초
  - `AX_CHANGE_DETECTOR_PUBLISH`: `1`이면 버스에 넣는다. 그 밖은 **그림자 모드**: 버스에 넣지
    않고 "내보냈을 이벤트"만 상태 파일(`shadow`)과 로그에 남긴다. 캣북스 outbox 웹훅이 살아
    있는 동안은 그림자 모드로 맞춰 본다(같은 환불이 두 길로 와 두 번 쌓이지 않게)
  - `AX_CHANGE_DETECTOR_DIR`: 상태 파일 폴더(기본 `backend/.data/change_detector/`)
  - `AX_CHANGE_DETECTOR_MAX_CALLS`: 한 회차 판매 서버 호출 상한(기본 30). 넘으면 커서를 저장하고
    다음 회차에 이어 읽는다. 상한은 판매 기록 쪽을 읽기 전에만 본다(한 쪽 안의 주문 상세 읽기는
    그 쪽을 다 처리할 때까지 센다)
- 처음 켤 때(상태 파일이 없거나 연결이 바뀌었을 때): 끝까지 읽어 상태 표만 채우고 이벤트를
  내보내지 않는다. 다 읽으면 `baseline_at`을 남긴다
- 쪽마다 이벤트를 다 넣은 뒤에 커서·상태 표를 저장한다. 중간에 실패하면 그 쪽을 다시 읽고,
  `event_id`가 변화 내용으로 정해져 있어 버스가 같은 이벤트를 두 번 쌓지 않는다
  (`chg:<회사>:payment.refunded:<주문>:<환불>`, `chg:<회사>:order.cancelled:<주문>`)
- 결제 id(`PaymentRefundedData.payment_id`, 필수)는 바뀐 주문만 `GET /api/shop/orders/{id}`로
  읽어 채운다. 없으면 재무 `books.py`와 같은 규칙(`pay_of_<주문>`, `rfd_of_<주문>_<누적금액>`)
- 취소 사유(`reason_code`)는 판매 기록의 `cancel_reason_code`만 쓴다. 비어 있으면 이벤트를
  만들지 않고 `skipped_no_reason` 건수만 남긴다(지어내지 않는다)
- 캣북스(shop-api 직결)만 읽는다. 사이트 변환기 고객사는 변환기 보완(PR 2: 커서·`updated_since`·
  환불 줄) 전까지 읽지 않는다(`ADAPTER_NOT_YET`)
- 판매 서버에는 dashboard-api 토큰(`DASHBOARD_API_TOKEN` 또는 토큰 명부)을 붙인다
- 실패해도 서버는 안 죽고 다음 회차에 다시 한다. 같은 오류가 연달아 나면 로그는 한 줄만.
  잘림(`UPSTREAM_TRUNCATED` 503) 등 실패한 회차는 커서를 옮기지 않는다
- `GET /api/dashboard/change-detector/status`(사람 토큰): 켜짐·그림자 여부·마지막 성공 시각·
  마지막 오류·커서 시각·건너뜀 건수
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import ValidationError

from ax_agent_template import company_connection as store
from ax_agent_template import config, events
from ax_agent_template import tenant as tenants
from ax_contracts.data_access.shop_sales import SalesOrderRow, ShopSalesExportResult
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import OrderCancelledData, PaymentRefundedData
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ok

logger = logging.getLogger("ax.dashboard.change_detector")

SECONDS_ENV = "AX_CHANGE_DETECTOR_SECONDS"
PUBLISH_ENV = "AX_CHANGE_DETECTOR_PUBLISH"
DIR_ENV = "AX_CHANGE_DETECTOR_DIR"
MAX_CALLS_ENV = "AX_CHANGE_DETECTOR_MAX_CALLS"
DEFAULT_DIR = Path(__file__).resolve().parents[4] / ".data" / "change_detector"
STATE_FILE = "state.json"
MIN_SECONDS = 30
DEFAULT_MAX_CALLS = 30
PAGE_LIMIT = 500
TIMEOUT_SECONDS = 10.0
SHADOW_KEEP = 100  # 상태 파일에 남기는 최근 "내보냈을 이벤트" 수
SOURCE = "change-detector"
TOKEN_NAME = "dashboard-api"
# 판매 기록의 취소 상태 (캣북스 상태 값). 변환기 고객사는 상태 값 표로 같은 값에 맞춘다
CANCELLED_STATUSES = frozenset({"cancelled"})

ADAPTER_NOT_YET = "사이트 변환기 고객사는 변환기 보완 전이라 읽지 않아요"
NO_TENANT = "회사가 정해지지 않아 읽지 않아요 (회사 연결 확인)"
TOKEN_NOT_SET = "dashboard-api 토큰이 없어 판매 서버를 부를 수 없어요"

# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.AsyncBaseTransport | None = None

_lock = asyncio.Lock()
_last_logged: dict[str, Any] = {"error": None, "count": 0}


class DetectorError(Exception):
    """이번 회차를 실패로 두는 이유(문장은 화면·로그에 그대로 쓴다. 예외 원문은 넣지 않는다)."""


# ------------------------------------------------------------------ 스위치


def interval_seconds() -> int:
    """주기(초). 비었거나 0·잘못된 값이면 0(끔). 켜면 최소 `MIN_SECONDS`."""
    try:
        value = int((os.getenv(SECONDS_ENV) or "0").strip())
    except ValueError:
        return 0
    if value <= 0:
        return 0
    return max(value, MIN_SECONDS)


def publishing() -> bool:
    """`1`일 때만 버스에 넣는다. 그 밖은 그림자 모드."""
    return (os.getenv(PUBLISH_ENV) or "").strip() == "1"


def max_calls() -> int:
    try:
        value = int((os.getenv(MAX_CALLS_ENV) or "").strip() or DEFAULT_MAX_CALLS)
    except ValueError:
        return DEFAULT_MAX_CALLS
    return max(value, 1)


# ------------------------------------------------------------------ 상태 파일


def _state_path() -> Path:
    raw = (os.getenv(DIR_ENV) or "").strip()
    return (Path(raw) if raw else DEFAULT_DIR) / STATE_FILE


def _fresh(fingerprint: str | None) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "cursor": None,
        "cursor_at": None,
        "baseline_at": None,
        "orders": {},
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
        "skipped_no_reason": 0,
        "published_total": 0,
        "shadow_total": 0,
        "shadow": [],
    }


def _load() -> dict[str, Any]:
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _fresh(None)
    except (OSError, ValueError):
        logger.warning("변화 감지기 상태 파일을 읽지 못해 처음부터 다시 채운다 (%s)", path)
        return _fresh(None)
    state = _fresh(None)
    if isinstance(raw, dict):
        state.update(raw)
    return state


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ 감지


@dataclass
class RunResult:
    attempted_at: datetime
    pages: int = 0
    calls: int = 0
    events: list[EventEnvelope] = field(default_factory=list)
    skipped_no_reason: int = 0
    baseline: bool = False  # 처음 켤 때처럼 상태만 채운 회차
    finished: bool = False  # 끝까지 읽었는지
    error: str | None = None


@dataclass
class _Refund:
    refund_id: str
    amount: int
    at: datetime
    known_id: bool  # 판매 서버가 준 환불 id인지 (아니면 누적 금액으로 만든 id)


def _snapshot(row: SalesOrderRow) -> dict[str, Any]:
    return {
        "status": row.status,
        "payment_status": row.payment_status,
        "refund_ids": sorted({r.refund_id for r in row.refunds if r.refund_id}),
        "idless_total": sum(r.amount for r in row.refunds if not r.refund_id),
        "updated_at": _iso(row.updated_at),
    }


def _new_refunds(prev: dict[str, Any], row: SalesOrderRow) -> list[_Refund]:
    seen = set(prev.get("refund_ids") or [])
    found = [
        _Refund(r.refund_id, r.amount, r.refunded_at, True)
        for r in row.refunds
        if r.refund_id and r.refund_id not in seen
    ]
    idless = [r for r in row.refunds if not r.refund_id]
    total = sum(r.amount for r in idless)
    grown = total - int(prev.get("idless_total") or 0)
    if idless and grown > 0:
        at = max(r.refunded_at for r in idless)
        found.append(_Refund(f"rfd_of_{row.order_id}_{total}", grown, at, False))
    return found


def _cancelled_amount(row: SalesOrderRow) -> int:
    """주문 결제 금액에서 이미 환불된 몫을 뺀다 (계산식 고정)."""
    paid = row.items_amount + row.shipping_fee - row.points_used - row.coupon_discount
    return paid - sum(r.amount for r in row.refunds)


def _envelope(
    tenant: str, kind: EventType, key: str, at: datetime, data: dict[str, Any]
) -> EventEnvelope:
    event_id = f"chg:{tenant}:{kind.value}:{key}"
    return EventEnvelope(
        event_id=event_id,
        type=kind,
        occurred_at=at,
        tenant_id=tenant,
        source=SOURCE,
        idempotency_key=event_id,
        data=data,
    )


def _payment_id(order_id: str, refund: _Refund, detail: dict[str, Any] | None) -> str:
    if detail:
        for r in detail.get("refunds") or []:
            if (
                isinstance(r, dict)
                and r.get("refund_id") == refund.refund_id
                and r.get("payment_id")
            ):
                return str(r["payment_id"])
        payments = [p for p in detail.get("payments") or [] if isinstance(p, dict)]
        for p in payments:
            if p.get("payment_id") and p.get("status") == "succeeded":
                return str(p["payment_id"])
        for p in payments:
            if p.get("payment_id"):
                return str(p["payment_id"])
    return f"pay_of_{order_id}"


class _Shop:
    """판매 서버 호출 (호출 수를 센다)."""

    def __init__(self, client: httpx.AsyncClient, base: str, token: str) -> None:
        self.client = client
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"}
        self.calls = 0

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        self.calls += 1
        try:
            return await self.client.get(f"{self.base}{path}", params=params, headers=self.headers)
        except httpx.HTTPError as error:
            raise DetectorError(f"판매 서버에 닿지 못했어요 ({type(error).__name__})") from None

    @staticmethod
    def _failure(res: httpx.Response) -> DetectorError:
        code = ""
        try:
            error = res.json().get("error") or {}
            code = str(error.get("code") or "") if isinstance(error, dict) else ""
        except (ValueError, AttributeError):
            pass
        tail = f" {code}" if code else ""
        return DetectorError(f"판매 서버 응답 HTTP {res.status_code}{tail}")

    async def sales_export(self, cursor: str | None) -> ShopSalesExportResult:
        params: dict[str, Any] = {"limit": PAGE_LIMIT}
        if cursor:
            params["cursor"] = cursor
        res = await self._get("/api/shop/sales-export", params)
        if res.status_code != 200:
            raise self._failure(res)
        try:
            return ShopSalesExportResult.model_validate(res.json().get("data"))
        except (ValueError, AttributeError, ValidationError):
            raise DetectorError("판매 기록 응답 형식이 계약과 달라요") from None

    async def order_detail(self, order_id: str) -> dict[str, Any] | None:
        res = await self._get(f"/api/shop/orders/{order_id}")
        if res.status_code == 404:
            return None
        if res.status_code != 200:
            raise self._failure(res)
        try:
            data = res.json().get("data")
        except (ValueError, AttributeError):
            raise DetectorError("주문 응답 형식이 달라요") from None
        return data if isinstance(data, dict) else None


async def _changes(
    shop: _Shop, tenant: str, row: SalesOrderRow, prev: dict[str, Any] | None
) -> tuple[list[EventEnvelope], int]:
    """한 주문의 새 변화 → (이벤트, 사유 없어 건너뛴 취소 수)."""
    before = prev or {}
    out: list[EventEnvelope] = []
    skipped = 0
    refunds = _new_refunds(before, row)
    if refunds:
        detail = await shop.order_detail(row.order_id)
        for refund in refunds:
            data = PaymentRefundedData(
                payment_id=_payment_id(row.order_id, refund, detail),
                order_id=row.order_id,
                refund_id=refund.refund_id,
                refund_amount=refund.amount,
            )
            key = f"{row.order_id}:{refund.refund_id}"
            out.append(_envelope(tenant, EventType.PAYMENT_REFUNDED, key, refund.at,
                                 data.model_dump(mode="json")))  # fmt: skip
    was_cancelled = before.get("status") in CANCELLED_STATUSES
    if row.status in CANCELLED_STATUSES and not was_cancelled:
        if row.cancel_reason_code is None:
            skipped += 1
        else:
            data = OrderCancelledData(
                order_id=row.order_id,
                reason_code=row.cancel_reason_code,
                cancelled_amount=_cancelled_amount(row),
            )
            out.append(_envelope(tenant, EventType.ORDER_CANCELLED, row.order_id, row.updated_at,
                                 data.model_dump(mode="json")))  # fmt: skip
    return out, skipped


# ------------------------------------------------------------------ 한 회차


def _fingerprint(connection: store.Connection | None, tenant: str) -> str:
    kind = connection.kind if connection else "shop_api"
    return f"{kind}|{config.agent_url('shop-api')}|{tenant}"


def _log_error(error: str | None) -> None:
    """같은 오류가 연달아 나면 한 줄만. 바뀌거나 회복하면 몇 번 연속이었는지 적는다."""
    last, count = _last_logged["error"], _last_logged["count"]
    if error == last:
        _last_logged["count"] = count + 1
        return
    if last is not None and count > 1:
        logger.warning("변화 감지기: 앞 오류가 %d번 연속이었다 (%s)", count, last)
    if error is not None:
        logger.warning("변화 감지기 실패: %s", error)
    elif last is not None:
        logger.info("변화 감지기 회복")
    _last_logged["error"], _last_logged["count"] = error, 1


def _fail(state: dict[str, Any], result: RunResult, message: str) -> RunResult:
    result.error = message
    state["last_attempt_at"] = _iso(result.attempted_at)
    state["last_error"] = message
    try:
        _save(state)
    except OSError:
        logger.warning("변화 감지기 상태를 저장하지 못했다", exc_info=True)
    _log_error(message)
    return result


async def run_once(now: datetime | None = None) -> RunResult:
    """커서부터 끝까지(또는 호출 상한까지) 읽고 바뀐 것을 이벤트로 만든다. 예외를 내지 않는다."""
    async with _lock:
        return await _run(now)


async def _run(now: datetime | None) -> RunResult:
    attempted = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    result = RunResult(attempted)
    state = _load()
    connection = store.read_connection()
    if connection is not None and connection.kind == "adapter":
        return _fail(state, result, ADAPTER_NOT_YET)
    tenant = tenants.current()
    if not tenant:
        return _fail(state, result, NO_TENANT)
    try:
        token = config.agent_token(TOKEN_NAME)
    except RuntimeError:
        return _fail(state, result, TOKEN_NOT_SET)
    fingerprint = _fingerprint(connection, tenant)
    if state.get("fingerprint") != fingerprint:
        if state.get("fingerprint") is not None:
            logger.info("회사 연결이 바뀌어 변화 감지기 상태를 새로 채운다")
        state = _fresh(fingerprint)
    result.baseline = state.get("baseline_at") is None
    publish = publishing()
    limit = max_calls()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=TRANSPORT) as client:
            shop = _Shop(client, config.agent_url("shop-api"), token)
            try:
                await _pages(shop, state, result, tenant, publish, limit)
            finally:
                result.calls = shop.calls
    except DetectorError as error:
        return _fail(state, result, str(error))
    except Exception as error:  # noqa: BLE001 - 감지기는 어떤 오류에도 다음 회차를 돈다
        return _fail(state, result, f"변화 감지 중 오류 ({type(error).__name__})")
    state["last_attempt_at"] = state["last_success_at"] = _iso(attempted)
    state["last_error"] = None
    _save(state)
    _log_error(None)
    return result


async def _pages(
    shop: _Shop,
    state: dict[str, Any],
    result: RunResult,
    tenant: str,
    publish: bool,
    limit: int,
) -> None:
    orders: dict[str, Any] = state["orders"]
    while shop.calls < limit:
        cursor = state.get("cursor")
        page = await shop.sales_export(cursor)
        result.pages += 1
        found: list[EventEnvelope] = []
        skipped = 0
        if not result.baseline:
            for row in page.orders:
                made, missed = await _changes(shop, tenant, row, orders.get(row.order_id))
                found.extend(made)
                skipped += missed
        await _emit(state, found, publish)
        # 이벤트를 다 넣은 뒤에만 이 쪽의 상태 표·커서를 옮긴다
        for row in page.orders:
            orders[row.order_id] = _snapshot(row)
        if page.orders:
            state["cursor_at"] = _iso(page.orders[-1].updated_at)
        if page.next_cursor:
            state["cursor"] = page.next_cursor
        state["skipped_no_reason"] = int(state.get("skipped_no_reason") or 0) + skipped
        result.events.extend(found)
        result.skipped_no_reason += skipped
        if not page.has_more:
            result.finished = True
            if result.baseline:
                state["baseline_at"] = _iso(result.attempted_at)
            _save(state)
            return
        if not page.next_cursor or page.next_cursor == cursor:
            raise DetectorError("판매 서버가 더 있다면서 다음 커서를 주지 않았어요")
        _save(state)


async def _emit(state: dict[str, Any], found: list[EventEnvelope], publish: bool) -> None:
    if publish:
        for envelope in found:
            await events.publish(envelope)  # 실패하면 이 쪽을 저장하지 않는다
        state["published_total"] = int(state.get("published_total") or 0) + len(found)
        return
    for envelope in found:
        logger.info("변화 감지기(그림자): %s 내보냈을 이벤트", envelope.event_id)
    shadow = list(state.get("shadow") or [])
    shadow.extend(e.model_dump(mode="json") for e in found)
    state["shadow"] = shadow[-SHADOW_KEEP:]
    state["shadow_total"] = int(state.get("shadow_total") or 0) + len(found)


async def loop() -> None:
    """`interval_seconds()`마다 한 회차. 0이면 바로 끝난다(main.py가 켜짐일 때만 띄운다)."""
    every = interval_seconds()
    if every <= 0:
        return
    while True:
        await run_once()
        await asyncio.sleep(every)


# ------------------------------------------------------------------ 상태 주소


def status() -> dict[str, Any]:
    state = _load()
    return {
        "enabled": interval_seconds() > 0,
        "interval_seconds": interval_seconds(),
        "publish": publishing(),
        "baseline_at": state.get("baseline_at"),
        "cursor_at": state.get("cursor_at"),
        "orders_tracked": len(state.get("orders") or {}),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_success_at": state.get("last_success_at"),
        "last_error": state.get("last_error"),
        "skipped_no_reason": int(state.get("skipped_no_reason") or 0),
        "published_total": int(state.get("published_total") or 0),
        "shadow_total": int(state.get("shadow_total") or 0),
        "shadow": list(state.get("shadow") or []),
    }


router = APIRouter(prefix="/api/dashboard/change-detector", dependencies=[Depends(require_human)])


@router.get("/status")
def get_status() -> dict[str, Any]:
    return ok(status())
