"""개인 MCP 연결 토큰(`axm_…`) 검사: 개발 서비스 MCP 서버가 dashboard-api에 묻는다 (11/20).

내 연결(dashboard-api `personal_mcp.py`)에서 발급한 토큰은 해시로만 dashboard-api에 있다. 이 서버는
토큰을 직접 풀지 않고 dashboard-api 검사 API(`POST /api/dashboard/me/ai-connection/mcp/check`,
개발 서비스 에이전트 토큰)에 계약 `PersonalMcpCheckInput`으로 묻는다. 도메인끼리 import하지 않는다.

- 이 서버의 AI 직원 이름은 `dev`(개발 AI). 내 연결에서 개발 AI를 고르지 않았으면 막힌다
- 막힌 이유는 계약의 `code`·`message` 그대로 돌려준다. 모르는/끊은 토큰·없는 계정은 401,
  고르지 않은 AI 직원은 403, 내 몫·회사 상한을 넘었으면 429
- dashboard-api에 닿지 않으면 통과시키지 않고 503(`MCP_CHECK_UNAVAILABLE`)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
from pydantic import ValidationError

from ax_agent_template.config import agent_token, agent_url
from ax_contracts.schemas import (
    PERSONAL_MCP_TOKEN_PREFIX,
    PersonalMcpCheckInput,
    PersonalMcpCheckOutput,
)
from ax_workflow.auth import AuthError

AGENT = "dev"
PATH = "/api/dashboard/me/ai-connection/mcp/check"
TIMEOUT_SECONDS = 5.0
STATUS = {
    "TOKEN_UNKNOWN": 401,
    "ACCOUNT_GONE": 401,
    "AGENT_NOT_CHOSEN": 403,
    "LIMIT_REACHED": 429,
    "COMPANY_LIMIT_REACHED": 429,
}


class CheckUnavailable(Exception):
    """dashboard-api 검사에 닿지 못했다(연결·응답 오류)."""


class Refused(AuthError):
    """개인 토큰을 막았다. `{error: {code, message}}`로 나간다(main.py AuthError 처리기)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status, message)
        self.code = code


def is_personal(token: str) -> bool:
    return token.startswith(PERSONAL_MCP_TOKEN_PREFIX)


async def ask_dashboard(
    body: PersonalMcpCheckInput, http_client: httpx.AsyncClient | None = None
) -> PersonalMcpCheckOutput:
    url = agent_url("dashboard-api") + PATH
    headers = {"Authorization": f"Bearer {agent_token(AGENT)}"}
    try:
        if http_client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as http:
                res = await http.post(url, json=body.model_dump(mode="json"), headers=headers)
        else:
            res = await http_client.post(url, json=body.model_dump(mode="json"), headers=headers)
        res.raise_for_status()
        return PersonalMcpCheckOutput.model_validate(res.json()["data"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, ValidationError) as exc:
        raise CheckUnavailable(type(exc).__name__) from None


# 시험에서 바꿔 끼우는 자리
CHECK: Callable[[PersonalMcpCheckInput], Awaitable[PersonalMcpCheckOutput]] = ask_dashboard


async def verify(token: str) -> PersonalMcpCheckOutput:
    """통과하면 검사 결과(주인 계정). 막히면 `Refused`, 검사에 닿지 못하면 503 `Refused`."""
    try:
        out = await CHECK(PersonalMcpCheckInput(token=token, agent=AGENT))
    except CheckUnavailable:
        raise Refused(
            503, "MCP_CHECK_UNAVAILABLE", "개인 연결 토큰을 확인하지 못했다 (dashboard-api)"
        ) from None
    except ValidationError:
        raise Refused(401, "TOKEN_UNKNOWN", "연결 토큰 형식이 아니다") from None
    if not out.allowed:
        status = STATUS.get(out.code, 403)
        raise Refused(status, out.code, out.message or "개인 연결 토큰을 쓸 수 없다")
    if not out.account_id:
        raise Refused(401, "ACCOUNT_GONE", "개인 연결 토큰의 주인 계정을 알 수 없다")
    return out
