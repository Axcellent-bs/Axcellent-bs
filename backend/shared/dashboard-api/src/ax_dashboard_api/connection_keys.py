"""연결 키: AXcellent 운영팀이 발급하고, 고객사 시스템 관리자가 키를 넣어 회사를 연결한다.

서버 파일에 저장해 어느 PC에서든 같은 키 목록·연결 회사가 보인다(예전에는 브라우저에만 있었다).
- 전체 키는 발급 응답에서 한 번만 준다. 목록(GET)에는 가린 키만 나온다
- 쓰기는 모두 POST + 사람 토큰(`require_human_for_writes`). 운영 nginx 게이트가 POST만 연다
- 파일: `AX_CONNECTION_KEYS_FILE` → 회사 연결 파일 옆 `connection_keys.json`. 없으면 읽기 전용
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from ax_agent_template import company_connection as store
from ax_dashboard_api.accounts import require_session_role
from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.envelope import ApiError, ok

KEYS_FILE_ENV = "AX_CONNECTION_KEYS_FILE"
KEYS_FILE = "connection_keys.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# 로그인 세션 토큰이면 역할을 본다(설계 3절): 키 발급·폐기는 운영자,
# 연결·회사 정보·끊기는 고객사 관리자. 공용 토큰은 지금처럼 통과한다(옮기는 동안)
OPERATOR_ONLY = [Depends(require_session_role("operator"))]
ADMIN_ONLY = [Depends(require_session_role("admin"))]

router = APIRouter(
    prefix="/api/dashboard/connection-keys", dependencies=[Depends(require_human_for_writes)]
)


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
    return data


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


def _state(data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or _load()
    company = data.get("company")
    by_id = {k["id"]: k for k in data["keys"]}
    rows = [
        {
            "id": k["id"],
            "key": _mask(k["key"]),
            "memo": k.get("memo") or None,
            "issued_at": k["issued_at"],
            "status": k["status"],
            "company": k.get("company") or None,  # 이 키로 연결한 회사 정보(이름·업종·사내망)
        }
        for k in data["keys"]
    ]
    public = None
    if company:
        public = {key: v for key, v in company.items() if key != "key_id"}
        public["key"] = (
            _mask(by_id[company["key_id"]]["key"]) if company.get("key_id") in by_id else None
        )
    return {"keys": rows, "company": public, "editable": _path() is not None}


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
    company = data.get("company")
    if found["status"] == "connected" and (not company or company.get("key_id") != found["id"]):
        return "KEY_IN_USE", "이미 다른 회사가 쓰고 있는 키예요."
    return None


@router.get("")
def get_keys() -> dict:
    return ok(_state())


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
    prefill = found.get("company") or None
    return ok({"ok": True, "reason": None, "company": prefill})


@router.post("/connect", dependencies=ADMIN_ONLY)
def connect(body: ConnectIn) -> dict:
    data = _load()
    found = _find(data, body.key)
    problem = _why_not(data, found)
    if problem:
        raise ApiError(422, problem[0], problem[1])
    assert found is not None
    prev = data.get("company")
    for k in data["keys"]:
        if prev and k["id"] == prev.get("key_id") and k["id"] != found["id"]:
            k["status"] = "issued"
    info = _info(body)
    if not info["name"]:
        raise ApiError(422, "NAME_REQUIRED", "회사 이름을 넣어 주세요")
    company = {"id": f"c-{found['id']}", **info, "key_id": found["id"], "connected_at": _now()}
    found["status"] = "connected"
    found["company"] = info
    data["company"] = company
    _save(data)
    return ok(_state(data))


@router.post("/company", dependencies=ADMIN_ONLY)
def update_company(body: InfoIn) -> dict:
    data = _load()
    company = data.get("company")
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
    return ok(_state(data))


@router.post("/revoke", dependencies=OPERATOR_ONLY)
def revoke(body: RevokeIn) -> dict:
    data = _load()
    found = next((k for k in data["keys"] if k["id"] == body.id), None)
    if found is None:
        raise ApiError(404, "KEY_NOT_FOUND", "그 키가 없어요")
    found["status"] = "revoked"
    company = data.get("company")
    if company and company.get("key_id") == found["id"]:
        data["company"] = None
    _save(data)
    return ok(_state(data))


@router.post("/disconnect", dependencies=ADMIN_ONLY)
def disconnect() -> dict:
    data = _load()
    company = data.get("company")
    if company:
        for k in data["keys"]:
            if k["id"] == company.get("key_id") and k["status"] == "connected":
                k["status"] = "issued"
        data["company"] = None
        _save(data)
    return ok(_state(data))
