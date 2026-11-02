"""재무 이상 이벤트 제목: 남은 규칙은 쉬운 말로, 없앤 감사 코드의 옛 기록도 코드가 보이지 않게."""

from __future__ import annotations

import pytest

from ax_orchestrator.naming import FINANCE_DEFAULT, event_title


def test_remaining_rule_has_a_plain_title() -> None:
    assert event_title("finance", "gate_hold_trend") == "안전 장치 보류·거절이 늘었어요"


@pytest.mark.parametrize(
    "old",
    ["split_under_limit", "duplicate_coupon", "amount_recheck_failed", "repeat_refund", None],
)
def test_old_codes_fall_back_without_showing_the_code(old: str | None) -> None:
    assert event_title("finance", old) == FINANCE_DEFAULT
