"""배포 전 기획서 대조 → 배포 지시 계약 (`request_release_check`·`receive_release_order`)."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    ReceiveReleaseOrderInput,
    ReceiveReleaseOrderOutput,
    RequestReleaseCheckInput,
    RequestReleaseCheckOutput,
)


def test_check_input_needs_prs() -> None:
    ok = RequestReleaseCheckInput.model_validate(
        {
            "spec_id": "SPEC-8",
            "request_id": "REQ-22",
            "prs": [{"number": 31, "task_id": "T-11", "merged": True}],
        }
    )
    assert ok.env == "dev" and ok.prs[0].changed_paths == []
    with pytest.raises(ValidationError):
        RequestReleaseCheckInput.model_validate(
            {"spec_id": "SPEC-8", "request_id": "REQ-22", "prs": []}
        )
    with pytest.raises(ValidationError):
        RequestReleaseCheckInput.model_validate(
            {"spec_id": "SPEC-8", "request_id": "REQ-22", "env": "stage", "prs": []}
        )


def test_check_output_verdict_rules() -> None:
    ready = RequestReleaseCheckOutput.model_validate(
        {
            "check_id": "RC-1",
            "verdict": "ready",
            "items": [{"para_id": "R1", "status": "covered", "pr_numbers": [31]}],
            "order_sent": True,
        }
    )
    assert ready.hold_reasons == []
    hold = RequestReleaseCheckOutput.model_validate(
        {"check_id": "RC-2", "verdict": "hold", "hold_reasons": ["task_not_merged"]}
    )
    assert hold.order_sent is False
    with pytest.raises(ValidationError):  # hold인데 이유가 없음
        RequestReleaseCheckOutput.model_validate({"check_id": "RC-3", "verdict": "hold"})
    with pytest.raises(ValidationError):  # ready가 아닌데 지시를 보냄
        RequestReleaseCheckOutput.model_validate(
            {"check_id": "RC-4", "verdict": "needs_human", "order_sent": True}
        )
    with pytest.raises(ValidationError):  # 모르는 이유 코드
        RequestReleaseCheckOutput.model_validate(
            {"check_id": "RC-5", "verdict": "hold", "hold_reasons": ["looks_risky"]}
        )


def test_release_order_round_trip() -> None:
    order = ReceiveReleaseOrderInput.model_validate(
        {
            "check_id": "RC-1",
            "spec_id": "SPEC-8",
            "request_id": "REQ-22",
            "env": "prod",
            "pr_numbers": [31],
            "decided_by": "agent",
        }
    )
    assert order.note is None
    ok = ReceiveReleaseOrderOutput.model_validate(
        {"outcome": "accepted", "deployment_id": "DEP-3", "needs_approval": True}
    )
    assert ok.needs_approval
    assert ReceiveReleaseOrderOutput.model_validate({"outcome": "duplicate"}).deployment_id is None
    with pytest.raises(ValidationError):
        ReceiveReleaseOrderOutput.model_validate({"outcome": "accepted"})
    with pytest.raises(ValidationError):
        ReceiveReleaseOrderOutput.model_validate({"outcome": "rejected"})
