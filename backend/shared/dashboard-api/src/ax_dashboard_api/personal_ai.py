"""개인 AI 연결(시스템 관리 › 도구 연결 › 내 연결, 예진 결정 11/20).

회원 각자가 자기 LLM 키로 AI 직원을 부르게 한다. 고른 AI 직원(`agents`)에게 **내가** 일을 시킬 때
회사 키 대신 내 키로 부른다(실제로 고르는 곳은 공통 LLM 호출부 몫, 이 모듈은 저장·보기만).
계약: `ax_contracts.schemas.personal_ai`. 설계: 회사 AI 연결 설계 9장.

권한(로그인 세션만, 본인만)
- 고객사 회원(`admin`·`member`)만. 운영자·에이전트 토큰·공용 토큰은 403
- 계정은 세션에서만 꺼낸다. 경로·쿼리·본문으로 계정을 고르는 길이 없다(관리자도 남의 것을 못 본다)
- 회사 ID는 세션 서버의 지금 회사(`tenant.current()`)에서만 꺼낸다(회사 AI 설정과 같다)

키: 암호화 키 저장소(`ax-secret-store`), 이름 `PERSONAL_LLM_API_KEY`, tenant는 회사 ID + 계정 ID로
만든 계정 전용 값(`secret_tenant`). 회사 키(tenant=회사 ID)·같은 회사 다른 계정 키와 섞이지 않는다.
키 관리 화면은 담당 고객사(`secret_tenants`) tenant만 보이므로 이 값은 거기에도 나오지 않는다.
나만 쓰는 키라 승인 없이 바로 적용한다. 응답·기록에는 끝 글자(최대 4자)만.
키 저장소가 꺼져 있으면 키·설정 저장을 막고 이유를 돌려준다(409).

설정: 회사 AI 설정 폴더 `<회사 ID>/personal/<계정 slug>.json`, 이력은 `<계정 slug>.history.jsonl`.

사용량: `llm_usage` 기록 중 이 회사의 `account_id`가 이 계정인 호출(개인 키 호출)만 센다.
개인 키 호출은 회사 월 예산에 세지 않는다(회사 쪽은 `account_id`가 빈 호출만, company_ai.py).
`account_id`를 채우는 것은 공통 LLM 호출부 몫이라 그 전에는 0으로 보인다.

경로(nginx: GET·HEAD·POST만, `/api/dashboard/me/ai-connection` 아래 최대 2단계)
- GET  ``                 내 상태·설정·사용량
- POST `/settings`        모델·주소·월 한도·고른 AI 직원
- POST `/key`             키 넣기·바꾸기
- POST `/key/delete`      키 지우기
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from ax_agent_template import human_auth, llm_usage
from ax_contracts.data_access.llm_usage import LlmUsageQuery
from ax_contracts.schemas import (
    PERSONAL_AI_AGENTS,
    PERSONAL_AI_KEY_NAME,
    PERSONAL_AI_TENANT_PREFIX,
    PersonalAiAgent,
    PersonalAiSettings,
)
from ax_dashboard_api import accounts, company_ai
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.secret_routes import ValueBody, _api_error
from ax_secret_store import resolver
from ax_secret_store.errors import SecretStoreError

ROLES = ("admin", "member")
FOLDER = "personal"

router = APIRouter(prefix="/api/dashboard/me/ai-connection", dependencies=[Depends(require_human)])
_file_lock = threading.Lock()


# ---- 이름 규칙(계약 personal_ai 문서와 같다) ----


def secret_tenant(company_id: str, account_id: str) -> str:
    """키 저장소 tenant: 회사 ID + 계정 ID. 40자, 저장소 tenant 규칙에 맞는다."""
    digest = hashlib.sha256(f"{company_id}\n{account_id}".encode()).hexdigest()
    return PERSONAL_AI_TENANT_PREFIX + digest[: 40 - len(PERSONAL_AI_TENANT_PREFIX)]


def account_slug(account_id: str) -> str:
    return hashlib.sha256(account_id.encode()).hexdigest()[:32]


# ---- 사람·회사 ----


def _me(authorization: str | None) -> human_auth.Human:
    human = accounts.session_human(authorization)
    if human.role not in ROLES or not human.account_id:
        raise ApiError(403, "ROLE_REQUIRED", "고객사 회원 계정만 개인 AI 연결을 쓸 수 있어요")
    if not any(r.get("id") == human.account_id for r in accounts.load_accounts()):
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return human


def _account(human: human_auth.Human) -> str:
    assert human.account_id  # _me가 확인했다
    return human.account_id


# ---- 파일 ----


def _folder(cid: str) -> Path | None:
    root = company_ai.base_dir()
    return root / cid / FOLDER if root else None


def _settings_path(cid: str, account_id: str) -> Path | None:
    folder = _folder(cid)
    return folder / f"{account_slug(account_id)}.json" if folder else None


def load_settings(cid: str, account_id: str) -> PersonalAiSettings:
    """저장한 개인 AI 설정. 없거나 깨졌으면 빈 설정."""
    path = _settings_path(cid, account_id)
    if path is None:
        return PersonalAiSettings()
    try:
        return PersonalAiSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return PersonalAiSettings()


def _need_folder(cid: str) -> Path:
    folder = _folder(cid)
    if folder is None:
        raise ApiError(
            409,
            "COMPANY_AI_STORE_MISSING",
            f"AI 설정을 둘 곳이 없어요 ({company_ai.DIR_ENV} 필요)",
        )
    return folder


def _save_settings(folder: Path, account_id: str, settings: PersonalAiSettings) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{account_slug(account_id)}.json"
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2)
    with _file_lock:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)


def _log(folder: Path, account_id: str, entry: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    line = json.dumps({"at": stamp, **entry}, ensure_ascii=False)
    path = folder / f"{account_slug(account_id)}.history.jsonl"
    with _file_lock, path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---- 키 ----


def _key_row(cid: str, account_id: str) -> dict[str, Any] | None:
    if not company_ai._store_state()["enabled"]:
        return None
    try:
        rows = resolver.service().list_status([secret_tenant(cid, account_id)])
    except SecretStoreError:
        return None
    return next((r for r in rows if r["name"] == PERSONAL_AI_KEY_NAME), None)


def _apply(op: str, cid: str, human: human_auth.Human, value: str | None) -> dict[str, Any]:
    svc = company_ai._store()
    account = _account(human)
    try:
        tenant = secret_tenant(cid, account)
        req = svc.stage(op, tenant, PERSONAL_AI_KEY_NAME, value, human.name, "")
        svc.apply(req["id"], human.name)
    except SecretStoreError as error:
        raise _api_error(error) from None
    resolver.invalidate()
    return req


# ---- 사용량 ----


def _usage(cid: str, account_id: str, limit: int | None) -> dict[str, Any]:
    month, start, end = company_ai._month_range(cid, None)
    store = llm_usage.get_store()
    if store is None:
        return {
            "month": month, "recording": False, "used_tokens": 0,
            "limit_tokens": limit, "percent": None, "partial": False,
        }
    used, partial = 0, False
    for tid in company_ai._tenant_ids(cid):
        res = store.query(
            LlmUsageQuery(
                tenant_id=tid, account_id=account_id, from_=start, to=end,
                limit=company_ai.PAGE,
            )
        )
        used += company_ai._tokens(res.records)
        partial = partial or res.truncated
    return {
        "month": month,
        "recording": True,
        "used_tokens": used,
        "limit_tokens": limit,
        "percent": company_ai._percent(used, limit),
        "partial": partial,
    }


# ---- 입력 ----


class SettingsIn(BaseModel):
    """전체 저장(빠진 칸은 기본값). 키 값·계정 칸은 없다."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field("", max_length=200)
    base_url: str = Field("", max_length=500)
    monthly_limit_tokens: int | None = Field(None, ge=0, le=10**13)
    agents: list[PersonalAiAgent] = Field(default_factory=list, max_length=20)


