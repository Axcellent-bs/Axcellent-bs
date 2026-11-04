"""개발 LLM 설정은 기획(`PLANNING_LLM_*`)과 따로 `DEV_LLM_*` → `LLM_*`.

예전에 `PLANNING_LLM_*`로 개발을 돌리던 설정은 `DEV_LLM_*`가 없을 때만 한동안 쓰고 경고한다.
"""

from __future__ import annotations

import logging

import pytest

from dev_agent import llm_env

pytestmark = pytest.mark.role("dev")


@pytest.fixture(autouse=True)
def _fresh_warnings():
    llm_env._warned.clear()
    yield
    llm_env._warned.clear()


def test_dev_settings_ignore_planning_when_dev_is_set() -> None:
    env = {
        "DEV_LLM_MODEL": "claude-sonnet",
        "DEV_LLM_BASE_URL": "https://dev.example",
        "PLANNING_LLM_MODEL": "gemini",
        "PLANNING_LLM_BASE_URL": "https://planning.example",
        "LLM_API_KEY": "common-key",
    }
    s = llm_env.settings(env)
    got = (s.model, s.base_url, s.api_key)
    assert got == ("claude-sonnet", "https://dev.example", "common-key")


def test_dev_falls_back_to_common_llm() -> None:
    s = llm_env.settings({"LLM_MODEL": "m", "LLM_BASE_URL": "https://c.example"})
    assert (s.model, s.base_url) == ("m", "https://c.example")


def test_old_planning_only_setup_still_works_with_warning(caplog) -> None:  # noqa: ANN001
    env = {"PLANNING_LLM_MODEL": "old", "PLANNING_LLM_BASE_URL": "https://p.example"}
    with caplog.at_level(logging.WARNING, logger=llm_env.__name__):
        s = llm_env.settings(env)
        llm_env.settings(env)
    assert (s.model, s.base_url) == ("old", "https://p.example")
    warns = [r for r in caplog.records if "DEV_LLM_*로 옮기세요" in r.getMessage()]
    assert len(warns) == 1  # 한 번만


def test_key_store_new_name_then_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_LLM_KEY_STORE", raising=False)
    monkeypatch.setenv("PLANNING_LLM_KEY_STORE", "/tmp/old.json")
    assert llm_env.key_store_path() == "/tmp/old.json"
    monkeypatch.setenv("DEV_LLM_KEY_STORE", "/tmp/new.json")
    assert llm_env.key_store_path() == "/tmp/new.json"
