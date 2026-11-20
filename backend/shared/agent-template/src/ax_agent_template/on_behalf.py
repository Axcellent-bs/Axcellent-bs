"""이 요청을 시킨 사람(계정 ID)을 요청 문맥에 묶는다 (계약 `RequestMeta.on_behalf_of`, 11/20).

개인 MCP 연결로 들어온 요청만 묶는다: MCP 서버(개발 서비스 `work_api.py`)가 개인 MCP 연결 토큰을
dashboard-api에서 검사한 뒤 토큰 주인 계정을 묶는다. 묶여 있으면 `call_agent`가 보내는 메타의
`on_behalf_of`를 이 값으로 덮어쓴다(요청 본문·도구 입력의 값은 버린다). 묶지 않은 서버는 받은
메타 값을 그대로 복사해 다음 에이전트로 넘긴다(총괄 → 부서).
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_UNSET = object()
_var: ContextVar[object] = ContextVar("ax_on_behalf_of", default=_UNSET)


def bind(account_id: str | None) -> Token[object]:
    """지금 문맥에 사람 계정을 묶는다. 풀 때 `unbind`에 돌려준 값을 넘긴다."""
    return _var.set(account_id)


def unbind(token: Token[object]) -> None:
    _var.reset(token)


def bound() -> tuple[bool, str | None]:
    """(묶였는지, 계정 ID). 묶이지 않았으면 (False, None)."""
    value = _var.get()
    if value is _UNSET:
        return False, None
    return True, value if isinstance(value, str) else None
