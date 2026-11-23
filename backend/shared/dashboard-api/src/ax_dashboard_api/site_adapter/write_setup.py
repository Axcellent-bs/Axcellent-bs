"""회사 연결 › 사이트 쓰기 (③-쓰기 W4 화면): 쓰기 짝·신청 받은함 짝·동작별 직접 쓰기·키 권한 표시·
최근 사이트 쓰기.

설계: `docs/superpowers/specs/2026-11-18-site-adapter-design.md` "③-쓰기" W4, "기록", 정한 것 3번
(동작별 켜기, 기본 꺼짐).

- `GET  /api/dashboard/site-adapter/writes`: 동작마다 쓰기 짝·켜짐·"키에 쓰기 권한 없음" 표시,
  받은함 짝
- `GET  /api/dashboard/site-adapter/writes/recent`: 쓰기 장부 최근 50건 (본문 원문·키 없음)
- `POST /api/dashboard/site-adapter/writes/map`: 동작 하나의 쓰기 짝 저장(`write: null`이면 지움).
  모양은 `WriteMap` 검사 그대로
- `POST /api/dashboard/site-adapter/inboxes/map`: 받은함 짝 저장(`map: null`이면 지움). 첫 쪽을
  실제로 바꿔 본 뒤 저장
- `POST /api/dashboard/site-adapter/writes/switch`: 동작 하나의 직접 쓰기 켜기·끄기. 돈이 걸린
  동작(`refund`·`coupon_issue`)은 `confirm_money: true`가 있어야 켠다. 쓰기 짝이 없으면 못 켠다
- `POST /api/dashboard/site-adapter/writes/denied/clear`: "키에 쓰기 권한 없음" 표시 풀기

모두 로그인 세션의 회사 줄만 읽고 쓴다(`company_route`가 `adapter_dir()`의 회사를 건다).
쓰기(POST)는 사람 토큰만, 로그인 세션이면 운영자·관리자만(`setup.EDITORS`).
주문·상품 짝 저장(`setup.save`)은 여기서 저장한 `writes`·`inboxes`를 지우지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, get_args

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError

from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.site_adapter import claims, direct, resources, setup, upstream, writes
from ax_dashboard_api.site_adapter.mapping import (
    Inbox,
    MappingError,
    ResourceMap,
    SiteMapping,
    WriteAction,
    WriteMap,
    load_mapping,
)

logger = logging.getLogger("ax.site_adapter.setup")

router = APIRouter(
    prefix="/api/dashboard/site-adapter",
    dependencies=[Depends(require_human)],
)

# 화면에 먼저 보이는 동작 (12/3 누비투어: 문의 답변·환불)
FIRST: tuple[WriteAction, ...] = ("inquiry_answer", "refund")
ACTIONS: tuple[WriteAction, ...] = FIRST + tuple(a for a in get_args(WriteAction) if a not in FIRST)
# 신청 받은함 짝에 꼭 있어야 하는 칸 (`claims.to_claim`이 required로 읽는다)
CLAIM_REQUIRED = ("claim_id", "order_id", "status")
MAX_REASONS = 5


class WriteMapIn(BaseModel):
    action: WriteAction
    write: dict[str, Any] | None = None


class InboxMapIn(BaseModel):
    inbox: Inbox
    map: dict[str, Any] | None = None


class SwitchIn(BaseModel):
    action: WriteAction
    on: bool
    # 돈이 걸린 동작을 켤 때 화면의 확인 문구에 사람이 동의했다는 표시
    confirm_money: bool = False


class ActionIn(BaseModel):
    action: WriteAction


def _human(request: Request) -> str:
    return str(getattr(request.state, "human", "unknown"))


def _mapping_or_409() -> SiteMapping:
    try:
        found = load_mapping()
    except MappingError as exc:
        raise ApiError(409, "MAPPING_BROKEN", "짝 파일이 깨져 고칠 수 없어요") from exc
    if found is None:
        raise ApiError(409, "NOT_CONFIGURED", "먼저 주문·상품 짝을 연결해 주세요")
    return found


def _shape_error(exc: ValidationError, what: str) -> ApiError:
    """pydantic 검사 오류 → 422. 문구는 사람이 넣은 짝에 대한 것만(키는 여기 오지 않는다)."""
    reasons = []
    for err in exc.errors()[:MAX_REASONS]:
        where = ".".join(str(p) for p in err.get("loc", ()))
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        reasons.append(f"{where}: {msg}" if where else msg)
    return ApiError(422, "MAPPING_BROKEN", f"{what} 모양이 맞지 않아요: {' / '.join(reasons)}")


def _state() -> dict[str, Any]:
    try:
        found = load_mapping()
        broken = False
    except MappingError:
        found, broken = None, True
    enabled = set(writes.enabled_actions())
    denied = direct.denied_actions()
    actions = []
    for action in ACTIONS:
        wm = found.writes.get(action) if found else None
        actions.append(
            {
                "action": action,
                "title": writes.TITLES[action],
                "service": writes.ALLOWED_SERVICE[action],
                "money": action in writes.MONEY_ACTIONS,
                "write": wm.model_dump() if wm else None,
                "enabled": action in enabled,
                "denied_at": denied.get(action),
            }
        )
    inboxes = found.inboxes if found else {}
    return {
        "configured": found is not None,
        "broken": broken,
        "actions": actions,
        "inboxes": {
            name: (inboxes[name].model_dump() if name in inboxes else None)
            for name in get_args(Inbox)
        },
    }


@router.get("/writes")
def get_writes() -> dict:
    return ok(_state())


@router.get("/writes/recent")
def get_recent() -> dict:
    """최근 사이트 쓰기 50건: 시각·동작·결과·사이트 응답 코드·경로 틀만 (본문 원문·키 없음)."""
    return ok({"items": writes.recent(), "limit": writes.RECENT_LIMIT})


def _save(m: SiteMapping) -> None:
    setup._write_mapping(m)
    upstream.clear_cache()


@router.post("/writes/map", dependencies=setup.EDITORS)
def save_write_map(body: WriteMapIn, request: Request) -> dict:
    """동작 하나의 쓰기 짝을 저장한다. 다른 동작·읽기 짝·받은함 짝은 그대로 둔다."""
    found = _mapping_or_409()
    updated = dict(found.writes)
    if body.write is None:
        updated.pop(body.action, None)
    else:
        try:
            updated[body.action] = WriteMap.model_validate(body.write)
        except ValidationError as exc:
            raise _shape_error(exc, "쓰기 짝") from exc
    _save(found.model_copy(update={"writes": updated}))
    logger.info(
        "사이트 쓰기 짝 %s: action=%s (by %s)",
        "지움" if body.write is None else "저장",
        body.action,
        _human(request),
    )
    return ok(_state())


async def _verify_claims(found: SiteMapping, rm: ResourceMap) -> int:
    """신청 첫 쪽을 읽어 받은함 줄로 바꿔 본다. 안 되면 422. 읽은 줄 수."""
    try:
        base = upstream.base_url()
    except upstream.UpstreamError as exc:
        raise ApiError(409, "NOT_CONFIGURED", "먼저 사이트를 연결해 주세요") from exc
    headers = upstream.auth_headers(found.auth_header, found.auth_scheme, upstream.saved_token())
    try:
        rows = await upstream.first_page(base + rm.path, rm.paging, headers)
    except upstream.UpstreamError as exc:
        raise ApiError(422, exc.code, str(exc)) from exc
    for row in rows:
        try:
            claims.to_claim(row, rm, {})
        except resources.MappingBroken as exc:
            raise ApiError(422, "MAPPING_BROKEN", str(exc)) from exc
    return len(rows)


@router.post("/inboxes/map", dependencies=setup.EDITORS)
async def save_inbox_map(body: InboxMapIn, request: Request) -> dict:
    """받은함 짝을 저장한다(신청 첫 쪽을 바꿔 본 뒤). 다른 짝은 그대로 둔다."""
    found = _mapping_or_409()
    updated = dict(found.inboxes)
    count: int | None = None
    if body.map is None:
        updated.pop(body.inbox, None)
    else:
        try:
            rm = ResourceMap.model_validate(body.map)
        except ValidationError as exc:
            raise _shape_error(exc, "받은함 짝") from exc
        empty = [k for k in CLAIM_REQUIRED if not rm.fields.get(k, "").strip()]
        if empty:
            raise ApiError(
                422, "MAPPING_INCOMPLETE", f"아직 정하지 않은 칸이 있어요: {', '.join(empty)}"
            )
        count = await _verify_claims(found, rm)
        updated[body.inbox] = rm
    _save(found.model_copy(update={"inboxes": updated}))
    logger.info(
        "받은함 짝 %s: inbox=%s (by %s)",
        "지움" if body.map is None else "저장",
        body.inbox,
        _human(request),
    )
    return ok({**_state(), "count": count})


@router.post("/writes/switch", dependencies=setup.EDITORS)
def switch(body: SwitchIn, request: Request) -> dict:
    """동작 하나의 직접 쓰기를 켜거나 끈다(기본 꺼짐). 끄기는 언제나 된다."""
    if body.on:
        found = _mapping_or_409()
        if body.action not in found.writes:
            raise ApiError(409, "NO_WRITE_MAP", "이 동작의 쓰기 짝을 먼저 저장해 주세요")
        if body.action in writes.MONEY_ACTIONS and not body.confirm_money:
            raise ApiError(
                422,
                "MONEY_CONFIRM_REQUIRED",
                "돈이 걸린 동작이에요. 확인 문구에 동의해야 켤 수 있어요",
            )
    writes.set_direct_write(body.action, body.on, _human(request))
    return ok(_state())


@router.post("/writes/denied/clear", dependencies=setup.EDITORS)
def clear_denied(body: ActionIn, request: Request) -> dict:
    """키를 바꾼 뒤 "키에 쓰기 권한 없음" 표시를 푼다. 다음 쓰기가 다시 확인한다."""
    writes.clear_denied(body.action, _human(request))
    return ok(_state())
