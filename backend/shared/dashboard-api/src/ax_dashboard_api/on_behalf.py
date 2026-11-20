"""사람 로그인 세션 요청이면 그 계정을 요청 문맥에 묶는다 (계약 `RequestMeta.on_behalf_of`, 11/20).

모든 HTTP 요청을 감싸는 ASGI 미들웨어다. `Authorization`이 검증되는 사람 로그인 세션이면 그 계정 ID,
아니면(에이전트 토큰·공용 토큰·토큰 없음·검증 실패) `None`을 묶는다. 이 요청 안에서 보내는 A2A
요청은 `call_agent`가 메타의 `on_behalf_of`를 이 값으로 덮어쓴다. 요청 본문에 적힌 값은 쓰지 않는다.
"""

from __future__ import annotations

from ax_agent_template import human_auth, on_behalf


def session_account(authorization: str | None) -> str | None:
    token = human_auth.bearer(authorization)
    if not token or not human_auth.is_session_token(token):
        return None
    try:
        human = human_auth.verify_session(token)
    except human_auth.HumanAuthError:
        return None
    return human.account_id or None


class OnBehalfMiddleware:
    def __init__(self, app) -> None:  # noqa: ANN001  (ASGI 앱)
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        auth = None
        for key, value in scope.get("headers") or []:
            if key.lower() == b"authorization":
                auth = value.decode("latin-1")
                break
        token = on_behalf.bind(session_account(auth))
        try:
            await self.app(scope, receive, send)
        finally:
            on_behalf.unbind(token)
