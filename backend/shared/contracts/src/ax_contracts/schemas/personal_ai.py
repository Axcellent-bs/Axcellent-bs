r"""개인 AI 연결 설정 (시스템 관리 › 도구 연결 › 내 연결, 예진 결정 11/20).

회원 각자가 자기 LLM 키로 AI 직원을 부르게 하는 설정이다. 나만 보고 나만 바꾼다
(관리자도 남의 개인 설정·키를 못 본다).

- 키 값은 이 모양에 없다. 키는 암호화 키 저장소(`ax-secret-store`)의 `PERSONAL_AI_KEY_NAME`에 있고,
  저장소 tenant는 회사 ID + 계정 ID로 만든 계정 전용 값이다:
  `PERSONAL_AI_TENANT_PREFIX + sha256(f"{회사 ID}\n{계정 ID}").hexdigest()[:38]`
  (40자, 저장소 tenant 규칙 `^[a-z0-9][a-z0-9_-]{0,39}$`에 맞는다).
  회사 키(tenant=회사 ID)·다른 계정 키와 섞이지 않는다
- 설정 파일 위치(dashboard-api가 쓰고 공통 LLM 호출부가 읽는다): 회사 AI 설정 폴더
  (`AX_COMPANY_AI_DIR` → 회사 연결 파일 옆 `company_ai/`) 아래
  `<회사 ID>/personal/<계정 slug>.json`.
  계정 slug는 `sha256(계정 ID).hexdigest()[:32]`. 바꾼 이력은 같은 폴더 `<계정 slug>.history.jsonl`
- `agents`: 이 사람이 일을 시킬 때 회사 키 대신 개인 키로 부를 AI 직원(`PERSONAL_AI_AGENTS`).
  다른 사람이 시킨 일·사람 없이 도는 일에는 쓰지 않는다
- `monthly_limit_tokens`: 개인 키 호출의 월 한도(토큰). 개인 키 호출은 회사 월 예산에 세지 않고
  이 한도로만 센다(사용량 기록 `LlmCallRecord.account_id`). None이면 한도 없음
- 개인 키 호출이 실패해도 회사 키로 자동 전환하지 않고 멈춘다(설계 9장)
- 모델 이름은 코드에 적지 않는다. `provider="auto"`면 모델 이름 앞부분으로 공급자를 판별하고,
  `openai_compatible`이면 `base_url`을 쓴다(회사 AI 설정과 같은 규칙)
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime

PERSONAL_AI_KEY_NAME = "PERSONAL_LLM_API_KEY"
PERSONAL_AI_TENANT_PREFIX = "p-"

# 고를 수 있는 AI 직원 = 서비스 목록(frontend/src/demo/services.ts AGENTS + 인프라)과 LLM 부서 이름
PersonalAiAgent = Literal[
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
PERSONAL_AI_AGENTS: tuple[str, ...] = (
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


class PersonalAiSettings(AxModel):
    """개인 AI 설정 파일 한 개(계정 하나)."""

    model: str = Field("", max_length=200)
    provider: str = Field("auto", pattern=r"^[a-z0-9_]{1,40}$")
    base_url: str = Field("", max_length=500)
    monthly_limit_tokens: int | None = Field(None, ge=0, le=10**13)
    agents: list[PersonalAiAgent] = Field(default_factory=list, max_length=10)
    updated_at: UtcDatetime | None = None
