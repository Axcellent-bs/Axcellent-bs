"""고객지원 AI의 LLM이 회사 AI 키를 먼저 쓴다 (여러 회사 6+, 2026-11-23). 값은 더미만 쓴다.

초안(drafting)·대화(chat)·욕설 완화(abuse_soften)·영수증 사진(receipt_vision) 네 곳이 같은
설정(`llm_settings.settings()` = `company_ai.settings_for("support")`)을 쓴다.
"""

from __future__ import annotations

import pytest

from ax_agent_template import company_ai
from support_agent import abuse_soften, chat_handlers, drafting, llm_settings, receipt_vision

COMPANY = "DUMMY-COMPANY-KEY-0001"
SERVER = "SERVER-DEFAULT-NOT-COMPANY"


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPPORT_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    for name in ("SUPPORT_LLM_MODEL", "SUPPORT_LLM_BASE_URL", "SUPPORT_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "server-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://server.example")
    monkeypatch.setenv("LLM_API_KEY", SERVER)
    for module in (drafting, chat_handlers):
        monkeypatch.setattr(module, "is_mock", lambda *a, **k: False)


def _company(monkeypatch: pytest.MonkeyPatch, **fields: str) -> None:
    monkeypatch.setattr(
        company_ai, "company_llm", lambda cid=None: company_ai.CompanyLlm(api_key=COMPANY, **fields)
    )


def _clients() -> list:
    return [
        drafting.get_llm(),
        chat_handlers.get_llm(),
        abuse_soften._llm(),
        receipt_vision._real_llm(),
    ]


def test_without_a_company_key_every_client_uses_the_server_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(company_ai, "company_llm", lambda cid=None: None)
    for llm in _clients():
        assert (llm.settings.api_key, llm.settings.model) == (SERVER, "server-model")


def test_every_client_uses_the_company_key_when_there_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _company(monkeypatch, model="company-model")
    for llm in _clients():
        assert (llm.settings.api_key, llm.settings.model) == (COMPANY, "company-model")
        assert llm.settings.base_url == "https://server.example"  # 회사 주소가 비면 서버 값


def test_switches_read_the_company_model_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버에 모델이 없어도 회사가 모델·주소를 넣었으면 욕설 완화·사진 읽기를 켤 수 있다."""
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("SUPPORT_ABUSE_SOFTEN", "on")
    monkeypatch.setenv("SUPPORT_VISION", "on")
    monkeypatch.setattr(company_ai, "company_llm", lambda cid=None: None)
    assert abuse_soften.enabled() is False and receipt_vision.configured() is False
    _company(monkeypatch, model="company-model", base_url="https://company.example")
    assert abuse_soften.enabled() is True and receipt_vision.configured() is True
    assert llm_settings.settings().base_url == "https://company.example"


def test_the_company_key_is_read_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """키를 바꿔도 다시 켤 필요가 없다."""
    monkeypatch.setattr(company_ai, "company_llm", lambda cid=None: None)
    assert drafting.get_llm().settings.api_key == SERVER
    _company(monkeypatch)
    assert drafting.get_llm().settings.api_key == COMPANY
