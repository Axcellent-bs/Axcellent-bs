"""총괄 대화의 LLM이 회사 AI 키를 먼저 쓴다 (여러 회사 6+, 11/23). 값은 더미만 쓴다."""

from __future__ import annotations

import pytest

from ax_agent_template import company_ai
from ax_orchestrator.chat import turn

COMPANY = "DUMMY-COMPANY-KEY-0001"


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("LLM_MODEL", "server-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://server.example")
    monkeypatch.setenv("LLM_API_KEY", "SERVER-DEFAULT-NOT-COMPANY")
    monkeypatch.setattr("ax_agent_template.config.is_mock", lambda *a, **k: False)
    monkeypatch.setattr(turn.template_config, "is_mock", lambda *a, **k: False)


def test_default_llm_uses_the_server_settings_without_a_company_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(company_ai, "company_llm", lambda cid=None: None)
    llm = turn.default_llm()
    assert llm is not None
    assert llm.settings.api_key == "SERVER-DEFAULT-NOT-COMPANY"


def test_default_llm_uses_the_company_key_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        company_ai,
        "company_llm",
        lambda cid=None: company_ai.CompanyLlm(api_key=COMPANY, model="company-model"),
    )
    llm = turn.default_llm()
    assert llm is not None
    assert (llm.settings.api_key, llm.settings.model) == (COMPANY, "company-model")
    assert llm.settings.base_url == "https://server.example"  # 회사 주소가 비면 서버 값
