"""dashboard-api 사람 토큰 확인 (#295, 읽기까지: 보안 점검 2차).

형식은 안전 관문·재무와 같다: `Authorization: Bearer <토큰>`.
- 설정된 토큰: `DASHBOARD_HUMAN_TOKENS="이름:토큰,이름:토큰"`(없으면 `SAFETY_HUMAN_TOKENS`를 읽는다.
  화면은 안전 관문과 같은 사람 토큰 `VITE_SAFETY_HUMAN_TOKEN`을 쓴다)
- 목 모드에서는 `human:<이름>`도 받는다. 에이전트 토큰(`mock:`)은 항상 거부한다.
- 로그인 세션 토큰(`axs1.`, `ax_agent_template.human_auth`)도 받는다. 기록 이름은 계정 이름.
  역할(운영자·관리자·일반회원)은 여기서 보지 않는다. 일반회원 세션도 통과하고, 관리자 전용
  경로(회사 연결·회원 관리)만 `accounts.require_session_role`·계정 API가 따로 막는다
- 토큰이 설정되지 않았고 목 모드도 아니면 읽기·쓰기를 모두 거부한다 (fail-closed).

읽기(GET)도 같은 사람 토큰이 있어야 한다. 판매 서버 관리 데이터 중계(`/shop/*`)·도구 연결·
연결 키처럼 서버 쪽 토큰으로 읽어 주는 경로가 있어서, 8020에 닿기만 하면 읽히면 안 된다.
토큰 없이 열린 경로는 아래 `PUBLIC_ROUTES` 한 곳에만 적는다(상태 확인과 로그인 전 화면이 읽는 것).
CORS 사전 확인(OPTIONS)도 연다.
에이전트가 넣는 창구(할 일함 `POST /handoffs`, 에이전트 토큰)·알림 받는 주소(`POST /events`,
회사 키)·사이트 변환기(`/site-adapter`)는 이 의존성을 쓰지 않고 각자 확인한다(`MACHINE_ROUTES`).
계정 API(`/auth/me`·`/auth/accounts` 등)는 로그인 세션 토큰을 직접 본다(`accounts.session_human`).

부서끼리 import하지 않으므로 safety의 `auth.py`를 쓰지 않고 같은 규칙을 여기 작게 다시 쓴다.
"""

from __future__ import annotations

import os

from fastapi import Request

from ax_agent_template import human_auth
from ax_dashboard_api.envelope import ApiError, is_mock

# CORS 사전 확인은 브라우저가 토큰 없이 보낸다 (CORSMiddleware가 먼저 답하지만 혹시 넘어와도 연다)
OPEN_METHODS = frozenset({"OPTIONS"})

# 사람 토큰 없이 열린 경로 (메서드, 경로).
# 여기 없는 경로는 사람 토큰·세션·기계 토큰 중 하나가 있어야 한다. 늘릴 때는 로그인 전에 꼭
# 필요한지, 민감한 값(키·주소·계정 목록)이 없는지 보고 테스트(test_get_auth_roles.py)도 고친다
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health"),
        # 로그인 전 화면: 서버 로그인이 켜져 있는지, 로그인, 설치된 회사 이름(public_info.py)
        ("GET", "/api/dashboard/auth/status"),
        ("POST", "/api/dashboard/auth/login"),
        ("GET", "/api/dashboard/auth/company"),
        # 구글이 돌려보내는 유튜브 연결 주소. Authorization이 없어 서명한 state로 회사를 확인한다
        ("GET", "/api/dashboard/marketing/youtube/callback"),
        # Higgsfield가 돌려보내는 회사 계정 연결 주소(MCP OAuth). 유튜브와 같이 state로 확인한다
        ("GET", "/api/dashboard/marketing/higgsfield/callback"),
        # Meta가 돌려보내는 회사 인스타 로그인 연결 주소. 유튜브와 같이 state로 확인하고 교환은 complete에서
        ("GET", "/api/dashboard/marketing/instagram/callback"),
    }
)
# 사람이 아니라 서버가 부르는 경로. 사람 토큰 대신 각자 다른 토큰을 본다
MACHINE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/dashboard/handoffs"),  # 에이전트 토큰 (handoffs.intake)
        ("POST", "/api/dashboard/events"),  # 회사 키 (shop_events)
        # MCP 서버 에이전트 토큰 (personal_mcp.check_router)
        ("POST", "/api/dashboard/me/ai-connection/mcp/check"),
        # 개인 MCP 연결 토큰 `axm_` (mcp_gateway.router, 전 직원 MCP 창구)
        ("POST", "/api/dashboard/me/ai-connection/mcp"),
        ("GET", "/api/dashboard/me/ai-connection/mcp"),
    }
)
MACHINE_PREFIXES = ("/site-adapter",)  # 회사 연결 토큰 (site_adapter.router)


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _configured() -> dict[str, str]:
    raw = os.getenv("DASHBOARD_HUMAN_TOKENS") or os.getenv("SAFETY_HUMAN_TOKENS") or ""
    return human_auth.parse_shared_tokens(raw)


