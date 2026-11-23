"""기획·개발 쓰기 API(`/api/planning/*`) 토큰 확인.

nginx에만 기대지 않고 서버가 직접 막는다. 형식은 `Authorization: Bearer <토큰>`.
- 에이전트 토큰: `AX_AGENT_TOKENS`(agent-template `verify_token`).
  목 모드에서는 `mock:<이름>`도 받는다
- 사람 토큰: `SAFETY_HUMAN_TOKENS="이름:토큰,이름:토큰"`. 목 모드에서는 `human:<이름>`도 받는다
- 로그인 세션 토큰(`axs1.`, `ax_agent_template.human_auth`)도 사람으로 받는다. 이름은 계정 이름.
  위조면 403, 만료·로그아웃이면 401
- 운영자(admin): 에이전트 토큰 명부의 `admin` 이름

토큰이 없으면 401, 다른 종류의 토큰(사람 자리에 에이전트, admin 자리에 다른 에이전트 등)이면 403,
모르는 토큰이면 401이다. 토큰이 설정되지 않았고 목 모드도 아니면 모두 거부한다(fail-closed).
총괄(`ax_orchestrator/auth.py`)과 같은 규칙을 다른 패키지라 여기 작게 다시 쓴다.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator

from fastapi import Header, HTTPException

from ax_agent_template import human_auth
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
    """사람 토큰이면 이름, 아니면 None. 세션 토큰인데 받을 수 없으면 AuthError."""
    try:
        human = human_auth.find_human(
            token,
            shared_tokens=human_auth.parse_shared_tokens(os.getenv("SAFETY_HUMAN_TOKENS")),
            mock=is_mock(MOCK_NAME),
        )
    except human_auth.HumanAuthError as exc:
        raise AuthError(exc.status_code, exc.message) from None
    return human.name if human else None


def _agent(token: str) -> str | None:
    """에이전트 토큰이면 이름, 아니면 None."""
    if token.startswith("human:") or human_auth.is_session_token(token):
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


def session_mode() -> bool:
    """서버가 로그인 세션을 쓰는 설정인가 (`AX_SESSION_SECRET` 32자 이상, sessions_enabled).

    켜져 있으면 개발 AI 대화·작업 조작은 세션 토큰이 있어야 하고 계정은 세션 계정만 쓴다.
    꺼진 데모 모드(공용 토큰 데모 로그인)에서만 화면이 적은 계정을 쓴다. 판정이 실패하면 켜진
    것으로 본다 (fail-closed)."""
    try:
        return human_auth.sessions_enabled()
    except Exception:  # noqa: BLE001 - 설정을 못 읽으면 막는 쪽
        return True


def session_human(authorization: str | None) -> human_auth.Human | None:
    """로그인 세션 토큰의 사람. 세션 토큰이 아니면(공용·목·에이전트·없음) None.

    세션 토큰인데 받을 수 없으면(위조 403, 만료·로그아웃 401) AuthError. 읽기(GET)에도 쓴다."""
    token = human_auth.bearer(authorization)
    if not token or not human_auth.is_session_token(token):
        return None
    try:
        return human_auth.verify_session(token)
    except human_auth.HumanAuthError as exc:
        raise AuthError(exc.status_code, exc.message) from None


def session_account(authorization: str | None) -> str | None:
    """로그인 세션 토큰의 계정 id. 세션 토큰이 아니면 None."""
    human = session_human(authorization)
    return human.account_id if human else None


def session_admin(authorization: str | None) -> bool:
    """세션 토큰의 사람이 회사 관리자인가 (계정 역할 admin 또는 회사 역할 admin)."""
    human = session_human(authorization)
    return human is not None and (human.role == "admin" or human.company_role == "admin")


def scoped_account(authorization: str | None, given: str | None) -> str | None:
    """화면이 적은 계정(`?account=`·본문 `account`)을 토큰으로 확인한다.

    - 세션 모드(`session_mode`): 세션 토큰이 있어야 하고(없으면 401, 공용·에이전트 토큰이면 403)
      계정은 세션 계정이다. 적은 계정이 다르면 403
    - 세션 설정이 꺼진 데모 모드: 세션 토큰이면 위와 같고, 아니면 적은 값을 그대로 쓴다
      (계정을 모르면 None: 계정이 있는 대화·작업의 주인이 되지 못한다)"""
    mine = session_account(authorization)
    if mine is None:
        if not session_mode():
            return given
        if not human_auth.bearer(authorization):
            raise AuthError(401, "로그인 세션 토큰이 필요하다")
        raise AuthError(403, "로그인 세션 토큰이 필요하다 (공용·에이전트 토큰으로는 안 된다)")
    if given is not None and given != mine:
        raise AuthError(403, "로그인한 계정과 다른 계정으로 요청했다")
    return mine


SESSION_REQUIRED = "SESSION_REQUIRED"


def session_decider(authorization: str | None) -> str | None:
    """승인 같은 사람 결정(머지·레포 승인·배포 승인 등)을 한 사람. 세션 모드에서만 본다.

    - 세션 모드(`session_mode`): 로그인 세션 토큰이어야 하고 그 계정 이름을 돌려준다.
      공용 사람 토큰(`SAFETY_HUMAN_TOKENS`·화면 번들 토큰)·에이전트 토큰이면 403 `SESSION_REQUIRED`
      (누가 승인했는지 모르는 토큰으로 승인하지 않는다), 토큰이 없으면 401
    - 세션 설정이 꺼진 로컬·시험 모드: None (예전처럼 `check_human`만)"""
    if not session_mode():
        return None
    human = session_human(authorization)
    if human is None:
        if not human_auth.bearer(authorization):
            raise AuthError(401, "로그인 세션 토큰이 필요하다")
        err = AuthError(
            403, "승인은 로그인한 사람만 할 수 있다 (공용·에이전트 토큰으로는 승인하지 않는다)"
        )
        err.code = SESSION_REQUIRED
        raise err
    return human.name or human.account_id


# 지금 사람 결정을 하는 사람 (세션 모드에서 `session_decider`가 정한 이름). 없으면 None
_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar("ax_actor", default=None)


@contextlib.contextmanager
def acting(name: str | None) -> Iterator[None]:
    """이 안의 액션은 `name`이 한 사람 결정으로 기록한다 (None이면 바꾸지 않는다)."""
    token = _actor.set(name) if name else None
    try:
        yield
    finally:
        if token is not None:
            _actor.reset(token)


def current_actor() -> str | None:
    return _actor.get()


def require_human(authorization: str | None = Header(default=None)) -> str:
    return check_human(authorization)


def require_admin(authorization: str | None = Header(default=None)) -> str:
    return check_admin(authorization)


def require_caller(authorization: str | None = Header(default=None)) -> str:
    return check_caller(authorization)
