"""개인 MCP 연결(시스템 관리 › 도구 연결 › 내 연결, 예진 결정 11/20).

내 AI 도구(Claude Code·Codex·Cursor)가 MCP로 AXcellent에 붙어 AI 직원에게 일을 시킨다.
모델·비용은 내 도구 몫이라 키·모델·주소는 받지 않는다. 이 모듈은 개인 MCP 연결 토큰과
"어느 AI 직원에 쓸지"·"회사 예산 중 내 몫 %"를 맡고, MCP 서버(개발 서비스 `/api/planning/mcp`)가
토큰을 물어보는 검사 API를 연다. 계약: `ax_contracts.schemas.personal_mcp`.
설계: 회사 AI 연결 설계 9장.

권한
- 보기·발급·다시 발급·끊기·설정: 로그인 세션의 고객사 회원(`admin`·`member`) 본인만.
  운영자·에이전트 토큰·공용 토큰은 403. 계정은 세션에서만 꺼낸다(경로·쿼리·본문으로 고를 수 없다)
- 회사 ID는 세션의 회사(#1524 `company_id`), 없으면 세션 서버의 지금 회사(`tenant.current()`)
- 검사(`POST /mcp/check`): MCP 서버 에이전트 토큰(`MCP_SERVERS`)만. 사람 세션·개인 토큰 자체는 403

토큰
- `axm_` + 무작위 32바이트. 원문은 발급·다시 발급 응답에 한 번만 싣는다. 이후에는 끝 4자리만
- 저장은 sha256 해시만(`<AI 설정 폴더>/personal_mcp_tokens.json`: 해시 → 회사 ID·계정 ID).
  다시 발급하면 옛 해시를 지우고 새 해시를 넣는다(옛 토큰은 바로 무효). 끊으면 지운다
- 계정이 없어지면(회원 삭제) 검사에서 `ACCOUNT_GONE`

한도(회사 예산 중 내 몫 %)
- `monthly_share_percent`(1~100, 비우면 제한 없음). 내 몫 = 회사 월 예산(토큰) × %.
  회사 예산이 없으면 % 계산이 안 되므로 막지 않는다(화면에 그렇게 보인다)
- 내 사용량 = 이번 달 LLM 사용 기록 중 `account_id`가 나인 호출(이 회사). MCP 서버가
  `RequestMeta.on_behalf_of`에 나를 채우고, 받는 에이전트 서버가 그 값으로 기록에 계정을 붙인다
- 회사 상한(`CompanyAiSettings.monthly_limit_percent`)에 이르렀으면 MCP도 막는다

설정·이력: `<AI 설정 폴더>/<회사 ID>/personal/<계정 slug>.json`·`<계정 slug>.history.jsonl`

경로(nginx: GET·HEAD·POST만, `/api/dashboard/me/ai-connection` 아래 최대 3단계)
- GET  ``               내 연결 상태·토큰 끝 4자리·고른 AI 직원·내 몫·이번 달 사용량
- POST `/token`         발급(이미 있으면 409, 다시 발급을 쓴다)
- POST `/token/reissue` 다시 발급(옛 것 바로 무효)
- POST `/token/delete`  끊기
- POST `/settings`      고른 AI 직원·내 몫 %
- POST `/mcp/check`     MCP 서버 → 토큰 검사(에이전트 토큰)
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from ax_agent_template import human_auth, llm_usage
from ax_agent_template.server import verify_token
from ax_contracts.data_access.llm_usage import LlmUsageQuery
from ax_contracts.schemas import (
    PERSONAL_MCP_AGENTS,
    PERSONAL_MCP_TOKEN_PREFIX,
    TENANT_ID_ALIASES,
    PersonalMcpAgent,
    PersonalMcpCheckInput,
    PersonalMcpCheckOutput,
)
from ax_contracts.schemas.common import UtcDatetime
from ax_dashboard_api import accounts, company_ai
from ax_dashboard_api.auth import bearer, require_human
from ax_dashboard_api.envelope import ApiError, ok

ROLES = ("admin", "member")
FOLDER = "personal"
INDEX_FILE = "personal_mcp_tokens.json"
# 검사 API를 부를 수 있는 MCP 서버(에이전트 이름). 지금은 개발 서비스의 MCP 서버 하나
MCP_SERVERS = frozenset({"dev"})

router = APIRouter(prefix="/api/dashboard/me/ai-connection", dependencies=[Depends(require_human)])
# MCP 서버가 부르는 창구: 사람 토큰 대신 에이전트 토큰(auth.MACHINE_ROUTES)
check_router = APIRouter(prefix="/api/dashboard/me/ai-connection")
_file_lock = threading.Lock()


class PersonalMcpSettings(BaseModel):
    """계정 하나의 설정 파일. 토큰 원문·해시는 없다(해시는 색인 파일에만)."""

    model_config = ConfigDict(extra="ignore")

    agents: list[PersonalMcpAgent] = Field(default_factory=list, max_length=10)
    monthly_share_percent: int | None = Field(None, ge=1, le=100)
    token_hint: str = Field("", max_length=4)
    token_issued_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


# ---- 이름 규칙 ----


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def account_slug(account_id: str) -> str:
    return hashlib.sha256(account_id.encode()).hexdigest()[:32]


def _new_token() -> str:
    return PERSONAL_MCP_TOKEN_PREFIX + secrets.token_urlsafe(32)


# ---- 사람·회사 ----


def _me(authorization: str | None) -> human_auth.Human:
    human = accounts.session_human(authorization)
    if human.role not in ROLES or not human.account_id:
        raise ApiError(403, "ROLE_REQUIRED", "고객사 회원 계정만 개인 MCP 연결을 쓸 수 있어요")
    if not any(r.get("id") == human.account_id for r in accounts.load_accounts()):
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return human


def _account(human: human_auth.Human) -> str:
    assert human.account_id  # _me가 확인했다
    return human.account_id


def _company_of(human: human_auth.Human) -> str | None:
    cid = human.company_id or company_ai._company_id()
    return TENANT_ID_ALIASES.get(cid, cid) if cid else None


def _company_view(cid: str | None) -> dict[str, str] | None:
    if cid is None:
        return None
    now = company_ai._company()
    if now and now["id"] == cid:
        return now
    return {"id": cid, "name": cid}


def _need_company(human: human_auth.Human) -> str:
    cid = _company_of(human)
    if cid is None:
        raise ApiError(409, "NO_COMPANY", "회사 연결에서 회사를 먼저 정해 주세요")
    return cid


def _store_missing() -> ApiError:
    return ApiError(
        409, "COMPANY_AI_STORE_MISSING", f"AI 설정을 둘 곳이 없어요 ({company_ai.DIR_ENV} 필요)"
    )


# ---- 파일 ----


def _folder(cid: str) -> Path | None:
    root = company_ai.base_dir()
    return root / cid / FOLDER if root else None


def _need_folder(cid: str) -> Path:
    folder = _folder(cid)
    if folder is None:
        raise _store_missing()
    return folder


def load_settings(cid: str, account_id: str) -> PersonalMcpSettings:
    folder = _folder(cid)
    if folder is None:
        return PersonalMcpSettings()
    try:
        path = folder / f"{account_slug(account_id)}.json"
        return PersonalMcpSettings.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return PersonalMcpSettings()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _save_settings(folder: Path, account_id: str, settings: PersonalMcpSettings) -> None:
    with _file_lock:
        _write_json(folder / f"{account_slug(account_id)}.json", settings.model_dump(mode="json"))


def _log(folder: Path, account_id: str, entry: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    line = json.dumps({"at": stamp, **entry}, ensure_ascii=False)
    path = folder / f"{account_slug(account_id)}.history.jsonl"
    with _file_lock, path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _index_path() -> Path | None:
    root = company_ai.base_dir()
    return root / INDEX_FILE if root else None


def _read_index() -> dict[str, dict[str, str]]:
    path = _index_path()
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def _swap_token(cid: str, account_id: str, new_hash: str | None) -> bool:
    """이 회사·계정의 토큰 해시를 바꾼다(None이면 지운다). 옛 해시가 있었는지 돌려준다."""
    path = _index_path()
    if path is None:
        raise _store_missing()
    with _file_lock:
        index = _read_index()
        mine = [
            h
            for h, v in index.items()
            if v.get("company_id") == cid and v.get("account_id") == account_id
        ]
        for h in mine:
            index.pop(h)
        if new_hash is not None:
            index[new_hash] = {"company_id": cid, "account_id": account_id}
        _write_json(path, index)
    return bool(mine)


def _has_token(cid: str, account_id: str) -> bool:
    return any(
        v.get("company_id") == cid and v.get("account_id") == account_id
        for v in _read_index().values()
    )


# ---- 사용량 ----


def _usage(cid: str, account_id: str, share: int | None) -> dict[str, Any]:
    month, start, end = company_ai._month_range(cid, None)
    budget = company_ai.load_settings(cid).monthly_budget_tokens
    limit = budget * share // 100 if (budget and share) else None
    store = llm_usage.get_store()
    used, partial = 0, False
    if store is not None:
        for tid in company_ai._tenant_ids(cid):
            res = store.query(
                LlmUsageQuery(
                    tenant_id=tid,
                    account_id=account_id,
                    from_=start,
                    to=end,
                    limit=company_ai.PAGE,
                )
            )
            used += company_ai._tokens(res.records)
            partial = partial or res.truncated
    return {
        "month": month,
        "recording": store is not None,
        "used_tokens": used,
        "budget_tokens": budget,
        "share_percent": share,
        "limit_tokens": limit,
        "percent_of_budget": company_ai._percent(used, budget),
        "percent_of_limit": company_ai._percent(used, limit),
        "partial": partial,
    }


# ---- 입력 ----


class SettingsIn(BaseModel):
    """전체 저장(빠진 칸은 기본값). 계정·토큰 칸은 없다."""

    model_config = ConfigDict(extra="forbid")

    agents: list[PersonalMcpAgent] = Field(default_factory=list, max_length=20)
    monthly_share_percent: int | None = Field(None, ge=1, le=100)


# ---- 경로 ----


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _token_view(cid: str, account: str, settings: PersonalMcpSettings) -> dict[str, Any]:
    issued = _has_token(cid, account)
    return {
        "issued": issued,
        "hint": settings.token_hint if issued else "",
        "issued_at": _iso(settings.token_issued_at) if issued else None,
    }


@router.get("")
def get_mine(authorization: str | None = Header(None)) -> dict:
    """내 개인 MCP 연결: 토큰(있는지·끝 4자리), 고른 AI 직원, 내 몫 %, 이번 달 내 사용량."""
    human = _me(authorization)
    account = _account(human)
    cid = _company_of(human)
    base = {"company": _company_view(cid), "agents_available": list(PERSONAL_MCP_AGENTS)}
    if cid is None:
        return ok(
            {
                **base,
                "state": "no_company",
                "can_save": False,
                "token": {"issued": False, "hint": "", "issued_at": None},
                "settings": {"agents": [], "monthly_share_percent": None, "updated_at": None},
                "usage": None,
            }
        )
    settings = load_settings(cid, account)
    token = _token_view(cid, account, settings)
    return ok(
        {
            **base,
            "state": "connected" if token["issued"] else "not_issued",
            "can_save": company_ai.base_dir() is not None,
            "token": token,
            "settings": {
                "agents": list(settings.agents),
                "monthly_share_percent": settings.monthly_share_percent,
                "updated_at": _iso(settings.updated_at),
            },
            "usage": _usage(cid, account, settings.monthly_share_percent),
        }
    )


def _put_token(human: human_auth.Human, *, reissue: bool) -> dict:
    account = _account(human)
    cid = _need_company(human)
    folder = _need_folder(cid)
    if not reissue and _has_token(cid, account):
        raise ApiError(409, "TOKEN_EXISTS", "이미 발급했어요. 잃어버렸으면 다시 발급해 주세요")
    token = _new_token()
    had = _swap_token(cid, account, token_hash(token))
    now = datetime.now(UTC)
    settings = load_settings(cid, account).model_copy(
        update={"token_hint": token[-4:], "token_issued_at": now}
    )
    _save_settings(folder, account, settings)
    action = "token_reissue" if reissue and had else "token_issue"
    _log(folder, account, {"action": action, "hint": token[-4:]})
    return ok({"token": token, "hint": token[-4:], "issued_at": now.isoformat()})


@router.post("/token")
def issue_token(authorization: str | None = Header(None)) -> dict:
    """발급. 원문은 이 응답에만 있다(다시 볼 수 없다)."""
    return _put_token(_me(authorization), reissue=False)


@router.post("/token/reissue")
def reissue_token(authorization: str | None = Header(None)) -> dict:
    """다시 발급. 옛 토큰은 바로 쓸 수 없다."""
    return _put_token(_me(authorization), reissue=True)


@router.post("/token/delete")
def delete_token(authorization: str | None = Header(None)) -> dict:
    """끊기. 내 AI 도구의 MCP 연결이 바로 막힌다."""
    human = _me(authorization)
    account = _account(human)
    cid = _need_company(human)
    folder = _need_folder(cid)
    if not _has_token(cid, account):
        raise ApiError(404, "NO_TOKEN", "끊을 연결이 없어요")
    _swap_token(cid, account, None)
    settings = load_settings(cid, account)
    hint = settings.token_hint
    cleared = settings.model_copy(update={"token_hint": "", "token_issued_at": None})
    _save_settings(folder, account, cleared)
    _log(folder, account, {"action": "token_delete", "hint": hint})
    return ok({"issued": False})


@router.post("/settings")
def save_settings(body: SettingsIn, authorization: str | None = Header(None)) -> dict:
    """고른 AI 직원·회사 예산 중 내 몫 %. 나만 쓰는 설정이라 바로 적용한다."""
    human = _me(authorization)
    account = _account(human)
    cid = _need_company(human)
    folder = _need_folder(cid)
    agents = sorted(set(body.agents), key=PERSONAL_MCP_AGENTS.index)
    before = load_settings(cid, account)
    after = before.model_copy(
        update={
            "agents": agents,
            "monthly_share_percent": body.monthly_share_percent,
            "updated_at": datetime.now(UTC),
        }
    )
    changed = [k for k in SettingsIn.model_fields if getattr(before, k) != getattr(after, k)]
    _save_settings(folder, account, after)
    if changed:
        _log(folder, account, {"action": "settings", "changed": changed})
    view = {
        "agents": agents,
        "monthly_share_percent": body.monthly_share_percent,
        "updated_at": _iso(after.updated_at),
    }
    return ok({"settings": view, "changed": changed})


# ---- MCP 서버 검사 ----


def _mcp_server(authorization: str | None) -> str:
    token = bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "MCP 서버 에이전트 토큰이 필요하다")
    try:
        name = verify_token(token)
    except PermissionError:
        raise ApiError(403, "AGENT_TOKEN_INVALID", "에이전트 토큰을 검증할 수 없다") from None
    if name not in MCP_SERVERS:
        raise ApiError(403, "NOT_MCP_SERVER", "MCP 서버만 개인 토큰을 검사할 수 있다")
    return name


def check(body: PersonalMcpCheckInput) -> PersonalMcpCheckOutput:
    """토큰 → 주인 계정, 고른 AI 직원인지, 회사 상한·내 몫 안인지."""
    found = _read_index().get(token_hash(body.token))
    if found is None:
        return PersonalMcpCheckOutput(
            allowed=False,
            code="TOKEN_UNKNOWN",
            message="모르는 연결 토큰이에요 (끊었거나 다시 발급함)",
        )
    cid, account = found.get("company_id", ""), found.get("account_id", "")
    row = next((r for r in accounts.load_accounts() if r.get("id") == account), None)
    if row is None or row.get("role") not in ROLES:
        return PersonalMcpCheckOutput(
            allowed=False, code="ACCOUNT_GONE", message="토큰 주인 계정이 없어요", company_id=cid
        )
    settings = load_settings(cid, account)
    usage = _usage(cid, account, settings.monthly_share_percent)
    base: dict[str, Any] = {
        "account_id": account,
        "name": str(row.get("name") or account),
        "company_id": cid,
        "agents": list(settings.agents),
        "used_tokens": usage["used_tokens"],
        "limit_tokens": usage["limit_tokens"],
        "percent": usage["percent_of_budget"],
    }
    if body.agent not in settings.agents:
        message = f"내 연결에서 고르지 않은 AI 직원이에요 ({body.agent})"
        return PersonalMcpCheckOutput(
            allowed=False, code="AGENT_NOT_CHOSEN", message=message, **base
        )
    if company_ai.over_limit(cid):
        message = "회사 이번 달 AI 예산 상한에 이르렀어요"
        return PersonalMcpCheckOutput(
            allowed=False, code="COMPANY_LIMIT_REACHED", message=message, **base
        )
    limit = usage["limit_tokens"]
    if limit is not None and usage["used_tokens"] >= limit:
        message = f"이번 달 회사 예산 중 내 몫({settings.monthly_share_percent}%)을 다 썼어요"
        return PersonalMcpCheckOutput(
            allowed=False, code="LIMIT_REACHED", message=message, **base
        )
    return PersonalMcpCheckOutput(allowed=True, code="OK", **base)


@check_router.post("/mcp/check")
def check_route(body: PersonalMcpCheckInput, authorization: str | None = Header(None)) -> dict:
    """MCP 서버(에이전트 토큰) → 개인 토큰 검사. 막아도 200이고 `allowed`·`code`로 알린다."""
    _mcp_server(authorization)
    return ok(check(body).model_dump(mode="json"))
