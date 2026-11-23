"""고객지원 AI가 쓸 LLM 설정 (여러 회사 6+, 2026-11-23).

서버 설정(`SUPPORT_LLM_*`·`LLM_*`, `LLMSettings.from_env("support")`) 위에 회사 AI 키·주소·모델이
있으면 회사 값을 얹는다(`company_ai.settings_for`). 회사 키가 없거나 회사 연결 전이거나 키 저장소가
db 모드가 아니면 서버 설정 그대로다(로컬·테스트 동작 그대로).

부를 때마다 읽으므로 회사 키를 바꿔도 다시 켤 필요가 없다.
클라이언트는 각 모듈이 `LLMClient("support", settings=llm_settings.settings())`로 만든다.
"""

from __future__ import annotations

from ax_agent_template import company_ai
from ax_agent_template.llm import LLMSettings

DEPT = "support"


def settings() -> LLMSettings:
    return company_ai.settings_for(DEPT)