def human_from_authorization(authorization: str | None) -> str:
    """사람 이름. 토큰이 없으면 401, 에이전트 토큰이거나 모르는 토큰이면 403."""
    token = bearer(authorization)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "사람 토큰이 필요하다 (Authorization: Bearer 토큰)")
    if token.startswith("mock:"):
        raise ApiError(403, "HUMAN_TOKEN_REQUIRED", "에이전트 토큰으로는 쓸 수 없다")
    try:  # 로그인 세션 토큰(accounts.py)·공용 토큰·목 `human:` (ax_agent_template.human_auth)
        human = human_auth.find_human(token, shared_tokens=_configured(), mock=is_mock())
    except human_auth.HumanAuthError as err:
        raise ApiError(err.status_code, err.code, err.message) from None
    if human is None:
        raise ApiError(403, "HUMAN_TOKEN_INVALID", "사람 토큰을 검증할 수 없다")
    return human.name


def buyer_id_from_authorization(authorization: str | None) -> str | None:
    """로그인 세션 계정에 연결된 구매자 번호. 세션이 아니거나 연결이 없으면 None(지어내지 않는다).

    공용 사람 토큰에는 계정이 없어 구매자도 없다. 값은 이 함수로만 읽고 화면의 값은 쓰지 않는다."""
    token = bearer(authorization)
    if not token or token.startswith("mock:"):
        return None
    try:
        human = human_auth.find_human(token, shared_tokens=_configured(), mock=is_mock())
    except human_auth.HumanAuthError:
        return None
    if human is None or not human.account_id:
        return None
    from ax_dashboard_api import accounts

    return accounts.buyer_id_of_account(human.account_id)


def requester_label(authorization: str | None) -> str | None:
    """감사 기록용 호출자 표시: `이름 (account:계정ID, company:회사ID)`. 로그인 세션 토큰만.

    돈이 움직이는 경로가 support에 넘겨 기록하게 한다. 확인할 수 없으면 None (지어내지 않는다)."""
    token = bearer(authorization)
    if not token or token.startswith("mock:"):
        return None
    try:
        human = human_auth.find_human(token, shared_tokens=_configured(), mock=is_mock())
    except human_auth.HumanAuthError:
        return None
    if human is None:
        return None
    if human.shared or not human.account_id:
        return None  # 공용·목 토큰은 누가 쓰는지 모른다. 이름을 사람으로 기록하지 않는다
    parts = [f"account:{human.account_id}"]
    if human.company_id:
        parts.append(f"company:{human.company_id}")
    return f"{human.name} ({', '.join(parts)})"


def require_human(request: Request) -> None:
    """읽기·쓰기 모두 사람 토큰을 요구한다. 없으면 401, 에이전트·모르는 토큰이면 403."""
    if request.method in OPEN_METHODS:
        return
    request.state.human = human_from_authorization(request.headers.get("authorization"))
