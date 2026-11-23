"""사람 토큰 공용 검사: 로그인 세션 토큰 + (옮기는 동안) 공용 사람 토큰.

설계: `docs/superpowers/specs/2026-11-19-human-session-tokens-design.md`.

- 세션 토큰 = `axs1.<내용>.<서명>`. 내용은 base64url JSON(계정 id·이름·역할·만든 시각·만료·토큰 id),
  서명은 `AX_SESSION_SECRET`으로 만든 HMAC-SHA256. 서명 토큰이라 서비스마다 dashboard-api에
  묻지 않고 각자 검사한다
- 로그아웃한 토큰은 "끊은 토큰 목록" 파일에 만료까지만 남긴다(`AX_REVOKED_SESSIONS_FILE`,
  없으면 회사 연결 파일 옆 `revoked_sessions.json`). 파일이 없으면 이 프로세스 안에서만 막는다
- 비밀번호를 바꾸거나 초기화하면 그 계정의 "세대 번호"를 올린다(`AX_SESSION_GENERATIONS_FILE`,
  없으면 끊은 토큰 목록 옆 `session_generations.json`). 토큰은 만들 때의 세대(`gen`)를 담고,
  계정의 지금 세대보다 낮은 토큰은 받지 않는다. 그 계정의 기존 세션을 한 번에 끊는 방법
- 서명 키가 없거나 짧으면(32자 미만) 세션 토큰을 하나도 받지 않는다(fail-closed)
- 회사(여러 회사 설계 1-3): 토큰 내용에 회사 ID `cid`·회사 역할 `crole`(`admin`·`member`)을
  담는다. 형식은 `axs1` 그대로(칸 추가만). 회사가 없는 옛 토큰은 1단계에서는 캣북스로 보고,
  회사 역할은 계정 역할(`admin`·`member`)을 캣북스의 그 역할로 읽는다(운영자는 회사 역할 없음).
  옛 토큰 거절은 4단계에서 한다
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
from ax_agent_template.companies import canonical_company_id, default_company_id

SESSION_SECRET_ENV = "AX_SESSION_SECRET"
ALLOW_SHARED_ENV = "AX_ALLOW_SHARED_HUMAN_TOKEN"
REVOKED_FILE_ENV = "AX_REVOKED_SESSIONS_FILE"
REVOKED_FILE = "revoked_sessions.json"
GENERATIONS_FILE_ENV = "AX_SESSION_GENERATIONS_FILE"
GENERATIONS_FILE = "session_generations.json"
PREFIX = "axs1"
SESSION_TTL_SECONDS = 12 * 60 * 60  # 정한 것: 12시간 (설계 문서)
MIN_SECRET_LENGTH = 32
ROLES: tuple[str, ...] = ("operator", "admin", "member")
SHARED_ROLE = "shared"
COMPANY_ROLES: tuple[str, ...] = ("admin", "member")  # 계약 `CompanyRole`
LEGACY_COMPANY_ENV = "AX_LEGACY_COMPANY"


def legacy_company_id() -> str:
    """회사가 없는 옛 토큰의 회사. env `AX_LEGACY_COMPANY` → 없으면 설치의 기본 회사.
    (1단계: 기본은 캣북스.) 4단계에서 옛 토큰을 거절한다. 부를 때마다 읽는다."""
    return canonical_company_id(os.getenv(LEGACY_COMPANY_ENV, "").strip()) or default_company_id()


def __getattr__(name: str) -> str:  # 옛 이름 `LEGACY_COMPANY_ID` 호환
    if name == "LEGACY_COMPANY_ID":
        return legacy_company_id()
    raise AttributeError(name)


logger = logging.getLogger("ax.human_auth")
_revoked_in_memory: dict[str, int] = {}
_generations_in_memory: dict[str, int] = {}
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
    """쓰기를 한 사람. 공용 토큰이면 role은 `shared`(어느 역할 검사도 통과하지 않는다).

    company_id·company_role: 세션 토큰의 회사와 그 회사 안의 역할. 공용·목 토큰은 회사가 없다(None).
    """

    name: str
    role: str
    account_id: str | None = None
    session_id: str | None = None
    expires_at: int | None = None
    company_id: str | None = None
    company_role: str | None = None

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
    *,
    account_id: str,
    name: str,
    role: str,
    now: int | None = None,
    ttl: int | None = None,
    gen: int | None = None,
    company_id: str | None = None,
    company_role: str | None = None,
) -> tuple[str, int]:
    """세션 토큰과 만료 시각(유닉스 초)을 만든다. 서명 키가 없으면 RuntimeError.

    gen을 안 주면 그 계정의 지금 세대 번호를 담는다.
    company_id를 주면 정식 회사 ID로 `cid`에, company_role은 `crole`에 담는다.
    회사 없이 회사 역할만 주거나, 형식이 맞지 않으면 ValueError.
    """
    secret = _secret()
    if secret is None:
        raise RuntimeError(f"{SESSION_SECRET_ENV}가 없거나 {MIN_SECRET_LENGTH}자보다 짧다")
    if role not in ROLES:
        raise ValueError(f"role은 {', '.join(ROLES)} 중 하나여야 한다")
    cid = None
    if company_id is not None:
        cid = canonical_company_id(company_id)
        if cid is None:
            raise ValueError("company_id가 회사 ID 형식이 아니다")
    if company_role is not None and (cid is None or company_role not in COMPANY_ROLES):
        raise ValueError(f"company_role은 회사와 같이, {', '.join(COMPANY_ROLES)} 중 하나여야 한다")
    issued = int(time.time()) if now is None else now
    exp = issued + (SESSION_TTL_SECONDS if ttl is None else ttl)
    payload = {
        "sub": account_id,
        "name": name,
        "role": role,
        "iat": issued,
        "exp": exp,
        "jti": secrets.token_hex(12),
        "gen": account_generation(account_id) if gen is None else gen,
    }
    if cid is not None:
        payload["cid"] = cid
        if company_role is not None:
            payload["crole"] = company_role
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
    gen = payload.get("gen", 0)  # 세대 번호가 없는 토큰(이 기능 전에 만든 것)은 0세대
    if not isinstance(gen, int) or isinstance(gen, bool):
        raise _invalid()
    if isinstance(sub, str) and gen < account_generation(sub):
        raise HumanAuthError(
            401, "SESSION_ENDED", "비밀번호가 바뀌어 로그인이 끊겼어요. 다시 로그인해 주세요"
        )
    company_id, company_role = _session_company(payload, str(role))
    return Human(
        name=name,
        role=str(role),
        account_id=sub if isinstance(sub, str) else None,
        session_id=jti,
        expires_at=exp,
        company_id=company_id,
        company_role=company_role,
    )


def _session_company(payload: dict, role: str) -> tuple[str, str | None]:
    """토큰 내용의 회사·회사 역할. 서명은 이미 확인했다. 형식이 깨졌으면 받지 않는다.

    `cid`가 없는 옛 토큰은 캣북스로 보고, 회사 역할은 계정 역할을 그대로 읽는다(1단계).
    """
    if "cid" not in payload:
        if "crole" in payload:
            raise _invalid()
        return legacy_company_id(), role if role in COMPANY_ROLES else None
    cid = canonical_company_id(payload["cid"])
    if cid is None:
        raise _invalid()
    crole = payload.get("crole")
    if crole is not None and crole not in COMPANY_ROLES:
        raise _invalid()
    return cid, crole


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


# ---- 계정 세대 번호 (비밀번호를 바꾸면 그 계정의 기존 세션을 모두 끊는다) ----


def generations_file() -> Path | None:
    raw = os.getenv(GENERATIONS_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    revoked = revoked_file()
    return revoked.with_name(GENERATIONS_FILE) if revoked else None


def _read_generations(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, int) and not isinstance(v, bool)}


def account_generation(account_id: str) -> int:
    """그 계정의 지금 세대 번호. 한 번도 안 바꿨으면 0."""
    path = generations_file()
    stored = _read_generations(path).get(account_id, 0) if path else 0
    return max(stored, _generations_in_memory.get(account_id, 0))


def end_account_sessions(account_id: str) -> int:
    """그 계정의 세대 번호를 하나 올려 지금까지 만든 세션을 모두 끊는다. 새 세대를 돌려준다."""
    with _lock:
        new = account_generation(account_id) + 1
        _generations_in_memory[account_id] = new
        path = generations_file()
        if path is not None:
            data = _read_generations(path)
            data[account_id] = new
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        return new


def reset_revoked_for_tests() -> None:
    with _lock:
        _revoked_in_memory.clear()
        _generations_in_memory.clear()
