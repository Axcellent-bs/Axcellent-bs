"""실제 서버에 목 스위치가 남아 있으면 시작을 거부한다 (pytest를 잠시 가려 시험)."""

import sys

import pytest

from ax_agent_template.config import refuse_mock_outside_tests


def _outside_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "pytest")  # 테스트가 끝나면 monkeypatch가 되돌린다
    monkeypatch.delenv("AX_ALLOW_MOCK", raising=False)


def test_refuses_when_mock_switch_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _outside_pytest(monkeypatch)
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    with pytest.raises(RuntimeError, match="목 스위치"):
        refuse_mock_outside_tests("hr")


def test_refuses_department_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    _outside_pytest(monkeypatch)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("HR_MOCK", "1")
    with pytest.raises(RuntimeError, match="목 스위치"):
        refuse_mock_outside_tests("hr")


def test_allows_real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _outside_pytest(monkeypatch)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("HR_MOCK", raising=False)
    refuse_mock_outside_tests("hr")


def test_explicit_allow_for_dev_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    _outside_pytest(monkeypatch)
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("AX_ALLOW_MOCK", "1")
    refuse_mock_outside_tests("hr")


def test_pytest_itself_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    refuse_mock_outside_tests("hr")
