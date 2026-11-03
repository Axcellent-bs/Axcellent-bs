"""장애 연결로 개발이 만든 재발 방지 요청이 기획 서비스 요청 목록에도 들어간다 (분리 뒤 빈틈).

개발(dev)이 link_incident로 후속 요청을 만들면 기획서 없이 요청만 `receive_dev_result`로 넘긴다.
기획은 요청 목록에 같은 id로 받아 기획서를 쓸 수 있다. 같은 내용은 다시 보내지 않는다.
"""

from __future__ import annotations

import pytest

from ax_workflow_testing import Wire

pytestmark = pytest.mark.role("dev")

TITLE = "결제 설정 변경은 사전 부하 테스트 필수"


async def _link(w: Wire, key: str = "ctx_inc_0002:link_incident:28"):
    return await w.dev.link_incident(
        context_id="ctx_inc_0002",
        requester="orchestrator",
        incident_context_id="ctx_inc_0002",
        pr_numbers=[28],
        follow_up_title=TITLE,
        idempotency_key=key,
    )


def test_follow_up_request_reaches_planning_once() -> None:
    w = Wire()
    out = w.run(_link(w))
    rid = out.output["follow_up_request_id"]
    got = next((r for r in w.planning.state.requests if r.id == rid), None)
    assert got is not None
    assert (got.title, got.state, got.context_id) == (TITLE, "접수", "ctx_inc_0002")
    assert got.via.startswith("link_incident") and got.from_ == "orchestrator"
    texts = [a.text for a in w.planning.state.activity if a.point == "receive_dev_result"]
    assert any(f"요청 {rid} 받음: {TITLE}" in x for x in texts)

    sent = len(w.sent)
    w.run(w.dev.tick())  # 바뀐 게 없으면 다시 보내지 않는다
    assert len(w.sent) == sent


def test_follow_up_with_spec_goes_by_spec_not_request() -> None:
    """기획서가 생긴 후속 요청은 요청만 따로 보내지 않는다 (기획서 단위로 간다)."""
    w = Wire()
    out = w.run(_link(w))
    rid = out.output["follow_up_request_id"]
    req = next(r for r in w.dev.state.requests if r.id == rid)
    req.spec = "SPEC-99"
    assert rid not in {r.request_id for _, _, r, _ in w.dev._follow_up_outgoing(w.dev.state, True)}


def test_planning_keeps_its_own_copy_of_follow_up() -> None:
    """이미 받은 후속 요청은 다시 와도 기획 쪽 기획서·상태를 덮어쓰지 않는다."""
    w = Wire()
    out = w.run(_link(w))
    rid = out.output["follow_up_request_id"]
    mine = next(r for r in w.planning.state.requests if r.id == rid)
    mine.spec, mine.state = "SPEC-50", "기획 중"
    for skill, ctx, res, key in w.dev._follow_up_outgoing(w.dev.state, full=True):
        w.run(w.send(skill, ctx, "t", res, key + ":again"))
    mine = next(r for r in w.planning.state.requests if r.id == rid)
    assert (mine.spec, mine.state) == ("SPEC-50", "기획 중")
