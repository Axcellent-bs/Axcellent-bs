"""회사 AI 설정 (회사 AI 연결 설계 3장, 2026-11-20).

회사당 모델 하나·키 하나다. 예외는 코딩 에이전트("따로 설정")와 영상 생성뿐이다.
직원별 모델 선택(`overrides`)은 이번 범위 밖이라 칸이 없다(원하는 고객사가 생기면 추가).

- 키 값은 이 모양에 없다. 키는 암호화 키 저장소(`ax-secret-store`, tenant=회사 ID)의
  `COMPANY_AI_KEY_NAMES` 이름에 있다
- 월 예산은 토큰 수(`monthly_budget_tokens`). 화면은 사용 %만 계산해 보인다. None이면 예산 없음
- 모델 이름은 코드에 적지 않는다. `provider="auto"`면 공통 LLM 호출부가 모델 이름 앞부분으로
  공급자를 판별하고, `openai_compatible`이면 `base_url`을 쓴다
- 임베딩(`embed_model`·`embed_base_url`)은 결정 대기라 선택 칸만 둔다. 비어 있으면 지금처럼
  환경변수 설정(`from_env`)을 쓴다
- 저장 위치(dashboard-api가 쓰고 공통 LLM 호출부가 읽는다):
  `AX_COMPANY_AI_DIR` → 회사 연결 파일(`AX_COMPANY_CONNECTION_FILE`) 옆 `company_ai/`,
  그 아래 `<회사 ID>/company_ai.json`. 바꾼 이력은 같은 폴더 `company_ai.history.jsonl`
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime

# 회사 AI 키 저장소 이름: 회사 키, 코딩 에이전트 따로 키, 영상 공급자 키(마케팅 이름 그대로)
COMPANY_AI_KEY_NAMES: tuple[str, ...] = (
    "LLM_API_KEY",
    "DEV_CODER_LLM_API_KEY",
    "MARKETING_VIDEO_API_KEY",
)

CompanyAiCoderMode = Literal["company", "separate"]


class CompanyAiCoder(AxModel):
    """개발 AI 코딩 에이전트. `separate`면 `DEV_CODER_LLM_API_KEY`와 아래 모델·주소."""

    mode: CompanyAiCoderMode = "company"
    model: str = Field("", max_length=200)
    base_url: str = Field("", max_length=500)


class CompanyAiVideo(AxModel):
    """마케팅 영상 생성. 키(`MARKETING_VIDEO_API_KEY`)가 있어야 켜진다."""

    provider: str = Field("", max_length=40)
    enabled: bool = False


class CompanyAiSettings(AxModel):
    """회사 AI 설정 파일(`company_ai.json`) 한 개."""

    model: str = Field("", max_length=200)
    provider: str = Field("auto", pattern=r"^[a-z0-9_]{1,40}$")
    base_url: str = Field("", max_length=500)
    monthly_budget_tokens: int | None = Field(None, ge=0, le=10**13)
    coder: CompanyAiCoder = Field(default_factory=CompanyAiCoder)
    video: CompanyAiVideo = Field(default_factory=CompanyAiVideo)
    embed_model: str = Field("", max_length=200)
    embed_base_url: str = Field("", max_length=500)
    updated_at: UtcDatetime | None = None
    updated_by: str = Field("", max_length=64)
