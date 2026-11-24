"""회사 AI 설정(시스템 관리 › 회사 연결 › 공통 AI 도구 연결)과 AI 연결 보기(도구 연결 › AI 연결).

회사 AI 연결 설계(`docs/superpowers/specs/2026-11-20-company-ai-connection-design.md`) 7·8장,
예진 결정(11/20). 회사당 키 하나·모델 하나(예외: 코딩 에이전트 따로 설정, 영상 생성).

권한(로그인 세션만, 공용 토큰은 403)
- `/api/dashboard/company-ai*`: 고객사 관리자(`admin`)만. 운영자·일반회원은 403
- `/api/dashboard/ai-connection/*`: 로그인한 회원 모두(보기만)

회사 ID는 세션 서버의 지금 회사(`tenant.current()`)에서만 꺼낸다(요청 값으로 받지 않는다).
옛 기본값(`shop-demo`)은 계약 `TENANT_ID_ALIASES`대로 정식 id로 바꿔 본다.

키(암호화 키 저장소 `ax-secret-store`, tenant=회사 ID, 이름은 계약 `COMPANY_AI_KEY_NAMES`)
- 키 저장소가 꺼져 있으면(`AX_SECRET_STORE`가 db가 아님) 키·설정 저장을 막고 이유를 돌려준다(409)
- 처음 등록: 관리자만 하므로 바로 적용
- 교체·삭제: 대기 요청 → 요청자가 아닌 다른 관리자가 승인하면 적용. 거절하면 요청을 버린다
- 관리자가 한 명뿐이면 교체·삭제도 바로 적용하고 회원 모두에게 알린다(알림 목록 `notices`)
- 응답·로그·이력에는 끝 글자(최대 4자)만. 값을 돌려주는 경로는 없다

설정(`company_ai.json`, 계약 `CompanyAiSettings`): 모델·주소·월 예산(토큰)·상한 %·코딩 방식·영상.
상한 %(`monthly_limit_percent`, 없으면 100): 이번 달 사용량이 예산의 이 %에 이르면
`budget_exhausted`.
바로 적용하고 회원에게 알린다. 바꿀 때마다 `company_ai.history.jsonl`에 남긴다(키는 끝 글자만).
저장 위치: `AX_COMPANY_AI_DIR` → 회사 연결 파일 옆 `company_ai/`, 그 아래 `<회사 ID>/`.

사용량은 LLM 사용량 저장소(`llm_usage`, `AX_LLM_USAGE_DB`)를 계약 조회(`LlmUsageQuery`)로
읽기만 한다. 개인 MCP 연결로 시킨 일(`account_id`가 있는 기록)도 회사 키로 돌아 회사 사용량에
그대로 센다(그 사람 몫은 personal_mcp.py가 따로 센다). 비용(원)은 보이지 않는다. 토큰과 %만.
"월"은 회사 시간대(`DATA_TENANT_TIMEZONE`, 없으면 UTC) 1일 0시부터.

체험 키(설치 운영자가 `.env`에 넣은 `LLM_TRIAL_API_KEY`, 한도 `LLM_TRIAL_TOKENS` 토큰)
- 회사 키가 없을 때만 쓴다. 회사마다 한 번이라 그 회사의 전체 사용량으로 센다
  (다른 회사 사용량은 섞이지 않는다). 여기서는 상태만 보이고, 실제 폴백은 공통 LLM 호출부 몫
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from ax_agent_template import human_auth, llm_usage
from ax_agent_template import tenant as tenants
from ax_contracts.data_access.llm_usage import LlmCallRecord, LlmUsageQuery
from ax_contracts.schemas import TENANT_ID_ALIASES
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS
from ax_contracts.schemas.company_ai import (
    COMPANY_AI_KEY_NAMES,
    CompanyAiCoder,
    CompanyAiSettings,
    CompanyAiVideo,
)
from ax_dashboard_api import accounts
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok, session_company
from ax_dashboard_api.secret_routes import ReasonBody, ValueBody, _api_error
from ax_secret_store import resolver
from ax_secret_store.errors import SecretStoreError
from ax_secret_store.service import SecretService

DIR_ENV = "AX_COMPANY_AI_DIR"
SETTINGS_FILE = "company_ai.json"
HISTORY_FILE = "company_ai.history.jsonl"
TRIAL_KEY_ENV = "LLM_TRIAL_API_KEY"
TRIAL_TOKENS_ENV = "LLM_TRIAL_TOKENS"
COMPANY_KEY = "LLM_API_KEY"
VIDEO_KEY = "MARKETING_VIDEO_API_KEY"
PAGE = 5000  # 사용량 조회 한 번의 최대 건수(계약 상한)
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
NOTICE_ACTIONS = ("key_add", "key_replace", "key_delete", "settings")

admin_router = APIRouter(prefix="/api/dashboard/company-ai", dependencies=[Depends(require_human)])
view_router = APIRouter(
    prefix="/api/dashboard/ai-connection", dependencies=[Depends(require_human)]
)
_file_lock = threading.Lock()


# ---- 사람·회사 ----


def _admin(authorization: str | None) -> human_auth.Human:
    human = accounts.session_human(authorization)
    if human.role != "admin":
        raise ApiError(403, "ROLE_REQUIRED", "고객사 관리자만 회사 AI 설정을 볼 수 있어요")
    if not any(r.get("id") == human.account_id for r in accounts.load_accounts()):
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return human


def _admin_count() -> int:
    """이 회사 관리자 수(교체·삭제 승인 방식을 정한다). 다른 회사 관리자는 세지 않는다."""
    cid = _company_id()
    return sum(
        1
        for r in accounts.load_accounts()
        if r.get("role") == "admin"
        and (cid is None or any(m.company_id == cid for m in accounts.memberships_of(r)))
    )


def _company_id() -> str | None:
    """이 요청의 회사. 다른 회사 로그인 세션이면 그 세션 회사,
    캣북스 세션·공용 토큰은 예전처럼 설치의 지금 회사(`tenant.current()`)다(11/24, 회사 격리)."""
    session = session_company()
    cid = TENANT_ID_ALIASES.get(session, session) if session else None
    if cid and cid != TENANT_ID_CATBOOKS:
        return cid
    now = tenants.current()
    return TENANT_ID_ALIASES.get(now, now) if now else None


def _company() -> dict[str, str] | None:
    cid = _company_id()
    if cid is None:
        return None
    name = next((t["name"] for t in tenants.list_tenants() if t["id"] == cid), cid)
    return {"id": cid, "name": name}


def _need_company() -> str:
    cid = _company_id()
    if cid is None:
        raise ApiError(409, "NO_COMPANY", "회사 연결에서 회사를 먼저 정해 주세요")
    return cid


# ---- 파일 ----


def base_dir() -> Path | None:
    raw = os.getenv(DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name("company_ai") if conn else None


def _folder(cid: str) -> Path | None:
    root = base_dir()
    return root / cid if root else None


def _need_folder(cid: str) -> Path:
    folder = _folder(cid)
    if folder is None:
        raise ApiError(
            409, "COMPANY_AI_STORE_MISSING", f"회사 AI 설정을 둘 곳이 없어요 ({DIR_ENV} 필요)"
        )
    return folder


def load_settings(cid: str) -> CompanyAiSettings:
    """저장한 회사 AI 설정. 없거나 깨졌으면 빈 설정."""
    folder = _folder(cid)
    if folder is None:
        return CompanyAiSettings()
    try:
        raw = json.loads((folder / SETTINGS_FILE).read_text(encoding="utf-8"))
        return CompanyAiSettings.model_validate(raw)
    except (OSError, ValueError):
        return CompanyAiSettings()


def _save_settings(folder: Path, settings: CompanyAiSettings) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SETTINGS_FILE
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2)
    with _file_lock:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


def _log(folder: Path, entry: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": _now(), **entry}, ensure_ascii=False)
    with _file_lock, (folder / HISTORY_FILE).open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _history(cid: str, limit: int | None = None) -> list[dict[str, Any]]:
    """새것이 먼저."""
    folder = _folder(cid)
    if folder is None:
        return []
    try:
        lines = (folder / HISTORY_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
        if limit is not None and len(out) >= limit:
            break
    return out


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---- 키 저장소 ----


def _store_state() -> dict[str, Any]:
    if not resolver.is_db_mode():
        reason = "키 저장소가 꺼져 있어요 (서버 설정 AX_SECRET_STORE=db 필요)"
        return {"enabled": False, "reason": reason}
    try:
        resolver.service()
    except SecretStoreError as error:
        return {"enabled": False, "reason": f"키 저장소를 열지 못했어요 ({error.code})"}
    return {"enabled": True, "reason": ""}


def _store() -> SecretService:
    state = _store_state()
    if not state["enabled"]:
        raise ApiError(409, "SECRET_STORE_DISABLED", state["reason"])
    return resolver.service()


def _key_rows(cid: str) -> dict[str, dict[str, Any]]:
    if not _store_state()["enabled"]:
        return {}
    try:
        rows = resolver.service().list_status([cid])
    except SecretStoreError:
        return {}
    return {r["name"]: r for r in rows if r["name"] in COMPANY_AI_KEY_NAMES}


def _keys_view(cid: str) -> list[dict[str, Any]]:
    rows = _key_rows(cid)
    out = []
    for name in COMPANY_AI_KEY_NAMES:
        row = rows.get(name)
        out.append(
            {
                "name": name,
                "has_key": row is not None,
                "hint": row["hint"] if row else "",
                "updated_at": row["updated_at"] if row else None,
                "updated_by": row["updated_by"] if row else None,
            }
        )
    return out


def _requests_view(cid: str) -> list[dict[str, Any]]:
    if not _store_state()["enabled"]:
        return []
    try:
        rows = resolver.service().list_requests([cid])
    except SecretStoreError:
        return []
    keep = ("id", "op", "name", "hint", "reason", "requested_by", "requested_at")
    return [{k: r[k] for k in keep} for r in rows if r["name"] in COMPANY_AI_KEY_NAMES]


def _check_key_name(name: str) -> None:
    if name not in COMPANY_AI_KEY_NAMES:
        raise ApiError(422, "UNKNOWN_KEY_NAME", "회사 AI 키 이름이 아니에요")


def _apply(svc: SecretService, request_id: str, human: human_auth.Human) -> dict[str, Any]:
    try:
        done = svc.apply(request_id, human.name)
    except SecretStoreError as error:
        raise _api_error(error) from None
    resolver.invalidate()  # 이 프로세스는 바로, 다른 서비스는 캐시 TTL 안에 따라온다
    return done


async def _change(
    op: str, name: str, value: str | None, reason: str, authorization: str | None
) -> dict:
    human = _admin(authorization)
    _check_key_name(name)
    cid = _need_company()
    svc = _store()
    folder = _need_folder(cid)
    exists = name in _key_rows(cid)
    if op == "replace" and not exists:
        op = "add"
    try:
        req = svc.stage(op, cid, name, value, human.name, reason)
    except SecretStoreError as error:
        raise _api_error(error) from None
    single = _admin_count() <= 1
    if op == "add" or single:
        _apply(svc, req["id"], human)
        entry = {
            "by": human.name,
            "by_id": human.account_id,
            "action": f"key_{op}",
            "name": name,
            "hint": req["hint"],
            "request_id": req["id"],
        }
        if op != "add":
            entry["single_admin"] = True
        _log(folder, entry)
        return ok({"applied": True, "op": op, "name": name, "hint": req["hint"]})
    _log(
        folder,
        {
            "by": human.name,
            "by_id": human.account_id,
            "action": f"request_{op}",
            "name": name,
            "hint": req["hint"],
            "request_id": req["id"],
        },
    )
    keep = ("id", "op", "name", "hint", "requested_by", "requested_at")
    return ok({"applied": False, "op": op, "name": name, "request": {k: req[k] for k in keep}})


def _pending_for(svc: SecretService, request_id: str, cid: str) -> Any:
    try:
        pending = svc.pending(request_id)
    except SecretStoreError as error:
        raise _api_error(error) from None
    if pending.tenant != cid or pending.name not in COMPANY_AI_KEY_NAMES:
        raise ApiError(404, "REQUEST_NOT_FOUND", "그런 요청이 없어요")
    return pending


def _requester_id(cid: str, request_id: str) -> str | None:
    for row in _history(cid):
        if row.get("request_id") == request_id and str(row.get("action", "")).startswith(
            "request_"
        ):
            by_id = row.get("by_id")
            return by_id if isinstance(by_id, str) else None
    return None


# ---- 사용량 ----


def _tz(cid: str) -> Any:
    name = tenants.setting("DATA_TENANT_TIMEZONE") if cid else ""
    try:
        return ZoneInfo(name) if name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _month_range(cid: str, month: str | None) -> tuple[str, datetime, datetime]:
    tz = _tz(cid)
    if month is None:
        now = datetime.now(tz)
        year, mon = now.year, now.month
    else:
        if not MONTH_RE.match(month):
            raise ApiError(422, "INVALID_MONTH", "month는 YYYY-MM 형식이어야 해요")
        year, mon = int(month[:4]), int(month[5:])
    start = datetime(year, mon, 1, tzinfo=tz)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=tz) - timedelta(microseconds=1)
    return f"{year:04d}-{mon:02d}", start.astimezone(UTC), end.astimezone(UTC)


def _tenant_ids(cid: str) -> list[str]:
    """이 회사로 셀 사용량의 tenant_id 값들: 정식 id와 그 id로 바뀌는 옛 값."""
    return [cid, *sorted(old for old, new in TENANT_ID_ALIASES.items() if new == cid)]


def _records(
    cid: str, start: datetime | None, end: datetime | None
) -> tuple[list[LlmCallRecord], bool, bool]:
    """(기록, 기록이 켜져 있는지, 다 읽지 못했는지). 상한에 걸리면 앞쪽으로 넘겨 가며 읽는다."""
    store = llm_usage.get_store()
    if store is None:
        return [], False, False
    seen: dict[str, LlmCallRecord] = {}
    partial = False
    for tid in _tenant_ids(cid):
        to = end
        while True:
            res = store.query(LlmUsageQuery(tenant_id=tid, from_=start, to=to, limit=PAGE))
            new = [r for r in res.records if r.log_id not in seen]
            seen.update((r.log_id, r) for r in new)
            if not res.truncated:
                break
            if not new:  # 같은 시각에 상한보다 많이 몰렸다
                partial = True
                break
            to = res.records[0].occurred_at
    return list(seen.values()), True, partial


def _tokens(records: list[LlmCallRecord]) -> int:
    return sum(r.input_tokens + r.output_tokens for r in records)


def _percent(used: int, budget: int | None) -> float | None:
    return round(used * 100 / budget, 1) if budget else None


def limit_percent(settings: CompanyAiSettings) -> int:
    """월 예산 상한 %. 정하지 않았으면 100."""
    return settings.monthly_limit_percent or 100


def limit_tokens(settings: CompanyAiSettings) -> int | None:
    """예산 × 상한 % (토큰). 예산이 없으면 None(상한 없음)."""
    budget = settings.monthly_budget_tokens
    if not budget:
        return None
    return budget * limit_percent(settings) // 100


def month_usage(cid: str) -> tuple[int, bool, bool]:
    """(이번 달 회사 사용 토큰, 기록이 켜져 있는지, 다 읽지 못했는지)."""
    _, start, end = _month_range(cid, None)
    records, recording, partial = _records(cid, start, end)
    return _tokens(records), recording, partial


def over_limit(cid: str) -> bool:
    """이번 달 회사 사용량이 예산 상한(예산 × 상한 %)에 이르렀는지. 예산이 없으면 False."""
    cap = limit_tokens(load_settings(cid))
    return cap is not None and month_usage(cid)[0] >= cap


def _trial(cid: str) -> dict[str, Any]:
    has_key = bool(os.getenv(TRIAL_KEY_ENV, "").strip())
    try:
        limit = max(0, int(os.getenv(TRIAL_TOKENS_ENV, "").strip() or 0))
    except ValueError:
        limit = 0
    if not (has_key and limit):
        return {"available": False, "limit_tokens": 0, "used_tokens": 0, "percent": None}
    records, _, _ = _records(cid, None, None)
    used = _tokens(records)
    return {
        "available": True,
        "limit_tokens": limit,
        "used_tokens": used,
        "percent": _percent(used, limit),
    }


def _notices(cid: str, limit: int = 10) -> list[dict[str, Any]]:
    keep = ("at", "by", "action", "name", "hint", "changed", "single_admin")
    out = []
    for row in _history(cid):
        if row.get("action") in NOTICE_ACTIONS:
            out.append({k: row[k] for k in keep if k in row})
        if len(out) >= limit:
            break
    return out


# ---- 설정 입력 ----


class SettingsIn(BaseModel):
    """전체 저장(빠진 칸은 기본값). 키 값 칸은 없다."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field("", max_length=200)
    provider: str = Field("auto", pattern=r"^[a-z0-9_]{1,40}$")
    base_url: str = Field("", max_length=500)
    monthly_budget_tokens: int | None = Field(None, ge=0, le=10**13)
    monthly_limit_percent: int | None = Field(None, ge=1, le=100)
    coder: CompanyAiCoder = Field(default_factory=CompanyAiCoder)
    video: CompanyAiVideo = Field(default_factory=CompanyAiVideo)
    embed_model: str = Field("", max_length=200)
    embed_base_url: str = Field("", max_length=500)


