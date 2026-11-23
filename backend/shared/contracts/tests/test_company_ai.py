"""회사 AI 설정 계약 (회사 AI 연결 설계 3장, 2026-11-20).

- 회사당 모델 하나·키 하나. 예외는 코딩 에이전트("따로 설정")와 영상 생성
- 키 값은 이 모양에 없다(키는 암호화 키 저장소). 월 예산은 토큰 수
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    COMPANY_AI_KEY_NAMES,
    CompanyAiCoder,
    CompanyAiSettings,
    CompanyAiVideo,
)


def test_defaults_are_empty_and_use_the_company_model() -> None:
    s = CompanyAiSettings()
    assert s.model == "" and s.provider == "auto" and s.base_url == ""
    assert s.monthly_budget_tokens is None
    assert s.coder == CompanyAiCoder() and s.coder.mode == "company"
    assert s.video == CompanyAiVideo() and s.video.enabled is False
    assert s.updated_at is None and s.updated_by == ""
    # 임베딩은 결정 대기라 선택 칸만 둔다(비어 있으면 from_env 그대로)
    assert s.embed_model == "" and s.embed_base_url == ""


def test_design_example_round_trips() -> None:
    raw = {
        "model": "any-model-name",
        "provider": "openai_compatible",
        "base_url": "https://llm.example.test/v1",
        "monthly_budget_tokens": 20_000_000,
        "coder": {"mode": "separate", "model": "coder-model", "base_url": ""},
        "video": {"provider": "seedance", "enabled": True},
        "updated_at": "2026-11-20T01:02:03Z",
        "updated_by": "민지",
    }
    s = CompanyAiSettings.model_validate(raw)
    assert s.coder.mode == "separate" and s.monthly_budget_tokens == 20_000_000
    again = CompanyAiSettings.model_validate(s.model_dump(mode="json"))
    assert again == s


def test_there_is_no_place_for_a_key_value() -> None:
    with pytest.raises(ValidationError):
        CompanyAiSettings.model_validate({"api_key": "x" * 20})
    with pytest.raises(ValidationError):
        CompanyAiSettings.model_validate({"overrides": {"hr": {"model": "m"}}})  # 이번 범위 밖


@pytest.mark.parametrize(
    "bad",
    [
        {"monthly_budget_tokens": -1},
        {"coder": {"mode": "per_employee"}},
        {"provider": "Bad Provider!"},
        {"model": "m" * 201},
    ],
)
def test_rejects_bad_values(bad: dict) -> None:
    with pytest.raises(ValidationError):
        CompanyAiSettings.model_validate(bad)


def test_key_names_are_the_three_from_the_design() -> None:
    assert COMPANY_AI_KEY_NAMES == (
        "LLM_API_KEY",
        "DEV_CODER_LLM_API_KEY",
        "MARKETING_VIDEO_API_KEY",
        "MARKETING_IG_ACCESS_TOKEN",
        "MARKETING_IG_USER_ID",
        "MARKETING_YOUTUBE_REFRESH_TOKEN",
        "MARKETING_HIGGSFIELD_API_KEY",
        "MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN",
        "MARKETING_IG_OAUTH_REFRESH_TOKEN",
    )


def test_monthly_limit_percent_is_optional_and_between_1_and_100() -> None:
    """월 예산 상한 %(예산의 몇 %에 이르면 AI 호출을 멈출지, 11/20). None이면 100으로 본다."""
    assert CompanyAiSettings().monthly_limit_percent is None
    assert CompanyAiSettings(monthly_limit_percent=80).monthly_limit_percent == 80
    for bad in (0, 101, -1):
        with pytest.raises(ValidationError):
            CompanyAiSettings(monthly_limit_percent=bad)
    old = CompanyAiSettings().model_dump(mode="json")
    old.pop("monthly_limit_percent")
    assert CompanyAiSettings.model_validate(old).monthly_limit_percent is None
