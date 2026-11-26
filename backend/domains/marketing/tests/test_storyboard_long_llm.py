"""11/25 뒤에서 만드는 스토리보드는 LLM 응답 제한을 늘린다(기본 60초에 llm_failed로 끝났다)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta
from marketing_agent import handlers


def _request(inp: dict) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_sb",
            token="t",
            idempotency_key="k",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        ),
        skill="storyboard_prompt",
        input=inp,
        untrusted={"story": "들판", "shots": 6},
    )


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", None)
    monkeypatch.setenv("MARKETING_LLM_MODEL", "test-model")
    monkeypatch.setenv("MARKETING_LLM_BASE_URL", "http://llm.test")
    monkeypatch.setenv("MARKETING_LLM_TIMEOUT_SEC", "60")


def test_background_storyboard_gets_a_longer_llm_timeout(llm_env: None) -> None:
    llm = handlers.storyboard_llm_factory(_request({"background": True}))()
    assert llm is not None
    assert llm.timeout == handlers.BACKGROUND_STORYBOARD_LLM_TIMEOUT_SEC > 60


def test_screen_storyboard_keeps_the_default_llm(llm_env: None) -> None:
    from marketing_agent.chat import default_llm

    assert handlers.storyboard_llm_factory(_request({})) is default_llm
    assert handlers.storyboard_llm_factory(_request({"background": "yes"})) is default_llm


def test_long_factory_always_gives_the_long_timeout(llm_env: None) -> None:
    """11/26 background 판단 없이 긴 LLM을 주는 함수(대화 스토리보드가 쓴다).

    env가 있으면 fallback보다 env."""
    llm = handlers.long_storyboard_llm_factory(fallback=lambda: "chat")()
    assert llm is not None and llm != "chat"
    assert llm.timeout == handlers.BACKGROUND_STORYBOARD_LLM_TIMEOUT_SEC == 240.0


def test_test_llm_wins_in_the_long_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = object()
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: fake)
    assert handlers.long_storyboard_llm_factory(fallback=lambda: None)() is fake


def test_test_llm_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = object()
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: fake)
    assert handlers.storyboard_llm_factory(_request({"background": True}))() is fake
