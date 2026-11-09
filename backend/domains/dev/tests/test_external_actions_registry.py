"""개발 서비스의 외부 연결 쓰기 목록과 안전 레지스트리의 `connection_field`는 같아야 한다.

한쪽에만 더하면 연결 id 없는 쓰기가 관문을 지나거나, 개발 쓰기가 전부 보류된다.
"""

from __future__ import annotations

from ax_safety.rules import load_registry
from dev_agent.external_actions import VCS_WRITE_ACTIONS


def test_external_write_list_matches_safety_registry() -> None:
    marked = {a.action for a in load_registry().actions if a.connection_field}
    assert marked == set(VCS_WRITE_ACTIONS)