def _check_url(url: str) -> None:
    """형식만 본다(http·https, 계정·쿼리 없음). 호스트 정책은 부를 때(공통 LLM 호출부) 본다."""
    if not url:
        return
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ApiError(422, "INVALID_URL", "주소는 http(s)://호스트/경로 형식이어야 해요")


def _validate(body: SettingsIn) -> None:
    for url in (body.base_url, body.coder.base_url, body.embed_base_url):
        _check_url(url.strip())
    if body.provider == "openai_compatible" and not body.base_url.strip():
        raise ApiError(422, "BASE_URL_REQUIRED", "OpenAI 호환 공급자는 주소가 필요해요")
    if body.coder.mode == "separate" and not body.coder.model.strip():
        raise ApiError(422, "CODER_MODEL_REQUIRED", "코딩 에이전트를 따로 설정하면 모델이 필요해요")


# ---- 관리자 경로 ----


@admin_router.get("")
def get_company_ai(authorization: str | None = Header(None)) -> dict:
    """설정 전체(키는 있는지·끝 글자만), 대기 중인 교체·삭제 요청, 이력."""
    _admin(authorization)
    company = _company()
    store = _store_state()
    cid = company["id"] if company else None
    admins = _admin_count()
    return ok(
        {
            "company": company,
            "secret_store": store,
            "can_save": bool(store["enabled"] and cid and base_dir()),
            "settings": (load_settings(cid) if cid else CompanyAiSettings()).model_dump(
                mode="json"
            ),
            "keys": _keys_view(cid) if cid else [],
            "requests": _requests_view(cid) if cid else [],
            "approval": {"needed": admins > 1, "admins": admins},
            "usage_recording": llm_usage.get_store() is not None,
            "history": _history(cid, 30) if cid else [],
        }
    )


