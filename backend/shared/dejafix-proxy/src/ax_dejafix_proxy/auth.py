"""dejafix-proxy 호출자 토큰 확인과 넘길 경로 검사.

nginx에만 기대지 않고 프록시가 직접 막는다. 형식은 `Authorization: Bearer <토큰>`.
- 에이전트 토큰: `AX_AGENT_TOKENS`(agent-template `verify_token`). 인프라·개발 AI가 붙인다
- 사람 토큰: `SAFETY_HUMAN_TOKENS="이름:토큰,이름:토큰"`. 화면이 붙인다
  (서버에서는 nginx가 바꿔 붙인다)
- 목 모드(`DEJAFIX_PROXY_MOCK=1`, 테스트 전용)에서만 `human:<이름>`·`mock:<이름>`도 받는다
- 자리표시자(`data-via-nginx`)는 받지 않는다. nginx가 바꿔 붙이지 않았다는 뜻이다

토큰이 없으면 401 `AUTH_REQUIRED`, 모르는 토큰이면 401 `TOKEN_INVALID`.
토큰이 설정되지 않았고 목 모드도 아니면 모두 거부한다(fail-closed).
총괄(`ax_orchestrator/auth.py`)·기획(`ax_workflow/auth.py`)의 `check_caller`와 같은 규칙을
다른 패키지라 여기 작게 다시 쓴다.
"""

from __future__ import annotations

import hmac
import os
import re

from ax_agent_template.config import PLACEHOLDER_TOKEN, is_mock
from ax_agent_template.server import verify_token

MOCK_NAME = "dejafix-proxy"  # `DEJAFIX_PROXY_MOCK=1`

# 넘기는 DejaFix 조회 (GET만). 화면 `frontend/src/lib/dejafix.ts`, 인프라 AI `dejafix_watch.py`,
# 개발 AI `deploy_signals.py`가 부르는 것뿐이다. 그 밖의 `/api/*`는 404
_ALLOWED = (
    re.compile(r"/api/incidents"),  # 쿼리 허용 (status·limit 등)
    re.compile(r"/api/incidents/[0-9]+"),
    re.compile(r"/api/dashboard/summary"),
)


class AuthFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthFailure("AUTH_REQUIRED", "토큰이 필요합니다 (Authorization: Bearer 토큰)")
    return token.strip()


def _human(token: str) -> str | None:
    """사람 토큰이면 이름, 아니면 None."""
    for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        known = known.strip()
        if sep and name and known and known != PLACEHOLDER_TOKEN:
            if hmac.compare_digest(known, token):
                return name.strip()
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
        return verify_token(token)  # 명부의 자리표시자 항목은 token_registry가 이미 뺀다
    except PermissionError:
        return None


def check_caller(authorization: str | None) -> str:
    """사람 토큰이나 에이전트 토큰이면 그 이름. 아니면 `AuthFailure`."""
    token = _bearer(authorization)
    if token == PLACEHOLDER_TOKEN:
        raise AuthFailure("TOKEN_INVALID", "토큰을 검증할 수 없습니다")
    who = _human(token) or _agent(token)
    if who:
        return who
    raise AuthFailure("TOKEN_INVALID", "토큰을 검증할 수 없습니다")


def unsafe_raw_path(raw_path: bytes) -> bool:
    """디코딩 전 경로가 조작돼 보이면 True.

    `%`(인코딩된 `.`·`/` 등), `.`·`..` 구간, `//`, 역슬래시, 제어 문자를 막는다.
    허용 경로는 영문·숫자·`/`만으로 이루어지므로 정상 호출은 걸리지 않는다.
    """
    try:
        text = raw_path.decode("ascii")
    except UnicodeDecodeError:
        return True
    text = text.split("?", 1)[0]  # 서버에 따라 raw_path에 쿼리가 붙어 온다. 쿼리는 그대로 넘긴다
    if "%" in text or "\\" in text or "//" in text:
        return True
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return True
    return any(part in (".", "..") for part in text.split("/"))


def allowed_path(path: str) -> bool:
    return any(p.fullmatch(path) for p in _ALLOWED)
