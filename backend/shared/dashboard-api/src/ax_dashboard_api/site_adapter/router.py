"""`/site-adapter` 주소: AI들이 shop-api로 알고 부르는 곳.

- ① 짝 맞춘 읽기: 주문(목록·하나)·상품·분류·고객·판매 합계·내보내기·상태 확인
- ③ 사이트에 없는 자료: 각 주소의 응답 모양 그대로 빈 값 (`meta.source = "site_adapter"`)
- 고객지원 접수 동기화(POST): 변환기 장부에 기록만 (에이전트 토큰 또는 판매 서버 서비스 키 필요)
- 그 밖: 404 `NOT_MAPPED` (② 사람 반영 쓰기와 문의·신청 읽기는 다음 PR)

읽기는 shop-api처럼 토큰 없이 받는다(dashboard-api는 사내망에만 있다).
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, Request

from ax_agent_template.server import verify_token
from ax_dashboard_api.auth import bearer
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.site_adapter import bookkeeping, resources, upstream
from ax_dashboard_api.site_adapter.mapping import MappingError, Resource, SiteMapping, load_mapping

router = APIRouter(prefix="/site-adapter")
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
        found = load_mapping()
    except MappingError as exc:
        raise ApiError(502, "MAPPING_BROKEN", str(exc)) from exc
    if found is None:
        raise ApiError(
            503, "NOT_CONFIGURED", "사이트 짝이 아직 없어요. 회사 연결에서 짝을 정해 주세요"
        )
    return found


async def _rows(resource: Resource, since: str | None = None) -> tuple[list[dict[str, Any]], bool]:
    """자원을 읽어 shop-api 모양으로 바꾼다. 오류는 shop-api 오류 모양으로."""
    m = _mapping()
    res = m.resources.get(resource)
    if res is None:
        raise ApiError(503, "NOT_CONFIGURED", f"{resource} 짝이 아직 없어요")
    convert: Callable[[dict[str, Any], Any], dict[str, Any]] = (
        resources.to_order if resource == "orders" else resources.to_product
    )
    try:
        raw, truncated = await upstream.fetch_all(m, res, since=since)
        return [convert(r, res) for r in raw], truncated
    except upstream.UpstreamError as exc:
        raise ApiError(exc.status, exc.code, str(exc)) from exc
    except resources.MappingBroken as exc:
        raise ApiError(502, "MAPPING_BROKEN", str(exc)) from exc


def _at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(422, "INVALID_REQUEST", f"시각 형식이 아니에요: {value}") from exc


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
    start, end = _at(since), _at(until)
    rows, truncated = await _rows("orders", since=since)
    picked = []
    for o in rows:
        at = _at(o["created_at"])
        if start and (at is None or at < start):
            continue
        if end and (at is None or at >= end):
            continue
        if buyer_id and o["buyer_id"] != buyer_id:
            continue
        picked.append(o)
    picked.sort(key=lambda o: (o["created_at"] or "", o["order_id"]))
    chunk, nxt = _page(picked, limit, cursor)
    return _ok(chunk, next_cursor=nxt, truncated=truncated)


@router.get("/api/shop/orders/attributed")
def attributed() -> dict[str, Any]:  # ③: 유입 꼬리표는 사이트에 없다 (orders/{id}보다 먼저 등록)
    return _ok({"orders": [], "truncated": False})


@router.get("/api/shop/orders/{order_id}")
async def order_detail(order_id: str) -> dict[str, Any]:
    rows, _ = await _rows("orders")
    for o in rows:
        if o["order_id"] == order_id:
            return _ok(o)
    raise ApiError(404, "NOT_FOUND", "주문을 찾지 못했어요")


@router.get("/api/shop/products")
async def products() -> dict[str, Any]:
    rows, _ = await _rows("products")
    return _ok(rows, total=len(rows))


@router.get("/api/shop/categories")
async def categories() -> dict[str, Any]:
    rows, _ = await _rows("products")
    return _ok(resources.categories_from(rows))


@router.get("/api/shop/users")
async def users() -> dict[str, Any]:
    rows, _ = await _rows("orders")
    return _ok(resources.users_from(rows))


@router.get("/api/shop/sales-summary")
async def sales_summary(
    group_by: str = "product", since: str | None = None, until: str | None = None
) -> dict[str, Any]:
    body = await orders(since=since, until=until, limit=MAX_LIMIT)
    try:
        return _ok({"rows": resources.sales_summary(body["data"], group_by)})
    except ValueError as exc:
        raise ApiError(422, "INVALID_REQUEST", str(exc)) from exc


@router.get("/api/shop/sales-export")
async def sales_export(limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
    rows, truncated = await _rows("orders")
    chunk, nxt = _page(rows, limit, cursor)
    return _ok(chunk, next_cursor=nxt, truncated=truncated)


@router.get("/api/shop/customers-export")
async def customers_export(limit: int | None = None, cursor: str | None = None) -> dict[str, Any]:
    rows, _ = await _rows("orders")
    chunk, nxt = _page(resources.users_from(rows), limit, cursor)
    return _ok(chunk, next_cursor=nxt)


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
def metric_series() -> dict[str, Any]:
    return _ok({"points": []})


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
    try:
        body = await request.json()
    except ValueError:
        body = {}
    bookkeeping.record(kind, key, action, body if isinstance(body, dict) else {})
    return _ok({"ok": True})


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
    allowed = (
        (kind == "inquiries" and action in _INQUIRY_ACTIONS | {"retry"})
        or (kind == "claims" and action in {"failed", "retry"})
        or (kind == "ideas" and action == "synced")
        or (kind == "returns" and action == "synced")
    )
    if not allowed:
        return _not_mapped("POST", request.url.path)
    return await _sync(request, kind, seq, action, authorization)


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
