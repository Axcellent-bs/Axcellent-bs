"""계정·로그인: 사람마다 다른 세션 토큰으로 쓰게 한다 (사람별 토큰 설계 1단계).

- 파일: `AX_ACCOUNTS_FILE` → 회사 연결 파일 옆 `accounts.json` (연결 키처럼).
  없으면 로그인 꺼짐
- 칸: id, 이름, 이메일, 역할(`operator`·`admin`·`member`), 비밀번호 해시(scrypt), 만든 시각,
  프로필(부서 `dept`, 확인 권한 `approver`, 열람 담당 `readers`).
  열람 담당 값은 화면 `members.ts`의 `ReaderArea`와 같다
- 첫 계정: 계정이 하나도 없을 때 `AX_BOOTSTRAP_ADMIN="이메일:초기 비밀번호"`로 운영자 1명을 만든다
- 같은 이메일로 연속 5번 틀리면 15분 동안 로그인을 막는다(맞는 비밀번호여도).
  이 프로세스 메모리에 센다
- 토큰 만들기·검사는 공용 `ax_agent_template.human_auth`. 서명 키 `AX_SESSION_SECRET`

경로
- `GET  /api/dashboard/auth/status` → {login_enabled}
  (화면이 서버 로그인과 데모 로그인 중 무엇을 쓸지 고른다)
- `POST /api/dashboard/auth/login` {email, password} → {token, expires_at, account}
- `POST /api/dashboard/auth/logout` (세션 토큰) → 끊은 토큰 목록에 올린다
- `GET  /api/dashboard/auth/me` (세션 토큰) → 내 계정
- `GET  /api/dashboard/auth/accounts`
  (운영자: 모든 계정 / 고객사 관리자: 고객사 계정(admin·member)만) → 계정 목록
  (공개 칸만. 비밀번호 해시·세대 번호 같은 내부 값은 내보내지 않는다)
- `POST /api/dashboard/auth/accounts/{id}/update` {role?, name?, dept?, approver?, readers?}
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
from pydantic import BaseModel, Field

from ax_agent_template import company_connection as store
from ax_agent_template import human_auth
from ax_dashboard_api.envelope import ApiError, ok

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
EMAIL_RE = re.compile(r"^[^@\s:]+@[^@\s:]+\.[^@\s:]+$")

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


def _new_row(name: str, email: str, role: str, password: str) -> dict[str, Any]:
    return {
        "id": "acc-" + secrets.token_hex(6),
        "name": name,
        "email": _norm_email(email),
        "role": role,
        "password_hash": hash_password(password),
        "created_at": _now(),
    }


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


def load_accounts() -> list[dict[str, Any]]:
    path = _path()
    if path is None:
        return []
    with _file_lock:
        return _bootstrap(path, _read(path))


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """밖으로 내보내는 칸만 고른다(허용 목록). 해시·비밀번호 바꾼 시각 같은 내부 값은 넣지 않는다.

    확인 권한을 한 번도 정하지 않은 계정은 역할로 정한다(고객사 관리자는 있음, 일반회원은 없음).
    """
    out = {k: row.get(k) for k in ("id", "name", "email", "role", "created_at")}
    dept = row.get("dept")
    out["dept"] = dept if isinstance(dept, str) and dept else None
    approver = row.get("approver")
    out["approver"] = approver if isinstance(approver, bool) else row.get("role") == "admin"
    readers = row.get("readers")
    out["readers"] = [r for r in READER_AREAS if r in readers] if isinstance(readers, list) else []
    return out


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


def session_human(authorization: str | None) -> human_auth.Human:
    """로그인 세션 토큰만 받는다(공용 토큰은 계정이 아니라 여기서 거절)."""
    token = human_auth.bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요하다 (Authorization: Bearer 토큰)")
    if not human_auth.is_session_token(token):
        raise ApiError(403, "SESSION_REQUIRED", "로그인한 계정의 토큰이 필요하다")
    try:
        return human_auth.verify_session(token)
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
    approver: bool | None = None
    readers: list[ReaderArea] | None = Field(None, max_length=8)


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
    token, exp = human_auth.issue_session(
        account_id=str(row["id"]), name=str(row["name"]), role=str(row["role"])
    )
    return ok({"token": token, "expires_at": exp, "account": _public(row)})


@router.post("/logout")
def logout(authorization: str | None = Header(None)) -> dict:
    human = session_human(authorization)
    human_auth.revoke_session(human)
    return ok({"logged_out": True})


@router.get("/me")
def me(authorization: str | None = Header(None)) -> dict:
    human = session_human(authorization)
    row = next((r for r in load_accounts() if r.get("id") == human.account_id), None)
    if row is None:
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return ok({"account": _public(row), "expires_at": human.expires_at})


@router.get("/accounts")
def list_accounts(authorization: str | None = Header(None)) -> dict:
    """운영자는 모든 계정, 고객사 관리자는 고객사 계정(admin·member)만 본다."""
    human = session_human(authorization)
    if human.role == "operator":
        visible = load_accounts()
    elif human.role == "admin":
        visible = [r for r in load_accounts() if r.get("role") in CUSTOMER_ROLES]
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
        row = _new_row(body.name.strip(), email, body.role, body.password)
        rows.append(row)
        _write(path, rows)
    logger.info("계정을 만들었다: %s(%s) by %s", email, body.role, human.name)
    return ok(_public(row))


@router.post("/password")
def change_password(body: PasswordChangeIn, authorization: str | None = Header(None)) -> dict:
    """내 비밀번호 바꾸기. 성공하면 이 계정의 기존 세션을 모두 끊고 새 세션을 준다."""
    human = session_human(authorization)
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
    token, exp = human_auth.issue_session(
        account_id=str(row["id"]), name=str(row["name"]), role=str(row["role"])
    )
    logger.info("비밀번호를 바꿨다: %s", row["id"])
    return ok({"token": token, "expires_at": exp, "account": _public(row)})


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
        _set_password(row, body.new_password)
        _write(path, rows)
    _clear_failures(str(row.get("email", "")))
    human_auth.end_account_sessions(account_id)
    logger.info("비밀번호를 초기화했다: %s by %s", account_id, human.name)
    return ok(_public(row))


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
    if old_role == "admin" and sum(1 for r in rows if r.get("role") == "admin") <= 1:
        raise ApiError(409, "LAST_ADMIN", "마지막 남은 고객사 관리자는 내릴 수 없어요")


@router.post("/accounts/{account_id}/update")
def update_account(
    account_id: str, body: AccountUpdateIn, authorization: str | None = Header(None)
) -> dict:
    """역할·이름·부서·확인 권한·열람 담당 바꾸기. 역할이 바뀌면 그 계정의 세션을 끊는다."""
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
        role_changed = "role" in patch and patch["role"] != row.get("role")
        if "role" in patch:
            _check_role_change(human, row, patch["role"], rows)
            row["role"] = patch["role"]
        if "name" in patch:
            name = patch["name"].strip()
            if not name:
                raise ApiError(422, "NAME_EMPTY", "이름을 입력해 주세요")
            row["name"] = name
        if "dept" in patch:
            dept = patch["dept"].strip()
            if dept:
                row["dept"] = dept
            else:
                row.pop("dept", None)
        if "approver" in patch:
            row["approver"] = patch["approver"]
        if "readers" in patch:
            row["readers"] = [r for r in READER_AREAS if r in patch["readers"]]
        row["updated_at"] = _now()
        _write(path, rows)
    if role_changed:
        human_auth.end_account_sessions(account_id)
    logger.info("계정을 바꿨다: %s (%s) by %s", account_id, ",".join(sorted(patch)), human.name)
    return ok(_public(row))