def _save(body: SettingsIn, authorization: str | None) -> dict:
    human = _admin(authorization)
    cid = _need_company()
    _store()
    folder = _need_folder(cid)
    _validate(body)
    before = load_settings(cid).model_dump(mode="json")
    settings = CompanyAiSettings.model_validate(
        {**body.model_dump(), "updated_at": datetime.now(UTC), "updated_by": human.name}
    )
    after = settings.model_dump(mode="json")
    changed = [k for k in SettingsIn.model_fields if before.get(k) != after.get(k)]
    _save_settings(folder, settings)
    if changed:
        _log(
            folder,
            {"by": human.name, "by_id": human.account_id, "action": "settings", "changed": changed},
        )
    return ok({"settings": after, "changed": changed})


@admin_router.post("/settings")
def save_settings(body: SettingsIn, authorization: str | None = Header(None)) -> dict:
    """모델·주소·월 예산(토큰)·상한 %·코딩 방식·영상. 바로 적용하고 회원에게 알린다."""
    return _save(body, authorization)


@admin_router.put("/settings")
def put_settings(body: SettingsIn, authorization: str | None = Header(None)) -> dict:
    return _save(body, authorization)


@admin_router.post("/keys/{name}")
async def put_key(name: str, body: ValueBody, authorization: str | None = Header(None)) -> dict:
    """키 등록(없으면 바로) 또는 교체 요청(다른 관리자 승인, 관리자 한 명이면 바로)."""
    return await _change("replace", name, body.value, body.reason, authorization)


