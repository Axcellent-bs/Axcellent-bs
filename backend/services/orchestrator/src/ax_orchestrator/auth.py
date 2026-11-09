"""총괄 쓰기 API 토큰 확인.

nginx에만 기대지 않고 서버가 직접 막는다. 형식은 `Authorization: Bearer <토큰>`.
- 에이전트 토큰: `AX_AGENT_TOKENS`(agent-template `verify_token`).
  목 모드에서는 `mock:<이름>`도 받는다
- 사람 토큰: `SAFETY_HUMAN_TOKENS="이름:토큰,이름:토큰"`. 목 모드에서는 `human:<이름>`도 받는다
- 운영자(admin): 에이전트 토큰 명부의 `admin` 이름

토큰이 없으면 401, 다른 종류의 토큰(사람 자리에 에이전트, admin 자리에 다른 에이전트 등)이면 403,
모르는 토큰이면 401이다. 토큰이 설정되지 않았고 목 모드도 아니면 모두 거부한다(fail-closed).
safety의 `auth.py`는 다른 패키지라 같은 규칙을 여기 작게 다시 쓴다.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Callable

from fastapi import Header, HTTPException

from ax_agent_template.config import is_mock
from ax_agent_template.server import verify_token
from ax_agent_template.services import department_of

ADMIN = "admin"


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "토큰이 필요하다 (Authorization: Bearer 토큰)")
    return token.strip()


def _human(token: str) -> str | None:
    """사람 토큰이면 이름, 아니면 None."""
    for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        if sep and name and known and hmac.compare_digest(known, token):
            return name
    if is_mock("orchestrator") and token.startswith("human:") and len(token) > len("human:"):
        return token.removeprefix("human:")
    return None


def _agent(token: str) -> str | None:
    """에이전트 토큰이면 이름, 아니면 None."""
    if token.startswith("human:"):
        return None
    # 총괄은 에이전트 이름을 늦게 정하므로 목 모드 판정은 `ORCHESTRATOR_MOCK`으로 직접 한다
    if is_mock("orchestrator") and token.startswith("mock:") and len(token) > len("mock:"):
        return token.removeprefix("mock:")
    try:
        return verify_token(token)
    except PermissionError:
        return None


def require_human(authorization: str | None = Header(default=None)) -> str:
    """사람 결정(승인·거절)만. 에이전트 토큰은 403."""
    token = _bearer(authorization)
    person = _human(token)
    if person:
        return person
    if _agent(token):
        raise HTTPException(403, "사람 토큰만 결정할 수 있다")
    raise HTTPException(401, "토큰을 검증할 수 없다")


def require_agent(allowed: set[str] | None = None) -> Callable[..., str]:
    """에이전트끼리. `allowed`가 없으면 명부의 모든 에이전트, 있으면 그 이름(또는 원래 부서)만."""

    def dependency(authorization: str | None = Header(default=None)) -> str:
        token = _bearer(authorization)
        agent = _agent(token)
        if agent is None:
            if _human(token):
                raise HTTPException(403, "에이전트 토큰이 필요하다")
            raise HTTPException(401, "토큰을 검증할 수 없다")
        if allowed is not None and agent not in allowed and department_of(agent) not in allowed:
            raise HTTPException(403, "이 API를 호출할 수 없는 요청자다")
        return agent

    return dependency


require_admin = require_agent({ADMIN})
