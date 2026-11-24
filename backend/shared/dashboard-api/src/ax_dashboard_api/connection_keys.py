"""연결 키: AXcellent 운영팀이 발급하고, 고객사 시스템 관리자가 키를 넣어 회사를 연결한다.

서버 파일에 저장해 어느 PC에서든 같은 키 목록·연결 회사가 보인다(예전에는 브라우저에만 있었다).
- 전체 키는 발급 응답에서 한 번만 준다. 목록(GET)에는 가린 키만 나온다
- 읽기·쓰기 모두 사람 토큰(`require_human`). 쓰기는 모두 POST. 운영 nginx 게이트가 POST만 연다
- 파일: `AX_CONNECTION_KEYS_FILE` → 회사 연결 파일 옆 `connection_keys.json`. 없으면 읽기 전용

회사별 (여러 회사 설계 6.2, 2026-11-23)
- 키 목록은 설치에 하나(운영팀이 발급하는 "연결해도 된다"는 권한이라 회사 정보가 없다)
- 연결된 회사는 회사마다 한 줄: 캣북스는 예전 칸 `company` 그대로(파일 모양·되돌리기 그대로),
  다른 회사는 `companies.<회사 ID>`. 연결한 키 줄에는 `company_id`를 남긴다
- 연결 전 키 줄(발급만 함)은 운영자 세션·공용 토큰만 본다. 끊거나 다른 키로 바꾸면 그 키 줄은
  연결 전으로 돌아간다(`company_id`·회사 정보 지움). 회사별 전 예전 줄의 주인은 `_owner()`
- 화면은 로그인 세션 회사의 줄만 읽고 쓴다. 공용 토큰(세션 아님)은 캣북스(예전 그대로)
- 다른 회사가 이미 쓰는 키는 `KEY_IN_USE`. 다른 회사 관리자에게는 그 회사 키 줄·회사 정보를
  보이지 않는다(운영자 세션·공용 토큰은 예전처럼 전부 본다)
- 로그인 전 화면(`public_company`)은 세션이 없어 캣북스 줄(예전 그대로)

회사 전용 키 (11/24, 화면만으로 고객사 첫 연결)
- 발급 때 회사 이름(·업종·사이트 주소, 회사 ID는 비우면 자동)을 넣으면 회사 목록에 그 회사를
  더하고(있는 회사는 그대로) 키 줄에 `for_company`를 남긴다. 회사 ID만 넣으면 이미 있는 회사 전용
- 회사 전용 키는 그 회사 세션에서만 확인·연결된다(다른 회사 세션이면 `KEY_OTHER_COMPANY`).
- 회사 옮기기(`/move`): 지금 회사 연결을 끊은 관리자가 다른 회사 전용 키를 넣으면 그 관리자와
  연결된 일반회원이 그 회사로 옮긴다. 기록은 회사 ID별이라 섞이지 않는다(7단계 B안).
  그래서 캣북스 관리자가 누비투어 키로 캣북스 연결을 덮는 일이 없다
- 로그인 전 `/signup` {key, name, email, password}: 회사 전용 키로 그 회사의 첫 관리자를 만들고
  로그인 세션을 준다. 키 하나에 한 번(`signup_at`), 그 회사에 관리자가 이미 있으면 거절.
  `for_company`가 없는 예전 키(캣북스 키 포함)는 예전 그대로다(이전 작업 없음)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, field_validator

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection as store
from ax_agent_template import company_scope, human_auth
from ax_contracts.schemas import Company
from ax_dashboard_api import accounts
from ax_dashboard_api import companies as company_write
from ax_dashboard_api.accounts import require_session_role, session_human
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok, session_company

KEYS_FILE_ENV = "AX_CONNECTION_KEYS_FILE"
KEYS_FILE = "connection_keys.json"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

router = APIRouter(prefix="/api/dashboard/connection-keys", dependencies=[Depends(require_human)])
# 로그인 전 경로(회사 전용 키로 첫 관리자 가입). 사람 토큰이 아직 없다
signup_router = APIRouter(prefix="/api/dashboard/connection-keys")
# 키 파일 읽고-고치고-쓰기 잠금. 두 관리자가 동시에 연결·발급해도 한쪽 저장이 덮이지 않게 한다.
# dashboard-api의 다른 저장 파일(accounts·company_ai·personal_mcp)과 같은 방식:
# 서버 하나(워커 1개)라 프로세스 안 스레드 잠금으로 막고, 파일은 임시 파일 + `os.replace`로 바꾼다
_file_lock = threading.Lock()


# 로그인 세션 토큰이면 역할을 본다(설계 3절): 키 발급·폐기는 운영자,
# 연결·회사 정보·끊기는 고객사 관리자. 공용 토큰은 지금처럼 통과한다(옮기는 동안)
OPERATOR_ONLY = [Depends(require_session_role("operator"))]
ADMIN_ONLY = [Depends(require_session_role("admin"))]


class IssueIn(BaseModel):
    memo: str = Field("", max_length=200)
    # 회사 전용 키: 새 회사 이름(회사 목록에 더함) 또는 이미 있는 회사 ID. 둘 다 없으면 예전 키
    company_name: str = Field("", max_length=80)
    industry: str = Field("", max_length=80)
    site_url: str = Field("", max_length=300)
    company_id: str = Field("", max_length=64)


# 로그인 전 경로(check-public·signup) 속도 제한. 서버 하나(워커 1개)라 프로세스 안 기억으로 센다.
# 운영 nginx도 IP당 limit_req를 건다(재헌 리뷰 11/24). 여기서는 IP와 키 두 기준으로 막는다.
PUBLIC_WINDOW_SECONDS = 60.0
PUBLIC_MAX_PER_IP = 10
PUBLIC_MAX_PER_KEY = 5
_public_hits: dict[str, deque[float]] = {}
_public_lock = threading.Lock()
_public_clock = time.monotonic  # 시험에서 바꿔 끼운다


def reset_public_limits_for_tests() -> None:
    with _public_lock:
        _public_hits.clear()


def _limit_public(request: Request, key: str) -> None:
    """로그인 전 키 경로를 IP·키마다 분당 몇 번으로 막는다. 넘으면 429."""
    ip = (request.client.host if request.client else "") or "unknown"
    now = _public_clock()
    buckets = [(f"ip:{ip}", PUBLIC_MAX_PER_IP), (f"key:{_norm(key)}", PUBLIC_MAX_PER_KEY)]
    with _public_lock:
        for name, limit in buckets:
            hits = _public_hits.setdefault(name, deque())
            while hits and now - hits[0] > PUBLIC_WINDOW_SECONDS:
                hits.popleft()
            if len(hits) >= limit:
                raise ApiError(429, "TOO_MANY_TRIES", "너무 자주 시도했어요. 1분 뒤 다시 해 주세요")
        for name, _limit in buckets:
            _public_hits[name].append(now)


class SignupIn(BaseModel):
    key: str = Field(max_length=40)
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


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


def _owner(row: dict[str, Any], data: dict[str, Any]) -> str | None:
    """키 줄의 회사. 없으면 None(아직 아무 회사도 쓰지 않은 키: 운영자만 본다).

    - `company_id`가 있으면 그 회사
    - `company_id`가 없으면 회사별 전의 예전 줄이거나 연결 전 키다. 회사별 전에는 연결이 모두
      캣북스였으므로 연결 기록이 있는 줄만 캣북스로 본다: 예전 칸 `company`의 `key_id`인 줄
      (지금 캣북스가 쓰는 키), 또는 키 줄에 회사 정보(`company`)가 남은 줄(예전에 캣북스가 쓴 키).
      지금은 연결할 때 `company_id`를 남기고 끊거나 바꿀 때 회사 정보를 지워서,
      이 둘은 예전 줄에만 있다
    - 그 밖(발급만 한 키)은 어느 회사 것도 아니다. 운영자가 누비용으로 발급해 둔 키가 연결 전에
      캣북스 관리자 목록에 보이지 않게 한다
    """
    if row.get("company_id"):
        return company_scope.scope_id(row["company_id"])
    if row.get("for_company"):
        return company_scope.scope_id(row["for_company"])
    legacy = data.get("company")
    if (isinstance(legacy, dict) and legacy.get("key_id") == row.get("id")) or row.get("company"):
        return company_scope.CATBOOKS
    return None


def _release(row: dict[str, Any]) -> None:
    """회사가 끊거나 다른 키로 바꾼 키 줄: 연결 전 상태로 되돌린다(회사·회사 정보를 지운다)."""
    row["status"] = "issued"
    row.pop("company_id", None)
    row.pop("company", None)


def _sees_all(authorization: str | None) -> bool:
    """키 줄을 전부 보는 사람: 공용 토큰(예전 그대로)·운영자 세션. 그 밖은 자기 회사 키 줄만."""
    token = human_auth.bearer(authorization)
    if not token or not human_auth.is_session_token(token):
        return True
    return session_human(authorization).role == "operator"


def _save(data: dict[str, Any]) -> None:
    """임시 파일에 쓰고 `os.replace`로 바꾼다. 읽고-고치고-쓰기는 `_file_lock` 안에서 한다."""
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
    listed = data["keys"] if everyone else [k for k in data["keys"] if _owner(k, data) == cid]
    rows = [
        {
            "id": k["id"],
            "key": _mask(k["key"]),
            "memo": k.get("memo") or None,
            "issued_at": k["issued_at"],
            "status": k["status"],
            "company": k.get("company") or None,  # 이 키로 연결한 회사 정보(이름·업종·사내망)
            "for_company": k.get("for_company") or None,  # 회사 전용 키의 회사 ID
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


_UNKNOWN_KEY = "AXcellent에서 발급한 키가 아니에요. 키를 다시 확인해 주세요."


def _why_not(
    data: dict[str, Any], found: dict[str, Any] | None, cid: str | None = None
) -> tuple[str, str] | None:
    """키를 이 회사(cid, 기본은 세션 회사)가 쓸 수 없는 이유. 쓸 수 있으면 None."""
    if found is None:
        return "KEY_UNKNOWN", _UNKNOWN_KEY
    if found["status"] == "revoked":
        return "KEY_REVOKED", "폐기된 키예요. AXcellent에 새 키를 요청해 주세요."
    cid = cid or _cid()
    bound = found.get("for_company")
    if bound and company_scope.scope_id(bound) != cid:
        return (
            "KEY_OTHER_COMPANY",
            "다른 회사용 키예요. 그 회사 관리자 계정으로 로그인하거나 처음이면 키로 가입해 주세요.",
        )
    # 이 회사(세션 회사)가 지금 쓰는 키가 아닌데 연결 중이면 다른 회사 것
    company = _record(data, cid)
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
    part = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))  # noqa: E731
    key = f"AXC-{part()}-{part()}-{part()}"
    row = {
        "id": secrets.token_hex(6),
        "key": key,
        "memo": body.memo.strip(),
        "issued_at": _now(),
        "status": "issued",
    }
    for_company = _company_for_key(body)
    if for_company:
        row["for_company"] = for_company
    with _file_lock:
        data = _load()
        data["keys"].insert(0, row)
        _save(data)
    return ok({"id": row["id"], "key": key, **_state(data)})


def _company_for_key(body: IssueIn) -> str | None:
    """발급할 키의 회사. 새 회사 이름이면 목록에 더하고, 회사 ID만이면 있는 회사여야 한다."""
    name = body.company_name.strip()
    given = body.company_id.strip()
    if not name and not given:
        return None
    if not name:
        cid = company_list.canonical_company_id(given)
        if cid is None or cid not in company_list.company_ids():
            raise ApiError(422, "UNKNOWN_COMPANY", "등록되지 않은 회사 ID예요")
        return cid
    try:
        site = InfoIn(name=name, site_url=body.site_url).site_url
        company = Company(
            company_id=given or "c-" + secrets.token_hex(3),
            name=name,
            industry=body.industry.strip(),
            site_url=site,
            created_at=datetime.now(UTC),
        )
    except ValueError:  # pydantic ValidationError 포함
        raise ApiError(
            422,
            "COMPANY_INVALID",
            "회사 ID는 영어 소문자·숫자·-로 3~40자, 사이트 주소는 http(s)://로 시작해요",
        ) from None
    if given and given != company.company_id:  # 별칭(shop-demo)은 캣북스다
        raise ApiError(409, "COMPANY_EXISTS", "이미 있는 회사 ID예요")
    try:
        company_write.add(company)
    except company_write.CompanyExists:
        raise ApiError(409, "COMPANY_EXISTS", "이미 있는 회사 ID예요") from None
    except company_write.CompaniesFileBroken:
        raise ApiError(
            409, "COMPANIES_FILE_BROKEN", "회사 목록 파일을 읽을 수 없어 등록하지 않았어요"
        ) from None
    except RuntimeError:
        raise ApiError(
            409, "COMPANIES_FILE_UNSET", "회사 목록 파일 위치가 정해지지 않았어요"
        ) from None
    return company.company_id


def _session_cid(authorization: str | None) -> str | None:
    """로그인 세션이면 그 회사, 세션이 아니면(로그인 전·공용 토큰) None."""
    token = human_auth.bearer(authorization)
    if not token or not human_auth.is_session_token(token):
        return None
    return _cid()


def _registered(cid: str) -> dict[str, str] | None:
    """회사 목록의 그 회사 정보(이름·업종·사이트 주소). 키 확인 때 미리 채운다."""
    found = next((c for c in company_list.list_companies() if c.company_id == cid), None)
    if found is None:
        return None
    return {"name": found.name, "industry": found.industry, "site_url": found.site_url}


@router.post("/check")
def check(body: KeyIn, authorization: str | None = Header(None)) -> dict:
    return ok(_check(body.key, authorization))


@signup_router.post("/check-public")
def check_public(body: KeyIn, request: Request) -> dict:
    """로그인 전 키 확인(/setup 첫 단계). 회사 전용 키만 가입 가능 여부를 알려 준다.

    회사 전용 키가 아니면(예전 키·없는 키) 모두 같은 "모르는 키" 응답이다. 로그인 없이 예전 키의
    회사 정보(미리 채울 값)를 내주지 않는다(재헌 리뷰 11/24).
    """
    _limit_public(request, body.key)
    return ok(_check(body.key, None, public=True))


def _check(key: str, authorization: str | None, *, public: bool = False) -> dict[str, Any]:
    data = _load()
    found = _find(data, key)
    bound = (
        company_scope.scope_id(found["for_company"]) if found and found.get("for_company") else None
    )
    session = _session_cid(authorization)
    if public and not bound:  # 로그인 전 경로는 예전 키가 있는지조차 알려 주지 않는다
        return {"ok": False, "reason": _UNKNOWN_KEY}
    if bound and session is None:
        # 로그인 전: 그 회사 전용 키. 첫 관리자 가입이 되는지만 본다(연결은 가입·로그인 뒤)
        problem = _why_not(data, found, bound)
        if problem:
            return {"ok": False, "reason": problem[1]}
        assert found is not None
        signup = _signup_problem(found, bound) is None
        return {
            "ok": True,
            "reason": None,
            "company": _registered(bound),
            "company_id": bound,
            "signup": signup,
        }
    problem = _why_not(data, found)
    if problem:
        return {"ok": False, "reason": problem[1]}
    assert found is not None
    # 미리 채울 회사 정보는 그 키를 전에 쓴 회사가 이 회사일 때만(다른 회사 이름·업종은 안 보임)
    prefill = (found.get("company") or None) if _owner(found, data) == _cid() else None
    if prefill is None and bound:
        prefill = _registered(bound)
    out: dict[str, Any] = {"ok": True, "reason": None, "company": prefill}
    if bound:  # 회사 전용 키만 칸을 더한다(예전 키 응답은 그대로)
        out.update(company_id=bound, signup=False)
    return out


def _signup_problem(found: dict[str, Any], bound: str) -> tuple[str, str] | None:
    if found.get("signup_at"):
        return (
            "KEY_SIGNUP_USED",
            "이 키로는 이미 관리자 가입을 했어요. 그 계정으로 로그인해 주세요.",
        )
    if accounts.company_has_admin(bound):
        return "COMPANY_HAS_ADMIN", "이 회사에는 관리자가 있어요. 그 관리자에게 계정을 받아 주세요."
    return None


@signup_router.post("/signup")
def signup(body: SignupIn, request: Request) -> dict:
    """회사 전용 키로 그 회사의 첫 관리자 가입(로그인 전). 성공하면 로그인 세션을 준다.

    순서(재헌 리뷰 11/24): 키 파일에 "가입에 씀"을 먼저 적고 잠금을 푼 뒤 계정을 만든다.
    비밀번호 해시는 잠금 밖에서 계산한다(요청이 몰려도 키 파일 잠금을 오래 쥐지 않게).
    계정을 못 만들면 키 표시를 되돌린다.
    """
    _limit_public(request, body.key)
    accounts.check_new_password(body.password)
    with _file_lock:
        data = _load()
        found = _find(data, body.key)
        bound = found.get("for_company") if found else None
        if found is None or not bound:
            raise ApiError(
                422, "KEY_NOT_FOR_SIGNUP", "회사 전용으로 발급한 키로만 관리자 가입을 할 수 있어요"
            )
        bound = company_scope.scope_id(bound)
        problem = _why_not(data, found, bound) or _signup_problem(found, bound)
        if problem:
            raise ApiError(409 if problem[0] != "KEY_REVOKED" else 422, problem[0], problem[1])
        key_id = found["id"]
        found["signup_at"] = _now()
        _save(data)
    try:
        account, login = accounts.create_first_admin(body.name, body.email, body.password, bound)
    except Exception:
        _undo_signup_mark(key_id)
        raise
    with _file_lock:
        data = _load()
        row = next((k for k in data["keys"] if k["id"] == key_id), None)
        if row is not None:
            row["signup_account"] = account["id"]
            _save(data)
    return ok(login)


def _undo_signup_mark(key_id: str, extra: tuple[str, ...] = ()) -> None:
    """계정 쪽이 실패하면 키 줄의 "가입·옮기기에 씀" 표시를 지운다(다시 시도할 수 있게)."""
    with _file_lock:
        data = _load()
        row = next((k for k in data["keys"] if k["id"] == key_id), None)
        if row is not None:
            for name in ("signup_at", "signup_account", *extra):
                row.pop(name, None)
            _save(data)


@router.post("/connect", dependencies=ADMIN_ONLY)
def connect(body: ConnectIn, authorization: str | None = Header(None)) -> dict:
    """세션 회사의 줄에만 저장한다. 다른 회사 줄(캣북스 포함)은 그대로."""
    cid = _cid()
    with _file_lock:
        data = _load()
        found = _find(data, body.key)
        problem = _why_not(data, found)
        if problem:
            raise ApiError(422, problem[0], problem[1])
        assert found is not None
        info = _info(body)
        if not info["name"]:
            raise ApiError(422, "NAME_REQUIRED", "회사 이름을 넣어 주세요")
        prev = _record(data, cid)
        for k in data["keys"]:
            if prev and k["id"] == prev.get("key_id") and k["id"] != found["id"]:
                _release(k)  # 다른 키로 바꾸면 예전 키 줄은 연결 전으로(회사·회사 정보 지움)
        company = {"id": f"c-{found['id']}", **info, "key_id": found["id"], "connected_at": _now()}
        found["status"] = "connected"
        found["company"] = info
        found["company_id"] = cid
        _set_record(data, cid, company)
        _save(data)
    return ok(_state(data, everyone=_sees_all(authorization)))


@router.post("/move", dependencies=ADMIN_ONLY)
def move(body: KeyIn, authorization: str | None = Header(None)) -> dict:
    """지금 회사 연결을 끊은 관리자가 다른 회사 전용 키로 회사를 옮긴다(로드맵 7단계 B안).

    그 관리자와 연결된 일반회원(그 관리자가 만든 회원)이 키의 회사로 옮겨 가고, 지금 회사의
    기록·연결 줄은 그대로 남는다(다시 연결하면 그대로 돌아온다). 옮긴 뒤 세션은 새 회사로
    다시 주고, 연결은 그 세션으로 `/connect`에서 한다.
    지금 회사가 연결 중이면 먼저 끊어야 한다(`DISCONNECT_FIRST`).
    """
    human = session_human(authorization)
    if not human.account_id:
        raise ApiError(403, "ROLE_REQUIRED", "관리자 로그인으로만 회사를 옮길 수 있어요")
    old = _cid()
    with _file_lock:
        data = _load()
        found = _find(data, body.key)
        bound = found.get("for_company") if found else None
        if found is None or not bound:
            raise ApiError(
                422, "KEY_NOT_FOR_MOVE", "회사 전용으로 발급한 키로만 회사를 옮길 수 있어요"
            )
        new = company_scope.scope_id(bound)
        if new == old:
            raise ApiError(
                409, "SAME_COMPANY", "지금 회사의 키예요. 회사 연결에서 바로 연결해 주세요"
            )
        if _record(data, old):
            raise ApiError(
                409, "DISCONNECT_FIRST", "지금 회사 연결을 먼저 끊어 주세요. 기록은 그대로 남아요"
            )
        problem = _why_not(data, found, new) or _signup_problem(found, new)
        if problem:
            raise ApiError(409 if problem[0] != "KEY_REVOKED" else 422, problem[0], problem[1])
        key_id = found["id"]
        # 키에 먼저 적고(가입과 같은 순서) 계정을 옮긴다. 옮기기가 실패하면 되돌린다
        found["signup_at"] = _now()
        found["signup_account"] = human.account_id
        found["moved_from"] = old
        _save(data)
    try:
        ids, login = accounts.move_company(human.account_id, old, new)
    except Exception:
        _undo_signup_mark(key_id, ("moved_from",))
        raise
    return ok({**login, "moved_accounts": len(ids), "from_company": old})


@router.post("/company", dependencies=ADMIN_ONLY)
def update_company(body: InfoIn, authorization: str | None = Header(None)) -> dict:
    with _file_lock:
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
    with _file_lock:
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
    cid = _cid()
    with _file_lock:
        data = _load()
        company = _record(data, cid)
        if company:
            for k in data["keys"]:
                if k["id"] == company.get("key_id") and k["status"] == "connected":
                    _release(k)  # 끊은 키 줄은 연결 전으로(회사·회사 정보 지움)
            _set_record(data, cid, None)
            _save(data)
    return ok(_state(data, everyone=_sees_all(authorization)))