@admin_router.post("/keys/{name}/delete")
async def delete_key(
    name: str, body: ReasonBody | None = None, authorization: str | None = Header(None)
) -> dict:
    """키 삭제 요청(다른 관리자 승인, 관리자 한 명이면 바로)."""
    return await _change("delete", name, None, body.reason if body else "", authorization)


@admin_router.delete("/keys/{name}")
async def delete_key_method(name: str, authorization: str | None = Header(None)) -> dict:
    return await _change("delete", name, None, "", authorization)


@admin_router.post("/requests/{request_id}/approve")
def approve_request(request_id: str, authorization: str | None = Header(None)) -> dict:
    """다른 관리자가 교체·삭제 요청을 승인하면 적용한다. 요청한 사람 본인은 못 한다."""
    human = _admin(authorization)
    cid = _need_company()
    svc = _store()
    folder = _need_folder(cid)
    pending = _pending_for(svc, request_id, cid)
    requester = _requester_id(cid, request_id)
    if (requester or None) == human.account_id or (
        requester is None and pending.requested_by == human.name
    ):
        raise ApiError(403, "SELF_APPROVAL", "요청한 사람은 스스로 승인할 수 없어요")
    _apply(svc, request_id, human)
    _log(
        folder,
        {
            "by": human.name,
            "by_id": human.account_id,
            "action": f"key_{pending.op}",
            "name": pending.name,
            "hint": pending.hint,
            "request_id": request_id,
            "requested_by": pending.requested_by,
        },
    )
    return ok({"applied": True, "op": pending.op, "name": pending.name, "hint": pending.hint})


