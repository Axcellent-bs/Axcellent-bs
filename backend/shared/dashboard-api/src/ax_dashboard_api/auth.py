"""dashboard-api 사람 토큰 확인 (#295, 읽기까지: 보안 점검 2차).

형식은 안전 관문·재무와 같다: `Authorization: Bearer <토큰>`.
- 설정된 토큰: `DASHBOARD_HUMAN_TOKENS="이름:토큰,이름:토큰"`(없으면 `SAFETY_HUMAN_TOKENS`를 읽는다.
  화면은 안전 관문과 같은 사람 토큰 `VITE_SAFETY_HUMAN_TOKEN`을 쓴다)
- 목 모드에서는 `human:<이름>`도 받는다. 에이전트 토큰(`mock:`)은 항상 거부한다.
- 토큰이 설정되지 않았고 목 모드도 아니면 읽기·쓰기를 모두 거부한다 (fail-closed).

읽기(GET)도 같은 사람 토큰이 있어야 한다. 판매 서버 관리 데이터 중계(`/shop/*`)·도구 연결·
연결 키처럼 서버 쪽 토큰으로 읽어 주는 경로가 있어서, 8020에 닿기만 하면 읽히면 안 된다.
열려 있는 것은 상태 확인(`/health`)과 CORS 사전 확인(OPTIONS)뿐이다.
에이전트가 넣는 창구(할 일함 `POST /handoffs`, 에이전트 토큰)·알림 받는 주소(`POST /events`,
회사 키)·사이트 변환기(`/site-adapter`)는 이 의존성을 쓰지 않고 각자 확인한다.

부서끼리 import하지 않으므로 safety의 `auth.py`를 쓰지 않고 같은 규칙을 여기 작게 다시 쓴다.
사람별 로그인 토큰(설계 `docs/superpowers/specs/2026-11-19-human-session-tokens-design.md`)이 오면
`human_from_authorization` 안의 검사만 바뀐다.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request

from ax_dashboard_api.envelope import ApiError, is_mock

# CORS 사전 확인은 브라우저가 토큰 없이 보낸다 (CORSMiddleware가 먼저 답하지만 혹시 넘어와도 연다)
OPEN_METHODS = frozenset({"OPTIONS"})


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _configured() -> dict[str, str]:
    raw = os.getenv("DASHBOARD_HUMAN_TOKENS") or os.getenv("SAFETY_HUMAN_TOKENS") or ""
    pairs: dict[str, str] = {}
    for item in raw.split(","):
        name, sep, token = item.strip().partition(":")
        if sep and name and token:
            pairs[token] = name
    return pairs


def human_from_authorization(authorization: str | None) -> str:
    """사람 이름. 토큰이 없으면 401, 에이전트 토큰이거나 모르는 토큰이면 403."""
    token = bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "사람 토큰이 필요하다 (Authorization: Bearer 토큰)")
    if token.startswith("mock:"):
        raise ApiError(403, "HUMAN_TOKEN_REQUIRED", "에이전트 토큰으로는 쓸 수 없다")
    for known, name in _configured().items():
        if hmac.compare_digest(known, token):
            return name
    if is_mock() and token.startswith("human:") and len(token) > len("human:"):
        return token.removeprefix("human:")
    raise ApiError(403, "HUMAN_TOKEN_INVALID", "사람 토큰을 검증할 수 없다")


def require_human(request: Request) -> None:
    """읽기·쓰기 모두 사람 토큰을 요구한다. 없으면 401, 에이전트·모르는 토큰이면 403."""
    if request.method in OPEN_METHODS:
        return
    request.state.human = human_from_authorization(request.headers.get("authorization"))