# ---- 경로 ----


@router.get("")
def get_mine(authorization: str | None = Header(None)) -> dict:
    """내 개인 AI 연결: 키(있는지·끝 글자), 설정, 이번 달 내 사용량, 고를 수 있는 AI 직원."""
    human = _me(authorization)
    account = _account(human)
    company = company_ai._company()
    store = company_ai._store_state()
    base = {
        "company": company,
        "secret_store": store,
        "agents_available": list(PERSONAL_AI_AGENTS),
    }
    if company is None:
        return ok(
            {
                **base,
                "state": "no_company",
                "can_save": False,
                "key": {"has_key": False, "hint": "", "updated_at": None},
                "settings": PersonalAiSettings().model_dump(mode="json"),
                "usage": None,
            }
        )
    cid = company["id"]
    settings = load_settings(cid, account)
    row = _key_row(cid, account)
    return ok(
        {
            **base,
            "state": "connected" if row else "no_key",
            "can_save": bool(store["enabled"] and company_ai.base_dir()),
            "key": {
                "has_key": row is not None,
                "hint": row["hint"] if row else "",
                "updated_at": row["updated_at"] if row else None,
            },
            "settings": settings.model_dump(mode="json"),
            "usage": _usage(cid, account, settings.monthly_limit_tokens),
        }
    )


