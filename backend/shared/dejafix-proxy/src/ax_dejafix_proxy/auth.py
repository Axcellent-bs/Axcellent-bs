"""dejafix-proxy 호출자 토큰 확인과 넘길 경로 검사.

nginx에만 기대지 않고 프록시가 직접 막는다. 형식은 `Authorization: Bearer <토큰>`.
- 에이전트 토큰: `AX_AGENT_TOKENS`(agent-template `verify_token`). 인프라·개발 AI가 붙인다
- 사람 토큰: 로그인 세션 토큰(`axs1.`, `ax_agent_template.human_auth`, 역할 상관없이 관리자·일반회원
  모두)과 공용 사람 토큰(`SAFETY_HUMAN_TOKENS="이름:토큰,이름:토큰"`,
  `AX_ALLOW_SHARED_HUMAN_TOKEN=0`이 되기 전까지). 화면이 붙인다.
  장애 조회는 읽기라 역할을 가리지 않는다
- 목 모드(`DEJAFIX_PROXY_MOCK=1`, 테스트 전용)에서만 `human:<이름>`·`mock:<이름>`도 받는다
- 자리표시자(`data-via-nginx`)는 받지 않는다. nginx가 바꿔 붙이지 않았다는 뜻이다

토큰이 없으면 401 `AUTH_REQUIRED`, 모르는 토큰이면 401 `TOKEN_INVALID`.
세션 토큰이 만료·로그아웃·비밀번호 변경으로 끊겼으면 401과 그 코드(`SESSION_EXPIRED` 등),
위조면 `TOKEN_INVALID`.
토큰이 설정되지 않았고 목 모드도 아니면 모두 거부한다(fail-closed).
총괄(`ax_orchestrator/auth.py`)·기획(`ax_workflow/auth.py`)의 `check_caller`와 같은 규칙을
다른 패키지라 여기 작게 다시 쓴다.
"""

from __future__ import annotations

import os
import re

from ax_agent_template import human_auth
from ax_agent_template.config import PLACEHOLDER_TOKEN, is_mock
from ax_agent_template.server import verify_token

MOCK_NAME = "dejafix-proxy"  # `DEJAFIX_PROXY_MOCK=1`

# 넘기는 DejaFix 조회 (GET만). 화면 `frontend/src/lib/dejafix.ts`(장애·요약·호스트),
# 인프라 AI `dejafix_watch.py`,
# 개발 AI `deploy_signals.py`가 부르는 것뿐이다. 그 밖의 `/api/*`는 404
_ALLOWED = (
    re.compile(r"/api/incidents"),  # 쿼리 허용 (status·limit 등)
    re.compile(r"/api/incidents/[0-9]+"),
    re.compile(r"/api/dashboard/summary"),
    re.compile(r"/api/hosts"),  # 감시 호스트·에이전트 연결 상태 (DejaFix 계약 #7, 연결한 PC 칸)
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
    """사람 토큰(세션·공용·목 `human:`)이면 이름, 아니면 None.

    세션 토큰인데 받을 수 없으면 AuthFailure.
    """
    shared = {
        known: name
        for known, name in human_auth.parse_shared_tokens(os.getenv("SAFETY_HUMAN_TOKENS")).items()
        if known != PLACEHOLDER_TOKEN
    }
    try:
        human = human_auth.find_human(token, shared_tokens=shared, mock=is_mock(MOCK_NAME))
    except human_auth.HumanAuthError as exc:
        # 만료·로그아웃·세대 지남은 그 코드로, 위조(403 HUMAN_TOKEN_INVALID)는 모르는 토큰과 같이
        code = exc.code if exc.status_code == 401 else "TOKEN_INVALID"
        raise AuthFailure(code, exc.message) from None
    return human.name if human else None


def _agent(token: str) -> str | None:
    """에이전트 토큰이면 이름, 아니면 None."""
    if token.startswith("human:") or human_auth.is_session_token(token):
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


# 화면에서 Zabbix 연결을 바꾸는 쓰기(`/settings/zabbix`)는 로그인 세션의 운영자·관리자만.
# 에이전트 토큰·공용 사람 토큰·일반회원은 받지 않는다
# (설정 변경은 사람이 화면에서, CLAUDE.md 예외 규칙)
ADMIN_ROLES = ("operator", "admin")


def check_admin_session(authorization: str | None) -> human_auth.Human:
    """로그인 세션 토큰이고 운영자·관리자면 그 사람.

    아니면 `AuthFailure`(코드로 401·403을 나눈다).
    """
    token = _bearer(authorization)
    if not human_auth.is_session_token(token):
        raise AuthFailure("ADMIN_REQUIRED", "로그인한 운영자·관리자만 바꿀 수 있어요")
    try:
        human = human_auth.verify_session(token, allow_no_company=True)
    except human_auth.HumanAuthError as exc:
        code = exc.code if exc.status_code == 401 else "TOKEN_INVALID"
        raise AuthFailure(code, exc.message) from None
    if human.role not in ADMIN_ROLES:
        raise AuthFailure("ADMIN_REQUIRED", "로그인한 운영자·관리자만 바꿀 수 있어요")
    return human
