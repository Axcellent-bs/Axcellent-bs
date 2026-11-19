"""사이트 변환기 설정 API: 회사 연결 › 다른 형식 API에서 고객사 API를 잇는다.

- `GET  /api/dashboard/site-adapter`: 짝이 있는지, 짝, 키를 넣었는지(값은 내보내지 않음), 회사 연결
- `POST /api/dashboard/site-adapter/propose`: 주소·키로 둘러보기. `openapi.json`의 목록 경로와,
  주문·상품 경로를 주면 첫 쪽을 읽어 짝을 제안한다(저장하지 않음)
- `PUT  /api/dashboard/site-adapter`: 짝을 받아 첫 쪽을 실제로 바꿔 보고, 통과하면
  짝 파일·키 파일을 쓰고 회사 연결을 `kind=adapter`로 저장한다

쓰기(POST·PUT)는 사람 토큰만 된다. 주소 정책은 회사 연결과 같다(`check_host`).
키는 변환기 폴더의 `token` 파일에만 두고 응답·로그·오류 문구에 넣지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from ax_agent_template import company_connection as store
from ax_agent_template import tenant as tenants
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.site_adapter import propose, resources, upstream
from ax_dashboard_api.site_adapter.mapping import (
    MAPPING_FILE,
    MappingError,
    Paging,
    Resource,
    ResourceMap,
    SiteMapping,
    adapter_dir,
    load_mapping,
)

logger = logging.getLogger("ax.site_adapter.setup")

router = APIRouter(
    prefix="/api/dashboard/site-adapter",
    dependencies=[Depends(require_human)],
)

MAX_TOKEN = 1024
# shop-api 주문 상태 (contracts `shop_sales.py`)
SHOP_STATUSES = frozenset(
    {"pending_payment", "paid", "payment_failed", "cancelled", "shipped", "delivered"}
)
HEADER_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")


def _header_ok(value: str, *, empty: bool) -> str:
    """헤더 이름·키 앞말: 영문·숫자·`-`만 (헤더 주입·인코딩 오류를 막는다)."""
    if (not value and not empty) or any(c not in HEADER_CHARS for c in value):
        raise ValueError("영문·숫자·-만 쓸 수 있어요")
    return value


def _token_ok(value: str | None) -> str | None:
    if value and any(not (33 <= ord(c) < 127) for c in value):
        raise ValueError("키는 공백 없는 영문·숫자·기호만 쓸 수 있어요")
    return value


CONVERT = {"orders": resources.to_order, "products": resources.to_product}


class ProposeIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=store.MAX_URL_LENGTH)
    auth_header: str = Field("Authorization", min_length=1, max_length=64)
    auth_scheme: str = Field("Bearer", max_length=32)
    token: str | None = Field(None, max_length=MAX_TOKEN)
    orders_path: str | None = None
    products_path: str | None = None

    @field_validator("auth_header")
    @classmethod
    def _h(cls, v: str) -> str:
        return _header_ok(v, empty=False)

    @field_validator("auth_scheme")
    @classmethod
    def _s(cls, v: str) -> str:
        return _header_ok(v, empty=True)

    @field_validator("token")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        return _token_ok(v)


class SaveIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=store.MAX_URL_LENGTH)
    token: str | None = Field(None, max_length=MAX_TOKEN)
    mapping: dict[str, Any]
    tenant: str | None = Field(None, max_length=64)  # 회사 설정 폴더 id (없으면 저장된 회사)

    @field_validator("token")
    @classmethod
    def _t(cls, v: str | None) -> str | None:
        return _token_ok(v)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _base(url: str) -> str:
    try:
        valid = store.validate_shop_url(url)
        store.check_host(valid)
    except ValueError as exc:
        raise ApiError(422, "INVALID_URL", str(exc)) from exc
    return valid


def _resource(path: str) -> ResourceMap:
    try:
        return ResourceMap(path=path, fields={})
    except ValidationError as exc:
        raise ApiError(422, "INVALID_PATH", f"{path}: /로 시작하는 경로만 쓸 수 있어요") from exc


def _saved_base() -> str | None:
    conn = store.read_connection()
    if conn and conn.kind == "adapter" and conn.adapter:
        return conn.adapter["base_url"]
    return None


def _token(given: str | None, base: str) -> str:
    """넣은 키, 없으면 저장한 키. 저장한 키는 저장한 사이트 주소로만 보낸다."""
    typed = (given or "").strip()
    if typed:
        return typed
    return upstream.saved_token() if _saved_base() == base else ""


def _upstream_422(exc: upstream.UpstreamError) -> ApiError:
    return ApiError(422, exc.code, str(exc))


def _state() -> dict[str, Any]:
    try:
        found = load_mapping()
    except MappingError:
        found = None
    conn = store.read_connection()
    return {
        "configured": found is not None,
        "mapping": found.model_dump() if found else None,
        "token_saved": bool(upstream.saved_token()),
        "connection": conn.as_dict() if conn else None,
    }


def _sample(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys: list[str] = []
    for row in rows:
        keys.extend(k for k in row if k not in keys)
    return {"count": len(rows), "keys": keys, "first": rows[0] if rows else None}


def _proposal(kind: Resource, path: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = propose.fields(kind, rows)
    out: dict[str, Any] = {"path": path, "paging": Paging().model_dump(), "fields": fields}
    # 주문에 줄 목록이 있고 주문 칸에 상품이 없으면: 줄 짝을 제안하고 주문 칸의 상품·수량은 뺀다
    lines = propose.order_items(rows) if kind == "orders" and not fields.get("product_id") else None
    if lines is not None:
        out["items"] = lines
        for name in ("product_id", "qty"):
            fields.pop(name, None)
        if not fields.get("paid_amount"):
            fields.pop("paid_amount", None)  # 줄 결제액 합계를 쓴다
    if (
        kind == "orders"
        and fields.get("status")
        and not fields["status"].startswith(("sum:", "const:"))
    ):
        out["values"] = {"status": propose.statuses(propose.status_values(rows, fields["status"]))}
    return out


@router.get("")
def get_state() -> dict:
    return ok(_state())


@router.post("/propose")
async def propose_mapping(body: ProposeIn) -> dict:
    """저장하지 않고 둘러본다. 경로 둘을 다 주면 첫 쪽을 읽어 짝을 제안한다."""
    base = _base(body.base_url)
    headers = upstream.auth_headers(body.auth_header, body.auth_scheme, _token(body.token, base))
    paths = await upstream.get_paths(base, headers)
    if not (body.orders_path and body.products_path):
        return ok({"paths": paths, "samples": None, "mapping": None})
    targets: dict[Resource, ResourceMap] = {
        "orders": _resource(body.orders_path),
        "products": _resource(body.products_path),
    }
    samples: dict[str, Any] = {}
    proposed: dict[str, Any] = {}
    for kind, res in targets.items():
        try:
            rows = await upstream.first_page(base + res.path, res.paging, headers)
        except upstream.UpstreamError as exc:
            raise _upstream_422(exc) from exc
        samples[kind] = _sample(rows)
        proposed[kind] = _proposal(kind, res.path, rows)
    mapping = {
        "version": 1,
        "auth_header": body.auth_header,
        "auth_scheme": body.auth_scheme,
        "resources": proposed,
    }
    return ok({"paths": paths, "samples": samples, "mapping": mapping})


def _complete(m: SiteMapping) -> None:
    """빈 칸이 남았으면 422. 짝 없는 필수 칸·상태를 사람이 채워야 저장한다."""
    empty: list[str] = []
    for kind, res in m.resources.items():
        empty += [f"{kind}.{k}" for k, v in res.fields.items() if not v.strip()]
        empty += [
            f"{kind}.{name}={raw}"
            for name, table in res.values.items()
            for raw, to in table.items()
            if to not in SHOP_STATUSES
        ]
        need = list(propose.SYNONYMS[kind])
        if res.items is not None:
            # 줄 목록 짝이 있으면 상품·수량·결제액은 줄에서 받는다
            need = [k for k in need if k not in ("product_id", "qty", "paid_amount")]
            empty += [
                f"{kind}.items.{k}"
                for k in ("product_id", "paid_amount")
                if not res.items.fields.get(k, "").strip()
            ]
        missing = [k for k in need if k not in res.fields]
        empty += [f"{kind}.{k}" for k in missing]
    if set(m.resources) != {"orders", "products"}:
        empty.append("주문·상품 경로")
    if empty:
        reason = f"아직 정하지 않은 칸이 있어요: {', '.join(empty)}"
        raise ApiError(422, "MAPPING_INCOMPLETE", reason)


async def verify(base: str, m: SiteMapping, token: str) -> dict[str, int]:
    """자원마다 첫 쪽을 읽어 shop-api 모양으로 바꿔 본다. 안 되면 422. 읽은 줄 수."""
    headers = upstream.auth_headers(m.auth_header, m.auth_scheme, token)
    counts: dict[str, int] = {}
    for kind, res in m.resources.items():
        try:
            rows = await upstream.first_page(base + res.path, res.paging, headers)
        except upstream.UpstreamError as exc:
            raise _upstream_422(exc) from exc
        for row in rows:
            try:
                CONVERT[kind](row, res)
            except resources.MappingBroken as exc:
                raise ApiError(422, "MAPPING_BROKEN", str(exc)) from exc
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    422, "MAPPING_BROKEN", f"{kind} 짝의 숫자 칸에 숫자가 아닌 값이 있어요"
                ) from exc
        counts[kind] = len(rows)
    return counts


def _write_mapping(m: SiteMapping) -> None:
    folder = adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / MAPPING_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(m.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _snapshot() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in (MAPPING_FILE, upstream.TOKEN_FILE):
        try:
            out[name] = (adapter_dir() / name).read_text(encoding="utf-8")
        except OSError:
            out[name] = None
    return out


def _restore(backup: dict[str, str | None]) -> None:
    for name, text in backup.items():
        path = adapter_dir() / name
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(text, encoding="utf-8")


@router.post("/save")  # PUT과 같다(운영 서버 nginx 게이트가 POST만 열 수 있어서)
@router.put("")
async def save(body: SaveIn, request: Request) -> dict:
    """짝을 확인하고 저장한다. 저장하면 AI들의 판매 서버 주소가 변환기로 바뀐다."""
    if not store.connection_file():
        raise ApiError(
            409, "CONNECTION_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    base = _base(body.base_url)
    try:
        m = SiteMapping.model_validate(body.mapping)
    except ValidationError as exc:
        reason = f"짝 모양이 맞지 않아요: {exc.error_count()}곳"
        raise ApiError(422, "MAPPING_BROKEN", reason) from exc
    try:
        _header_ok(m.auth_header, empty=False)
        _header_ok(m.auth_scheme, empty=True)
    except ValueError as exc:
        raise ApiError(422, "MAPPING_BROKEN", f"키 헤더: {exc}") from exc
    _complete(m)
    tenant = body.tenant or None
    if tenant and not tenants.is_tenant(tenant):
        raise ApiError(422, "UNKNOWN_TENANT", f"회사 설정이 없어요: {tenant}")
    if tenant is None and (saved := store.read_connection()):
        tenant = saved.tenant
    given = (body.token or "").strip()
    token = _token(given, base)
    counts = await verify(base, m, token)
    human = getattr(request.state, "human", "unknown")
    # 짝·키·연결 셋이 같이 바뀌어야 한다. 연결 저장이 실패하면 짝·키를 전으로 되돌린다
    # (옛 사이트에 새 키·새 짝이 가지 않게)
    backup = _snapshot()
    upstream.clear_cache()
    try:
        _write_mapping(m)
        upstream.save_token(token)
        store.save_connection(
            base,
            verified_at=_now(),
            updated_by=human,
            kind="adapter",
            adapter={"base_url": base},
            tenant=tenant,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        _restore(backup)
        raise ApiError(422, "INVALID_URL", f"연결을 저장하지 못했어요: {exc}") from exc
    finally:
        upstream.clear_cache()
    logger.info("사이트 변환기 저장: %s (by %s)", base, human)
    return ok({**_state(), "counts": counts})