@admin_router.post("/requests/{request_id}/reject")
def reject_request(request_id: str, authorization: str | None = Header(None)) -> dict:
    """대기 요청을 버린다(요청한 사람의 취소도 이것)."""
    human = _admin(authorization)
    cid = _need_company()
    svc = _store()
    folder = _need_folder(cid)
    pending = _pending_for(svc, request_id, cid)
    try:
        svc.repo.commit(
            drop_pending=request_id,
            history={
                "at": _now(),
                "by": human.name[:64],
                "action": "reject",
                "tenant": cid,
                "name": pending.name,
                "request_id": request_id,
            },
        )
    except SecretStoreError as error:
        raise _api_error(error) from None
    _log(
        folder,
        {
            "by": human.name,
            "by_id": human.account_id,
            "action": "reject",
            "name": pending.name,
            "request_id": request_id,
        },
    )
    return ok({"rejected": True, "id": request_id})


# ---- 회원 보기 ----


@view_router.get("/status")
def status(authorization: str | None = Header(None)) -> dict:
    """회사 AI 상태·이번 달 사용 %·영상 켜짐·바뀐 알림. 키 끝 글자는 보이지 않는다."""
    accounts.session_human(authorization)
    company = _company()
    if company is None:
        return ok(
            {
                "company": None,
                "state": "no_company",
                "model": "",
                "key": {"has_key": False},
                "month": None,
                "usage": None,
                "trial": None,
                "video": {"enabled": False},
                "coder": {"mode": "company"},
                "notices": [],
            }
        )
    cid = company["id"]
    settings = load_settings(cid)
    keys = _key_rows(cid)
    month, start, end = _month_range(cid, None)
    records, recording, partial = _records(cid, start, end)
    used = _tokens(records)
    budget = settings.monthly_budget_tokens
    cap = limit_tokens(settings)
    trial = _trial(cid)
    over_budget = cap is not None and used >= cap
    if COMPANY_KEY in keys:
        state = "budget_exhausted" if over_budget else "connected"
    elif trial["available"]:
        if trial["used_tokens"] >= trial["limit_tokens"]:
            state = "trial_exhausted"
        else:
            state = "budget_exhausted" if over_budget else "trial"
    else:
        state = "no_key"
    return ok(
        {
            "company": company,
            "state": state,
            "model": settings.model,
            "key": {"has_key": COMPANY_KEY in keys},
            "month": month,
            "usage": {
                "recording": recording,
                "used_tokens": used,
                "budget_tokens": budget,
                "percent": _percent(used, budget),
                "limit_percent": limit_percent(settings),
                "limit_tokens": cap,
                "partial": partial,
            },
            "trial": trial,
            "video": {
                "enabled": settings.video.enabled and VIDEO_KEY in keys,
                "provider": settings.video.provider,
            },
            "coder": {"mode": settings.coder.mode},
            "notices": _notices(cid),
        }
    )


