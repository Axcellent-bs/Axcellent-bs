"""사람 토큰 공용 검사: 로그인 세션 토큰 + (옮기는 동안) 공용 사람 토큰.

설계: `docs/superpowers/specs/2026-11-19-human-session-tokens-design.md`.

- 세션 토큰 = `axs1.<내용>.<서명>`. 내용은 base64url JSON(계정 id·이름·역할·만든 시각·만료·토큰 id),
  서명은 `AX_SESSION_SECRET`으로 만든 HMAC-SHA256. 서명 토큰이라 서비스마다 dashboard-api에
  묻지 않고 각자 검사한다
- 로그아웃한 토큰은 "끊은 토큰 목록" 파일에 만료까지만 남긴다(`AX_REVOKED_SESSIONS_FILE`,
  없으면 회사 연결 파일 옆 `revoked_sessions.json`). 파일이 없으면 이 프로세스 안에서만 막는다
- 서명 키가 없거나 짧으면(32자 미만) 세션 토큰을 하나도 받지 않는다(fail-closed)
- 공용 사람 토큰(`*_HUMAN_TOKENS`)은 `AX_ALLOW_SHARED_HUMAN_TOKEN=0`이 되기 전까지 받는다.
  받을 때마다 경고 로그를 남긴다

읽기(GET) 인증·A2A 에이전트 토큰은 여기서 다루지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ax_agent_template import company_connection

SESSION_SECRET_ENV = "AX_SESSION_SECRET"
ALLOW_SHARED_ENV = "AX_ALLOW_SHARED_HUMAN_TOKEN"
REVOKED_FILE_ENV = "AX_REVOKED_SESSIONS_FILE"
REVOKED_FILE = "revoked_sessions.json"
PREFIX = "axs1"
SESSION_TTL_SECONDS = 12 * 60 * 60  # 정한 것: 12시간 (설계 문서)
MIN_SECRET_LENGTH = 32
ROLES: tuple[str, ...] = ("operator", "admin", "member")
SHARED_ROLE = "shared"

logger = logging.getLogger("ax.human_auth")
_revoked_in_memory: dict[str, int] = {}
_lock = threading.Lock()


class HumanAuthError(Exception):
    """사람 토큰을 받을 수 없다. 서비스가 자기 오류 형식으로 바꿔 내보낸다."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Human:
    """쓰기를 한 사람. 공용 토큰이면 role은 `shared`(어느 역할 검사도 통과하지 않는다)."""

    name: str
    role: str
    account_id: str | None = None
    session_id: str | None = None
    expires_at: int | None = None

    @property
    def shared(self) -> bool:
        return self.role == SHARED_ROLE


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def is_session_token(token: str) -> bool:
    return token.startswith(PREFIX + ".")


def shared_tokens_allowed() -> bool:
    return os.getenv(ALLOW_SHARED_ENV, "1").strip() != "0"


def _secret() -> bytes | None:
    raw = os.getenv(SESSION_SECRET_ENV, "")
    if len(raw) < MIN_SECRET_LENGTH:
        return None
    return raw.encode("utf-8")


def sessions_enabled() -> bool:
    return _secret() is not None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, body: str) -> str:
    return _b64(hmac.new(secret, f"{PREFIX}.{body}".encode("ascii"), hashlib.sha256).digest())


