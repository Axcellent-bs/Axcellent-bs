"""계정·로그인: 사람마다 다른 세션 토큰으로 쓰게 한다 (사람별 토큰 설계 1단계).

- 파일: `AX_ACCOUNTS_FILE` → 회사 연결 파일 옆 `accounts.json` (연결 키처럼).
  없으면 로그인 꺼짐
- 칸: id, 이름, 이메일, 역할(`operator`·`admin`·`member`), 비밀번호 해시(scrypt), 만든 시각,
  프로필(부서 `dept`, 확인 권한 `approver`, 열람 담당 `readers`, 비용 상한 변경 권한
  `limit_editor`, 정책 변경 권한 `policy_editor`).
  키 담당 `secret_editor`와 담당 고객사 목록 `secret_tenants`(암호화 키 관리, `secret_routes.py`):
  운영자는 누구에게나, 고객사 관리자는 자기 담당 고객사 안에서만, 자기 자신에게는 못 준다.
  구매자 연결 `buyer_id`(shop-api 구매자 번호 `buy_0001` 꼴): 이 계정이 총괄 대화방에서
  환불·교환 같은 구매자 일을 맡길 때 어느 구매자의 주문인지를 서버가 정하는 값이다
  (대화 문장·화면이 보낸 값은 쓰지 않는다). 운영자·고객사 관리자가 정하고,
  한 구매자는 계정 하나에만 연결한다. 자기 계정의 연결은 운영자만 바꾼다.
  다른 서비스는 `buyer_id_of_account(계정 번호)`로 읽는다.
  `limit_editor`·`policy_editor`는 운영자·고객사 관리자 계정에만 켤 수 있다(일반회원은 못 켠다,
  일반회원으로 내리면 꺼진다). `policy_editor`는 운영자 또는 `policy_editor`인 관리자만 준다.
  열람 담당 값은 화면 `members.ts`의 `ReaderArea`와 같다
- 회사 소속 `memberships: [{company_id, role}]`(여러 회사 설계 1-3, 계약 `Membership`).
  계정은 회사 밖(전역)에 있고, 회사 안의 역할은 `admin`·`member`다. 칸이 없는 옛 계정은
  읽을 때만 캣북스 소속으로 본다(관리자·일반회원 → 캣북스의 그 역할, 운영자 → 소속 없음).
  파일을 고쳐 쓰지 않으므로 이 기능을 되돌려도 계정 파일은 그대로다. 칸이 있으면 그 값을 쓰고
  (별칭은 정식 회사 ID로, 형식이 깨진 줄·회사 목록에 없는 회사는 버린다), 칸이 목록이 아니면
  소속 없음으로 본다. 권한 검사는 1단계에서는 지금처럼 계정 역할(`role`)로 한다
- 첫 계정: 계정이 하나도 없을 때 `AX_BOOTSTRAP_ADMIN="이메일:초기 비밀번호"`로 운영자 1명을 만든다
- 같은 이메일로 연속 5번 틀리면 15분 동안 로그인을 막는다(맞는 비밀번호여도).
  이 프로세스 메모리에 센다
- 토큰 만들기·검사는 공용 `ax_agent_template.human_auth`. 서명 키 `AX_SESSION_SECRET`

경로
- `GET  /api/dashboard/auth/status` → {login_enabled}
  (화면이 서버 로그인과 데모 로그인 중 무엇을 쓸지 고른다)
- `POST /api/dashboard/auth/login` {email, password} → {token, expires_at, account, companies}
  (companies = 소속 목록. 토큰은 첫 소속 회사로 서명한다(`cid`·`crole`), 소속이 없으면 회사 없이)
- `POST /api/dashboard/auth/logout` (세션 토큰) → 끊은 토큰 목록에 올린다
- `GET  /api/dashboard/auth/me` (세션 토큰) → 내 계정, companies, 지금 회사(company_id·company_role)
- `GET  /api/dashboard/auth/accounts`
  (운영자: 모든 계정 / 고객사 관리자: 고객사 계정(admin·member)만) → 계정 목록
  (공개 칸만. 비밀번호 해시·세대 번호 같은 내부 값은 내보내지 않는다)
- `POST /api/dashboard/auth/accounts/{id}/update`
  {role?, name?, dept?, approver?, readers?, buyer_id?}
  (운영자: 모든 계정 / 고객사 관리자: 고객사 계정만, 운영자 역할로 올리지 못한다)
  → 역할·프로필 바꾸기.
  자기 역할은 스스로 바꾸지 못하고, 마지막 남은 고객사 관리자는 내리지 못한다.
  역할이 바뀌면 그 계정의 기존 세션을 모두 끊는다(토큰에 역할이 들어 있어서)
  nginx 게이트가 POST만 열어서 PATCH 대신 POST
- `POST /api/dashboard/auth/accounts` (운영자: 모든 역할 / 고객사 관리자: 일반회원만) → 계정 만들기
- `POST /api/dashboard/auth/password` (세션 토큰) {current_password, new_password}
  → 내 비밀번호 바꾸기. 현재 비밀번호가 틀리면 로그인 실패와 같이 센다. 성공하면 그 계정의
  기존 세션을 모두 끊고 새 세션 토큰을 돌려준다
- `POST /api/dashboard/auth/accounts/{id}/reset-password` {new_password}
  (운영자: 모든 계정 / 고객사 관리자: 일반회원만) → 임시 비밀번호로 초기화, 그 계정의 세션을 끊는다

고객사 관리자는 자기 세션 회사 계정만 보고·바꾸고·초기화한다(다른 회사 계정은 404).
마지막 관리자 검사도 회사마다 센다. 고객사 첫 관리자는 그 회사 전용 연결 키로 가입한다
(`create_first_admin`, 경로는 `connection_keys.py`의 `POST /connection-keys/signup`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field, ValidationError

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection as store
from ax_agent_template import human_auth
from ax_contracts.schemas import Membership
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS
from ax_dashboard_api.envelope import ApiError, ok
from ax_secret_store.service import TENANT_RE

ACCOUNTS_FILE_ENV = "AX_ACCOUNTS_FILE"
ACCOUNTS_FILE = "accounts.json"
BOOTSTRAP_ENV = "AX_BOOTSTRAP_ADMIN"
MIN_PASSWORD_LENGTH = 10
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
# scrypt: 메모리 16MiB(128 * n * r), 로그인 한 번에 수십 ms
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2**14, 8, 1, 32
# 열람 담당 영역: 화면 `frontend/src/demo/members.ts`의 READER_AREAS id와 같다
READER_AREAS = ("hr", "finance")
CUSTOMER_ROLES = ("admin", "member")
MAX_SECRET_TENANTS = 20
EMAIL_RE = re.compile(r"^[^@\s:]+@[^@\s:]+\.[^@\s:]+$")
# shop-api 구매자 번호: `buy_` + 4자리 (고객지원 `buyer_ids.py`와 같은 규칙).
# \d는 유니코드 숫자도 받아서 쓰지 않는다
BUYER_ID_RE = re.compile(r"^buy_[0-9]{4}$")

logger = logging.getLogger("ax.dashboard.accounts")
_file_lock = threading.Lock()
_fail_lock = threading.Lock()
_failures: dict[str, tuple[int, float]] = {}  # 이메일 → (연속 실패 수, 막힘이 풀리는 시각)

router = APIRouter(prefix="/api/dashboard/auth")
Role = Literal["operator", "admin", "member"]
ReaderArea = Literal["hr", "finance"]


# ---- 비밀번호 ----


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN
    )
    enc = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${enc(salt)}${enc(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if kind != "scrypt":
            return False
        salt, want = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        got = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(want)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# 없는 이메일도 같은 시간이 걸리게 비교할 가짜 해시 (계정이 있는지 응답 시간으로 알 수 없게)
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# ---- 파일 ----


def _path() -> Path | None:
    raw = os.getenv(ACCOUNTS_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = store.connection_file()
    return Path(conn).with_name(ACCOUNTS_FILE) if conn else None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _norm_email(email: str) -> str:
    return email.strip().lower()


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("accounts") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"accounts": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _new_row(
    name: str,
    email: str,
    role: str,
    password: str,
    company_id: str | None = None,
    *,
    password_hash: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "acc-" + secrets.token_hex(6),
        "name": name,
        "email": _norm_email(email),
        "role": role,
        "password_hash": password_hash or hash_password(password),
        "created_at": _now(),
    }
    if role in CUSTOMER_ROLES:
        # 만든(초대한) 회사에 소속시킨다. 회사를 모르면 설치 기본 회사. 운영자는 소속 없음.
        target = company_list.canonical_company_id(company_id) or company_list.default_company_id()
        if (
            target != TENANT_ID_CATBOOKS
        ):  # 칸이 없으면 옛 규칙대로 캣북스로 읽으니 파일 모양을 그대로 둔다
            row["memberships"] = [{"company_id": target, "role": role}]
    return row


def _bootstrap(path: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """계정이 하나도 없을 때만 부트스트랩 운영자를 만든다. 계정이 생기면 다시 보지 않는다."""
    if rows:
        return rows
    email, sep, password = os.getenv(BOOTSTRAP_ENV, "").partition(":")
    email = _norm_email(email)
    if not sep:
        return rows
    if not EMAIL_RE.match(email) or len(password) < MIN_PASSWORD_LENGTH:
        logger.warning(
            "%s 형식이 맞지 않아 첫 운영자를 만들지 않았다 (이메일:비밀번호, 비밀번호 %d자 이상)",
            BOOTSTRAP_ENV,
            MIN_PASSWORD_LENGTH,
        )
        return rows
    row = _new_row(email.split("@", 1)[0], email, "operator", password)
    _write(path, [row])
    logger.info("첫 운영자 계정을 만들었다: %s", email)
    return [row]


def _name_key(name: str) -> str:
    """이름 비교 키: 앞뒤 공백·대소문자·연속 공백 차이는 같은 이름으로 본다."""
    return " ".join(name.split()).casefold()


def _company_ids(row: dict[str, Any]) -> set[str]:
    return {m.company_id for m in memberships_of(row)}


def _name_taken(
    rows: list[dict[str, Any]], name: str, companies: set[str], *, except_id: str | None = None
) -> bool:
    """같은 회사 안에 같은 이름의 다른 계정이 있나 (#1562).

    대화 본인 확인이 계정 이름으로 돼 있어서, 같은 회사에 이름이 같은 계정이 둘이면 서로의
    AI 대화를 읽을 수 있다. 회사가 다르면 대화도 회사별이라 같은 이름이어도 된다.
    """
    key = _name_key(name)
    return any(
        r.get("id") != except_id
        and _name_key(str(r.get("name") or "")) == key
        and companies & _company_ids(r)
        for r in rows
    )


_warned_duplicate_names = False


def _warn_duplicate_names(rows: list[dict[str, Any]]) -> None:
    """예전 계정 파일에 같은 회사 안 같은 이름이 있으면 시작 때 한 번 경고한다.

    이름 값은 로그에 남기지 않는다.
    """
    global _warned_duplicate_names
    if _warned_duplicate_names:
        return
    _warned_duplicate_names = True
    seen: dict[tuple[str, str], str] = {}
    pairs = 0
    for r in rows:
        for cid in _company_ids(r):
            k = (cid, _name_key(str(r.get("name") or "")))
            if k in seen:
                pairs += 1
            else:
                seen[k] = str(r.get("id"))
    if pairs:
        logger.warning(
            "같은 회사 안에 이름이 같은 계정이 %d쌍 있어요. 대화 본인 확인이 섞일 수 있으니 "
            "계정 관리에서 이름을 바꿔 주세요 (#1562)",
            pairs,
        )


def load_accounts() -> list[dict[str, Any]]:
    path = _path()
    if path is None:
        return []
    with _file_lock:
        rows = _bootstrap(path, _read(path))
    _warn_duplicate_names(rows)
    return rows


def secret_tenants_of(row: dict[str, Any]) -> list[str]:
    """키 담당 고객사 목록(형식이 맞는 것만, 중복 없이)."""
    raw = row.get("secret_tenants")
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(t for t in raw if isinstance(t, str) and TENANT_RE.match(t)))


def memberships_of(row: dict[str, Any]) -> list[Membership]:
    """계정의 회사 소속. 회사 목록에 있는 회사만, 같은 회사는 한 번만(파일 줄 순서대로).

    칸이 없는 옛 계정은 캣북스 소속으로 본다(관리자·일반회원 → 그 역할, 운영자 → 없음).
    """
    if "memberships" not in row:
        role = row.get("role")
        if role not in CUSTOMER_ROLES:
            return []
        raw: Any = [{"company_id": TENANT_ID_CATBOOKS, "role": role}]
    else:
        raw = row.get("memberships")
        if not isinstance(raw, list):
            logger.warning("계정 %s의 소속 칸 모양이 달라 소속 없음으로 본다", row.get("id"))
            return []
    known = set(company_list.company_ids())
    out: list[Membership] = []
    for item in raw:
        try:
            m = Membership.model_validate(item)  # 계약 TenantId가 별칭을 정식 id로 바꾼다
        except ValidationError:
            continue
        if m.company_id in known and all(o.company_id != m.company_id for o in out):
            out.append(m)
    return out


def _companies_out(memberships: list[Membership]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in memberships]


def _issue_for(row: dict[str, Any]) -> tuple[str, int, list[Membership]]:
    """계정의 세션 토큰. 첫 소속 회사로 서명한다(회사 고르기는 뒤 단계에서)."""
    memberships = memberships_of(row)
    first = memberships[0] if memberships else None
    token, exp = human_auth.issue_session(
        account_id=str(row["id"]),
        name=str(row["name"]),
        role=str(row["role"]),
        company_id=first.company_id if first else None,
        company_role=first.role if first else None,
        # 고객사 계정인데 소속이 없으면(회사에서 끊김) 캣북스로 읽히지 않게 표시한다
        no_company=first is None and row.get("role") in CUSTOMER_ROLES,
    )
    return token, exp, memberships


def _sync_company_membership(
    row: dict[str, Any], new_role: str, company_id: str | None = None
) -> None:
    """계정 역할이 고객사 역할로 바뀌면, 적어 둔 해당 회사 소속의 역할도 같이 바꾼다.

    company_id가 없으면 설치 기본 회사. 소속 칸이 없는 계정은 역할에서 읽으므로 손대지 않는다.
    """
    stored = row.get("memberships")
    if new_role not in CUSTOMER_ROLES or not isinstance(stored, list):
        return
    target = company_list.canonical_company_id(company_id) or company_list.default_company_id()
    for item in stored:
        if (
            isinstance(item, dict)
            and company_list.canonical_company_id(item.get("company_id")) == target
        ):
            item["role"] = new_role


def _sync_catbooks_membership(row: dict[str, Any], new_role: str) -> None:
    """옛 이름(호환용). 캣북스 소속의 역할을 맞춘다."""
    _sync_company_membership(row, new_role, TENANT_ID_CATBOOKS)


def _buyer_id_of(row: dict[str, Any]) -> str | None:
    """연결한 구매자 번호(형식이 맞을 때만). 없으면 None."""
    value = row.get("buyer_id")
    return value if isinstance(value, str) and BUYER_ID_RE.match(value) else None


def buyer_id_of_account(account_id: str) -> str | None:
    """계정에 연결한 shop-api 구매자 번호. 연결이나 계정이 없으면 None (지어내지 않는다)."""
    for row in load_accounts():
        if row.get("id") == account_id:
            return _buyer_id_of(row)
    return None


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """밖으로 내보내는 칸만 고른다(허용 목록). 해시·비밀번호 바꾼 시각 같은 내부 값은 넣지 않는다.

    확인 권한을 한 번도 정하지 않은 계정은 역할로 정한다(고객사 관리자는 있음, 일반회원은 없음).
    """
    out = {k: row.get(k) for k in ("id", "name", "email", "role", "created_at")}
    dept = row.get("dept")
    out["dept"] = dept if isinstance(dept, str) and dept else None
    out["buyer_id"] = _buyer_id_of(row)
    approver = row.get("approver")
    out["approver"] = approver if isinstance(approver, bool) else row.get("role") == "admin"
    readers = row.get("readers")
    out["readers"] = [r for r in READER_AREAS if r in readers] if isinstance(readers, list) else []
    can_edit = row.get("role") in ("operator", "admin")
    out["limit_editor"] = can_edit and row.get("limit_editor") is True
    out["policy_editor"] = can_edit and row.get("policy_editor") is True
    out["secret_editor"] = can_edit and row.get("secret_editor") is True
    out["secret_tenants"] = secret_tenants_of(row) if can_edit else []
    linked = row.get("linked_admin")
    out["linked_admin"] = linked if isinstance(linked, str) and linked else None
    return out


def _in_my_company(human: human_auth.Human, row: dict[str, Any]) -> bool:
    """고객사 관리자가 다룰 수 있는 계정인가: 내 세션 회사에 소속된 고객사 계정."""
    return (
        row.get("role") in CUSTOMER_ROLES
        and human.company_id is not None
        and human.company_id in _company_ids(row)
    )


# ---- 실패 횟수 제한 ----


def _locked_until(email: str, now: float) -> float | None:
    with _fail_lock:
        count, until = _failures.get(email, (0, 0.0))
        if until and until <= now:
            _failures.pop(email, None)  # 막힘이 풀리면 처음부터 다시 센다
            return None
        return until if count >= MAX_FAILURES else None


def _record_failure(email: str, now: float) -> None:
    with _fail_lock:
        count = _failures.get(email, (0, 0.0))[0] + 1
        _failures[email] = (count, now + LOCK_SECONDS if count >= MAX_FAILURES else 0.0)


def _clear_failures(email: str) -> None:
    with _fail_lock:
        _failures.pop(email, None)


def reset_failures_for_tests() -> None:
    with _fail_lock:
        _failures.clear()


# ---- 세션 ----


def _api_error(err: human_auth.HumanAuthError) -> ApiError:
    return ApiError(err.status_code, err.code, err.message)


def session_human(authorization: str | None, *, allow_no_company: bool = False) -> human_auth.Human:
    """로그인 세션 토큰만 받는다(공용 토큰은 계정이 아니라 여기서 거절).

    소속 회사가 없는 계정의 세션은 403 `NO_COMPANY`로 거절한다. 내 정보·로그아웃·비밀번호
    바꾸기만 allow_no_company=True로 받는다.
    """
    token = human_auth.bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요하다 (Authorization: Bearer 토큰)")
    if not human_auth.is_session_token(token):
        raise ApiError(403, "SESSION_REQUIRED", "로그인한 계정의 토큰이 필요하다")
    try:
        return human_auth.verify_session(token, allow_no_company=allow_no_company)
    except human_auth.HumanAuthError as err:
        raise _api_error(err) from None


def require_session_role(*roles: str) -> Callable[..., None]:
    """로그인 세션 토큰이면 역할을 본다. 공용 토큰·토큰 없음은 그대로 둔다(옮기는 동안, 설계 3절).

    공용 토큰은 누가 쓰는지 몰라 역할이 없으므로 이 검사를 건너뛰고 기존 사람 토큰 검사
    (`auth.require_human`)에만 맡긴다. 공용 토큰을 끄는 단계(설계 5단계)에서 막힌다.
    """

    def dependency(authorization: str | None = Header(None)) -> None:
        token = human_auth.bearer(authorization)
        if not token or not human_auth.is_session_token(token):
            return
        if session_human(authorization).role not in roles:
            raise ApiError(403, "ROLE_REQUIRED", "이 계정의 역할로는 할 수 없어요")

    return dependency


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=200)
    role: Role
    password: str = Field(max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


class PasswordResetIn(BaseModel):
    new_password: str = Field(max_length=200)


class AccountUpdateIn(BaseModel):
    """보낸 칸만 바꾼다. 부서는 빈 문자열이면 지운다"""

    role: Role | None = None
    name: str | None = Field(None, min_length=1, max_length=80)
    dept: str | None = Field(None, max_length=40)
    buyer_id: str | None = Field(None, max_length=20)  # 빈 문자열이면 연결을 푼다
    approver: bool | None = None
    readers: list[ReaderArea] | None = Field(None, max_length=8)
    limit_editor: bool | None = None
    policy_editor: bool | None = None
    secret_editor: bool | None = None
    secret_tenants: list[str] | None = Field(None, max_length=MAX_SECRET_TENANTS)


def login_enabled() -> bool:
    return human_auth.sessions_enabled() and _path() is not None


@router.get("/status")
def status() -> dict:
    """서버 로그인이 켜져 있는지. 꺼져 있으면 화면은 데모 로그인을 쓴다(설계 1~3단계)."""
    return ok({"login_enabled": login_enabled()})


def _raise_if_locked(email: str, now: float) -> None:
    until = _locked_until(email, now)
    if until is not None:
        minutes = max(1, int((until - now + 59) // 60))
        raise ApiError(
            429,
            "LOGIN_LOCKED",
            f"로그인을 여러 번 틀려 잠시 막았어요. {minutes}분 뒤 다시 해 주세요",
        )


def check_new_password(password: str) -> None:
    """새 비밀번호 길이 검사(키 가입이 잠금 전에 부른다)."""
    _check_new_password(password)


def _check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(
            422, "PASSWORD_TOO_SHORT", f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 해요"
        )


def _editable_path() -> Path:
    path = _path()
    if path is None:
        raise ApiError(
            409, "ACCOUNTS_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )
    return path


def _set_password(row: dict[str, Any], password: str) -> None:
    row["password_hash"] = hash_password(password)
    row["password_changed_at"] = _now()


@router.post("/login")
def login(body: LoginIn) -> dict:
    if not human_auth.sessions_enabled():
        raise ApiError(503, "LOGIN_DISABLED", f"{human_auth.SESSION_SECRET_ENV}가 설정되지 않았다")
    if _path() is None:
        raise ApiError(503, "LOGIN_DISABLED", f"{store.CONNECTION_FILE_ENV}가 없어 계정이 없다")
    email = _norm_email(body.email)
    now = time.time()
    _raise_if_locked(email, now)
    row = next((r for r in load_accounts() if r.get("email") == email), None)
    matched = check_password(body.password, str(row["password_hash"]) if row else _DUMMY_HASH)
    if row is None or not matched:
        _record_failure(email, now)
        raise ApiError(401, "LOGIN_FAILED", "이메일이나 비밀번호가 맞지 않아요")
    _clear_failures(email)
    token, exp, memberships = _issue_for(row)
    return ok(
        {
            "token": token,
            "expires_at": exp,
            "account": _public(row),
            "companies": _companies_out(memberships),
        }
    )


@router.post("/logout")
def logout(authorization: str | None = Header(None)) -> dict:
    human = session_human(authorization, allow_no_company=True)
    human_auth.revoke_session(human)
    return ok({"logged_out": True})


@router.get("/me")
def me(authorization: str | None = Header(None)) -> dict:
    human = session_human(authorization, allow_no_company=True)
    row = next((r for r in load_accounts() if r.get("id") == human.account_id), None)
    if row is None:
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return ok(
        {
            "account": _public(row),
            "expires_at": human.expires_at,
            "companies": _companies_out(memberships_of(row)),
            "company_id": human.company_id,
            "company_role": human.company_role,
        }
    )


@router.get("/accounts")
def list_accounts(authorization: str | None = Header(None)) -> dict:
    """운영자는 모든 계정, 고객사 관리자는 고객사 계정(admin·member)만 본다."""
    human = session_human(authorization)
    if human.role == "operator":
        visible = load_accounts()
    elif human.role == "admin":
        visible = [r for r in load_accounts() if _in_my_company(human, r)]
    else:
        raise ApiError(403, "ROLE_REQUIRED", "운영자와 고객사 관리자만 볼 수 있어요")
    rows = [_public(r) for r in visible]
    return ok(rows, total=len(rows))


@router.post("/accounts")
def create_account(body: AccountIn, authorization: str | None = Header(None)) -> dict:
    human = session_human(authorization)
    allowed = {"operator": human_auth.ROLES, "admin": ("member",)}.get(human.role, ())
    if body.role not in allowed:
        raise ApiError(403, "ROLE_REQUIRED", "이 역할의 계정을 만들 권한이 없어요")
    email = _norm_email(body.email)
    if not EMAIL_RE.match(email):
        raise ApiError(422, "EMAIL_INVALID", "이메일 형식이 아니에요")
    _check_new_password(body.password)
    path = _editable_path()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        if any(r.get("email") == email for r in rows):
            raise ApiError(409, "EMAIL_TAKEN", "이미 있는 이메일이에요")
        row = _new_row(body.name.strip(), email, body.role, body.password, human.company_id)
        if body.role == "member" and human.role == "admin" and human.account_id:
            row["linked_admin"] = (
                human.account_id
            )  # 이 회원을 데려온 관리자(회사 옮기기 때 같이 간다)
        if _name_taken(rows, row["name"], _company_ids(row)):
            raise ApiError(409, "NAME_TAKEN", "같은 회사에 이름이 같은 계정이 있어요")
        rows.append(row)
        _write(path, rows)
    logger.info("계정을 만들었다: %s(%s) by %s", email, body.role, human.name)
    return ok(_public(row))


def company_has_admin(company_id: str) -> bool:
    """그 회사에 고객사 관리자가 있나 (키 가입은 첫 관리자만)."""
    return any(r.get("role") == "admin" and company_id in _company_ids(r) for r in load_accounts())


def create_first_admin(
    name: str, email: str, password: str, company_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """회사 전용 연결 키로 그 회사의 첫 관리자를 만든다(`connection_keys.signup`이 부른다).

    키 검사·키 쓰임 표시는 부르는 쪽(키 파일 잠금 안)에서 한다. 여기서는 계정 파일 잠금 안에서
    이메일·이름 중복과 "그 회사에 관리자가 이미 있는가"를 다시 보고 만든 뒤, 로그인 응답을 돌려준다.
    돌려주는 값: (공개 계정, 로그인 데이터 {token, expires_at, account, companies})
    """
    email = _norm_email(email)
    if not EMAIL_RE.match(email):
        raise ApiError(422, "EMAIL_INVALID", "이메일 형식이 아니에요")
    _check_new_password(password)
    hashed = hash_password(
        password
    )  # 잠금 밖에서 계산한다(요청이 몰려도 계정 파일 잠금을 오래 쥐지 않게)
    path = _editable_path()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        if any(r.get("email") == email for r in rows):
            raise ApiError(409, "EMAIL_TAKEN", "이미 있는 이메일이에요")
        if any(r.get("role") == "admin" and company_id in _company_ids(r) for r in rows):
            raise ApiError(
                409,
                "COMPANY_HAS_ADMIN",
                "이 회사에는 관리자가 있어요. 그 관리자에게 계정을 받아요",
            )
        row = _new_row(name.strip(), email, "admin", password, company_id, password_hash=hashed)
        if company_id not in _company_ids(row):  # 회사 목록에 없는 회사(읽을 때 버려진다)
            raise ApiError(409, "UNKNOWN_COMPANY", "등록되지 않은 회사예요")
        if _name_taken(rows, row["name"], _company_ids(row)):
            raise ApiError(409, "NAME_TAKEN", "같은 회사에 이름이 같은 계정이 있어요")
        rows.append(row)
        _write(path, rows)
    logger.info("키로 첫 관리자를 만들었다: %s(%s)", email, company_id)
    token, exp, memberships = _issue_for(row)
    login = {
        "token": token,
        "expires_at": exp,
        "account": _public(row),
        "companies": _companies_out(memberships),
    }
    return _public(row), login


def _swap_company(row: dict[str, Any], old: str, new: str) -> None:
    """계정 소속의 old 회사를 new 회사로 바꾼다(역할 그대로). 칸 없는 옛 계정은 칸을 적는다."""
    stored = [m.model_dump(mode="json") for m in memberships_of(row)]
    row["memberships"] = [{**m, "company_id": new} if m["company_id"] == old else m for m in stored]


def move_company(admin_id: str, old: str, new: str) -> tuple[list[str], dict[str, Any]]:
    """관리자가 회사를 옮긴다(로드맵 7단계 B안): 그 관리자와 연결된 일반회원이 new 회사로.

    연결된 일반회원 = 그 관리자가 만든(데려온) 회원(`linked_admin`). old 회사의 다른 관리자와
    그들의 회원, 연결 표시가 없는 옛 회원은 그대로 둔다. 옮긴 계정의 지금 세션은 끊는다
    (세션에 옛 회사가 적혀 있어서). 키 검사·"old 연결을 먼저 끊었는가"는 부르는 쪽
    (키 파일 잠금 안)에서 한다.
    돌려주는 값: (옮긴 계정 id들, 관리자 로그인 데이터 {token, expires_at, account, companies})
    """
    path = _editable_path()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        if new not in company_list.company_ids():
            raise ApiError(409, "UNKNOWN_COMPANY", "등록되지 않은 회사예요")
        if any(r.get("role") == "admin" and new in _company_ids(r) for r in rows):
            raise ApiError(
                409,
                "COMPANY_HAS_ADMIN",
                "옮길 회사에 이미 관리자가 있어요. 그 관리자에게 계정을 받아요",
            )
        admin = next((r for r in rows if str(r.get("id")) == admin_id), None)
        if admin is None or admin.get("role") != "admin" or old not in _company_ids(admin):
            raise ApiError(403, "ROLE_REQUIRED", "이 회사 관리자만 회사를 옮길 수 있어요")
        members = [
            r
            for r in rows
            if r.get("role") == "member"
            and r.get("linked_admin") == admin_id
            and old in _company_ids(r)
        ]
        moved = [admin, *members]
        for row in moved:
            _swap_company(row, old, new)
        _write(path, rows)
    ids = [str(r["id"]) for r in moved]
    for account_id in ids:
        human_auth.end_account_sessions(account_id)
    logger.info("회사를 옮겼다: %s → %s, 계정 %s개(관리자 %s)", old, new, len(ids), admin_id)
    token, exp, memberships = _issue_for(admin)
    login = {
        "token": token,
        "expires_at": exp,
        "account": _public(admin),
        "companies": _companies_out(memberships),
    }
    return ids, login


@router.post("/password")
def change_password(body: PasswordChangeIn, authorization: str | None = Header(None)) -> dict:
    """내 비밀번호 바꾸기. 성공하면 이 계정의 기존 세션을 모두 끊고 새 세션을 준다."""
    human = session_human(authorization, allow_no_company=True)
    _check_new_password(body.new_password)
    path = _editable_path()
    now = time.time()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        row = next((r for r in rows if r.get("id") == human.account_id), None)
        if row is None:
            raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
        email = str(row.get("email", ""))
        _raise_if_locked(email, now)
        # check_password는 hmac.compare_digest로 비교한다(상수 시간)
        if not check_password(body.current_password, str(row.get("password_hash", ""))):
            _record_failure(email, now)  # 로그인 실패와 같은 잠금 규칙에 같이 센다
            raise ApiError(401, "PASSWORD_WRONG", "현재 비밀번호가 맞지 않아요")
        _clear_failures(email)
        if hmac.compare_digest(
            body.new_password.encode("utf-8"), body.current_password.encode("utf-8")
        ):
            raise ApiError(422, "PASSWORD_SAME", "새 비밀번호는 지금 비밀번호와 달라야 해요")
        _set_password(row, body.new_password)
        _write(path, rows)
    human_auth.end_account_sessions(str(row["id"]))
    token, exp, memberships = _issue_for(row)
    logger.info("비밀번호를 바꿨다: %s", row["id"])
    return ok(
        {
            "token": token,
            "expires_at": exp,
            "account": _public(row),
            "companies": _companies_out(memberships),
        }
    )


@router.post("/accounts/{account_id}/reset-password")
def reset_password(
    account_id: str, body: PasswordResetIn, authorization: str | None = Header(None)
) -> dict:
    """운영자(모든 계정)·고객사 관리자(일반회원만)가 임시 비밀번호로 초기화한다."""
    human = session_human(authorization)
    if human.role not in ("operator", "admin"):
        raise ApiError(403, "ROLE_REQUIRED", "비밀번호를 초기화할 권한이 없어요")
    _check_new_password(body.new_password)
    path = _editable_path()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        row = next((r for r in rows if r.get("id") == account_id), None)
        if row is None:
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "그런 계정이 없어요")
        if human.role == "admin" and row.get("role") != "member":
            raise ApiError(403, "ROLE_REQUIRED", "고객사 관리자는 일반회원만 초기화할 수 있어요")
        if human.role == "admin" and not _in_my_company(human, row):
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "그런 계정이 없어요")
        _set_password(row, body.new_password)
        _write(path, rows)
    _clear_failures(str(row.get("email", "")))
    human_auth.end_account_sessions(account_id)
    logger.info("비밀번호를 초기화했다: %s by %s", account_id, human.name)
    return ok(_public(row))


@router.post("/accounts/{account_id}/unlink")
def unlink_member(account_id: str, authorization: str | None = Header(None)) -> dict:
    """관리자가 연결한 일반회원을 회사에서 끊는다(그 회원은 연결 대기: 할 수 있는 일이 없다).

    고객사 관리자는 내 세션 회사의 일반회원 중 내가 연결한 회원(또는 연결 표시가 없는 옛 회원)만,
    운영자는 어느 일반회원이든(그 회원의 모든 회사에서). 계정은 지우지 않고 그 회사 소속만 뺀다.
    끊은 회원의 지금 세션은 끊는다. 다시 연결하려면 관리자가 회원 연결로 다시 데려온다.
    """
    human = session_human(authorization)
    if human.role not in ("operator", "admin"):
        raise ApiError(403, "ROLE_REQUIRED", "회원 연결을 끊을 권한이 없어요")
    path = _editable_path()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        row = next((r for r in rows if r.get("id") == account_id), None)
        if row is None:
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "그런 계정이 없어요")
        if row.get("role") != "member":
            raise ApiError(409, "MEMBER_ONLY", "일반회원만 연결을 끊을 수 있어요")
        if human.role == "admin":
            if not _in_my_company(human, row):
                raise ApiError(404, "ACCOUNT_NOT_FOUND", "그런 계정이 없어요")
            linked = row.get("linked_admin")
            if linked and linked != human.account_id:
                raise ApiError(
                    403, "NOT_MY_MEMBER", "다른 관리자가 연결한 회원이에요. 그 관리자가 끊어요"
                )
            drop = {human.company_id}
        else:
            drop = _company_ids(row)
        row["memberships"] = [
            m.model_dump(mode="json") for m in memberships_of(row) if m.company_id not in drop
        ]
        row.pop("linked_admin", None)
        _write(path, rows)
    human_auth.end_account_sessions(account_id)
    logger.info("회원 연결을 끊었다: %s (%s) by %s", account_id, ",".join(sorted(drop)), human.name)
    return ok(_public(row))


def _last_admin(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """이 관리자를 내리면 관리자가 0명이 되는 회사가 있나. 소속이 없으면 예전처럼 전체로 센다."""
    admins = [r for r in rows if r.get("role") == "admin"]
    companies = _company_ids(row)
    if not companies:
        return len(admins) <= 1
    return any(sum(1 for r in admins if cid in _company_ids(r)) <= 1 for cid in companies)


def _check_role_change(
    human: human_auth.Human, row: dict[str, Any], new_role: str, rows: list[dict[str, Any]]
) -> None:
    """역할 바꾸기 규칙. 회사에 고객사 관리자가 0명이 되면 안 된다."""
    old_role = row.get("role")
    if new_role == old_role:
        return
    if row.get("id") == human.account_id:
        raise ApiError(409, "SELF_ROLE_CHANGE", "자기 역할은 스스로 바꿀 수 없어요")
    if human.role == "admin" and new_role not in CUSTOMER_ROLES:
        raise ApiError(403, "ROLE_REQUIRED", "고객사 관리자는 운영자 역할을 줄 수 없어요")
    # 마지막 관리자는 회사마다 센다(그 계정이 속한 회사 중 하나라도 관리자가 그 사람뿐이면 막는다)
    if old_role == "admin" and _last_admin(row, rows):
        raise ApiError(409, "LAST_ADMIN", "마지막 남은 고객사 관리자는 내릴 수 없어요")


def _clean_tenants(raw: list[str]) -> list[str]:
    for tenant in raw:
        if not TENANT_RE.match(tenant):
            raise ApiError(422, "TENANT_INVALID", "고객사 ID 형식이 아니에요 (영소문자·숫자·-·_)")
    return list(dict.fromkeys(raw))


def _check_secret_grant(
    human: human_auth.Human, row: dict[str, Any], patch: dict[str, Any], rows: list[dict[str, Any]]
) -> None:
    """키 담당 권한(secret_editor)·담당 고객사(secret_tenants) 주기.

    운영자는 누구에게나. 고객사 관리자는 자기가 키 담당인 고객사 안에서, 자기 자신에게는 못 준다.
    일반회원에게는 줄 수 없다(`update_account`가 막는다).
    """
    if "secret_tenants" in patch:
        _clean_tenants(patch["secret_tenants"])
    if human.role == "operator":
        return
    mine = next((r for r in rows if r.get("id") == human.account_id), {})
    if row.get("id") == human.account_id:
        raise ApiError(409, "SELF_SECRET_GRANT", "자기 키 담당 권한은 스스로 바꿀 수 없어요")
    allowed = set(secret_tenants_of(mine)) if mine.get("secret_editor") is True else set()
    wanted = set(patch.get("secret_tenants") or []) | set(secret_tenants_of(row))
    if not allowed or not wanted <= allowed:
        raise ApiError(403, "SECRET_TENANT_FORBIDDEN", "내가 담당한 고객사 안에서만 줄 수 있어요")


def _set_buyer_link(
    human: human_auth.Human, row: dict[str, Any], raw: str, rows: list[dict[str, Any]]
) -> None:
    """구매자 연결을 정하거나(`buy_0001`) 푼다(빈 문자열).

    자기 계정의 연결은 운영자만 바꾼다(관리자가 스스로 남의 구매자 주문을 맡을 수 없게).
    한 구매자는 계정 하나에만 연결한다.
    """
    if human.role != "operator" and row.get("id") == human.account_id:
        raise ApiError(403, "SELF_BUYER_LINK", "자기 계정의 구매자 연결은 운영자만 바꿀 수 있어요")
    buyer = raw.strip()
    if not buyer:
        row.pop("buyer_id", None)
        return
    if not BUYER_ID_RE.match(buyer):
        raise ApiError(422, "BUYER_ID_INVALID", "구매자 번호는 buy_0001처럼 buy_와 숫자 4자리예요")
    if any(r.get("buyer_id") == buyer and r is not row for r in rows):
        raise ApiError(409, "BUYER_ALREADY_LINKED", "이미 다른 계정에 연결된 구매자예요")
    row["buyer_id"] = buyer


@router.post("/accounts/{account_id}/update")
def update_account(
    account_id: str, body: AccountUpdateIn, authorization: str | None = Header(None)
) -> dict:
    """역할·이름·부서·구매자 연결·확인 권한·열람 담당 바꾸기. 역할이 바뀌면 세션을 끊는다."""
    human = session_human(authorization)
    if human.role not in ("operator", "admin"):
        raise ApiError(403, "ROLE_REQUIRED", "계정을 바꿀 권한이 없어요")
    patch = body.model_dump(exclude_unset=True)
    if not patch or any(v is None for v in patch.values()):
        raise ApiError(422, "NOTHING_TO_UPDATE", "바꿀 칸을 보내 주세요")
    path = _editable_path()
    with _file_lock:
        rows = _bootstrap(path, _read(path))
        row = next((r for r in rows if r.get("id") == account_id), None)
        if row is None:
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "그런 계정이 없어요")
        if human.role == "admin" and row.get("role") not in CUSTOMER_ROLES:
            raise ApiError(403, "ROLE_REQUIRED", "고객사 관리자는 고객사 계정만 바꿀 수 있어요")
        if human.role == "admin" and not _in_my_company(human, row):
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "그런 계정이 없어요")
        if "policy_editor" in patch and human.role != "operator":
            mine = next((r for r in rows if r.get("id") == human.account_id), {})
            if mine.get("policy_editor") is not True:
                raise ApiError(
                    403, "POLICY_EDITOR_REQUIRED", "정책 변경 권한은 정책 담당만 줄 수 있어요"
                )
        if "secret_editor" in patch or "secret_tenants" in patch:
            _check_secret_grant(human, row, patch, rows)
        role_changed = "role" in patch and patch["role"] != row.get("role")
        if "role" in patch:
            _check_role_change(human, row, patch["role"], rows)
            row["role"] = patch["role"]
            if role_changed:
                # 운영자는 세션 회사가 없다: 그 계정이 속한 회사들의 역할을 맞춘다
                targets = (
                    [human.company_id] if human.role != "operator" else sorted(_company_ids(row))
                )
                for cid in targets or [None]:
                    _sync_company_membership(row, patch["role"], cid)
        if "name" in patch:
            name = patch["name"].strip()
            if not name:
                raise ApiError(422, "NAME_EMPTY", "이름을 입력해 주세요")
            if _name_taken(rows, name, _company_ids(row), except_id=account_id):
                raise ApiError(409, "NAME_TAKEN", "같은 회사에 이름이 같은 계정이 있어요")
            row["name"] = name
        if "dept" in patch:
            dept = patch["dept"].strip()
            if dept:
                row["dept"] = dept
            else:
                row.pop("dept", None)
        if "buyer_id" in patch:
            _set_buyer_link(human, row, patch["buyer_id"], rows)
        if "approver" in patch:
            row["approver"] = patch["approver"]
        if "readers" in patch:
            row["readers"] = [r for r in READER_AREAS if r in patch["readers"]]
        if row.get("role") == "member":  # 일반회원은 변경 권한을 가질 수 없다
            if (
                patch.get("limit_editor")
                or patch.get("policy_editor")
                or patch.get("secret_editor")
                or patch.get("secret_tenants")
            ):
                raise ApiError(422, "EDITOR_ROLE_REQUIRED", "운영자·관리자 계정에만 켤 수 있어요")
            row.pop("limit_editor", None)
            row.pop("policy_editor", None)
            row.pop("secret_editor", None)
            row.pop("secret_tenants", None)
        for flag in ("limit_editor", "policy_editor", "secret_editor"):
            if flag in patch:
                row[flag] = patch[flag]
        if "secret_tenants" in patch:
            row["secret_tenants"] = _clean_tenants(patch["secret_tenants"])
        row["updated_at"] = _now()
        _write(path, rows)
    if role_changed:
        human_auth.end_account_sessions(account_id)
    logger.info("계정을 바꿨다: %s (%s) by %s", account_id, ",".join(sorted(patch)), human.name)
    return ok(_public(row))


# ---- 비용 상한·정책 변경 권한 (cost_limits 라우트가 쓴다) ----


def editor_flags(authorization: str | None) -> tuple[human_auth.Human, bool, bool]:
    """로그인 세션의 운영자·관리자만 통과. (사람, limit_editor, policy_editor).

    공용 토큰·일반회원·계정이 없어진 세션은 403/401이다. 읽기 화면도 이 검사를 쓴다.
    """
    human = session_human(authorization)
    if human.role not in ("operator", "admin"):
        raise ApiError(403, "ROLE_REQUIRED", "운영자와 고객사 관리자만 쓸 수 있어요")
    row = next((r for r in load_accounts() if r.get("id") == human.account_id), None)
    if row is None:
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return human, row.get("limit_editor") is True, row.get("policy_editor") is True


def secret_flags(authorization: str | None) -> tuple[human_auth.Human, bool, list[str]]:
    """키 관리 화면용. 로그인 세션의 운영자·관리자만 통과. (사람, secret_editor, 담당 고객사)."""
    human = session_human(authorization)
    if human.role not in ("operator", "admin"):
        raise ApiError(403, "ROLE_REQUIRED", "운영자와 고객사 관리자만 쓸 수 있어요")
    row = next((r for r in load_accounts() if r.get("id") == human.account_id), None)
    if row is None:
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    editor = row.get("secret_editor") is True
    return human, editor, secret_tenants_of(row) if editor else []
