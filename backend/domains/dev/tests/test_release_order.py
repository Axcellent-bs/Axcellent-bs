"""배포 전 기획서 대조 (개발 쪽): 작업이 다 머지되면 기획에 대조를 묻고, 배포 지시를 받아 배포 요청.

기획이 ready로 판정하면 바로 배포 지시(receive_release_order)를 보내고, 개발은 지금의 배포 요청
흐름을 탄다 (prod·보호 경로는 사람 승인 대기). 머지 안 된·모르는 PR 지시는 받지 않는다.
"""

from __future__ import annotations

import pytest

from ax_contracts.schemas.planning import ReceiveReleaseOrderInput
from ax_workflow_testing import Wire
from dev_agent.deploy_store import get_deploy_store

pytestmark = pytest.mark.role("dev")


def _order(pr_numbers: list[int], check: str = "RC-9") -> ReceiveReleaseOrderInput:
    return ReceiveReleaseOrderInput(
        check_id=check,
        spec_id="SPEC-5",
        request_id="REQ-9",
        env="dev",
        pr_numbers=pr_numbers,
        decided_by="human",
    )


def test_simple_request_asks_release_check_and_gets_deployed() -> None:
    get_deploy_store().reset()
    w = Wire()
    w.run(w.planning.inbound("support_simple"))
    checks = list(w.planning.release_checks.values())
    assert len(checks) == 1 and checks[0]["verdict"] == "ready"  # 검수 통과 → 바로 지시
    assert checks[0]["order_sent"] is True
    assert w.skills().count("request_release_check") == 1
    texts = [a.text for a in w.dev.state.activity if a.point == "receive_release_order"]
    assert any("배포 지시 받음 (기획 AI, dev" in t and "배포 요청 DEP-" in t for t in texts)
    pr = w.dev.state.prs[-1]
    assert any(pr.number in d.pr_numbers for d in get_deploy_store().deployments)

    w.run(w.dev.tick())  # 같은 PR 묶음이면 다시 묻지 않는다
    assert w.skills().count("request_release_check") == 1


def test_order_for_unmerged_or_unknown_pr_is_rejected(service) -> None:
    import asyncio

    out = asyncio.run(
        service.receive_release_order(
            context_id="ctx_req_0009", requester="planning", inp=_order([999]),
            idempotency_key="k-unknown",
        )
    )  # fmt: skip
    assert out.output == {"outcome": "rejected", "reason_code": "unknown_pr"}


def test_same_order_twice_is_duplicate(service) -> None:
    import asyncio

    get_deploy_store().reset()
    first = asyncio.run(
        service.receive_release_order(
            context_id="ctx_req_0009", requester="planning", inp=_order([28]), idempotency_key="k1"
        )
    )
    assert first.output["outcome"] == "accepted" and first.output["deployment_id"]
    again = asyncio.run(
        service.receive_release_order(
            context_id="ctx_req_0009", requester="planning", inp=_order([28]), idempotency_key="k1"
        )
    )
    assert again.output["outcome"] == "duplicate"
    assert again.output["deployment_id"] == first.output["deployment_id"]
