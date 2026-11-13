"""장애 대응의 실제 레포: 관련 PR 찾기(연결 레포 포함)·병합된 PR 되돌리기(revert_merged_pr).

형상관리는 목 부품을 바꿔 끼운다(연결 id `default`). 실제 GitHub는 부르지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import FindRelatedPrsOutput, RevertMergedPrOutput
from dev_agent import incident_repo, vcs
from dev_agent.main import app
from dev_agent.service import DevService
from dev_agent.vcs import MockVcsAdapter
from dev_agent.vcs.base import CheckRun

pytestmark = pytest.mark.role("dev")

CID = vcs.OVERRIDE_ID
T0 = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def _call(body: dict) -> dict:
    return TestClient(app).post("/a2a", json=body).json()


class Gate:
    def __init__(self, hold: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hold = hold or set()

    async def __call__(self, action, params, meta, evidence) -> ExecuteDecision:
        self.calls.append((action, {**params, "key": meta.idempotency_key}))
        if action in self.hold:
            return ExecuteDecision(execution_id="exe_hold", grade="C", status="held")
        return ExecuteDecision(execution_id=f"exe_{len(self.calls)}", grade="B", status="executed")


@pytest.fixture
def repo():
    m = MockVcsAdapter(clock=lambda: T0)
    m.seed_files({"backend/shared/shop-api/pay.py": "rate = 1\n"})
    vcs._screen = None
    vcs.set_vcs(m)
    yield m
    vcs.set_vcs(None)


def _bad_merge(m: MockVcsAdapter) -> int:
    """심은 버그: 결제 파일을 바꾼 PR을 열고 머지한다."""
    opened = run(
        m.open_change(
            "gen/pay",
            "결제 요율 바꾸기",
            "",
            files={"backend/shared/shop-api/pay.py": "rate = 0\n"},
        )
    )
    run(m.merge_change(opened.number, "squash"))
    return opened.number


# ---------------------------------------------------------------- 관련 PR


def test_find_related_prs_reads_merged_prs_from_the_connected_repo(service, repo, a2a_body) -> None:
    n = _bad_merge(repo)
    repo.seed(title="다른 날", files={"docs/a.md": (1, 0)}, merged_at=T0 - timedelta(days=3))
    window = {
        "window_from": (T0 + timedelta(minutes=10)).isoformat(),  # 앞 30분도 본다
        "window_to": (T0 + timedelta(minutes=20)).isoformat(),
    }
    result = _call(a2a_body("find_related_prs", window))
    assert result["status"] == "completed", result
    out = FindRelatedPrsOutput.model_validate(result["output"])
    real = [c for c in out.candidates if c.connection_id]
    assert [(c.pr_number, c.connection_id, c.merge_sha) for c in real] == [
        (n, CID, f"merge{n:04d}")
    ]
    assert real[0].changed_paths == ["backend/shared/shop-api/pay.py"]
    # 구성요소로 거른다: 경로가 맞으면 남고, 다른 구성요소면 빠진다
    pay = _call(a2a_body("find_related_prs", {**window, "component": "backend/shared/shop-api"}))
    assert [c["pr_number"] for c in pay["output"]["candidates"] if c["connection_id"]] == [n]
    other = _call(a2a_body("find_related_prs", {**window, "component": "shop-web/"}))
    assert not [c for c in other["output"]["candidates"] if c["connection_id"]]
    assert any("레포 머지 PR 후보" in a.text for a in service.state.activity)


def test_connection_description_matches_component() -> None:
    conn = vcs.Connection(id="c1", kind="mock", description="쇼핑몰 결제 payment 서버")
    assert incident_repo._connection_matches(conn, "payment")
    assert not incident_repo._connection_matches(conn, "search")


# ---------------------------------------------------------------- 되돌리기


def test_revert_opens_and_merges_through_both_gates(repo) -> None:
    n = _bad_merge(repo)
    gate = Gate()
    svc = DevService(gate=gate)
    out = run(
        incident_repo.revert_merged(
            svc,
            context_id="ctx_inc_9",
            requester="infra",
            connection_id=CID,
            pr_number=n,
            reason="결제 오류 원인",
        )
    )
    assert out.status == "merged" and out.merged and out.revert_pr_number is not None
    assert out.merge_sha == f"merge{out.revert_pr_number:04d}"
    assert [a for a, _ in gate.calls] == ["repo_revert_pr", "merge_pr"]
    assert all(p["connection_id"] == CID for _, p in gate.calls)
    assert run(repo.read_file("backend/shared/shop-api/pay.py")) == "rate = 1\n"
    # 다시 불러도 되돌리기 PR을 새로 열지 않는다
    again = run(
        incident_repo.revert_merged(
            svc,
            context_id="ctx_inc_9",
            requester="infra",
            connection_id=CID,
            pr_number=n,
            reason="결제 오류 원인",
        )
    )
    assert (again.status, again.revert_pr_number) == ("merged", out.revert_pr_number)
    assert len(gate.calls) == 2


def test_revert_schedules_auto_merge_while_ci_runs(repo) -> None:
    n = _bad_merge(repo)
    repo._default_checks = (CheckRun("ci", "pending"),)
    out = run(
        incident_repo.revert_merged(
            DevService(gate=Gate()),
            context_id="c",
            requester="infra",
            connection_id=CID,
            pr_number=n,
            reason="r",
        )
    )
    assert (out.status, out.merged) == ("auto_merge_scheduled", False)
    assert out.revert_pr_number in repo.auto_merge


def test_revert_held_by_gate(repo) -> None:
    n = _bad_merge(repo)
    first = run(
        incident_repo.revert_merged(
            DevService(gate=Gate(hold={"repo_revert_pr"})),
            context_id="c",
            requester="infra",
            connection_id=CID,
            pr_number=n,
            reason="r",
        )
    )
    assert (first.status, first.revert_pr_number) == ("held", None)
    merge_held = run(
        incident_repo.revert_merged(
            DevService(gate=Gate(hold={"merge_pr"})),
            context_id="c",
            requester="infra",
            connection_id=CID,
            pr_number=n,
            reason="r",
        )
    )
    assert merge_held.status == "held" and merge_held.revert_pr_number is not None
    assert not merge_held.merged


def test_revert_merged_pr_a2a(service, repo, a2a_body) -> None:
    n = _bad_merge(repo)
    ask = {
        "connection_id": CID,
        "pr_number": n,
        "reason": "결제 오류",
        "incident_context_id": "ctx_inc_0001",
    }
    assert _call(a2a_body("revert_merged_pr", ask))["outcome"] == "missing_idempotency_key"
    key = f"ctx_inc_0001:revert_merged_pr:repo:{CID}:{n}"
    result = _call(a2a_body("revert_merged_pr", ask, idempotency_key=key))
    assert result["status"] == "completed", result
    out = RevertMergedPrOutput.model_validate(result["output"])
    assert out.status == "merged" and out.merged
    assert f"pr:{n}" in result["evidence"]["ref_ids"]
    # 병합 안 된 PR·모르는 연결은 실패로 돌려준다
    open_n = repo.seed(title="열린 PR")
    bad = _call(a2a_body("revert_merged_pr", {**ask, "pr_number": open_n}, idempotency_key="k2"))
    assert (bad["status"], bad["outcome"]) == ("failed", "not_merged")
    gone = _call(
        a2a_body("revert_merged_pr", {**ask, "connection_id": "nope"}, idempotency_key="k3")
    )
    assert gone["outcome"] == "not_found"
    # 인프라만 부를 수 있다
    denied = _call(a2a_body("revert_merged_pr", ask, token="mock:data", idempotency_key="k4"))
    assert denied["status"] != "completed"
