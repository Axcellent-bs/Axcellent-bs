"""연결 키: AXcellent 운영팀이 발급하고, 고객사 시스템 관리자가 키를 넣어 회사를 연결한다.

서버 파일에 저장해 어느 PC에서든 같은 키 목록·연결 회사가 보인다(예전에는 브라우저에만 있었다).
- 전체 키는 발급 응답에서 한 번만 준다. 목록(GET)에는 가린 키만 나온다
- 읽기·쓰기 모두 사람 토큰(`require_human`). 쓰기는 모두 POST. 운영 nginx 게이트가 POST만 연다
- 파일: `AX_CONNECTION_KEYS_FILE` → 회사 연결 파일 옆 `connection_keys.json`. 없으면 읽기 전용

회사별 (여러 회사 설계 6.2, 2026-11-23)
- 키 목록은 설치에 하나(운영팀이 발급하는 "연결해도 된다"는 권한이라 회사 정보가 없다)
- 연결된 회사는 회사마다 한 줄: 캣북스는 예전 칸 `company` 그대로(파일 모양·되돌리기 그대로),
  다른 회사는 `companies.<회사 ID>`. 연결한 키 줄에는 `company_id`를 남긴다
- 화면은 로그인 세션 회사의 줄만 읽고 쓴다. 공용 토큰(세션 아님)은 캣북스(예전 그대로)
- 다른 회사가 이미 쓰는 키는 `KEY_IN_USE`. 다른 회사 관리자에게는 그 회사 키 줄·회사 정보를
  보이지 않는다(운영자 세션·공용 토큰은 예전처럼 전부 본다)
- 로그인 전 화면(`public_company`)은 세션이 없어 캣북스 줄(예전 그대로)
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field, field_validator

from ax_agent_template import company_connection as store
from ax_agent_template import company_scope, human_auth
from ax_dashboard_api.accounts import require_session_role, session_human
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok, session_company

KEYS_FILE_ENV = "AX_CONNECTION_KEYS_FILE"
KEYS_FILE = "connection_keys.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

router = APIRouter(prefix="/api/dashboard/connection-keys", dependencies=[Depends(require_human)])


# 로그인 세션 토큰이면 역할을 본다(설계 3절): 키 발급·폐기는 운영자,
# 연결·회사 정보·끊기는 고객사 관리자. 공용 토큰은 지금처럼 통과한다(옮기는 동안)
OPERATOR_ONLY = [Depends(require_session_role("operator"))]
ADMIN_ONLY = [Depends(require_session_role("admin"))]


class IssueIn(BaseModel):
    memo: str = Field("", max_length=200)


class KeyIn(BaseModel):
    key: str = Field("", max_length=40)


class RevokeIn(BaseModel):
    id: str = Field("", max_length=40)


class InfoIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    industry: str = Field("", max_length=80)
    network: str = Field("", max_length=80)
    # 회사 사이트 주소(머리글 파비콘·링크). 브라우저가 직접 불러 서버는 부르지 않는다
    site_url: str = Field("", max_length=300)

    @field_validator("site_url")
    @classmethod
    def _site(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not re.match(r"^https?://[^\s/@?#]+(/[^\s?#]*)?$", value):
            raise ValueError("사이트 주소는 http:// 또는 https://로 시작해야 해요")
        return value


class ConnectIn(InfoIn):
    key: str = Field(max_length=40)


def _path() -> Path | None:
    raw = os.getenv(KEYS_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = store.connection_file()
    return Path(conn).with_name(KEYS_FILE) if conn else None


def _not_editable() -> ApiError:
    return ApiError(409, "KEYS_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load() -> dict[str, Any]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("keys", [])
    data.setdefault("company", None)
    if not isinstance(data.get("companies"), dict):
        data["companies"] = {}
    return data


def _cid() -> str:
    """이 요청의 회사: 로그인 세션 회사. 공용 토큰·세션 없음은 캣북스(예전 그대로)."""
    return company_scope.scope_id(session_company())


def _record(data: dict[str, Any], cid: str) -> dict[str, Any] | None:
    """그 회사의 연결된 회사 줄. 캣북스는 예전 칸 `company`."""
    found = data.get("company") if cid == company_scope.CATBOOKS else data["companies"].get(cid)
    return found if isinstance(found, dict) else None


def _set_record(data: dict[str, Any], cid: str, value: dict[str, Any] | None) -> None:
    if cid == company_scope.CATBOOKS:
        data["company"] = value
    elif value is None:
        data["companies"].pop(cid, None)
    else:
        data["companies"][cid] = value


def _owner(row: dict[str, Any]) -> str:
    """키 줄의 회사. 회사 칸이 없는 예전 줄(회사별 전)은 캣북스."""
    return company_scope.scope_id(row.get("company_id"))


def _sees_all(authorization: str | None) -> bool:
    """키 줄을 전부 보는 사람: 공용 토큰(예전 그대로)·운영자 세션. 그 밖은 자기 회사 키 줄만."""
    token = human_auth.bearer(authorization)
    if not token or not human_auth.is_session_token(token):
        return True
    return session_human(authorization).role == "operator"


def _save(data: dict[str, Any]) -> None:
    path = _path()
    if path is None:
        raise ApiError(
            409, "KEYS_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _mask(key: str) -> str:
    return f"{key[:8]}••••-••••"


def _state(data: dict[str, Any] | None = None, *, everyone: bool = True) -> dict[str, Any]:
    """세션 회사의 연결된 회사와 키 목록. `everyone`이 아니면 그 회사 키 줄만."""
    data = data or _load()
    cid = _cid()
    company = _record(data, cid)
    by_id = {k["id"]: k for k in data["keys"]}
    listed = data["keys"] if everyone else [k for k in data["keys"] if _owner(k) == cid]
    rows = [
        {
            "id": k["id"],
            "key": _mask(k["key"]),
            "memo": k.get("memo") or None,
            "issued_at": k["issued_at"],
            "status": k["status"],
            "company": k.get("company") or None,  # 이 키로 연결한 회사 정보(이름·업종·사내망)
        }
        for k in listed
    ]
    public = None
    if company:
        public = {key: v for key, v in company.items() if key != "key_id"}
        public["key"] = (
            _mask(by_id[company["key_id"]]["key"]) if company.get("key_id") in by_id else None
        )
    return {"keys": rows, "company": public, "editable": _path() is not None}


def public_company() -> dict[str, str] | None:
    """로그인 전 화면(로그인·회원가입)에 보일 연결 회사: id·이름·사이트 주소만.

    키(가린 값 포함)·메모·업종·사내망·연결 시각은 넣지 않는다(허용 목록, `public_info.py`).
    로그인 전이라 세션 회사가 없어 캣북스 줄(예전 칸 `company`)만 본다.
    """
    company = _load().get("company")
    if not isinstance(company, dict) or not str(company.get("name") or "").strip():
        return None
    return {
        "id": str(company.get("id") or ""),
        "name": str(company["name"]).strip(),
        "site_url": str(company.get("site_url") or ""),
    }


def _info(body: InfoIn) -> dict[str, str]:
    return {
        "name": body.name.strip(),
        "industry": body.industry.strip(),
        "network": body.network.strip(),
        "site_url": body.site_url,
    }


def _norm(key: str) -> str:
    return key.strip().upper()


def _find(data: dict[str, Any], key: str) -> dict[str, Any] | None:
    want = _norm(key)
    return next((k for k in data["keys"] if secrets.compare_digest(k["key"], want)), None)


def _why_not(data: dict[str, Any], found: dict[str, Any] | None) -> tuple[str, str] | None:
    if found is None:
        return "KEY_UNKNOWN", "AXcellent에서 발급한 키가 아니에요. 키를 다시 확인해 주세요."
    if found["status"] == "revoked":
        return "KEY_REVOKED", "폐기된 키예요. AXcellent에 새 키를 요청해 주세요."
    # 이 회사(세션 회사)가 지금 쓰는 키가 아닌데 연결 중이면 다른 회사 것
    company = _record(data, _cid())
    if found["status"] == "connected" and (not company or company.get("key_id") != found["id"]):
        return "KEY_IN_USE", "이미 다른 회사가 쓰고 있는 키예요."
    return None


@router.get("")
def get_keys(authorization: str | None = Header(None)) -> dict:
    return ok(_state(everyone=_sees_all(authorization)))


@router.post("/issue", dependencies=OPERATOR_ONLY)
def issue(body: IssueIn) -> dict:
    if _path() is None:
        raise ApiError(
            409, "KEYS_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    data = _load()
    part = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))  # noqa: E731
    key = f"AXC-{part()}-{part()}-{part()}"
    row = {
        "id": secrets.token_hex(6),
        "key": key,
        "memo": body.memo.strip(),
        "issued_at": _now(),
        "status": "issued",
    }
    data["keys"].insert(0, row)
    _save(data)
    return ok({"id": row["id"], "key": key, **_state(data)})


@router.post("/check")
def check(body: KeyIn) -> dict:
    data = _load()
    found = _find(data, body.key)
    problem = _why_not(data, found)
    if problem:
        return ok({"ok": False, "reason": problem[1]})
    assert found is not None
    # 미리 채울 회사 정보는 그 키를 전에 쓴 회사가 이 회사일 때만(다른 회사 이름·업종은 안 보임)
    prefill = (found.get("company") or None) if _owner(found) == _cid() else None
    return ok({"ok": True, "reason": None, "company": prefill})


@router.post("/connect", dependencies=ADMIN_ONLY)
def connect(body: ConnectIn, authorization: str | None = Header(None)) -> dict:
    """세션 회사의 줄에만 저장한다. 다른 회사 줄(캣북스 포함)은 그대로."""
    data = _load()
    cid = _cid()
    found = _find(data, body.key)
    problem = _why_not(data, found)
    if problem:
        raise ApiError(422, problem[0], problem[1])
    assert found is not None
    prev = _record(data, cid)
    for k in data["keys"]:
        if prev and k["id"] == prev.get("key_id") and k["id"] != found["id"]:
            k["status"] = "issued"
    info = _info(body)
    if not info["name"]:
        raise ApiError(422, "NAME_REQUIRED", "회사 이름을 넣어 주세요")
    company = {"id": f"c-{found['id']}", **info, "key_id": found["id"], "connected_at": _now()}
    found["status"] = "connected"
    found["company"] = info
    found["company_id"] = cid
    _set_record(data, cid, company)
    _save(data)
    return ok(_state(data, everyone=_sees_all(authorization)))


@router.post("/company", dependencies=ADMIN_ONLY)
def update_company(body: InfoIn, authorization: str | None = Header(None)) -> dict:
    data = _load()
    company = _record(data, _cid())
    if not company:
        raise ApiError(409, "NOT_CONNECTED", "연결된 회사가 없어요")
    info = _info(body)
    if not info["name"]:
        raise ApiError(422, "NAME_REQUIRED", "회사 이름을 넣어 주세요")
    company.update(info)
    for k in data["keys"]:
        if k["id"] == company.get("key_id"):
            k["company"] = info
    _save(data)
    return ok(_state(data, everyone=_sees_all(authorization)))


@router.post("/revoke", dependencies=OPERATOR_ONLY)
def revoke(body: RevokeIn) -> dict:
    data = _load()
    found = next((k for k in data["keys"] if k["id"] == body.id), None)
    if found is None:
        raise ApiError(404, "KEY_NOT_FOUND", "그 키가 없어요")
    found["status"] = "revoked"
    # 그 키로 연결한 회사(어느 회사든)의 연결을 끊는다
    for cid in [company_scope.CATBOOKS, *data["companies"]]:
        company = _record(data, cid)
        if company and company.get("key_id") == found["id"]:
            _set_record(data, cid, None)
    _save(data)
    return ok(_state(data))


@router.post("/disconnect", dependencies=ADMIN_ONLY)
def disconnect(authorization: str | None = Header(None)) -> dict:
    """세션 회사의 연결만 끊는다."""
    data = _load()
    cid = _cid()
    company = _record(data, cid)
    if company:
        for k in data["keys"]:
            if k["id"] == company.get("key_id") and k["status"] == "connected":
                k["status"] = "issued"
        _set_record(data, cid, None)
        _save(data)
    return ok(_state(data, everyone=_sees_all(authorization)))
