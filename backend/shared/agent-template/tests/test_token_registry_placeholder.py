"""토큰 명부(AX_AGENT_TOKENS)의 자리표시자 거부.

화면용 자리표시자 "data-via-nginx"가 명부 값으로 들어가면 누구나 그 문자열로 통과할 수 있다.
그 항목은 받지 않고, 시작할 때 경고 로그(이름만)를 남긴다.
"""

from __future__ import annotations

import logging

import pytest

from ax_agent_template import config
from ax_agent_template.server import create_agent_app, verify_token


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch, real_mode: None) -> None:
    monkeypatch.setenv("AX_AGENT_TOKENS",
                       "orchestrator=tok-orch-7f3a9c,dashboard-api=data-via-nginx")  # fmt: skip


def test_placeholder_entry_is_not_in_the_registry(tokens: None) -> None:
    assert config.token_registry() == {"orchestrator": "tok-orch-7f3a9c"}
    assert config.placeholder_entries() == ["dashboard-api"]


def test_placeholder_does_not_pass_as_a_token(tokens: None) -> None:
    with pytest.raises(PermissionError):
        verify_token("data-via-nginx")
    assert verify_token("tok-orch-7f3a9c") == "orchestrator"


def test_startup_warns_with_the_name_only(tokens: None, card, handlers, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.WARNING)
    create_agent_app(card, handlers)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "dashboard-api" in text and "자리표시자" in text
    assert "tok-orch-7f3a9c" not in text


def test_no_warning_without_placeholder(monkeypatch, real_mode, card, handlers, caplog) -> None:  # noqa: ANN001
    monkeypatch.setenv("AX_AGENT_TOKENS", "orchestrator=tok-orch-7f3a9c")
    caplog.set_level(logging.WARNING)
    create_agent_app(card, handlers)
    assert "자리표시자" not in "\n".join(r.getMessage() for r in caplog.records)
