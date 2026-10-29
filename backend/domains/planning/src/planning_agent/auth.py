"""기획개발 쓰기 API(`/api/planning/*`) 토큰 확인.

nginx에만 기대지 않고 서버가 직접 막는다. 형식은 `Authorization: Bearer <토큰>`.
- 에이전트 토큰: `AX_AGENT_TOKENS`(agent-template `verify_token`).
  목 모드에서는 `mock:<이름>`도 받는다
- 사람 토큰: `SAFETY_HUMAN_TOKENS="이름:토큰,이름:토큰"`. 목 모드에서는 `human:<이름>`도 받는다
- 운영자(admin): 에이전트 토큰 명부의 `admin` 이름

토큰이 없으면 401, 다른 종류의 토큰(사람 자리에 에이전트, admin 자리에 다른 에이전트 등)이면 403,
모르는 토큰이면 401이다. 토큰이 설정되지 않았고 목 모드도 아니면 모두 거부한다(fail-closed).
총괄(`ax_orchestrator/auth.py`)과 같은 규칙을 다른 패키지라 여기 작게 다시 쓴다.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

from ax_agent_template.config import is_mock
from ax_agent_template.server import verify_token

ADMIN = "admin"
MOCK_NAME = "planning"  # 개발·배포 서비스(dev)도 같은 도메인이라 `PLANNING_MOCK`을 따른다


class AuthError(HTTPException):
    """화면이 읽는 `{error: {code, message}}`로 내보낸다 (main.py 처리기)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(status, message)
        self.code = "UNAUTHORIZED" if status == 401 else "FORBIDDEN"


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "토큰이 필요하다 (Authorization: Bearer 토큰)")
    return token.strip()


def _human(token: str) -> str | None:
    """사람 토큰이면 이름, 아니면 None."""
    for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        if sep and name and known and hmac.compare_digest(known, token):
            return name
    if is_mock(MOCK_NAME) and token.startswith("human:") and len(token) > len("human:"):
        return token.removeprefix("human:")
    return None


def _agent(token: str) -> str | None:
    """에이전트 토큰이면 이름, 아니면 None."""
    if token.startswith("human:"):
        return None
    if token.startswith("mock:"):
        name = token.removeprefix("mock:")
        return name if is_mock(MOCK_NAME) and name else None
    try:
        return verify_token(token)
    except PermissionError:
        return None


def check_human(authorization: str | None) -> str:
    """사람 결정(승인·머지·기각·재시도)만. 에이전트 토큰은 403."""
    token = _bearer(authorization)
    person = _human(token)
    if person:
        return person
    if _agent(token):
        raise AuthError(403, "사람 토큰만 결정할 수 있다")
    raise AuthError(401, "토큰을 검증할 수 없다")


def check_admin(authorization: str | None) -> str:
    """운영 설정(초기화·형상관리 연결·자동 범위)만. admin 에이전트 토큰이 아니면 403."""
    token = _bearer(authorization)
    agent = _agent(token)
    if agent == ADMIN:
        return agent
    if agent or _human(token):
        raise AuthError(403, "운영자(admin) 토큰이 필요하다")
    raise AuthError(401, "토큰을 검증할 수 없다")


def check_caller(authorization: str | None) -> str:
    """요청 접수·대화. 사람 토큰이나 에이전트 토큰이면 된다."""
    token = _bearer(authorization)
    who = _human(token) or _agent(token)
    if who:
        return who
    raise AuthError(401, "토큰을 검증할 수 없다")


def require_human(authorization: str | None = Header(default=None)) -> str:
    return check_human(authorization)


def require_admin(authorization: str | None = Header(default=None)) -> str:
    return check_admin(authorization)


def require_caller(authorization: str | None = Header(default=None)) -> str:
    return check_caller(authorization)