@view_router.get("/usage")
def usage(
    month: str | None = Query(None, max_length=7), authorization: str | None = Header(None)
) -> dict:
    """AI 직원(부서)별 토큰·%. 많이 쓴 순."""
    accounts.session_human(authorization)
    cid = _company_id()
    if cid is None:
        return ok(
            {
                "company": None,
                "month": month,
                "recording": False,
                "total_tokens": 0,
                "budget_tokens": None,
                "percent": None,
                "limit_percent": 100,
                "limit_tokens": None,
                "departments": [],
                "partial": False,
                "media": None,
            }
        )
    label, start, end = _month_range(cid, month)
    records, recording, partial = _records(cid, start, end)
    settings = load_settings(cid)
    budget = settings.monthly_budget_tokens
    total = _tokens(records)
    by: dict[str, dict[str, int]] = {}
    for r in records:
        row = by.setdefault(r.department, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        row["input_tokens"] += r.input_tokens
        row["output_tokens"] += r.output_tokens
        row["calls"] += 1
    departments = []
    for dept, row in by.items():
        sub = row["input_tokens"] + row["output_tokens"]
        departments.append(
            {
                "department": dept,
                **row,
                "total_tokens": sub,
                "percent_of_budget": _percent(sub, budget),
                "share_percent": _percent(sub, total),
            }
        )
    departments.sort(key=lambda d: (-d["total_tokens"], d["department"]))
    return ok(
        {
            "company": _company(),
            "month": label,
            "recording": recording,
            "total_tokens": total,
            "budget_tokens": budget,
            "percent": _percent(total, budget),
            "limit_percent": limit_percent(settings),
            "limit_tokens": limit_tokens(settings),
            "departments": departments,
            "partial": partial,
            "media": None,  # 영상·이미지 수량은 마케팅 몫(설계 11장 6번)에서 채운다
        }
    )