@router.post("/settings")
def save_settings(body: SettingsIn, authorization: str | None = Header(None)) -> dict:
    """모델·주소·월 한도(토큰)·고른 AI 직원. 주소가 있으면 OpenAI 호환, 없으면 자동 판별."""
    human = _me(authorization)
    account = _account(human)
    cid = company_ai._need_company()
    company_ai._store()
    folder = _need_folder(cid)
    base_url = body.base_url.strip()
    company_ai._check_url(base_url)
    agents = sorted(set(body.agents), key=PERSONAL_AI_AGENTS.index)
    settings = PersonalAiSettings(
        model=body.model.strip(),
        provider="openai_compatible" if base_url else "auto",
        base_url=base_url,
        monthly_limit_tokens=body.monthly_limit_tokens,
        agents=agents,
        updated_at=datetime.now(UTC),
    )
    before = load_settings(cid, account).model_dump(mode="json")
    after = settings.model_dump(mode="json")
    changed = [k for k in SettingsIn.model_fields if before.get(k) != after.get(k)]
    _save_settings(folder, account, settings)
    if changed:
        _log(folder, account, {"action": "settings", "changed": changed})
    return ok({"settings": after, "changed": changed})


@router.post("/key")
def put_key(body: ValueBody, authorization: str | None = Header(None)) -> dict:
    """키 넣기·바꾸기. 나만 쓰는 키라 바로 적용한다. 끝 글자만 돌려준다."""
    human = _me(authorization)
    account = _account(human)
    cid = company_ai._need_company()
    company_ai._store()
    folder = _need_folder(cid)
    op = "replace" if _key_row(cid, account) else "add"
    req = _apply(op, cid, human, body.value)
    _log(folder, account, {"action": f"key_{op}", "hint": req["hint"]})
    return ok({"has_key": True, "hint": req["hint"]})


@router.post("/key/delete")
def delete_key(authorization: str | None = Header(None)) -> dict:
    """키 지우기. 그 뒤로 고른 AI 직원도 회사 키로 돈다."""
    human = _me(authorization)
    account = _account(human)
    cid = company_ai._need_company()
    company_ai._store()
    folder = _need_folder(cid)
    row = _key_row(cid, account)
    if row is None:
        raise ApiError(404, "NO_KEY", "지울 키가 없어요")
    _apply("delete", cid, human, None)
    _log(folder, account, {"action": "key_delete", "hint": row["hint"]})
    return ok({"has_key": False, "hint": ""})
