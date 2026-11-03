"""기획 서비스가 자기 관문(`_execute`)으로 묻는 행동은 등록표에서 planning이 요청자여야 한다.

시험의 가짜 관문은 늘 통과라 등록표 요청자가 빠져도 시험은 붙는다. 실제 모드에서는
안전 레이어가 거절해(gate_rejected) 흐름이 멈춘다 (11/3 `request_release_check`).
"""

from __future__ import annotations

import pytest

from ax_safety.rules import load_registry

# 기획이 남이 보낸 요청을 받아 자기 관문으로 실행하는 행동
PLANNING_EXECUTED = (
    "request_release_check",
    "send_release_order",
    "receive_dev_result",
    "receive_planning_answer",
)


@pytest.mark.role("planning")
@pytest.mark.parametrize("action", PLANNING_EXECUTED)
def test_planning_may_execute_its_own_actions(action: str) -> None:
    spec = load_registry().get(action)
    assert spec is not None, f"{action}이 등록표에 없다"
    assert spec.owner == "planning"
    assert not spec.requesters or "planning" in spec.requesters, spec.requesters
