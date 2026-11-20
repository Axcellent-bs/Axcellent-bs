"""개인 MCP 연결 (시스템 관리 › 도구 연결 › 내 연결, 예진 결정 11/20).

내 AI 도구(Claude Code·Codex·Cursor)가 MCP로 AXcellent에 붙어 AI 직원에게 일을 시킨다.
모델·비용은 내 도구 몫이라 키·모델·주소는 받지 않는다. 나만 보고 나만 바꾼다.

- 토큰: `PERSONAL_MCP_TOKEN_PREFIX` + 무작위. dashboard-api가 발급하고 원문은 발급 응답에 한 번만
  싣는다. 저장은 sha256 해시뿐, 화면에는 끝 4자리. 회사 ID + 계정 ID에 묶인다
- MCP 서버(개발 서비스 `/api/planning/mcp`)는 토큰을 직접 풀지 않고 dashboard-api 검사 API
  (`POST /api/dashboard/me/ai-connection/mcp/check`, 에이전트 토큰)에 이 모양으로 묻는다
- `agents`: 이 토큰으로 부를 수 있는 AI 직원. 고르지 않은 직원은 막는다(`AGENT_NOT_CHOSEN`)
- 한도: 회사 월 예산(토큰) 중 내 MCP 사용이 쓸 수 있는 몫 %. 내 사용량은 LLM 사용 기록 중
  `LlmCallRecord.account_id`가 나인 호출(MCP 요청은 `RequestMeta.on_behalf_of`가 채운다)
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

PERSONAL_MCP_TOKEN_PREFIX = "axm_"

# 고를 수 있는 AI 직원 = 서비스 목록(frontend/src/demo/services.ts AGENTS + 인프라)과 LLM 부서 이름
PersonalMcpAgent = Literal[
    "orchestrator",
    "support",
    "finance",
    "data",
    "hr",
    "marketing",
    "planning",
    "dev",
    "infra",
    "archive",
]
PERSONAL_MCP_AGENTS: tuple[str, ...] = (
    "orchestrator",
    "support",
    "finance",
    "data",
    "hr",
    "marketing",
    "planning",
    "dev",
    "infra",
    "archive",
)

# OK: 써도 된다. 나머지는 막은 이유
PersonalMcpCheckCode = Literal[
    "OK",
    "TOKEN_UNKNOWN",  # 모르는 토큰·끊은 토큰·다시 발급해 무효가 된 토큰
    "ACCOUNT_GONE",  # 토큰 주인 계정이 없어졌다
    "AGENT_NOT_CHOSEN",  # 고르지 않은 AI 직원
    "LIMIT_REACHED",  # 회사 예산 중 내 몫(%)을 다 썼다
    "COMPANY_LIMIT_REACHED",  # 회사 월 예산 상한(%)에 이르렀다
]


class PersonalMcpCheckInput(AxModel):
    """MCP 서버 → dashboard-api: 이 토큰으로 이 AI 직원을 불러도 되는지."""

    token: str = Field(min_length=1, max_length=200)
    agent: PersonalMcpAgent


class PersonalMcpCheckOutput(AxModel):
    """검사 결과. 토큰 원문은 싣지 않는다.

    사용량은 실제 기록에서 센 값만(기록이 꺼졌으면 0·None).
    """

    allowed: bool
    code: PersonalMcpCheckCode
    message: str = Field("", max_length=300)
    account_id: str | None = None
    name: str | None = None
    company_id: str | None = None
    agents: list[PersonalMcpAgent] = Field(default_factory=list, max_length=10)
    used_tokens: int = Field(0, ge=0)
    limit_tokens: int | None = Field(None, ge=0)
    percent: float | None = None
