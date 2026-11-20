"""개인 AI 연결 계약 (내 연결, 예진 결정 11/20).

- 키 값은 이 모양에 없다(키는 암호화 키 저장소, 회사 ID + 계정 ID 범위)
- 고를 수 있는 AI 직원은 `PERSONAL_AI_AGENTS`(서비스 목록·LLM 부서 이름과 같다)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    PERSONAL_AI_AGENTS,
    PERSONAL_AI_KEY_NAME,
    PERSONAL_AI_TENANT_PREFIX,
    PersonalAiSettings,
)


def test_defaults_are_empty_and_pick_no_agent() -> None:
    s = PersonalAiSettings()
    assert s.model == "" and s.provider == "auto" and s.base_url == ""
    assert s.monthly_limit_tokens is None
    assert s.agents == []
    assert s.updated_at is None


def test_agent_list_matches_the_ten_ai_employees() -> None:
    assert set(PERSONAL_AI_AGENTS) == {
        "orchestrator", "support", "finance", "data", "hr",
        "marketing", "planning", "dev", "infra", "archive",
    }
    assert PERSONAL_AI_KEY_NAME == "PERSONAL_LLM_API_KEY"
    assert PERSONAL_AI_TENANT_PREFIX == "p-"


def test_round_trip_and_unknown_agent_is_rejected() -> None:
    raw = {
        "model": "any-model-name",
        "provider": "openai_compatible",
        "base_url": "https://llm.example.test/v1",
        "monthly_limit_tokens": 1_000_000,
        "agents": ["planning", "dev"],
        "updated_at": "2026-11-20T01:02:03Z",
    }
    s = PersonalAiSettings.model_validate(raw)
    assert PersonalAiSettings.model_validate(s.model_dump(mode="json")) == s
    with pytest.raises(ValidationError):
        PersonalAiSettings.model_validate({**raw, "agents": ["nope"]})


def test_there_is_no_place_for_a_key_value() -> None:
    with pytest.raises(ValidationError):
        PersonalAiSettings.model_validate({"api_key": "x" * 20})