def issue_session(
    *, account_id: str, name: str, role: str, now: int | None = None, ttl: int | None = None
) -> tuple[str, int]:
    """세션 토큰과 만료 시각(유닉스 초)을 만든다. 서명 키가 없으면 RuntimeError."""
    secret = _secret()
    if secret is None:
        raise RuntimeError(f"{SESSION_SECRET_ENV}가 없거나 {MIN_SECRET_LENGTH}자보다 짧다")
    if role not in ROLES:
        raise ValueError(f"role은 {', '.join(ROLES)} 중 하나여야 한다")
    issued = int(time.time()) if now is None else now
    exp = issued + (SESSION_TTL_SECONDS if ttl is None else ttl)
    payload = {
        "sub": account_id,
        "name": name,
        "role": role,
        "iat": issued,
        "exp": exp,
        "jti": secrets.token_hex(12),
    }
    body = _b64(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return f"{PREFIX}.{body}.{_sign(secret, body)}", exp


def _invalid() -> HumanAuthError:
    return HumanAuthError(403, "HUMAN_TOKEN_INVALID", "사람 토큰을 검증할 수 없다")


def verify_session(token: str, *, now: int | None = None) -> Human:
    """세션 토큰의 서명·만료·끊은 목록을 확인한다."""
    secret = _secret()
    parts = token.split(".")
    if secret is None or len(parts) != 3 or parts[0] != PREFIX:
        raise _invalid()
    _, body, signature = parts
    if not hmac.compare_digest(_sign(secret, body), signature):
        raise _invalid()
    try:
        payload = json.loads(_unb64(body))
    except ValueError:
        raise _invalid() from None
    if not isinstance(payload, dict):
        raise _invalid()
    name, role, exp, jti = (payload.get(k) for k in ("name", "role", "exp", "jti"))
    if not (isinstance(name, str) and name and role in ROLES and isinstance(exp, int)):
        raise _invalid()
    if not isinstance(jti, str) or not jti:
        raise _invalid()
    current = int(time.time()) if now is None else now
    if current >= exp:
        raise HumanAuthError(401, "SESSION_EXPIRED", "로그인이 만료됐다. 다시 로그인해 주세요")
    if is_revoked(jti, now=current):
        raise HumanAuthError(401, "SESSION_REVOKED", "로그아웃한 토큰이다. 다시 로그인해 주세요")
    sub = payload.get("sub")
    return Human(
        name=name,
        role=str(role),
        account_id=sub if isinstance(sub, str) else None,
        session_id=jti,
        expires_at=exp,
    )


def verify_human(
    authorization: str | None,
    *,
    shared_tokens: dict[str, str] | None = None,
    now: int | None = None,
) -> Human:
    """`Authorization` 헤더에서 사람을 찾는다.

    shared_tokens: 공용 사람 토큰 → 이름(서비스마다 `*_HUMAN_TOKENS`에서 읽어 넘긴다).
    토큰이 없으면 401, 에이전트 토큰(`mock:`)·모르는 토큰이면 403.
    """
    token = bearer(authorization)
    if not token:
        raise HumanAuthError(
            401, "AUTH_REQUIRED", "사람 토큰이 필요하다 (Authorization: Bearer 토큰)"
        )
    if token.startswith("mock:"):
        raise HumanAuthError(403, "HUMAN_TOKEN_REQUIRED", "에이전트 토큰으로는 쓸 수 없다")
    if is_session_token(token):
        return verify_session(token, now=now)
    shared = _shared_human(token, shared_tokens)
    if shared is not None:
        return shared
    raise _invalid()


def _shared_human(token: str, shared_tokens: dict[str, str] | None) -> Human | None:
    if not shared_tokens or not shared_tokens_allowed():
        return None
    for known, name in shared_tokens.items():
        if hmac.compare_digest(known.encode("utf-8"), token.encode("utf-8")):
            logger.warning("공용 사람 토큰으로 쓰기 (이름: %s). 로그인 세션으로 옮겨 주세요", name)
            return Human(name=name, role=SHARED_ROLE)
    return None


MOCK_HUMAN_PREFIX = "human:"


def find_human(
    token: str,
    *,
    shared_tokens: dict[str, str] | None = None,
    mock: bool = False,
    now: int | None = None,
) -> Human | None:
    """토큰 하나(Bearer 뒤)에서 사람을 찾는다. 서비스마다 가진 사람 검사가 이 함수를 쓴다.

    - 세션 토큰(`axs1.`)이면 서명·만료·끊은 목록을 보고, 못 받으면 HumanAuthError
    - 공용 사람 토큰이면 `verify_human`과 같다(`AX_ALLOW_SHARED_HUMAN_TOKEN=0`이면 받지 않음)
    - `mock`(그 서비스의 목 스위치)일 때만 `human:<이름>`을 받는다(테스트 전용)
    - 그 밖(에이전트 토큰·모르는 토큰)은 None. 에이전트로 볼지는 서비스가 정한다
    """
    if is_session_token(token):
        return verify_session(token, now=now)
    if token.startswith("mock:"):
        return None
    shared = _shared_human(token, shared_tokens)
    if shared is not None:
        return shared
    if mock and token.startswith(MOCK_HUMAN_PREFIX) and len(token) > len(MOCK_HUMAN_PREFIX):
        return Human(name=token.removeprefix(MOCK_HUMAN_PREFIX), role=SHARED_ROLE)
    return None


def parse_shared_tokens(raw: str | None) -> dict[str, str]:
    """`이름:토큰,이름:토큰` → {토큰: 이름}."""
    pairs: dict[str, str] = {}
    for item in (raw or "").split(","):
        name, sep, token = item.strip().partition(":")
        if sep and name and token:
            pairs[token] = name
    return pairs


# ---- 끊은 토큰 목록 ----


def revoked_file() -> Path | None:
    raw = os.getenv(REVOKED_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = company_connection.connection_file()
    return Path(conn).with_name(REVOKED_FILE) if conn else None


def _read_revoked(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}


def revoke_session(human: Human, *, now: int | None = None) -> None:
    """로그아웃: 그 토큰을 만료 시각까지 끊은 목록에 둔다. 지난 항목은 같이 지운다."""
    if not human.session_id or human.expires_at is None:
        return
    current = int(time.time()) if now is None else now
    with _lock:
        _revoked_in_memory[human.session_id] = human.expires_at
        for jti, exp in list(_revoked_in_memory.items()):
            if exp <= current:
                del _revoked_in_memory[jti]
        path = revoked_file()
        if path is None:
            return
        data = {k: v for k, v in _read_revoked(path).items() if v > current}
        data[human.session_id] = human.expires_at
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def is_revoked(jti: str, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else now
    if _revoked_in_memory.get(jti, 0) > current:
        return True
    path = revoked_file()
    return bool(path and _read_revoked(path).get(jti, 0) > current)


def reset_revoked_for_tests() -> None:
    with _lock:
        _revoked_in_memory.clear()
