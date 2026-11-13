"""새 기획서 번호는 기획서를 지운 뒤에도 남은 번호와 겹치지 않는다.

지우는 길: 사람 삭제, 요청 철회(withdraw_ticket).
"""

from __future__ import annotations

import pytest

from planning_agent.domain.service import _next_spec_id


@pytest.mark.role("planning")
def test_new_spec_id_does_not_reuse_after_middle_spec_removed(service) -> None:
    s = service.state
    base = s.specs[0]
    s.specs = [base.model_copy(update={"id": f"SPEC-{n}"}) for n in (6, 7, 8, 9)]
    # 가운데 하나를 지운다 (예전 규칙 "개수 + 6"이면 다음이 SPEC-9로 겹친다)
    s.specs = [x for x in s.specs if x.id != "SPEC-7"]
    assert _next_spec_id(s) == "SPEC-10"


@pytest.mark.role("planning")
def test_new_spec_id_without_removal_is_unchanged(service) -> None:
    s = service.state
    base = s.specs[0]
    s.specs = [base.model_copy(update={"id": f"SPEC-{n}"}) for n in (6, 7, 8)]
    assert _next_spec_id(s) == "SPEC-9"  # 개수 + 6 (예전 규칙)과 같다
