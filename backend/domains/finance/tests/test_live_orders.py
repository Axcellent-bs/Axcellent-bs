"""주문 번호 형식: 고객지원의 숫자 주문 번호를 같은 주문으로 본다."""

from __future__ import annotations

import pytest

from ax_agent_template import events
from finance_agent import handlers
from finance_agent.sources import MockSources, order_key


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("AX_SHOP_API_URL", "http://shop.test")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3, "3"),
        ("ord_7", "ord_7"),
        (" ord_7 ", "ord_7"),
        (0, None),
        (-1, None),
        (True, None),
        ("", None),
        (None, None),
    ],
)
def test_order_key(value: object, expected: str | None) -> None:
    assert order_key(value) == expected
