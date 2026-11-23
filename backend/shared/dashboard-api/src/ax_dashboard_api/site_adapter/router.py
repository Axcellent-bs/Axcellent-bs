"""`/site-adapter` 주소: AI들이 shop-api로 알고 부르는 곳.

- ① 짝 맞춘 읽기: 주문(목록·하나)·상품·분류·고객·판매 합계·내보내기·상태 확인
- ③ 사이트에 없는 자료: 각 주소의 응답 모양 그대로 빈 값 (`meta.source = "site_adapter"`)
- 고객지원 접수 동기화(POST): 변환기 장부에 기록만 (에이전트 토큰 또는 판매 서버 서비스 키 필요)
- ③-쓰기(환불·쿠폰·배너·신청 결과·문의 화면 값·롤백·장애 해제): `writes`가 판정한다.
  W1은 직접 쓰기가 없어 허용 서비스가 보낸 쓰기는 모두 할 일함(`applied: "pending_human"`)
- 그 밖: 404 `NOT_MAPPED` (문의·신청 읽기는 다음 PR)

읽기는 shop-api처럼 토큰 없이 받는다(dashboard-api는 사내망에만 있다).
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from ax_agent_template.server import verify_token
from ax_dashboard_api.auth import bearer
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.site_adapter import bookkeeping, resources, upstream, writes
from ax_dashboard_api.site_adapter.mapping import (
    MappingError,
    Resource,
    SiteMapping,
    WriteAction,
    load_mapping,
)
from ax_dashboard_api.site_adapter.writes import WriteRequest, digest


def _require_connected() -> None:
    """연결 전이면 모든 주소가 503 연결 안 됨이다.

    빈 목록·0건·"기록함"으로 답하면 지어낸 값이 된다.
    """
    try:
        upstream.base_url()
    except upstream.UpstreamError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc


router = APIRouter(prefix="/site-adapter", dependencies=[Depends(_require_connected)])
logger = logging.getLogger("ax.site_adapter")

SOURCE = "site_adapter"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
SHOP_CLIENT_KEYS_ENV = "AX_SHOP_CLIENT_KEYS"
_warned: set[str] = set()


def _ok(data: Any, **meta: Any) -> dict[str, Any]:
    return {"data": data, "meta": {"source": SOURCE, **meta}}


def _mapping() -> SiteMapping:
    try:
        upstream.base_url()  # 연결 안 됨이 짝 없음보다 먼저다
    except upstream.UpstreamError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    try:
        found = load_mapping()
    except MappingError as exc:
        raise ApiError(502, "MAPPING_BROKEN", str(exc)) from exc
    if found is None:
        raise ApiError(
            503, "NOT_CONFIGURED", "사이트 짝이 아직 없어요. 회사 연결에서 짝을 정해 주세요"
        )
    return found


async def _rows(
    resource: Resource,
    since: str | None = None,
    convert: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """자원을 읽어 shop-api 모양으로 바꾼다(`convert`가 없으면 주문·상품 모양).

    오류는 shop-api 오류 모양으로.

    고객사 쪽 수가 상한(`AX_SITE_ADAPTER_MAX_PAGES`)에 닿으면 일부만 주지 않고 503
    `UPSTREAM_TRUNCATED`로 실패한다(빠진 장부·합계를 만들지 않는다).
    """
    m = _mapping()
    res = m.resources.get(resource)
    if res is None:
        raise ApiError(503, "NOT_CONFIGURED", f"{resource} 짝이 아직 없어요")
    if convert is None:
        convert = resources.to_order if resource == "orders" else resources.to_product
    try:
        raw, truncated = await upstream.fetch_all(m, res, since=since)
        if truncated:
            raise ApiError(
                503,
                "UPSTREAM_TRUNCATED",
                f"고객사 {resource}가 읽을 수 있는 양보다 많아 일부만 받았어요. 상한을 늘려 주세요",
            )
        return [convert(r, res) for r in raw]
    except upstream.UpstreamError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    except resources.MappingBroken as exc:
        raise ApiError(502, "MAPPING_BROKEN", str(exc)) from exc


def _at(value: str | None, *, upstream_value: bool = False) -> datetime | None:
    """ISO 시각. 시간대가 없으면 UTC로 본다. 고객사 값이 깨졌으면 502, 요청 값이면 422."""
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        if upstream_value:
            raise ApiError(502, "MAPPING_BROKEN", f"고객사 시각 형식이 아니에요: {value}") from exc
        raise ApiError(422, "INVALID_REQUEST", f"시각 형식이 아니에요: {value}") from exc
    return at if at.tzinfo else at.replace(tzinfo=UTC)


async def _orders_in(
    since: str | None, until: str | None, buyer_id: str | None = None
) -> list[dict[str, Any]]:
    """`[since, until)` 안에 만든 주문(구매자 지정 가능), 만든 시각 순."""
    start, end = _at(since), _at(until)
    picked = []
    for o in await _rows("orders", since=since):
        at = _at(o["created_at"], upstream_value=True)
        if (start and (at is None or at < start)) or (end and (at is None or at >= end)):
            continue
        if buyer_id and o["buyer_id"] != buyer_id:
            continue
        picked.append(o)
    picked.sort(key=lambda o: (o["created_at"] or "", o["order_id"]))
    return picked


def _page(items: list[Any], limit: int | None, cursor: str | None) -> tuple[list[Any], str | None]:
    size = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
    start = int(cursor) if cursor and cursor.isdigit() else 0
    chunk = items[start : start + size]
    return chunk, str(start + size) if start + size < len(items) else None


# ---------------------------------------------------------------- ① 짝 맞춘 읽기


@router.get("/health")
async def health() -> dict[str, Any]:
    return _ok({"status": "ok" if await upstream.ping() else "degraded"})


@router.get("/api/shop/orders")
async def orders(
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    buyer_id: str | None = None,
) -> dict[str, Any]:
    chunk, nxt = _page(await _orders_in(since, until, buyer_id), limit, cursor)
    return _ok(chunk, next_cursor=nxt)


@router.get("/api/shop/orders/attributed")
def attributed() -> dict[str, Any]:  # ③: 유입 꼬리표는 사이트에 없다 (orders/{id}보다 먼저 등록)
    return _ok({"orders": [], "truncated": False})


@router.get("/api/shop/orders/{order_id}")
async def order_detail(order_id: str) -> dict[str, Any]:
    for o in await _rows("orders"):
        if o["order_id"] == order_id:
            return _ok(o)
    raise ApiError(404, "NOT_FOUND", "주문을 찾지 못했어요")


@router.get("/api/shop/products")
async def products() -> dict[str, Any]:
    rows = await _rows("products")
    return _ok(rows, total=len(rows))


@router.get("/api/shop/categories")
async def categories() -> dict[str, Any]:
    return _ok(resources.categories_from(await _rows("products")))


@router.get("/api/shop/users")
async def users() -> dict[str, Any]:
    return _ok(resources.users_from(await _rows("orders")))


@router.get("/api/shop/sales-summary")
async def sales_summary(
    group_by: str = "product",
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
) -> dict[str, Any]:
    """고객지원은 `from`·`to`(7일씩)로 묻는다. 그 구간 안에 만든 결제된 주문만 센다."""
    try:
        return _ok({"rows": resources.sales_summary(await _orders_in(start, end), group_by)})
    except ValueError as exc:
        raise ApiError(422, "INVALID_REQUEST", str(exc)) from exc


# 고객사 시계가 `updated_at`을 조금 늦게 찍거나 `updated_since` 경계를 다르게 셀 때를 위해
# 커서 시각보다 앞당겨 묻는다. 이미 준 줄은 커서 비교로 거른다 (설계 "누락 막기")
SINCE_LEAD = timedelta(minutes=2)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _sales_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """판매 기록 커서 `<updated_at ISO>|<order_id>` → 자리.

    비었거나 예전 순번 커서(숫자)면 처음부터(None).

    예전 순번 커서는 이 변환기가 전에 준 값이다. 처음부터 다시 주면 받는 쪽이 같은 주문을
    덮어쓰므로 안전하다. 그 밖의 모양은 422.
    """
    if not cursor or cursor.isdigit():
        return None
    at, sep, order_id = cursor.partition("|")
    parsed = _at(at) if sep and order_id else None
    if parsed is None:
        raise ApiError(422, "INVALID_REQUEST", "판매 기록 커서 모양이 아니에요")
    return parsed, order_id


def _sales_key(row: dict[str, Any]) -> tuple[datetime, str]:
    return _at(row["updated_at"], upstream_value=True) or _EPOCH, row["order_id"]


@router.get("/api/shop/sales-export")
async def sales_export(limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
    """`ShopSalesExportResult` 모양. `(updated_at, order_id)` 순, 커서 뒤의 줄만.

    커서는 마지막으로 준 `(updated_at, order_id)`라 중간 주문이 바뀌어도 빠지거나 두 번 나오지
    않는다(바뀐 주문은 `updated_at`이 바뀌어 뒤에 다시 나온다). 이어 읽을 때는 커서 시각을
    고객사 `updated_since`(짝의 `paging.since_param`)로 넘겨 바뀐 것만 받는다.
    """
    after = _sales_cursor(cursor)
    since = (after[0] - SINCE_LEAD).isoformat().replace("+00:00", "Z") if after else None
    keyed = sorted(
        ((_sales_key(r), r) for r in await _rows("orders", since, resources.sales_row)),
        key=lambda kr: kr[0],
    )
    if after:
        keyed = [kr for kr in keyed if kr[0] > after]
    size = max(1, min(limit or 500, MAX_LIMIT))
    chunk = keyed[:size]
    if chunk:
        last_at, last_id = chunk[-1][0]
        nxt: str | None = f"{last_at.astimezone(UTC).isoformat()}|{last_id}"
    else:
        nxt = cursor
    return _ok(
        {
            "orders": [r for _, r in chunk],
            "next_cursor": nxt,
            "has_more": len(keyed) > size,
            "collected_at": datetime.now(UTC).isoformat(),
        }
    )


@router.get("/api/shop/customers-export")
async def customers_export(limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
    """`ShopCustomersExportResult` 모양. 가입 시각이 없는 사이트라 첫 주문 시각을 쓴다."""
    rows = resources.customers_from(await _rows("orders"))
    return _ok(_export("customers", rows, limit, cursor))


def _export(
    name: str, rows: list[dict[str, Any]], limit: int | None, cursor: str | None
) -> dict[str, Any]:
    chunk, nxt = _page(rows, limit or 500, cursor)
    return {
        name: chunk,
        "next_cursor": nxt or cursor,
        "has_more": nxt is not None,
        "collected_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------- ③ 사이트에 없는 자료

_EMPTY_LISTS = [
    "/api/shop/sellers",
    "/api/shop/coupons",
    "/api/shop/banners",
    "/api/shop/admin/intake/stuck",
    "/api/shop/admin/feedback",
    "/api/shop/admin/ideas",
    "/api/shop/admin/returns",
    "/api/shop/admin/orchestrator/{rest:path}",
    "/api/shop/admin/marketing/{rest:path}",
]
_EMPTY_OBJECTS = [
    "/api/shop/sellers/{seller_id}/profile",
    "/api/shop/sellers/{seller_id}/prices",
    "/api/shop/admin/faults",
    "/api/shop/admin/payment-version",
]


def _empty_list() -> dict[str, Any]:
    return _ok([], total=0)


def _empty_object() -> dict[str, Any]:
    return _ok({})


for _path in _EMPTY_LISTS:
    router.add_api_route(_path, _empty_list, methods=["GET"])
for _path in _EMPTY_OBJECTS:
    router.add_api_route(_path, _empty_object, methods=["GET"])


@router.get("/api/shop/metric-series")
def metric_series(metric: str = "order_count", bucket: str = "5m") -> dict[str, Any]:
    """`ShopMetricSeriesResult` 모양: 물은 지표 그대로, 점은 없음."""
    return _ok({"metric": metric, "bucket": bucket, "points": [], "truncated": False})


@router.get("/api/shop/failed-orders")
def failed_orders() -> dict[str, Any]:
    return _ok({"orders": [], "truncated": False})


# ---------------------------------------------------------------- 접수 동기화 기록


def _writer(authorization: str | None) -> str:
    """에이전트 토큰 또는 판매 서버 서비스 키(`AX_SHOP_CLIENT_KEYS`)만 받는다. 사람 토큰은 403."""
    token = bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "에이전트 토큰이 필요해요")
    try:
        return verify_token(token)
    except PermissionError:
        pass
    for pair in os.getenv(SHOP_CLIENT_KEYS_ENV, "").split(","):
        name, _, key = pair.strip().partition(":")
        if key and hmac.compare_digest(key, token):
            return name
    raise ApiError(403, "AGENT_TOKEN_INVALID", "에이전트 토큰을 검증할 수 없어요")


async def _sync(
    request: Request, kind: str, key: str, action: str, authorization: str | None
) -> dict[str, Any]:
    _writer(authorization)
    bookkeeping.record(kind, key, action, await _json_body(request))
    return _ok({"ok": True})


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    return body if isinstance(body, dict) else {}


_INQUIRY_ACTIONS = {"synced", "failed", "view", "feedback-synced", "gone"}


@router.post("/api/shop/admin/inquiries/by-support/{support_id}/gone")
async def inquiry_gone_by_support(
    request: Request, support_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    return await _sync(request, "inquiries-by-support", support_id, "gone", authorization)


@router.post("/api/shop/admin/{kind}/{seq}/{action}")
async def sync_write(
    request: Request,
    kind: str,
    seq: str,
    action: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    if kind == "claims" and action == "result":
        return await claim_result(request, seq, authorization)
    if kind == "inquiries" and action == "view":
        return await inquiry_view(request, seq, authorization)
    allowed = (
        (kind == "inquiries" and action in _INQUIRY_ACTIONS | {"retry"})
        or (kind == "claims" and action in {"failed", "retry"})
        or (kind == "ideas" and action == "synced")
        or (kind == "returns" and action == "synced")
    )
    if not allowed:
        return _not_mapped("POST", request.url.path)
    return await _sync(request, kind, seq, action, authorization)


# ---------------------------------------------------------------- ③-쓰기 (W1: 모두 할 일함)


def _agent(authorization: str | None, action: WriteAction) -> str:
    """판정 1: 에이전트 토큰의 서비스가 그 동작의 허용 서비스인지.

    사람 토큰·판매 서버 서비스 키·모르는 토큰은 403 `AGENT_NOT_ALLOWED` (할 일함에도 안 넣음).
    """
    token = bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "에이전트 토큰이 필요해요")
    try:
        service = verify_token(token)
    except PermissionError:
        logger.warning("에이전트 토큰이 아닌 사이트 쓰기: action=%s", action)
        raise ApiError(
            403, "AGENT_NOT_ALLOWED", "사이트 쓰기는 에이전트 토큰으로만 보낼 수 있어요"
        ) from None
    writes.allowed_service(action, service)
    return service


def _text(body: dict[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "INVALID_REQUEST", f"{name}가 필요해요")
    return value


def _context(body: dict[str, Any]) -> str | None:
    value = body.get("context_id")
    return value if isinstance(value, str) and value else None


def _hour() -> str:
    """본문에 멱등 키가 없는 쓰기(롤백·장애 해제)의 키: 같은 시(UTC) 안의 같은 요청은 한 번."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H")


