"""원인 PR 경로: 배포 기록에 원인이 없으면 연결된 레포에서 머지된 PR을 원인으로 보고, 되돌리기는
개발 AI `revert_merged_pr`(되돌리기 PR 열고 머지)로 한다. 재배포는 고객사 자동 배포가 맡는다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, InputReason, TaskStatus
from infra_agent import handlers
from infra_agent.deployments import DeploymentHistory
from infra_agent.pr_suspects import PrSuspect, parse_candidates


class FakeDev:
    def __init__(self, prs: list[PrSuspect], status: str = "merged") -> None:
        self.prs = prs
        self.status = status
        self.reverted: list[int] = []

    async def related_prs(self, meta, component, since, now) -> list[PrSuspect]:
        return [p for p in self.prs if since <= p.merged_at <= now]

    async def revert_pr(self, meta, suspect, reason) -> AgentResult:
        self.reverted.append(suspect.pr_number)
        held = self.status == "held"
        return AgentResult(
            meta=meta,
            status=TaskStatus.INPUT_REQUIRED if held else TaskStatus.COMPLETED,
            outcome="held" if held else "executed",
            reason_code=InputReason.HUMAN_INPUT if held else None,
            output={"revert_pr_number": 501, "merged": not held, "status": self.status},
        )


NOW = datetime(2026, 10, 21, 5, 10, tzinfo=UTC)  # conftest 시계와 같다
BUG = PrSuspect(
    pr_number=77,
    title="결제 할인 계산 정리",
    merged_at=NOW - timedelta(minutes=5),
    repo="Axcellent-bs/Axcellent-bs",
    connection_id="conn-1",
)


@pytest.fixture
def real_mode(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    monkeypatch.setattr(handlers, "is_mock", lambda _name=None: False)
    runtime.history = DeploymentHistory([])  # 배포 기록에 원인 없음


def _call(a2a_body, skill: str, payload: dict) -> dict:
    request = AgentRequest.model_validate(a2a_body(skill, payload))
    fn = getattr(handlers, skill)
    return asyncio.run(fn(request)).model_dump(mode="json")


@pytest.mark.usefixtures("real_mode")
def test_merged_pr_becomes_the_suspect_and_is_reverted(runtime, a2a_body) -> None:
    runtime.dev = FakeDev([BUG])
    res = _call(a2a_body, "diagnose_incident", {"component": "payment"})
    assert res["outcome"] == "suspected"
    [suspect] = res["output"]["suspects"]
    assert (suspect["service"], suspect["version"], suspect["confidence"]) == (
        "repo:conn-1",
        "pr-77",
        "high",
    )

    out = _call(
        a2a_body, "rollback_deployment", {"service": "repo:conn-1", "from_version": "pr-77"}
    )
    assert out["status"] == "completed"
    assert out["output"]["rolled_back_to"] == "revert PR #501"
    assert runtime.dev.reverted == [77]
    # 같은 요청을 다시 보내도 되돌리기를 두 번 하지 않는다
    _call(a2a_body, "rollback_deployment", {"service": "repo:conn-1", "from_version": "pr-77"})
    assert runtime.dev.reverted == [77]


@pytest.mark.usefixtures("real_mode")
def test_held_revert_is_passed_back_for_human_approval(runtime, a2a_body) -> None:
    runtime.dev = FakeDev([BUG], status="held")
    _call(a2a_body, "diagnose_incident", {"component": "payment"})
    out = _call(
        a2a_body, "rollback_deployment", {"service": "repo:conn-1", "from_version": "pr-77"}
    )
    assert out["status"] == TaskStatus.INPUT_REQUIRED.value
    assert runtime.rollbacks and all(
        r["status"] == "not_executed" for r in runtime.rollbacks.values()
    )


@pytest.mark.usefixtures("real_mode")
def test_no_pr_and_no_deploy_stays_undetermined(runtime, a2a_body) -> None:
    runtime.dev = FakeDev([])
    res = _call(a2a_body, "diagnose_incident", {"component": "payment"})
    assert res["outcome"] == "undetermined"


@pytest.mark.usefixtures("real_mode")
def test_pr_suspect_needs_server_fault(runtime, a2a_body) -> None:
    from infra_agent.clients import MockShop

    runtime.shop = MockShop()  # 결제 서버 정상이면 PR이 있어도 되돌리지 않는다
    runtime.dev = FakeDev([BUG])
    assert _call(a2a_body, "diagnose_incident", {"component": "payment"})["outcome"] == (
        "undetermined"
    )


def test_rollback_of_unknown_pr_is_refused(runtime, a2a_body) -> None:
    out = _call(a2a_body, "rollback_deployment", {"service": "repo:conn-1", "from_version": "pr-9"})
    assert out["outcome"] == "invalid_input"


@pytest.mark.usefixtures("real_mode")
@pytest.mark.parametrize("value", ["off", "0", "false", "NO"])
def test_switch_off_skips_pr_suspects(runtime, a2a_body, monkeypatch, value: str) -> None:
    """INFRA_PR_SUSPECTS=off: 개발 AI에 PR을 묻지 않아 원인 미확정 → 사람 이관 그대로."""
    monkeypatch.setenv("INFRA_PR_SUSPECTS", value)
    runtime.dev = FakeDev([BUG])
    res = _call(a2a_body, "diagnose_incident", {"component": "payment"})
    assert res["outcome"] == "undetermined" and res["output"]["suspects"] == []


@pytest.mark.usefixtures("real_mode")
def test_switch_off_refuses_a_pr_revert_even_after_diagnosis(
    runtime, a2a_body, monkeypatch
) -> None:
    """켜진 채 진단했어도, 되돌리기 때 꺼져 있으면 실제 레포 PR을 되돌리지 않는다."""
    runtime.dev = FakeDev([BUG])
    _call(a2a_body, "diagnose_incident", {"component": "payment"})
    monkeypatch.setenv("INFRA_PR_SUSPECTS", "off")
    out = _call(
        a2a_body, "rollback_deployment", {"service": "repo:conn-1", "from_version": "pr-77"}
    )
    assert (out["status"], out["outcome"]) == ("failed", "invalid_input")
    assert runtime.dev.reverted == []


@pytest.mark.usefixtures("real_mode")
def test_switch_is_on_by_default(runtime, a2a_body, monkeypatch) -> None:
    monkeypatch.delenv("INFRA_PR_SUSPECTS", raising=False)
    runtime.dev = FakeDev([BUG])
    res = _call(a2a_body, "diagnose_incident", {"component": "payment"})
    assert res["outcome"] == "suspected"


def test_candidates_without_repo_are_ignored() -> None:
    rows = {
        "candidates": [
            {"pr_number": 3, "title": "메모리 PR", "merged_at": "2026-10-21T05:00:00Z"},
            {
                "pr_number": 77,
                "title": "실제 PR",
                "merged_at": "2026-10-21T05:05:00Z",
                "repo": "Axcellent-bs/Axcellent-bs",
            },
        ]
    }
    assert [p.pr_number for p in parse_candidates(rows)] == [77]
