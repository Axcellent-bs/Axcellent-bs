"""개인 MCP 연결 계약 (내 연결, 예진 결정 11/20).

- 내 AI 도구(Claude Code·Codex·Cursor)가 개인 MCP 연결 토큰으로 AXcellent MCP 서버에 붙는다
- 토큰 원문은 이 모양 어디에도 없다(검사 입력만 받는다). 저장은 해시
- 고를 수 있는 AI 직원은 `PERSONAL_MCP_AGENTS`(서비스 목록·LLM 부서 이름과 같다)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    PERSONAL_MCP_AGENTS,
    PERSONAL_MCP_TOKEN_PREFIX,
    PersonalMcpCheckInput,
    PersonalMcpCheckOutput,
)


def test_agent_list_matches_the_ten_ai_employees() -> None:
    assert set(PERSONAL_MCP_AGENTS) == {
        "orchestrator", "support", "finance", "data", "hr",
        "marketing", "planning", "dev", "infra", "archive",
    }
    assert PERSONAL_MCP_TOKEN_PREFIX == "axm_"


def test_check_input_takes_a_token_and_a_known_agent() -> None:
    PersonalMcpCheckInput(token="axm_" + "x" * 40, agent="dev")
    with pytest.raises(ValidationError):
        PersonalMcpCheckInput(token="axm_" + "x" * 40, agent="nope")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PersonalMcpCheckInput(token="", agent="dev")


def test_check_output_round_trips_and_has_no_token() -> None:
    out = PersonalMcpCheckOutput(
        allowed=False,
        code="LIMIT_REACHED",
        message="이번 달 내 몫을 다 썼어요",
        account_id="acc-1",
        name="민지",
        company_id="catbooks",
        agents=["dev"],
        used_tokens=500,
        limit_tokens=500,
        percent=100.0,
    )
    assert PersonalMcpCheckOutput.model_validate(out.model_dump(mode="json")) == out
    assert "token" not in PersonalMcpCheckOutput.model_fields
    with pytest.raises(ValidationError):
        PersonalMcpCheckOutput.model_validate({**out.model_dump(), "code": "WHATEVER"})