def _write(req: WriteRequest) -> dict[str, Any]:
    data, replayed = writes.apply(req)
    return _ok(data, replayed=replayed)


@router.post("/api/shop/payments/{payment_id}/refund")
async def refund(
    request: Request, payment_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    service = _agent(authorization, "refund")
    body = await _json_body(request)
    amount = body.get("refund_amount")
    return _write(
        WriteRequest(
            action="refund",
            service=service,
            shop_path=f"/api/shop/payments/{payment_id}/refund",
            idempotency_key=_text(body, "idempotency_key"),
            fingerprint=digest({"payment_id": payment_id, "body": body}),
            context_id=_context(body),
            summary={
                "payment_id": payment_id,
                "refund_amount": amount,
                "cancel_order": bool(body.get("cancel_order")),
            },
            # 환불 id·주문 취소 여부는 사람이 사이트에서 처리한 뒤에야 안다: 싣지 않는다
            response={"payment_id": payment_id, "refund_amount": amount},
        )
    )


@router.post("/api/shop/coupons")
async def coupon_issue(
    request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    service = _agent(authorization, "coupon_issue")
    body = await _json_body(request)
    picked = {
        k: body.get(k) for k in ("purpose_code", "buyer_id", "count", "unit_value", "campaign_id")
    }
    return _write(
        WriteRequest(
            action="coupon_issue",
            service=service,
            shop_path="/api/shop/coupons",
            idempotency_key=_text(body, "idempotency_key"),
            fingerprint=digest(body),
            context_id=_context(body),
            summary=picked,
            response=dict(picked),
            task_id_fields=("coupon_id",),
        )
    )


@router.post("/api/shop/banners")
async def banner_post(
    request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    service = _agent(authorization, "banner_post")
    body = await _json_body(request)
    notice_id = _text(body, "notice_id")
    return _write(
        WriteRequest(
            action="banner_post",
            service=service,
            shop_path="/api/shop/banners",
            idempotency_key=notice_id,
            fingerprint=digest(body),
            context_id=_context(body),
            # 공지 문구는 고객에게 보일 글이라 사람이 그대로 올릴 수 있게 싣는다
            summary={k: body.get(k) for k in ("notice_id", "text", "valid_from", "valid_to")},
            response={"notice_id": notice_id, "context_id": _context(body)},
        )
    )


@router.delete("/api/shop/banners/{notice_id}")
async def banner_end(
    notice_id: str, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    service = _agent(authorization, "banner_end")
    return _write(
        WriteRequest(
            action="banner_end",
            service=service,
            shop_path=f"/api/shop/banners/{notice_id}",
            idempotency_key=notice_id,
            fingerprint=digest({"notice_id": notice_id}),
            context_id=None,
            summary={"notice_id": notice_id},
            response={"notice_id": notice_id},
        )
    )


@router.post("/api/shop/admin/payment-version/rollback")
async def payment_rollback(
    request: Request, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    service = _agent(authorization, "payment_rollback")
    body = await _json_body(request)
    to_version = _text(body, "to_version")
    return _write(
        WriteRequest(
            action="payment_rollback",
            service=service,
            shop_path="/api/shop/admin/payment-version/rollback",
            idempotency_key=f"{to_version}@{_hour()}",
            fingerprint=digest(body),
            context_id=_context(body),
            summary={"to_version": to_version},
            # 되돌렸는지는 사람이 한 뒤에야 안다: 전후 버전을 비워 인프라 확인이 성공으로 보지 않게
            response={"before": None, "version": None, "to_version": to_version},
        )
    )


@router.delete("/api/shop/admin/faults")
async def fault_release(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    service = _agent(authorization, "fault_release")
    return _write(
        WriteRequest(
            action="fault_release",
            service=service,
            shop_path="/api/shop/admin/faults",
            idempotency_key=_hour(),
            fingerprint=digest("fault_release"),
            context_id=None,
            # 아직 풀지 않았다: `released: false`라 인프라는 해제 성공으로 보지 않는다
            response={"released": False},
        )
    )


async def claim_result(request: Request, seq: str, authorization: str | None) -> dict[str, Any]:
    """신청 결과 쓰기 (판정 결과를 고객 화면에 옮김, 관문 없음). `sync_write`가 넘긴다."""
    service = _agent(authorization, "claim_result")
    body = await _json_body(request)
    body_digest = digest(body)
    return _write(
        WriteRequest(
            action="claim_result",
            service=service,
            shop_path=f"/api/shop/admin/claims/{seq}/result",
            idempotency_key=f"{seq}:{body_digest[:16]}",
            fingerprint=body_digest,
            context_id=None,
            summary={
                "seq": seq,
                "support_claim_id": body.get("support_claim_id"),
                "status": body.get("status"),
                "result": body.get("result"),
            },
            response={"ok": True},
        )
    )


def _agent_name(authorization: str | None) -> str | None:
    """에이전트 토큰이면 그 이름, 아니면(판매 서버 서비스 키 등) None."""
    try:
        return verify_token(bearer(authorization) or "")
    except PermissionError:
        return None


async def inquiry_view(request: Request, seq: str, authorization: str | None) -> dict[str, Any]:
    """문의 화면 값 쓰기. `sync_write`가 넘긴다.

    접수 동기화 기록은 예전 그대로 남기고(같은 인증, 같은 기록), 고객지원 에이전트가 보낸 것은
    쓰기(`inquiry_answer`)로도 판정한다. 판매 서버 서비스 키나 다른 에이전트가 보낸 것은 예전처럼
    기록만 한다(사이트에 쓰지 않으니 할 일도 만들지 않는다).
    """
    _writer(authorization)
    body = await _json_body(request)
    bookkeeping.record("inquiries", seq, "view", body)
    agent = _agent_name(authorization)
    if agent is None or agent != writes.ALLOWED_SERVICE["inquiry_answer"]:
        return _ok({"ok": True})
    raw_view = body.get("view")
    view: dict[str, Any] = raw_view if isinstance(raw_view, dict) else {}
    view_digest = body.get("view_digest")
    body_digest = digest(body)
    key_part = view_digest if isinstance(view_digest, str) and view_digest else body_digest[:16]
    return _write(
        WriteRequest(
            action="inquiry_answer",
            service=agent,
            shop_path=f"/api/shop/admin/inquiries/{seq}/view",
            idempotency_key=f"{seq}:{key_part}",
            fingerprint=body_digest,
            context_id=None,
            # 메시지 글(고객 글·답변)은 싣지 않는다: 사람이 고객지원 화면에서 본다
            summary={
                "seq": seq,
                "support_inquiry_id": view.get("id"),
                "status": view.get("status"),
                "view_digest": view_digest,
            },
            response={"ok": True},
        )
    )


# ---------------------------------------------------------------- 그 밖


def _not_mapped(method: str, path: str) -> dict[str, Any]:
    key = f"{method} {path}"
    if key not in _warned:
        _warned.add(key)
        logger.warning("사이트 변환기가 받지 않는 주소: %s", key)
    raise ApiError(404, "NOT_MAPPED", f"변환기가 아직 받지 않는 주소예요: {key}")


@router.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def not_mapped(request: Request, rest: str) -> dict[str, Any]:
    return _not_mapped(request.method, "/" + rest)
