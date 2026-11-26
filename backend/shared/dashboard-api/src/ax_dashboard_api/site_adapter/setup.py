"""사이트 변환기 설정 API: 회사 연결 › 다른 형식 API에서 고객사 API를 잇는다.

- `GET  /api/dashboard/site-adapter`: 짝이 있는지, 짝, 키를 넣었는지(값은 내보내지 않음), 회사 연결
- `POST /api/dashboard/site-adapter/propose`: 주소·키로 둘러보기. `openapi.json`의 목록 경로와,
  주문·상품 경로를 주면 첫 쪽을 읽어 짝을 제안한다(저장하지 않음)
- `PUT  /api/dashboard/site-adapter`: 짝을 받아 첫 쪽을 실제로 바꿔 보고, 통과하면
  짝 파일·키 파일을 쓰고 회사 연결을 `kind=adapter`로 저장한다. 보낸 짝에 없는 쓰기 짝
  (`writes`)·받은함 짝(`inboxes`)은 저장된 값을 그대로 둔다(W4, `keep_extras`)
- 쓰기 짝·받은함 짝·동작별 직접 쓰기·최근 사이트 쓰기는 `write_setup.py`

쓰기(POST·PUT)는 사람 토큰만 되고, 로그인 세션이면 운영자·관리자만(`EDITORS`).
주소 정책은 회사 연결과 같다(`check_host`).
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
from ax_agent_template import company_scope
from ax_agent_template import tenant as tenants
from ax_agent_template.companies import canonical_company_id
from ax_agent_template.llm import LLMNotConfiguredError, LLMResponseError
from ax_dashboard_api.accounts import require_session_role
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.site_adapter import ai_propose, inquiries, propose, resources, upstream
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
# 둘러보기·저장은 운영자·고객사 관리자 세션만. 일반회원 세션은 403 ROLE_REQUIRED
# (회사 연결과 같은 규칙, 회사 간 격리 시험 6). 공용 토큰은 그대로 둔다
EDITORS = [Depends(require_session_role("operator", "admin"))]

MAX_TOKEN = 1024
# shop-api 주문 상태 (contracts `shop_sales.py`)
SHOP_STATUSES = frozenset(
    {"pending_payment", "paid", "payment_failed", "cancelled", "shipped", "delivered"}
)
# 값 표마다 받을 수 있는 값: 주문 상태는 shop-api 상태, 판매 중은 참·거짓 글자
VALUE_TARGETS: dict[str, frozenset[str]] = {
    "status": SHOP_STATUSES,
    "active": frozenset({propose.ACTIVE_TRUE, propose.ACTIVE_FALSE}),
}
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
    # 추가 필드 (W4): 신청 받은함 경로. 주면 첫 쪽을 읽어 칸 이름·첫 줄을 `claims_sample`로 준다
    claims_path: str | None = None
    # 추가 필드 (11/25): 알아서 연결. 경로를 안 줘도 목록 경로에서 고르고, 규칙으로 못 채운 칸은
    # 회사 AI가 채운 뒤, 빈 칸이 없으면 첫 쪽을 바꿔 보고 바로 저장한다(`ai_propose`)
    auto: bool = False
    tenant: str | None = Field(None, max_length=64)

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


def pick_tenant(given: str | None, found: store.Connection | None) -> str | None:
    """연결에 저장할 회사 설정: 준 값 → 저장된 회사. 회사 연결 화면도 이것을 쓴다.

    - 준 값은 설정 폴더가 있어야 한다(없으면 422 `UNKNOWN_TENANT`)
    - 캣북스가 아닌 세션 회사는 자기 회사 설정만 고를 수 있다(다르면 422 `TENANT_NOT_OWN`).
      다른 회사(캣북스 포함)의 설정을 자기 연결에 붙이지 못하게(#1634 리뷰 2)
    """
    if given:
        if not tenants.is_tenant(given):
            raise ApiError(422, "UNKNOWN_TENANT", f"회사 설정이 없어요: {given}")
        company = store.company_of()
        if company != company_scope.CATBOOKS and canonical_company_id(given) != company:
            raise ApiError(422, "TENANT_NOT_OWN", "우리 회사 설정만 고를 수 있어요")
        return given
    return found.tenant if found else None


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
    active_table(kind, rows, out)
    if (
        kind == "orders"
        and fields.get("status")
        and not fields["status"].startswith(("sum:", "const:"))
    ):
        out["values"] = {"status": propose.statuses(propose.status_values(rows, fields["status"]))}
    return out


def active_table(kind: Resource, rows: list[dict[str, Any]], proposal: dict[str, Any]) -> None:
    """상품 판매 중 칸이 글자 칸이면 값 표(글자 → "true"·"false")를 붙인다(제자리)."""
    expr = proposal["fields"].get("active", "") if kind == "products" else ""
    if not expr or ":" in expr:
        return
    table = propose.active_values(rows, expr)
    if table is not None:
        proposal.setdefault("values", {})["active"] = table


@router.get("")
def get_state() -> dict:
    return ok(_state())


@router.post("/propose", dependencies=EDITORS)
async def propose_mapping(body: ProposeIn, request: Request) -> dict:
    """저장하지 않고 둘러본다. 경로 둘을 다 주면 첫 쪽을 읽어 짝을 제안한다.

    `auto`면 알아서 연결한다: 경로를 고르고, 규칙 + 회사 AI로 짝을 채우고, 다 채워지면 저장한다.
    """
    base = _base(body.base_url)
    headers = upstream.auth_headers(body.auth_header, body.auth_scheme, _token(body.token, base))
    paths, write_paths = await upstream.get_all_paths(base, headers)
    if body.auto:
        return await _auto(body, request, base, headers, paths)
    # 추가 칸 (W4): 쓰기 짝 후보 경로, 신청 받은함 첫 쪽
    extra: dict[str, Any] = {"write_paths": write_paths, "claims_sample": None}
    if body.claims_path:
        res = _resource(body.claims_path)
        try:
            rows = await upstream.first_page(base + res.path, res.paging, headers)
        except upstream.UpstreamError as exc:
            raise _upstream_422(exc) from exc
        extra["claims_sample"] = _sample(rows)
    if not (body.orders_path and body.products_path):
        return ok({"paths": paths, "samples": None, "mapping": None, **extra})
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
    return ok({"paths": paths, "samples": samples, "mapping": mapping, **extra})


def _ai_error(exc: Exception) -> ApiError:
    if isinstance(exc, LLMNotConfiguredError):
        return ApiError(
            422,
            "AI_NOT_CONFIGURED",
            "회사 AI가 아직 없어요. 시스템 관리 › 회사 연결 › 공통 AI 도구 연결에서 넣어 주세요",
        )
    return ApiError(502, "AI_FAILED", "AI가 짝을 고르지 못했어요. 잠시 뒤 다시 해 주세요")


async def _auto(
    body: ProposeIn, request: Request, base: str, headers: dict[str, str], paths: list[str]
) -> dict:
    """알아서 연결: 경로 고르기 → 규칙 짝 → AI로 빈 칸 → 빈 칸 없으면 확인·저장."""
    try:
        chosen = await ai_propose.pick_paths(paths)
        if body.orders_path:
            chosen["orders"] = body.orders_path
        if body.products_path:
            chosen["products"] = body.products_path
        if not (chosen["orders"] and chosen["products"]):
            raise ApiError(
                422,
                "PATHS_NOT_FOUND",
                "주문·상품 목록 경로를 찾지 못했어요. API 주소와 키를 확인해 주세요",
            )
        proposed: dict[str, Any] = {}
        ai: dict[str, Any] = {}
        for kind in ("orders", "products"):
            res = _resource(chosen[kind])
            try:
                rows = await upstream.first_page(base + res.path, res.paging, headers)
            except upstream.UpstreamError as exc:
                raise _upstream_422(exc) from exc
            proposed[kind] = _proposal(kind, res.path, rows)
            ai[kind] = await ai_propose.fill(kind, rows, proposed[kind])
            if "active" in ai[kind]["filled"]:  # AI가 판매 중 칸을 찾았으면 그 칸으로 다시
                active_table(kind, rows, proposed[kind])
    except (LLMNotConfiguredError, LLMResponseError) as exc:
        raise _ai_error(exc) from exc
    for res in proposed.values():  # 끝내 못 찾은 칸은 저장하지 않고 이유로 알린다
        res["fields"] = {k: v for k, v in res["fields"].items() if v}
    mapping = {
        "version": 1,
        "auth_header": body.auth_header,
        "auth_scheme": body.auth_scheme,
        "resources": proposed,
    }
    try:
        m = SiteMapping.model_validate(keep_extras(mapping))
        _complete(m)
    except ApiError as exc:
        return ok({"saved": False, "reason": exc.message, "mapping": mapping, "ai": ai})
    except ValidationError:
        return ok({"saved": False, "reason": "짝 모양이 맞지 않아요", "mapping": mapping, "ai": ai})
    buyer = proposed["orders"]["fields"].get("buyer_id", "")
    inquiry_map, inquiry = await auto_inquiries(base, headers, paths, buyer, m)
    if inquiry_map is not None:
        m = m.model_copy(update={"inboxes": {**m.inboxes, "inquiries": inquiry_map}})
    ai["inquiries"] = inquiry
    token = _token(body.token, base)
    try:
        result = await _store(base, m, token, body.tenant, request)
    except ApiError as exc:
        return ok({"saved": False, "reason": exc.message, "mapping": mapping, "ai": ai})
    return ok({**result, "saved": True, "mapping": mapping, "ai": ai})


async def auto_inquiries(
    base: str, headers: dict[str, str], paths: list[str], order_buyer: str, m: SiteMapping
) -> tuple[ResourceMap | None, dict[str, Any]]:
    """알아서 연결이 문의 받은함 짝도 넣는다 (11/26). 규칙으로만 고르고 못 하면 이유만 준다.

    - 이미 저장된 문의 짝이 있으면 건드리지 않는다(사람이 고친 짝을 덮지 않는다)
    - 문의 목록 경로는 이름 규칙으로 찾는다. 필수 칸(문의 id·제목·내용)을 못 찾거나,
      모르는 상태 낱말이 있거나, 첫 쪽을 받은함 줄로 바꾸지 못하면 넣지 않는다
    - 문의 짝이 없어도 주문·상품 연결은 그대로 저장한다
    """
    if "inquiries" in m.inboxes:
        return None, {"saved": False, "reason": "이미 저장된 문의 짝을 그대로 둬요"}
    path = ai_propose.rule_inquiry_path(paths)
    if not path:
        return None, {"saved": False, "reason": "문의 목록 경로가 없어요"}
    res = _resource(path)
    try:
        rows = await upstream.first_page(base + res.path, res.paging, headers)
    except upstream.UpstreamError as exc:
        return None, {"saved": False, "path": path, "reason": str(exc)}
    fields = propose.inquiry_fields(rows, order_buyer)
    empty = [k for k in propose.INQUIRY_REQUIRED if not fields.get(k)]
    if empty:
        return None, {
            "saved": False,
            "path": path,
            "reason": f"문의 칸을 찾지 못했어요: {', '.join(empty)}",
        }
    values: dict[str, dict[str, str]] = {}
    if "status" in fields:
        table = propose.inquiry_statuses(propose.status_values(rows, fields["status"]))
        unknown = [v for v, to in table.items() if not to]
        if unknown:
            return None, {
                "saved": False,
                "path": path,
                "reason": f"문의 상태 낱말을 모르겠어요: {', '.join(unknown)}",
            }
        values["status"] = table
    try:
        rm = ResourceMap.model_validate(
            {"path": path, "paging": Paging().model_dump(), "fields": fields, "values": values}
        )
        for row in rows:
            inquiries.to_inquiry(row, rm, {})
    except (ValidationError, resources.MappingBroken):
        return None, {"saved": False, "path": path, "reason": "문의 첫 쪽을 바꾸지 못했어요"}
    return rm, {"saved": True, "path": path, "fields": fields, "count": len(rows)}


def _complete(m: SiteMapping) -> None:
    """빈 칸이 남았으면 422. 짝 없는 필수 칸·상태를 사람이 채워야 저장한다."""
    empty: list[str] = []
    for kind, res in m.resources.items():
        empty += [f"{kind}.{k}" for k, v in res.fields.items() if not v.strip()]
        empty += [
            f"{kind}.{name}={raw}"
            for name, table in res.values.items()
            for raw, to in table.items()
            if to not in VALUE_TARGETS.get(name, SHOP_STATUSES)
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


# 주문·상품 짝 저장이 보내지 않으면 저장된 값을 그대로 두는 칸 (W4). 쓰기 짝·받은함 짝은
# 회사 연결 › 사이트 쓰기 화면(`write_setup`)이 따로 저장한다
KEPT_FIELDS = ("writes", "inboxes")


def keep_extras(sent: dict[str, Any]) -> dict[str, Any]:
    """보낸 짝에 없는 `writes`·`inboxes`를 저장된 짝에서 채운다(보낸 칸만 바꾼다).

    저장된 짝이 깨졌으면 채우지 않는다(깨진 값을 되살리지 않는다).
    """
    try:
        found = load_mapping()
    except MappingError:
        found = None
    if found is None:
        return sent
    saved = found.model_dump()
    return {**sent, **{k: saved[k] for k in KEPT_FIELDS if k not in sent}}


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


@router.post(
    "/save", dependencies=EDITORS
)  # PUT과 같다(운영 서버 nginx 게이트가 POST만 열 수 있어서)
@router.put("", dependencies=EDITORS)
async def save(body: SaveIn, request: Request) -> dict:
    """짝을 확인하고 저장한다. 저장하면 AI들의 판매 서버 주소가 변환기로 바뀐다."""
    if not store.connection_file():
        raise ApiError(
            409, "CONNECTION_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    base = _base(body.base_url)
    try:
        m = SiteMapping.model_validate(keep_extras(body.mapping))
    except ValidationError as exc:
        reason = f"짝 모양이 맞지 않아요: {exc.error_count()}곳"
        raise ApiError(422, "MAPPING_BROKEN", reason) from exc
    try:
        _header_ok(m.auth_header, empty=False)
        _header_ok(m.auth_scheme, empty=True)
    except ValueError as exc:
        raise ApiError(422, "MAPPING_BROKEN", f"키 헤더: {exc}") from exc
    _complete(m)
    given = (body.token or "").strip()
    return ok(await _store(base, m, _token(given, base), body.tenant, request))


async def _store(
    base: str, m: SiteMapping, token: str, given_tenant: str | None, request: Request
) -> dict[str, Any]:
    """첫 쪽을 바꿔 보고(안 되면 422) 짝·키·연결을 같이 저장한다. 화면 상태 + 읽은 줄 수."""
    if not store.connection_file():
        raise ApiError(
            409, "CONNECTION_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    tenant = pick_tenant(given_tenant, store.read_connection())
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
    return {**_state(), "counts": counts}
